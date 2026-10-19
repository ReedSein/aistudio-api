"""Client evidence is bounded, protected and distinct from durable server evidence."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import tarfile

import httpx
import pytest
from fastapi import Depends, FastAPI

from aistudio_api.api import routes_accounts, routes_diagnostics
from aistudio_api.api.dependencies import get_account_service, require_web_auth
from aistudio_api.api.diagnostics_client import (
    CLIENT_BODY_LIMIT,
    CLIENT_REPORT_LIMIT,
    BrowserReport,
)
from aistudio_api.api.diagnostics_middleware import DiagnosticsMiddleware
from aistudio_api.application.account_service import AccountService
from aistudio_api.config import settings
from aistudio_api.infrastructure.account.account_store import AccountStore
from aistudio_api.infrastructure.diagnostics.store import (
    get_store,
    operation_id,
    request_id,
)

AUTH = {"Authorization": "Bearer fixture-management-password"}
OPERATION = "a" * 32


def records(directory):
    return [
        json.loads(line)
        for path in directory.glob("events-*.jsonl")
        for line in path.read_text().splitlines()
    ]


def report():
    return {
        "schema_version": 1,
        "script_version": "0.1.3",
        "browser": "Fixture browser",
        "manager": "Fixture manager",
        "manager_version": "1",
        "storage_error": False,
        "pruned_events": 0,
        "pruned_bytes": 0,
        "events": [
            {
                "id": "c" * 32,
                "session_id": "d" * 32,
                "operation_id": OPERATION,
                "timestamp": "2026-10-19T00:00:00.000Z",
                "script_version": "0.1.3",
                "sequence": 1,
                "stage": "result",
                "outcome": "unconfirmed",
                "elapsed_ms": 25.5,
                "reason": "network_error",
            }
        ],
    }


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("AISTUDIO_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(settings, "web_password", "fixture-management-password")
    monkeypatch.setattr(
        "aistudio_api.infrastructure.account.account_store._resolve_legacy_auth_file",
        lambda: None,
    )
    project = tmp_path / "project"
    project.mkdir()
    (project / "config.yaml").write_text("logging:\n  level: DEBUG\n")
    (project / "scripts").mkdir()
    (project / "scripts/cloakbrowser.version").write_text("fixture-browser")
    export = routes_diagnostics.export_bundle

    def export_here(output, **kwargs):
        return export(
            output, root=project, config_file=project / "config.yaml", legacy_dirs=()
        )

    monkeypatch.setattr(routes_diagnostics, "export_bundle", export_here)
    service = AccountService(AccountStore(tmp_path / "accounts"))
    app = FastAPI()
    app.include_router(
        routes_diagnostics.router,
        prefix="/api",
        dependencies=[Depends(require_web_auth)],
    )
    app.include_router(
        routes_accounts.router, prefix="/api", dependencies=[Depends(require_web_auth)]
    )
    app.dependency_overrides[get_account_service] = lambda: service
    app.add_middleware(DiagnosticsMiddleware)
    return app, service, tmp_path / "logs"


@pytest.mark.asyncio
async def test_report_export_contains_both_sources_and_reports_browser_gaps(isolated):
    app, _, logs = isolated
    get_store().emit("fixture_server_evidence", text="saved on server")
    payload = report()
    payload.update(storage_error=True, pruned_events=2, pruned_bytes=120)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        denied = await client.post(
            "/api/diagnostics/export", json={"client_report": payload}
        )
        assert denied.status_code == 401
        assert not any(
            row["event"] == "client_diagnostic_report" for row in records(logs)
        )
        response = await client.post(
            "/api/diagnostics/export",
            headers={**AUTH, "X-Aistudio-Operation-ID": OPERATION},
            json={"client_report": payload},
        )
    assert response.status_code == 200
    with tarfile.open(fileobj=io.BytesIO(response.content)) as archive:
        contents = {}
        for member in archive:
            if member.isfile():
                stream = archive.extractfile(member)
                assert stream is not None
                contents[member.name] = stream.read()
    manifest = json.loads(contents["manifest.json"])
    for item in manifest["files"]:
        assert hashlib.sha256(contents[item["name"]]).hexdigest() == item["sha256"]
    evidence = [
        json.loads(line)
        for name, data in contents.items()
        if name.startswith("logs/events-")
        for line in data.splitlines()
    ]
    assert any(row["event"] == "fixture_server_evidence" for row in evidence)
    saved = next(row for row in evidence if row["event"] == "client_diagnostic_report")
    assert saved["client_report"] == payload
    assert saved["evidence_source"] == "unverified_browser_report"
    assert saved["operation_id"] == OPERATION
    assert saved["request_id"] == response.headers["x-request-id"] != OPERATION
    assert manifest["has_omissions"]
    assert any(
        "browser_history_pruned" in item["reason"] for item in manifest["omissions"]
    )
    assert any(
        "browser_storage_error" in item["reason"] for item in manifest["omissions"]
    )
    assert b"fixture-management-password" not in b"".join(contents.values())


@pytest.mark.parametrize(
    "invalid", ["cookie", "type", "origin", "stack", "json", "size"]
)
@pytest.mark.asyncio
async def test_rejected_report_never_echoes_or_records_input(isolated, invalid):
    app, _, logs = isolated
    payload = report()
    if invalid == "cookie":
        payload["events"][0]["cookies"] = "fixture-forbidden-secret"
    elif invalid == "type":
        payload["events"][0]["cookie_count"] = {"value": "fixture-forbidden-secret"}
    elif invalid == "origin":
        payload["events"][0]["server_origin"] = (
            "http://user:fixture-forbidden-secret@example.invalid/"
        )
    elif invalid == "stack":
        payload["events"][0]["error_stack"] = "Error: fixture-forbidden-secret"
    content = json.dumps({"client_report": payload}).encode()
    if invalid == "json":
        content = b'{"fixture-forbidden-secret"'
    elif invalid == "size":
        content = b"fixture-forbidden-secret" + b" " * CLIENT_BODY_LIMIT
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/diagnostics/export", headers=AUTH, content=content
        )
    assert response.status_code == (413 if invalid == "size" else 400)
    assert "fixture-forbidden-secret" not in response.text + json.dumps(records(logs))
    assert not any(row["event"] == "client_diagnostic_report" for row in records(logs))


@pytest.mark.asyncio
async def test_capacity_counts_recorded_fields_without_expanding_absent_values(
    isolated,
):
    app, _, _ = isolated
    payload = report()
    payload["events"] = [
        {**payload["events"][0], "id": f"{index:032x}", "sequence": index}
        for index in range(2500)
    ]
    assert len(json.dumps({"client_report": payload}).encode()) < CLIENT_REPORT_LIMIT
    assert (
        len(BrowserReport.model_validate(payload).model_dump_json().encode())
        > CLIENT_REPORT_LIMIT
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/diagnostics/export", headers=AUTH, json={"client_report": payload}
        )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_report_capture_failure_is_explicit(isolated, monkeypatch):
    app, _, _ = isolated
    monkeypatch.setattr(type(get_store()), "emit", lambda *args, **kwargs: False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/diagnostics/export", headers=AUTH, json={"client_report": report()}
        )
    assert response.status_code == 503


@pytest.mark.parametrize("drop_response", [False, True])
@pytest.mark.asyncio
async def test_durable_import_and_delivery_have_separate_correlated_evidence(
    isolated, monkeypatch, drop_response
):
    app, service, logs = isolated

    async def probe(cookies):
        assert cookies == "SID=fixture-import-cookie"
        return [{"auth_user": "0"}, {"auth_user": "1"}]

    monkeypatch.setattr(
        "aistudio_api.infrastructure.account.cookie_parser.probe_google_accounts_infinite",
        probe,
    )
    sent = []

    async def receive():
        return {
            "type": "http.request",
            "body": b'{"cookies":"SID=fixture-import-cookie"}',
            "more_body": False,
        }

    async def send(message):
        if message["type"] == "http.response.body" and drop_response:
            raise BrokenPipeError("fixture reply dropped after import")
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "method": "POST",
        "path": "/api/accounts/probe-import",
        "query_string": b"",
        "headers": [
            (b"authorization", AUTH["Authorization"].encode()),
            (b"content-type", b"application/json"),
            (b"x-aistudio-operation-id", OPERATION.encode()),
        ],
        "scheme": "http",
        "server": ("test", 80),
        "client": ("127.0.0.1", 1),
        "root_path": "",
    }
    if drop_response:
        with pytest.raises(BrokenPipeError):
            await app(scope, receive, send)
    else:
        await app(scope, receive, send)
    accounts = service.list_accounts()
    assert len(accounts) == 2
    for account in accounts:
        path = service.get_account_auth_path(account.id)
        assert path is not None and "fixture-import-cookie" in path.read_text()
    rows = records(logs)
    saved = [row for row in rows if row["event"] == "account_import_account_saved"]
    assert {row["account_id"] for row in saved} == {account.id for account in accounts}
    assert [row["saved_count"] for row in saved] == [1, 2]
    assert any(
        row["event"] == "account_import_completed"
        and row["response_received_by_client"] == "unknown"
        for row in rows
    )
    end = next(row for row in rows if row["event"] == "http_end")
    assert end["outcome"] == ("client_disconnected" if drop_response else "completed")
    assert end["response_complete"] is not drop_response
    identifier = dict(sent[0]["headers"])[b"x-request-id"].decode()
    assert all(
        row["operation_id"] == OPERATION and row["request_id"] == identifier
        for row in rows
    )
    assert "fixture-import-cookie" not in json.dumps(rows)
    assert operation_id.get() == request_id.get() == ""


def test_partial_save_failure_preserves_committed_count_and_exception(
    isolated, monkeypatch
):
    _, service, logs = isolated
    save = service._store.save_account
    calls = 0

    def fail_second(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("fixture disk failure")
        return save(**kwargs)

    monkeypatch.setattr(service._store, "save_account", fail_second)
    with pytest.raises(OSError, match="fixture disk failure"):
        service.batch_import_accounts(
            [{"auth_user": "0"}, {"auth_user": "1"}], {"cookies": []}
        )
    accounts = service.list_accounts()
    assert len(accounts) == 1
    failure = next(
        row for row in records(logs) if row["event"] == "account_import_save_failed"
    )
    assert failure["saved_count"] == 1 and failure["expected_count"] == 2
    assert failure["account_ids"] == [accounts[0].id] and failure["stage"] == "save"
    assert not any(row["event"] == "account_import_saved" for row in records(logs))


@pytest.mark.asyncio
async def test_client_operation_is_validated_and_isolated_between_requests(isolated):
    _, _, logs = isolated

    async def producer(scope, receive, send):
        await asyncio.sleep(0)
        get_store().emit("fixture_inside_request")
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    async def invoke(operation):
        async def receive():
            return {"type": "http.request", "body": b""}

        async def send(message):
            return None

        await DiagnosticsMiddleware(producer)(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/test",
                "headers": [(b"X-Aistudio-Operation-ID", operation.encode())],
            },
            receive,
            send,
        )

    await asyncio.gather(
        invoke(OPERATION), invoke("b" * 32), invoke("fixture-invalid-operation-secret")
    )
    rows = records(logs)
    assert {
        row["operation_id"] for row in rows if row["event"] == "fixture_inside_request"
    } == {OPERATION, "b" * 32, ""}
    for identifier in {row["request_id"] for row in rows}:
        assert (
            len(
                {row["operation_id"] for row in rows if row["request_id"] == identifier}
            )
            == 1
        )
    assert "fixture-invalid-operation-secret" not in json.dumps(rows)
    assert operation_id.get() == request_id.get() == ""
