"""Owned-process lifecycle and fork update behavior, without real accounts."""

from __future__ import annotations

import json
import socket
import subprocess
import sys
from dataclasses import asdict, replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from termux_runtime import (
    Installation,
    ManagerError,
    ProcessRecord,
    Service,
    operation_lock,
    write_object,
)
from termux_update import Updater


@pytest.fixture
def installation(tmp_path, monkeypatch):
    root = tmp_path / "project with spaces"
    root.mkdir()
    (root / "config.yaml").write_text("api_keys: []\n")
    (root / ".venv/bin").mkdir(parents=True)
    (root / ".venv/bin/python").symlink_to(sys.executable)
    (root / "main.py").write_text("""
import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
parser = argparse.ArgumentParser()
parser.add_argument("command")
parser.add_argument("--port", type=int)
args = parser.parse_args()
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"status":"ok"}')
print("test service stdout", flush=True)
HTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
""")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))
    monkeypatch.delenv("AISTUDIO_CONFIG_FILE", raising=False)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        monkeypatch.setenv("AISTUDIO_BROWSER_PORT", str(probe.getsockname()[1]))
    result = Installation.load(root, port)
    yield result
    service = Service(result)
    if service.running():
        service.stop()


def test_register_preserves_configuration_and_quotes_paths(installation):
    entry = installation.register()
    installation.config_file.write_text("user_setting: keep\n")
    installation.register()
    assert installation.config_file.read_text() == "user_setting: keep\n"
    assert "project with spaces" in entry.read_text()
    assert entry.stat().st_mode & 0o777 == 0o755
    assert installation.config_file.stat().st_mode & 0o777 == 0o600
    assert Installation.load().root == installation.root


def test_register_refuses_an_unrelated_command(installation):
    entry = Path.home() / ".local/bin/aistudio"
    entry.parent.mkdir(parents=True)
    entry.write_text("unrelated command")
    with pytest.raises(ManagerError, match="其他命令"):
        installation.register()
    assert entry.read_text() == "unrelated command"


def test_silent_start_is_ready_and_stop_only_owns_its_process(installation, capfd):
    installation.register()
    service = Service(installation)
    process = service.start("silent", timeout=10)
    try:
        assert service.healthy()
        running = service.running()
        assert running is not None and running.pid == process.pid
        assert "test service stdout" not in capfd.readouterr().out
        with pytest.raises(ManagerError, match="已经运行"):
            service.start("silent")
        assert service.stop()
        process.wait(timeout=5)
        assert service.running() is None
        assert not list(installation.state_dir.glob("*.log"))
    finally:
        if process.poll() is None:
            service.stop()
            process.wait(timeout=5)


def test_stale_identity_never_stops_a_live_process(installation):
    service = Service(installation)
    process = service.start("silent", timeout=10)
    original = ProcessRecord.load(installation.process_file)
    try:
        stale = replace(original, start_ticks=str(int(original.start_ticks) + 1))
        write_object(installation.process_file, asdict(stale))
        assert not service.stop()
        assert process.poll() is None
        assert service.healthy()
    finally:
        write_object(installation.process_file, asdict(original))
        service.stop()
        process.wait(timeout=5)


def test_foreign_listener_and_parallel_operations_are_not_overwritten(installation):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", installation.port))
        with pytest.raises(ManagerError, match="端口"):
            Service(installation).start("silent")
    with (
        operation_lock(installation),
        pytest.raises(ManagerError, match="另一个"),
        operation_lock(installation),
    ):
        pytest.fail("a concurrent operation acquired the lock")


def test_silent_environment_disables_all_dump_paths(installation):
    env = Service(installation).environment("silent")
    assert env["AISTUDIO_SILENT"] == "1"
    assert env["AISTUDIO_DUMP_FILE"] == "0"
    assert env["AISTUDIO_DUMP_RAW_RESPONSE"] == "0"


def test_browser_port_conflict_does_not_launch_a_service(installation, monkeypatch):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        monkeypatch.setenv("AISTUDIO_BROWSER_PORT", str(listener.getsockname()[1]))
        with pytest.raises(ManagerError, match="端口"):
            Service(installation).start("silent")
    assert not installation.process_file.exists()


def test_failed_metadata_write_cleans_up_the_owned_child(installation, monkeypatch):
    import termux_runtime

    original = subprocess.Popen
    children = []

    def spawn(*args, **kwargs):
        process = original(*args, **kwargs)
        children.append(process)
        return process

    def fail(*_args):
        raise OSError("fixture metadata failure")

    monkeypatch.setattr(termux_runtime.subprocess, "Popen", spawn)
    monkeypatch.setattr(termux_runtime, "write_object", fail)
    with pytest.raises(OSError, match="metadata failure"):
        Service(installation).start("silent")
    assert len(children) == 1
    assert children[0].poll() is not None
    assert not installation.process_file.exists()


def test_service_can_restart_immediately_after_stop(installation):
    service = Service(installation)
    for _ in range(2):
        process = service.start("silent", timeout=10)
        service.stop()
        process.wait(timeout=5)


def test_restart_preserves_original_launch_environment(installation, monkeypatch):
    service = Service(installation)
    monkeypatch.setenv("AISTUDIO_TIMEZONE", "Etc/UTC")
    process = service.start("silent", timeout=10)
    record = service.running()
    assert record is not None
    monkeypatch.setenv("AISTUDIO_TIMEZONE", "Europe/Berlin")
    original_env = service.capture_environment(record)
    assert original_env["AISTUDIO_TIMEZONE"] == "Etc/UTC"
    service.stop()
    process.wait(timeout=5)
    restarted = service.start("silent", timeout=10, inherited_env=original_env)
    try:
        restarted_record = service.running()
        assert restarted_record is not None
        assert (
            service.capture_environment(restarted_record)["AISTUDIO_TIMEZONE"]
            == "Etc/UTC"
        )
        assert "AISTUDIO_TIMEZONE" not in installation.process_file.read_text()
    finally:
        service.stop()
        restarted.wait(timeout=5)


