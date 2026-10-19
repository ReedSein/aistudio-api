"""Failure recovery, live export and offline script support without real accounts."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tarfile
from pathlib import Path

from aistudio_api.infrastructure.diagnostics.store import DiagnosticStore, LogPolicy

ROOT = Path(__file__).resolve().parents[2]


def test_offline_export_does_not_import_application_dependencies(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    logs = tmp_path / "logs"
    DiagnosticStore(LogPolicy(logs)).emit("offline", text="离线导出的完整原文")
    output = tmp_path / "offline.tar.gz"
    result = subprocess.run(
        [
            sys.executable,
            "-S",
            str(ROOT / "scripts/termux_diagnostics.py"),
            "--project-root",
            str(root),
            "--output",
            str(output),
        ],
        env={**os.environ, "AISTUDIO_LOG_DIR": str(logs)},
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    with tarfile.open(output) as archive:
        assert archive.getmember("manifest.json")


def test_system_python_failure_produces_an_explicit_basic_archive(tmp_path):
    binary = tmp_path / "bin"
    binary.mkdir()
    python = binary / "python"
    python.write_text("#!/bin/sh\necho 'interpreter cannot start' >&2\nexit 127\n")
    python.chmod(0o755)
    logs = tmp_path / "logs"
    DiagnosticStore(LogPolicy(logs)).emit("basic", text="基础记录")
    (logs / "script-failure.log").write_text(
        "Cookie: fixture-fallback-cookie\ninstallation failed\n"
    )
    output = tmp_path / "basic.tar.gz"
    result = subprocess.run(
        ["bash", str(ROOT / "scripts/export-diagnostics.sh"), "--output", str(output)],
        env={
            **os.environ,
            "PATH": f"{binary}:{os.environ['PATH']}",
            "AISTUDIO_LOG_DIR": str(logs),
        },
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    with tarfile.open(output) as archive:
        assert archive.getmember("manifest.txt")
        content = archive.extractfile("logs/script-failure.log.txt")
        assert content is not None
        text = content.read().decode()
        assert "installation failed" in text and "fixture-fallback-cookie" not in text


def test_shell_capture_keeps_exit_status_and_redacts_saved_errors(tmp_path):
    logs = tmp_path / "logs"
    result = subprocess.run(
        [
            "bash",
            str(ROOT / "scripts/install-termux.sh"),
            "--diagnostics-exec",
            sys.executable,
            "-c",
            "import sys; print('native startup failure'); print('Cookie: fixture-script-cookie', file=sys.stderr); print('{\"cookies\":[{\"value\":\"fixture-json-cookie\"}]}', file=sys.stderr); sys.exit(42)",
        ],
        env={
            **os.environ,
            "AISTUDIO_LOG_DIR": str(logs),
            "AISTUDIO_SHELL_CAPTURE_ACTIVE": "0",
        },
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 42
    text = "".join(path.read_text() for path in logs.glob("script-*.log"))
    assert "native startup failure" in text
    assert "fixture-script-cookie" not in text
    assert "fixture-json-cookie" not in text


def test_documented_bootstrap_saves_download_failure_without_running_installer(
    tmp_path,
):
    prefix = tmp_path / "prefix"
    apt = prefix / "lib/apt/apt-helper"
    apt.parent.mkdir(parents=True)
    apt.write_text(
        "#!/bin/sh\necho 'TLS failed for https://fixture-user:fixture-proxy-password@example.invalid' >&2\nexit 23\n"
    )
    apt.chmod(0o755)
    snippet = re.search(r"```bash\n(.*?)\n```", (ROOT / "README.md").read_text(), re.S)
    assert snippet is not None
    logs = tmp_path / "logs"
    result = subprocess.run(
        ["bash", "-c", snippet[1]],
        env={**os.environ, "PREFIX": str(prefix), "AISTUDIO_LOG_DIR": str(logs)},
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 23
    text = "".join(path.read_text() for path in logs.glob("script-bootstrap-*.log"))
    assert "TLS failed" in text and "bootstrap exit=23" in text
    assert "fixture-proxy-password" not in text


def test_shell_capture_does_not_claim_or_change_unrelated_directory(tmp_path):
    logs = tmp_path / "personal"
    logs.mkdir(mode=0o755)
    original_mode = logs.stat().st_mode
    (logs / "script-personal.log").write_text("unrelated local data")
    result = subprocess.run(
        ["bash", str(ROOT / "scripts/install-termux.sh"), "--diagnostics-exec", "true"],
        env={
            **os.environ,
            "AISTUDIO_LOG_DIR": str(logs),
            "AISTUDIO_SHELL_CAPTURE_ACTIVE": "0",
        },
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0
    assert "unowned nonempty" in result.stderr
    assert not (logs / ".aistudio-diagnostics-v1").exists()
    assert logs.stat().st_mode == original_mode
    assert (logs / "script-personal.log").read_text() == "unrelated local data"
