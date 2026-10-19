"""Safe support archives, damaged records and concurrent log writing."""

from __future__ import annotations

import hashlib
import json
import tarfile
import threading

import pytest

from aistudio_api.infrastructure.diagnostics.bundle import export_bundle
from aistudio_api.infrastructure.diagnostics.store import DiagnosticStore, LogPolicy


def test_export_manifest_hashes_old_dump_redaction_and_symlink_refusal(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    root = tmp_path / "project"
    root.mkdir()
    (root / "scripts").mkdir()
    (root / "scripts/cloakbrowser.version").write_text("146.0.7680.177.4\n")
    (root / "config.yaml").write_text(
        "api_keys:\n  - do-not-export-this-key\nlogging:\n  level: INFO\n"
    )
    policy = LogPolicy(tmp_path / "logs")
    store = DiagnosticStore(policy)
    store.emit("example", text="完整对话" * 3000)
    legacy = root / "dumps"
    legacy.mkdir()
    (legacy / "req_example.json").write_text(
        json.dumps(
            {
                "req_id": "old",
                "status_code": 200,
                "headers": {"cookie": "fixture-old-cookie"},
                "body": {"text": "原文完整"},
            }
        )
    )
    (legacy / "opaque.json").write_text('["opaque-auth-fixture"]')
    outside = tmp_path / "outside.json"
    outside.write_text(
        '{"req_id":"outside", "status_code":200, "text":"must-not-export"}'
    )
    (legacy / "linked.json").symlink_to(outside)
    output = tmp_path / "report.tar.gz"
    manifest = export_bundle(output, root=root, policy=policy)
    with tarfile.open(output) as archive:
        contents = {}
        for member in archive.getmembers():
            stream = archive.extractfile(member)
            assert stream is not None
            contents[member.name] = stream.read()
    assert output.stat().st_mode & 0o777 == 0o600
    assert manifest["has_omissions"]
    inventory = manifest["files"]
    assert isinstance(inventory, list)
    for entry in inventory:
        assert hashlib.sha256(contents[entry["name"]]).hexdigest() == entry["sha256"]
    combined = b"".join(contents.values()).decode()
    assert "fixture-old-cookie" not in combined
    assert "do-not-export-this-key" not in combined
    assert "must-not-export" not in combined and "opaque-auth-fixture" not in combined
    assert "原文完整" in combined and "完整对话" in combined
    with pytest.raises(FileExistsError):
        export_bundle(output, root=root, policy=policy)


def test_export_salvages_valid_records_before_a_damaged_tail(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    policy = LogPolicy(tmp_path / "logs")
    store = DiagnosticStore(policy)
    store.emit("before_crash", text="有效记录必须保留")
    path = store.files()[0]
    with path.open("ab") as stream:
        stream.write(b'{"event":"partial","text":"\xe7')
    output = tmp_path / "partial.tar.gz"
    manifest = export_bundle(output, root=root, policy=policy)
    assert manifest["has_omissions"]
    assert manifest["coverage_start"] and manifest["coverage_end"]
    with tarfile.open(output) as archive:
        content = archive.extractfile("logs/" + path.name)
        assert content is not None
        assert json.loads(content.read())["text"] == "有效记录必须保留"


def test_archive_compression_does_not_hold_the_log_writer_lock(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    policy = LogPolicy(tmp_path / "logs")
    store = DiagnosticStore(policy)
    store.emit("snapshot_content", text="截止点之前")
    original = tarfile.TarFile.addfile
    workers = []

    def addfile(archive, member, fileobj=None):
        if member.name.startswith("logs/events-"):
            worker = threading.Thread(
                target=lambda: store.emit("after_cutoff", text="导出时仍能写入")
            )
            workers.append(worker)
            worker.start()
            worker.join(timeout=2)
            assert not worker.is_alive(), "compression must not block the writer lock"
        return original(archive, member, fileobj)

    monkeypatch.setattr(tarfile.TarFile, "addfile", addfile)
    try:
        export_bundle(tmp_path / "live.tar.gz", root=root, policy=policy)
    finally:
        for worker in workers:
            worker.join(timeout=2)
    with tarfile.open(tmp_path / "live.tar.gz") as archive:
        for member in archive.getmembers():
            if member.name.startswith("logs/events-"):
                stream = archive.extractfile(member)
                assert stream is not None
                assert "after_cutoff" not in stream.read().decode()
