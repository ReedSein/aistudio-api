"""Owned-process lifecycle and fork update behavior, without real accounts."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from dataclasses import asdict, replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from termux_manager import release_check_message, release_notice_line
from termux_release import ReleaseCheck
from termux_runtime import (
    Installation,
    ManagerError,
    ProcessRecord,
    Service,
    operation_lock,
    write_object,
)
from termux_uninstall import Uninstaller
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
    monkeypatch.delenv("AISTUDIO_PROOT_NAME", raising=False)
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
    record = json.loads(
        (installation.config_dir / "installation.json").read_text(encoding="utf-8")
    )
    assert record["proot_name"] == "aistudio-api-termux"
    assert "project with spaces" in entry.read_text()
    assert entry.stat().st_mode & 0o777 == 0o755
    assert installation.config_file.stat().st_mode & 0o777 == 0o600
    loaded = Installation.load()
    assert loaded.root == installation.root
    assert loaded.proot_name == "aistudio-api-termux"


def _run_manager_register(installation, tmp_path, inherited_config=None):
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("AISTUDIO_")
    }
    env.update(
        HOME=str(tmp_path / "home"),
        XDG_CONFIG_HOME=str(tmp_path / "config"),
        XDG_STATE_HOME=str(tmp_path / "state"),
        AISTUDIO_LOG_DIR=str(tmp_path / "logs"),
    )
    if inherited_config is not None:
        env["AISTUDIO_CONFIG_FILE"] = str(inherited_config)
    return subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[2] / "scripts/termux_manager.py"),
            "--register",
            "--stable",
            "--project-root",
            str(installation.root),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_first_registration_through_the_manager_copies_project_config(
    installation, tmp_path
):
    result = _run_manager_register(installation, tmp_path)
    assert result.returncode == 0, result.stderr
    assert installation.config_file.read_text() == "api_keys: []\n"


def test_first_registration_imports_an_inherited_config_file(installation, tmp_path):
    inherited = tmp_path / "previous.yaml"
    inherited.write_text("user_setting: imported\n")
    result = _run_manager_register(installation, tmp_path, inherited)
    assert result.returncode == 0, result.stderr
    assert installation.config_file.read_text() == "user_setting: imported\n"


def test_legacy_registration_keeps_the_original_container(installation):
    installation.config_dir.mkdir(parents=True, exist_ok=True)
    write_object(
        installation.config_dir / "installation.json",
        {"project_root": str(installation.root), "port": installation.port},
    )
    loaded = Installation.load()
    assert loaded.proot_name == "aistudio-api"
    loaded.register()
    record = json.loads(
        (installation.config_dir / "installation.json").read_text(encoding="utf-8")
    )
    assert record["proot_name"] == "aistudio-api"


def test_invalid_record_container_name_is_rejected(installation):
    installation.config_dir.mkdir(parents=True, exist_ok=True)
    write_object(
        installation.config_dir / "installation.json",
        {
            "project_root": str(installation.root),
            "port": installation.port,
            "proot_name": "bad name",
        },
    )
    with pytest.raises(ManagerError, match="浏览器环境名称"):
        Installation.load()


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


def test_environment_injects_container_and_channel(installation):
    env = Service(installation).environment("silent")
    assert env["AISTUDIO_PROOT_NAME"] == "aistudio-api-termux"
    assert env["AISTUDIO_INSTALL_CHANNEL"] == "stable"
    development = replace(installation, development=True)
    dev_env = Service(development).environment("silent")
    assert dev_env["AISTUDIO_INSTALL_CHANNEL"] == "development"
    assert dev_env["AISTUDIO_PROOT_NAME"] == "aistudio-api-termux"


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


def test_prepare_passes_container_name_to_setup(installation, monkeypatch):
    import termux_update

    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(termux_update.subprocess, "run", fake_run)
    Updater(installation).prepare()
    assert captured["command"][:2] == ["bash", "scripts/setup-termux.sh"]
    assert captured["env"]["AISTUDIO_PROOT_NAME"] == "aistudio-api-termux"
    legacy = replace(installation, proot_name="aistudio-api")
    Updater(legacy).prepare()
    assert captured["env"]["AISTUDIO_PROOT_NAME"] == "aistudio-api"


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
    installation = Installation(
        client, tmp_path / "config", tmp_path / "state", development=True
    )
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


def test_update_source_accepts_both_repository_addresses(installation):
    git(installation.root, "init", "--initial-branch=master")
    for key, value in (
        ("user.name", "Test"),
        ("user.email", "test@example.invalid"),
        ("commit.gpgsign", "false"),
    ):
        git(installation.root, "config", key, value)
    git(installation.root, "add", "main.py", "config.yaml")
    git(installation.root, "commit", "-m", "fixture")
    for url in (
        "https://github.com/ReedSein/aistudio-api.git",
        "https://github.com/ReedSein/aistudio-api-termux.git",
        "git@github.com:ReedSein/aistudio-api.git",
        "git@github.com:ReedSein/aistudio-api-termux.git",
    ):
        if git(installation.root, "remote"):
            git(installation.root, "remote", "set-url", "origin", url)
        else:
            git(installation.root, "remote", "add", "origin", url)
        Updater(installation).verify_checkout()


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


def test_development_registration_and_dirty_source_can_run(installation):
    development = replace(installation, development=True)
    development.register()
    git(development.root, "init", "--initial-branch=work-in-progress")
    (development.root / "uncommitted.py").write_text("# local work\n")
    loaded = Installation.load()
    assert loaded.development
    service = Service(loaded)
    process = service.start("silent", timeout=10)
    try:
        assert service.healthy()
        assert (loaded.root / "uncommitted.py").read_text() == "# local work\n"
        assert git(loaded.root, "branch", "--show-current") == "work-in-progress"
    finally:
        service.stop()
        process.wait(timeout=5)


def test_public_updates_follow_release_tag_instead_of_master(
    update_repositories, monkeypatch
):
    import termux_update

    updater, source = update_repositories
    updater.installation = replace(updater.installation, development=False)
    git(source, "tag", "termux-v1.0.0")
    git(source, "push", "origin", "termux-v1.0.0")
    monkeypatch.setattr(termux_update, "latest_release", lambda: "termux-v1.0.0")
    initial = updater.check().current
    publish(source, "experimental.py")
    status = updater.check()
    assert status.available == initial and status.commits == 0
    assert status.version == "termux-v1.0.0"
    git(source, "tag", "-a", "termux-v1.1.0", "-m", "tested release")
    git(source, "push", "origin", "termux-v1.1.0")
    monkeypatch.setattr(termux_update, "latest_release", lambda: "termux-v1.1.0")
    monkeypatch.setattr(updater, "prepare", lambda: None)
    monkeypatch.setattr(Installation, "register", lambda self: Path("unused"))
    changed, _ = updater.update()
    assert changed
    assert updater.check().current == git(source, "rev-parse", "termux-v1.1.0^{commit}")
    publish(source, "next-experiment.py")
    assert updater.check().commits == 0
    assert not (updater.installation.root / "next-experiment.py").exists()


def test_public_update_does_not_downgrade_local_work(update_repositories, monkeypatch):
    import termux_update

    updater, source = update_repositories
    git(source, "tag", "termux-v1.0.0")
    git(source, "push", "origin", "termux-v1.0.0")
    publish(source)
    git(updater.installation.root, "pull", "--ff-only")
    updater.installation = replace(updater.installation, development=False)
    monkeypatch.setattr(termux_update, "latest_release", lambda: "termux-v1.0.0")
    head = git(updater.installation.root, "rev-parse", "HEAD")
    with pytest.raises(ManagerError, match="领先"):
        updater.check()
    assert git(updater.installation.root, "rev-parse", "HEAD") == head


def prepare_uninstall(installation, *, development=True):
    result = replace(installation, development=development)
    git(result.root, "init", "--initial-branch=master")
    for key, value in (
        ("user.name", "Test"),
        ("user.email", "test@example.invalid"),
        ("commit.gpgsign", "false"),
    ):
        git(result.root, "config", key, value)
    git(result.root, "add", "main.py", "config.yaml")
    git(result.root, "commit", "-m", "fixture program")
    git(
        result.root,
        "remote",
        "add",
        "origin",
        "https://github.com/ReedSein/aistudio-api.git",
    )
    git(result.root, "update-ref", "refs/remotes/origin/master", "HEAD")
    result.register()
    (result.root / "data").mkdir()
    (result.root / "data/account.json").write_text("fixture account data")
    (result.root / ".cloakbrowser").mkdir()
    (result.root / ".cloakbrowser/browser").write_text("fixture browser")
    return result


@pytest.mark.parametrize("development", [True, False])
def test_uninstall_keeps_data_and_development_source(
    installation, monkeypatch, development
):
    target = prepare_uninstall(installation, development=development)
    head = git(target.root, "rev-parse", "HEAD")
    (target.root / "personal-notes.md").write_text("keep this local file")
    monkeypatch.setattr("builtins.input", lambda _prompt: "UNINSTALL")
    with operation_lock(target):
        assert Uninstaller(target).run()
    assert (target.root / "data/account.json").read_text() == "fixture account data"
    assert target.config_file.read_text() == "api_keys: []\n"
    assert (target.config_dir / "installation.json").exists()
    assert (target.root / "personal-notes.md").exists()
    assert not (Path.home() / ".local/bin/aistudio").exists()
    assert not (target.root / ".venv").exists()
    assert not (target.root / ".cloakbrowser").exists()
    assert (target.root / "main.py").exists() == development
    assert (target.root / ".git").exists() == development
    if development:
        assert git(target.root, "rev-parse", "HEAD") == head


def test_clear_data_preserves_developer_edits_and_external_paths(
    installation, monkeypatch, tmp_path
):
    target = prepare_uninstall(installation)
    (target.root / "main.py").write_text("# unfinished source changes\n")
    external = tmp_path / "external-accounts"
    external.mkdir()
    (external / "auth.json").write_text("external fixture")
    monkeypatch.setenv("AISTUDIO_ACCOUNTS_DIR", str(external))
    monkeypatch.setattr("builtins.input", lambda _prompt: "UNINSTALL")
    with operation_lock(target):
        assert Uninstaller(target, clear_data=True).run()
    assert (target.root / "main.py").read_text() == "# unfinished source changes\n"
    assert (target.root / ".git").is_dir()
    assert not (target.root / "data").exists()
    assert not target.config_file.exists()
    assert not (target.config_dir / "installation.json").exists()
    assert (external / "auth.json").read_text() == "external fixture"


def test_cancel_uninstall_leaves_running_service_and_files(installation, monkeypatch):
    target = prepare_uninstall(installation)
    service = Service(target)
    process = service.start("silent", timeout=10)
    try:
        monkeypatch.setattr("builtins.input", lambda _prompt: "no")
        with operation_lock(target):
            assert not Uninstaller(target).run()
        assert service.healthy()
        assert (target.root / ".venv").exists()
        assert (Path.home() / ".local/bin/aistudio").exists()
    finally:
        service.stop()
        process.wait(timeout=5)


@pytest.mark.parametrize("clear_data", [False, True])
def test_uninstall_clears_only_owned_default_diagnostics(
    installation, monkeypatch, tmp_path, clear_data
):
    from aistudio_api.infrastructure.diagnostics.store import DiagnosticStore, LogPolicy

    target = prepare_uninstall(installation)
    logs = target.state_dir / "logs"
    store = DiagnosticStore(LogPolicy(logs))
    assert store.emit("before_uninstall", text="fixture diagnostic")
    external = DiagnosticStore(LogPolicy(tmp_path / "external-logs"))
    assert external.emit("external", text="preserve custom directory")
    monkeypatch.setenv("AISTUDIO_LOG_DIR", str(logs))
    monkeypatch.setattr("builtins.input", lambda _prompt: "UNINSTALL")
    with operation_lock(target):
        assert Uninstaller(target, clear_data=clear_data).run()
    assert logs.exists() is not clear_data
    assert store.emit("after_uninstall") is not clear_data
    assert logs.exists() is not clear_data
    assert external.files()


def test_uninstall_refuses_to_claim_foreign_diagnostics(installation):
    target = prepare_uninstall(installation)
    logs = target.state_dir / "logs"
    logs.mkdir(parents=True)
    (logs / "personal.txt").write_text("keep unrelated data")
    with pytest.raises(ManagerError, match="日志目录归属"):
        Uninstaller(target, clear_data=True).paths()
    assert (logs / "personal.txt").read_text() == "keep unrelated data"


def test_uninstall_stops_owned_service_before_removing_runtime(
    installation, monkeypatch
):
    target = prepare_uninstall(installation)
    service = Service(target)
    process = service.start("silent", timeout=10)
    try:
        monkeypatch.setattr("builtins.input", lambda _prompt: "UNINSTALL")
        with operation_lock(target):
            assert Uninstaller(target).run()
        process.wait(timeout=5)
        assert not target.process_file.exists()
        assert not (target.root / ".venv").exists()
    finally:
        if process.poll() is None:
            service.stop()
            process.wait(timeout=5)


def test_uninstall_does_not_follow_data_symlink(installation, monkeypatch, tmp_path):
    target = replace(installation, development=True)
    target.register()
    external = tmp_path / "external-data"
    external.mkdir()
    (external / "keep.json").write_text("keep")
    (target.root / "data").symlink_to(external, target_is_directory=True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "UNINSTALL")
    with operation_lock(target):
        assert Uninstaller(target, clear_data=True).run()
    assert (external / "keep.json").read_text() == "keep"


def test_uninstall_refuses_foreign_entry_before_stopping_service(installation):
    target = prepare_uninstall(installation)
    entry = Path.home() / ".local/bin/aistudio"
    entry.write_text("unrelated command")
    with pytest.raises(ManagerError, match="不属于"):
        Uninstaller(target).run()
    assert (target.root / ".venv").exists()
    assert entry.read_text() == "unrelated command"


def test_public_uninstall_protects_modified_source(installation):
    target = prepare_uninstall(installation, development=False)
    (target.root / "main.py").write_text("# local code\n")
    with pytest.raises(ManagerError, match="未提交"):
        Uninstaller(target).run()
    assert (target.root / ".git").exists()
    assert (target.root / ".venv").exists()


def test_public_uninstall_preserves_unpublished_branch_history(installation):
    target = prepare_uninstall(installation, development=False)
    git(target.root, "checkout", "-b", "unfinished")
    (target.root / "local-work.py").write_text("# saved local work\n")
    git(target.root, "add", "local-work.py")
    git(target.root, "commit", "-m", "unfinished work")
    unpublished = git(target.root, "rev-parse", "HEAD")
    git(target.root, "checkout", "master")
    with pytest.raises(ManagerError, match="仅保存在此处"):
        Uninstaller(target).run()
    assert git(target.root, "rev-parse", "unfinished") == unpublished
    assert (target.root / ".venv").exists()


def test_release_notice_lines_cover_all_states():
    newer = ReleaseCheck("newer", "termux-v1.0.0", "termux-v1.2.0")
    assert "v1.2.0" in release_notice_line(newer)
    assert "一键更新" in release_check_message(newer)
    current = ReleaseCheck("current", "termux-v1.2.0", "termux-v1.2.0")
    assert "已是最新" in release_notice_line(current)
    assert "已是最新" in release_check_message(current)
    assert "尚未发布" in release_notice_line(ReleaseCheck("unpublished", None, None))
    assert "稍后再试" in release_notice_line(ReleaseCheck("unreachable", None, None))
    unknown = ReleaseCheck("unknown", None, "termux-v1.2.0")
    assert "无法确认" in release_notice_line(unknown)
    assert "无法确认" in release_check_message(unknown)
