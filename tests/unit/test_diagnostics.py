"""Conversation fidelity, streaming behavior and private diagnostic archive boundaries."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from aistudio_api.api.app import app
from aistudio_api.api.diagnostics_middleware import DiagnosticsMiddleware
from aistudio_api.infrastructure.diagnostics.store import (
    get_store,
)


def records(directory: Path) -> list[dict]:
    return [
        json.loads(line)
        for path in sorted(directory.glob("events-*.jsonl"))
        for line in path.read_text().splitlines()
    ]


@pytest.mark.asyncio
async def test_stream_is_delivered_before_completion_and_every_frame_is_recorded(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("AISTUDIO_LOG_DIR", str(tmp_path / "logs"))
    prompt = "请求原文" * 2000
    first = 'data: {"text":"' + "首段" * 3000 + '"}\n\n'
    last = 'data: {"thoughtSignature":"原生签名"}\n\ndata: [DONE]\n\n'
    sent = []
    received = False

    async def receive():
        nonlocal received
        assert not received
        received = True
        return {
            "type": "http.request",
            "body": json.dumps({"contents": [{"text": prompt}]}).encode(),
            "more_body": False,
        }

    async def send(message):
        sent.append(message)

    async def producer(scope, receive, send):
        await receive()
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/event-stream")],
            }
        )
        raw = first.encode()
        await send({"type": "http.response.body", "body": raw[:19], "more_body": True})
        await send({"type": "http.response.body", "body": raw[19:], "more_body": True})
        assert b"".join(m.get("body", b"") for m in sent) == raw
        assert any(
            r["event"] == "http_response_body" for r in records(tmp_path / "logs")
        )
        await send(
            {"type": "http.response.body", "body": last.encode(), "more_body": False}
        )

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/v1beta/models/test:streamGenerateContent",
        "headers": [(b"authorization", b"Bearer fixture-stream-key")],
        "query_string": b"key=fixture-query-key",
    }
    await DiagnosticsMiddleware(producer)(scope, receive, send)
    rows = records(tmp_path / "logs")
    assert (
        "".join(r["text"] for r in rows if r["event"] == "http_response_body")
        == first + last
    )
    captured_request = "".join(
        r["text"] for r in rows if r["event"] == "http_request_body"
    )
    assert json.loads(captured_request)["contents"][0]["text"] == prompt
    assert b"".join(m.get("body", b"") for m in sent) == (first + last).encode()
    end = next(r for r in rows if r["event"] == "http_end")
    assert end["outcome"] == "completed" and end["response_complete"]
    assert end["response_chunks"] == 3
    assert end["response_bytes"] == len((first + last).encode())
    assert end["first_byte_ms"] is not None
    assert len({r["request_id"] for r in rows}) == 1
    all_rows = json.dumps(rows)
    assert "fixture-stream-key" not in all_rows and "fixture-query-key" not in all_rows


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (RuntimeError("upstream failed"), "exception"),
        (TimeoutError("upstream timeout"), "timeout"),
        (asyncio.CancelledError(), "cancelled"),
        (BrokenPipeError(), "client_disconnected"),
    ],
)
@pytest.mark.asyncio
async def test_interrupted_stream_keeps_partial_content_and_truthful_outcome(
    tmp_path, monkeypatch, error, expected
):
    monkeypatch.setenv("AISTUDIO_LOG_DIR", str(tmp_path / "logs"))

    async def producer(scope, receive, send):
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/event-stream")],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": b'data: {"text":"partial"}',
                "more_body": True,
            }
        )
        raise error

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        return None

    with pytest.raises(type(error)):
        await DiagnosticsMiddleware(producer)(
            {
                "type": "http",
                "method": "POST",
                "path": "/v1beta/models/test:streamGenerateContent",
            },
            receive,
            send,
        )
    rows = records(tmp_path / "logs")
    assert any("partial" in r.get("text", "") for r in rows)
    end = next(r for r in rows if r["event"] == "http_end")
    assert end["outcome"] == expected and not end["response_complete"]


@pytest.mark.asyncio
async def test_concurrent_requests_keep_body_and_response_correlation(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("AISTUDIO_LOG_DIR", str(tmp_path / "logs"))

    async def producer(scope, receive, send):
        received = await receive()
        value = json.loads(received["body"])["text"]
        await asyncio.sleep(0)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await asyncio.sleep(0)
        await send(
            {"type": "http.response.body", "body": value.encode(), "more_body": False}
        )

    middleware = DiagnosticsMiddleware(producer)

    async def invoke(value):
        sent = []

        async def receive():
            return {
                "type": "http.request",
                "body": json.dumps({"text": value}).encode(),
                "more_body": False,
            }

        async def send(message):
            sent.append(message)

        await middleware(
            {
                "type": "http",
                "method": "POST",
                "path": "/v1beta/models/test:generateContent",
            },
            receive,
            send,
        )
        identifier = dict(sent[0]["headers"])[b"x-request-id"].decode()
        return identifier, value

    results = await asyncio.gather(
        invoke("first-request-body"), invoke("second-request-body")
    )
    assert results[0][0] != results[1][0]
    rows = records(tmp_path / "logs")
    for identifier, value in results:
        selected = [row for row in rows if row["request_id"] == identifier]
        request = "".join(
            row["text"] for row in selected if row["event"] == "http_request_body"
        )
        response = "".join(
            row["text"] for row in selected if row["event"] == "http_response_body"
        )
        assert json.loads(request)["text"] == value
        assert response == value


@pytest.mark.asyncio
async def test_web_export_uses_management_auth_and_does_not_log_archive(
    tmp_path, monkeypatch
):
    from aistudio_api.api import routes_diagnostics
    from aistudio_api.config import settings

    monkeypatch.setenv("AISTUDIO_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(settings, "web_password", "fixture-web-password")
    project = tmp_path / "project"
    project.mkdir()
    real_export = routes_diagnostics.export_bundle

    def isolated_export(output, **kwargs):
        return real_export(
            output, root=project, config_file=project / "config.yaml", legacy_dirs=()
        )

    monkeypatch.setattr(routes_diagnostics, "export_bundle", isolated_export)
    get_store().emit("test", text="诊断测试正文")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.post("/api/diagnostics/export")).status_code == 401
        response = await client.post(
            "/api/diagnostics/export",
            headers={"Authorization": "Bearer fixture-web-password"},
        )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.content[:2] == b"\x1f\x8b"
    assert not any(
        r["event"] == "http_response_body" for r in records(tmp_path / "logs")
    )
