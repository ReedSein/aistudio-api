"""Original-author cleanup: program removal, data preservation and refusals."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import termux_legacy
from termux_legacy import GENERATED_UV_TOML, LegacyCleanup
from termux_runtime import ManagerError, write_object


def git(root, *args):
    return subprocess.run(
        ["git", *args], cwd=root, text=True, capture_output=True, check=True
    ).stdout.strip()


def answer_inputs(monkeypatch, *values):
    answers = iter(values)
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.delenv("AISTUDIO_PROOT_NAME", raising=False)
    monkeypatch.setattr(termux_legacy, "_installed_containers", list)
    monkeypatch.setattr(termux_legacy, "_service_pids", lambda _root: [])
    monkeypatch.setattr(termux_legacy, "_pids_using_container", lambda _name: [])


@pytest.fixture
def legacy_install(tmp_path):
    root = tmp_path / "legacy" / "aistudio-api"
    (root / "scripts").mkdir(parents=True)
    (root / "main.py").write_text("# legacy program\n")
    (root / "config.yaml").write_text("api_keys: []\n")
    (root / "scripts/setup-env.sh").write_text("exit 0\n")
    (root / "data/accounts").mkdir(parents=True)
    (root / "data/accounts/auth.json").write_text("fixture account")
    (root / ".venv").mkdir()
    (root / ".venv/pyvenv.cfg").write_text("home = /x\n")
    (root / ".cloakbrowser").mkdir()
    (root / ".cloakbrowser/browser").write_text("fixture")
    (root / "uv.toml").write_text(GENERATED_UV_TOML)
    (root / "personal-notes.md").write_text("keep me")
    git(root, "init", "--initial-branch=master")
    for key, value in (
        ("user.name", "Test"),
        ("user.email", "test@example.invalid"),
        ("commit.gpgsign", "false"),
    ):
        git(root, "config", key, value)
    git(root, "add", "main.py", "config.yaml", "scripts/setup-env.sh")
    git(root, "commit", "-m", "legacy fixture")
    git(
        root,
        "remote",
        "add",
        "origin",
        "https://github.com/DeconstructedCube/aistudio-api.git",
    )
    return root


def workspace_root(legacy_install):
    return legacy_install.parent / "workspace"


def removal_recorder(monkeypatch):
    removed = []
    monkeypatch.setattr(
        termux_legacy,
        "_remove_container",
        lambda name: removed.append(name) or subprocess.CompletedProcess([], 0),
    )
    return removed


def test_cleanup_removes_program_and_keeps_data(monkeypatch, legacy_install):
    monkeypatch.setattr(
        termux_legacy, "_installed_containers", lambda: ["aistudio-api"]
    )
    removed = removal_recorder(monkeypatch)
    answer_inputs(monkeypatch, str(legacy_install), "CLEAN")
    cleanup = LegacyCleanup(
        clear_data=False, repository_root=workspace_root(legacy_install)
    )
    assert cleanup.run() is True
    assert removed == ["aistudio-api"]
    assert not (legacy_install / "main.py").exists()
    assert not (legacy_install / "scripts").exists()
    assert not (legacy_install / ".venv").exists()
    assert not (legacy_install / ".cloakbrowser").exists()
    assert not (legacy_install / ".git").exists()
    assert not (legacy_install / "uv.toml").exists()
    assert (legacy_install / "config.yaml").read_text() == "api_keys: []\n"
    assert (legacy_install / "data/accounts/auth.json").read_text() == "fixture account"
    assert (legacy_install / "personal-notes.md").read_text() == "keep me"


def test_clear_data_removes_accounts_and_configuration(monkeypatch, legacy_install):
    answer_inputs(monkeypatch, str(legacy_install), "CLEAN")
    cleanup = LegacyCleanup(
        clear_data=True, repository_root=workspace_root(legacy_install)
    )
    assert cleanup.run() is True
    assert not (legacy_install / "config.yaml").exists()
    assert not (legacy_install / "data").exists()
    assert (legacy_install / "personal-notes.md").exists()


def test_cleanup_refuses_a_foreign_origin(monkeypatch, legacy_install):
    git(
        legacy_install,
        "remote",
        "set-url",
        "origin",
        "https://github.com/Example/other.git",
    )
    answer_inputs(monkeypatch, str(legacy_install))
    cleanup = LegacyCleanup(
        clear_data=False, repository_root=workspace_root(legacy_install)
    )
    with pytest.raises(ManagerError, match="来源"):
        cleanup.run()
    assert (legacy_install / "main.py").exists()


def test_cleanup_refuses_the_registered_install(monkeypatch, legacy_install):
    record = Path(os.environ["XDG_CONFIG_HOME"]) / "aistudio-termux/installation.json"
    write_object(record, {"project_root": str(legacy_install), "port": 11451})
    answer_inputs(monkeypatch, str(legacy_install))
    cleanup = LegacyCleanup(
        clear_data=False, repository_root=workspace_root(legacy_install)
    )
    with pytest.raises(ManagerError, match="已登记"):
        cleanup.run()
    assert (legacy_install / "main.py").exists()


def test_cleanup_refuses_while_the_original_service_runs(monkeypatch, legacy_install):
    monkeypatch.setattr(termux_legacy, "_service_pids", lambda _root: [4242])
    answer_inputs(monkeypatch, str(legacy_install))
    cleanup = LegacyCleanup(
        clear_data=False, repository_root=workspace_root(legacy_install)
    )
    with pytest.raises(ManagerError, match="仍在运行"):
        cleanup.run()
    assert (legacy_install / "main.py").exists()


def test_cleanup_preserves_the_container_in_use_by_current_install(
    monkeypatch, legacy_install
):
    our_root = workspace_root(legacy_install)
    our_root.mkdir()
    (our_root / "main.py").write_text("# ours\n")
    record = Path(os.environ["XDG_CONFIG_HOME"]) / "aistudio-termux/installation.json"
    write_object(
        record,
        {
            "project_root": str(our_root),
            "port": 11451,
            "development": True,
            "proot_name": "aistudio-api",
        },
    )
    monkeypatch.setattr(
        termux_legacy, "_installed_containers", lambda: ["aistudio-api"]
    )
    removed = removal_recorder(monkeypatch)
    answer_inputs(monkeypatch, str(legacy_install), "CLEAN")
    cleanup = LegacyCleanup(clear_data=False, repository_root=our_root)
    assert cleanup.run() is True
    assert removed == []
    assert not (legacy_install / "main.py").exists()


def test_cleanup_keeps_a_container_with_running_processes(
    monkeypatch, legacy_install, capsys
):
    monkeypatch.setattr(
        termux_legacy, "_installed_containers", lambda: ["aistudio-api"]
    )
    monkeypatch.setattr(termux_legacy, "_pids_using_container", lambda _name: [777])
    removed = removal_recorder(monkeypatch)
    answer_inputs(monkeypatch, str(legacy_install), "CLEAN")
    cleanup = LegacyCleanup(
        clear_data=False, repository_root=workspace_root(legacy_install)
    )
    assert cleanup.run() is True
    assert removed == []
    assert "777" in capsys.readouterr().out


def test_cleanup_cancel_has_no_side_effects(monkeypatch, legacy_install):
    answer_inputs(monkeypatch, str(legacy_install), "stop")
    cleanup = LegacyCleanup(
        clear_data=False, repository_root=workspace_root(legacy_install)
    )
    assert cleanup.run() is False
    assert (legacy_install / "main.py").exists()
    assert (legacy_install / ".git").exists()
    assert (legacy_install / "data/accounts/auth.json").exists()


def test_cleanup_reports_a_missing_target(monkeypatch, tmp_path, capsys):
    answer_inputs(monkeypatch, str(tmp_path / "nowhere"))
    cleanup = LegacyCleanup(clear_data=False, repository_root=tmp_path / "workspace")
    assert cleanup.run() is False
    assert "不存在" in capsys.readouterr().out


def test_cleanup_warns_about_local_changes(monkeypatch, legacy_install, capsys):
    (legacy_install / "main.py").write_text("# unsaved local edit\n")
    answer_inputs(monkeypatch, str(legacy_install), "CLEAN")
    cleanup = LegacyCleanup(
        clear_data=False, repository_root=workspace_root(legacy_install)
    )
    assert cleanup.run() is True
    assert "本地代码改动" in capsys.readouterr().out


def test_cleanup_reports_partial_failure(monkeypatch, legacy_install, capsys):
    original_rmtree = termux_legacy.shutil.rmtree

    def failing(path, *args, **kwargs):
        if str(path).endswith(".venv"):
            raise OSError(5, "fixture failure")
        return original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(termux_legacy.shutil, "rmtree", failing)
    answer_inputs(monkeypatch, str(legacy_install), "CLEAN")
    cleanup = LegacyCleanup(
        clear_data=False, repository_root=workspace_root(legacy_install)
    )
    assert cleanup.run() is False
    assert (legacy_install / ".venv").exists()
    assert not (legacy_install / ".cloakbrowser").exists()
    assert "部分完成" in capsys.readouterr().out


def test_cli_quit_cancels_without_changes(tmp_path):
    module = Path(__file__).resolve().parents[2] / "scripts/termux_legacy.py"
    env = dict(
        os.environ,
        XDG_CONFIG_HOME=str(tmp_path / "config"),
        XDG_STATE_HOME=str(tmp_path / "state"),
    )
    result = subprocess.run(
        [sys.executable, str(module)],
        input="q\n",
        text=True,
        capture_output=True,
        env=env,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0
    assert "已取消" in result.stdout


def test_cleanup_refuses_a_symlinked_target(monkeypatch, tmp_path, legacy_install):
    link = tmp_path / "legacy-link"
    link.symlink_to(legacy_install, target_is_directory=True)
    answer_inputs(monkeypatch, str(link))
    cleanup = LegacyCleanup(clear_data=False, repository_root=tmp_path / "workspace")
    with pytest.raises(ManagerError, match="符号链接"):
        cleanup.run()
    assert (legacy_install / "main.py").exists()


def test_cleanup_removes_a_leftover_container_without_the_directory(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setattr(
        termux_legacy, "_installed_containers", lambda: ["aistudio-api"]
    )
    removed = removal_recorder(monkeypatch)
    answer_inputs(monkeypatch, str(tmp_path / "gone"), "CLEAN")
    cleanup = LegacyCleanup(clear_data=False, repository_root=tmp_path / "workspace")
    assert cleanup.run() is True
    assert removed == ["aistudio-api"]
    assert "不存在" in capsys.readouterr().out
