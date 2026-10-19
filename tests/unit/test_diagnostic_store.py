"""Private storage, credential redaction, retention and visible collection failures."""

from __future__ import annotations

import json
import os
import time

from aistudio_api.infrastructure.diagnostics.redaction import (
    redact_text,
    redact_url,
    sanitize,
)
from aistudio_api.infrastructure.diagnostics.store import DiagnosticStore, LogPolicy


def test_credentials_removed_but_thought_signature_and_tool_result_preserved():
    original = {
        "authorization": "Bearer fixture-credential-only",
        "query": {"key": "fixture-query-credential"},
        "cookies": [{"name": "SID", "value": "fixture-cookie-value"}],
        "parts": [
            {"thoughtSignature": "native-signature-is-not-an-auth-token"},
            {
                "functionResponse": {
                    "response": {
                        "content": "工具结果完整保留",
                        "key": "ordinary-business-key",
                    }
                }
            },
        ],
        "usageMetadata": {"totalTokenCount": 99},
    }
    cleaned = sanitize(original)
    serialized = json.dumps(cleaned, ensure_ascii=False)
    assert "fixture-credential-only" not in serialized
    assert "fixture-query-credential" not in serialized
    assert "fixture-cookie-value" not in serialized
    assert "native-signature-is-not-an-auth-token" in serialized
    assert "工具结果完整保留" in serialized
    assert "ordinary-business-key" in serialized
    assert "99" in serialized
    assert "fixture-url-key" not in redact_url(
        "/v1beta/models/m:generateContent?%6bey=fixture-url-key&alt=sse"
    )


def test_retention_capacity_permissions_and_visible_cleanup(tmp_path):
    store = DiagnosticStore(
        LogPolicy(tmp_path / "logs", retention_days=1, max_bytes=1200)
    )
    store.emit("old", text="old record")
    old = store.files()[0]
    past = time.time() - 172800
    os.utime(old, (past, past))
    store.emit("current", text="本次记录")
    assert not old.exists()
    for index in range(10):
        store.emit("capacity", index=index, text="x" * 200)
    assert sum(path.stat().st_size for path in store.files()) <= 1200
    assert (tmp_path / "logs/.retention.jsonl").exists()
    assert (tmp_path / "logs").stat().st_mode & 0o777 == 0o700
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in store.files())


def test_bad_event_is_visible_and_does_not_block_later_events(tmp_path, capfd):
    store = DiagnosticStore(LogPolicy(tmp_path / "logs"))
    assert not store.emit("invalid", invalid=object())
    assert "TypeError" in capfd.readouterr().err
    assert store.emit("valid", text="业务仍可继续")
    assert store.status()["collection_error"] == "TypeError"


def test_policy_is_preserved_for_offline_export_when_shell_overrides_are_absent(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("AISTUDIO_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("AISTUDIO_LOG_RETENTION_DAYS", "14")
    monkeypatch.setenv("AISTUDIO_LOG_MAX_MIB", "24")
    policy = LogPolicy.from_environment()
    assert DiagnosticStore(policy).emit("configured")
    monkeypatch.delenv("AISTUDIO_LOG_RETENTION_DAYS")
    monkeypatch.delenv("AISTUDIO_LOG_MAX_MIB")
    assert LogPolicy.from_environment() == policy


def test_api_key_in_route_path_is_redacted():
    assert "fixture-path-secret" not in redact_text(
        "DELETE /api-keys/fixture-path-secret -> 200"
    )


def test_python_capture_does_not_claim_or_change_unrelated_directory(tmp_path):
    logs = tmp_path / "personal"
    logs.mkdir()
    logs.chmod(0o755)
    (logs / "script-personal.log").write_text("unrelated local data")
    store = DiagnosticStore(LogPolicy(logs))
    assert not store.emit("refused")
    assert store.status()["collection_error"] == "DiagnosticError"
    assert logs.stat().st_mode & 0o777 == 0o755
    assert sorted(path.name for path in logs.iterdir()) == ["script-personal.log"]


def test_invalid_policy_is_visible_without_breaking_business_or_replacing_it(
    tmp_path, monkeypatch, capfd
):
    from aistudio_api.infrastructure.diagnostics.store import get_store

    logs = tmp_path / "logs"
    DiagnosticStore(LogPolicy(logs)).emit("valid")
    (logs / ".policy").write_text("damaged policy")
    monkeypatch.setenv("AISTUDIO_LOG_DIR", str(logs))
    store = get_store()
    assert not store.emit("unavailable")
    assert store.status()["collection_error"] == "DiagnosticError"
    assert "DiagnosticError" in capfd.readouterr().err
    assert (logs / ".policy").read_text() == "damaged policy"
    (logs / ".policy").write_text("7 536870912\n")
    assert get_store().emit("recovered")