def test_foreground_interrupt_stops_and_returns(installation, monkeypatch):
    service = Service(installation)
    process = service.start("foreground", timeout=10)
    original_wait = process.wait
    interrupted = False

    def wait(*args, **kwargs):
        nonlocal interrupted
        if not interrupted:
            interrupted = True
            raise KeyboardInterrupt
        return original_wait(*args, **kwargs)

    monkeypatch.setattr(process, "wait", wait)
    service.foreground(process)
    assert process.poll() is not None
    assert service.running() is None


def git(root, *args):
    return subprocess.run(
        ["git", *args], cwd=root, text=True, capture_output=True, check=True
    ).stdout.strip()


@pytest.fixture
def update_repositories(tmp_path):
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "--bare", "--initial-branch=master", str(remote))
    source = tmp_path / "source"
    git(tmp_path, "clone", str(remote), str(source))
    for key, value in (
        ("user.name", "Test"),
        ("user.email", "test@example.invalid"),
        ("commit.gpgsign", "false"),
    ):
        git(source, "config", key, value)
    (source / "main.py").write_text("# fixture\n")
    git(source, "add", "main.py")
    git(source, "commit", "-m", "initial")
    git(source, "push", "-u", "origin", "master")
    client = tmp_path / "client"
    git(tmp_path, "clone", str(remote), str(client))
    git(client, "config", "user.name", "Test")
    git(client, "config", "user.email", "test@example.invalid")
    git(client, "config", "commit.gpgsign", "false")
    installation = Installation(client, tmp_path / "config", tmp_path / "state")
    return Updater(installation, str(remote)), source


def publish(source, name="version.txt"):
    (source / name).write_text("new version\n")
    git(source, "add", name)
    git(source, "commit", "-m", "new version")
    git(source, "push", "origin", "master")


def test_update_refuses_author_repository(installation):
    git(installation.root, "init", "--initial-branch=master")
    git(
        installation.root,
        "remote",
        "add",
        "origin",
        "https://github.com/DeconstructedCube/aistudio-api.git",
    )
    with pytest.raises(ManagerError, match="来源不符"):
        Updater(installation).check()


def test_fast_forward_updates_manager_code_and_handles_no_update(
    update_repositories, monkeypatch
):
    updater, source = update_repositories
    assert updater.check().commits == 0
    publish(source, "manager.py")
    assert updater.check().commits == 1
    monkeypatch.setattr(updater, "prepare", lambda: None)
    monkeypatch.setattr(Installation, "register", lambda self: Path("unused"))
    changed, process = updater.update()
    assert changed and process is None
    assert (updater.installation.root / "manager.py").read_text() == "new version\n"
    assert updater.check().commits == 0


def test_local_edits_and_divergence_stop_updates(update_repositories):
    updater, source = update_repositories
    root = updater.installation.root
    (root / "main.py").write_text("# local edit\n")
    with pytest.raises(ManagerError, match="未提交"):
        updater.check()
    git(root, "add", "main.py")
    git(root, "commit", "-m", "local")
    publish(source)
    head = git(root, "rev-parse", "HEAD")
    with pytest.raises(ManagerError, match="分叉"):
        updater.check()
    assert git(root, "rev-parse", "HEAD") == head


def test_update_stops_instead_of_overwriting_ignored_local_files(
    update_repositories, monkeypatch
):
    updater, source = update_repositories
    root = updater.installation.root
    with (root / ".git/info/exclude").open("a") as exclude:
        exclude.write("/notes.md\n")
    (root / "notes.md").write_text("local notes\n")
    head = git(root, "rev-parse", "HEAD")
    publish(source, "notes.md")
    monkeypatch.setattr(updater, "prepare", lambda: None)
    monkeypatch.setattr(Installation, "register", lambda self: Path("unused"))
    with pytest.raises(ManagerError, match=r"notes\.md"):
        updater.update()
    assert (root / "notes.md").read_text() == "local notes\n"
    assert git(root, "rev-parse", "HEAD") == head


def test_failed_environment_keeps_recovery_record_and_user_data(
    update_repositories, monkeypatch
):
    updater, source = update_repositories
    root = updater.installation.root
    (root / "data").mkdir()
    (root / "data/account.json").write_text("fixture user data")
    publish(source)

    def fail():
        raise ManagerError("fixture dependency failure")

    monkeypatch.setattr(updater, "prepare", fail)
    with pytest.raises(ManagerError, match="dependency failure"):
        updater.update()
    journal = json.loads(
        (updater.installation.state_dir / "last-update.json").read_text()
    )
    assert journal["stage"] == "environment"
    assert journal["previous_commit"] != journal["target_commit"]
    assert (root / "data/account.json").read_text() == "fixture user data"


def test_network_failure_is_reported(update_repositories):
    updater, _ = update_repositories
    git(
        updater.installation.root,
        "remote",
        "set-url",
        "origin",
        str(updater.installation.root / "missing.git"),
    )
    updater.repository = str(updater.installation.root / "missing.git")
    with pytest.raises(ManagerError, match="Git 操作失败"):
        updater.check()


def test_installation_default_port_is_11451():
    installation = Installation(Path("/nonexistent"), Path("/cfg"), Path("/state"))
    assert installation.port == 11451
