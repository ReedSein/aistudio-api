"""Installer orchestration in disposable repositories, without package changes."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from termux_runtime import Installation, operation_lock
from termux_uninstall import Uninstaller

INSTALLER = Path(__file__).resolve().parents[2] / "scripts/install-termux.sh"


def run_installer(*arguments: str, env: dict[str, str] | None = None):
    return subprocess.run(
        ["bash", str(INSTALLER), *arguments],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_missing_directory_argument_is_a_clear_error():
    result = run_installer("--dir")
    assert result.returncode == 2
    assert "requires a parent directory" in result.stderr


def test_existing_unrelated_directory_is_preserved(tmp_path, disposable_install):
    root = tmp_path / "unrelated/aistudio-api-termux"
    root.mkdir(parents=True)
    owned_file = root / "keep.txt"
    owned_file.write_text("keep")
    result = run_installer(
        "--dev", "--dir", str(root.parent), env=disposable_install[-1]
    )
    assert result.returncode == 1
    assert "nothing was overwritten" in result.stderr
    assert owned_file.read_text() == "keep"


@pytest.fixture
def disposable_install(tmp_path):
    parent = tmp_path / "parent with spaces"
    root = parent / "aistudio-api-termux"
    (root / "scripts").mkdir(parents=True)
    (root / "main.py").write_text("# fixture\n")
    (root / "scripts/setup-env.sh").write_text("exit 0\n")
    (root / "config.yaml").write_text("api_keys: []\n")
    shutil.copy(
        INSTALLER.parent / "termux_runtime.py", root / "scripts/termux_runtime.py"
    )

    def git(*arguments):
        return subprocess.run(
            ["git", *arguments], cwd=root, capture_output=True, text=True, check=True
        ).stdout.strip()

    git("init", "--initial-branch=master")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.invalid")
    git("config", "commit.gpgsign", "false")
    git(
        "add",
        "main.py",
        "config.yaml",
        "scripts/setup-env.sh",
        "scripts/termux_runtime.py",
    )
    git("commit", "-m", "fixture")
    git("remote", "add", "origin", "https://github.com/ReedSein/aistudio-api.git")
    git("update-ref", "refs/remotes/origin/master", "HEAD")
    git("branch", "--set-upstream-to=origin/master", "master")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("pkg", "exit 99"), ("uname", "printf 'aarch64\\n'")):
        stub = bin_dir / name
        stub.write_text(f"#!/bin/sh\n{body}\n")
        stub.chmod(0o755)
    log = tmp_path / "uv-arguments.json"
    uv = bin_dir / "uv"
    uv.write_text(
        f"#!{sys.executable}\nimport json, os, sys\nfrom pathlib import Path\n"
        "if '-' in sys.argv:\n    os.execv(sys.executable, [sys.executable, *sys.argv[sys.argv.index('-'):]])\n"
        f"Path({str(log)!r}).write_text(json.dumps(sys.argv[1:]))\n"
    )
    uv.chmod(0o755)
    curl = bin_dir / "curl"
    curl.write_text(
        "#!/bin/sh\nprintf '%s' 'https://github.com/ReedSein/aistudio-api-termux/releases/tag/termux-v1.0.0'\n"
    )
    curl.chmod(0o755)
    git("tag", "termux-v1.0.0")
    env = dict(
        os.environ,
        PATH=f"{bin_dir}:{os.environ['PATH']}",
        HOME=str(tmp_path / "home"),
        XDG_CONFIG_HOME=str(tmp_path / "config"),
        XDG_STATE_HOME=str(tmp_path / "state"),
    )
    return parent, root, git, log, env


def test_custom_parent_and_repeated_install_reuse_the_checkout(disposable_install):
    parent, root, git, log, env = disposable_install
    head = git("rev-parse", "HEAD")
    for _ in range(2):
        result = run_installer("--dev", "--dir", str(parent), env=env)
        assert result.returncode == 0, result.stderr
        arguments = json.loads(log.read_text())
        assert arguments[-2:] == ["--project-root", str(root)]
        assert "--install" in arguments
        assert "--dev" in arguments
        assert git("rev-parse", "HEAD") == head


def test_installer_refuses_local_changes_before_environment_work(disposable_install):
    parent, root, _, log, env = disposable_install
    (root / "main.py").write_text("# user edit\n")
    result = run_installer("--dir", str(parent), env=env)
    assert result.returncode == 1
    assert "Uncommitted source changes" in result.stderr
    assert not log.exists()


def test_installer_refuses_foreign_origin(disposable_install):
    parent, _, git, log, env = disposable_install
    git(
        "remote",
        "set-url",
        "origin",
        "https://github.com/DeconstructedCube/aistudio-api.git",
    )
    result = run_installer("--dev", "--dir", str(parent), env=env)
    assert result.returncode == 1
    assert "not ReedSein/aistudio-api-termux" in result.stderr
    assert not log.exists()


def test_installer_accepts_both_repository_addresses(disposable_install):
    parent, _, git, _, env = disposable_install
    result = run_installer("--dev", "--dir", str(parent), env=env)
    assert result.returncode == 0, result.stderr
    git(
        "remote",
        "set-url",
        "origin",
        "https://github.com/ReedSein/aistudio-api-termux.git",
    )
    result = run_installer("--dev", "--dir", str(parent), env=env)
    assert result.returncode == 0, result.stderr


def test_developer_install_keeps_local_branch_and_edits(disposable_install):
    parent, root, git, log, env = disposable_install
    git("checkout", "-b", "feature/local")
    (root / "main.py").write_text("# in-progress local code\n")
    result = run_installer("--dev", "--dir", str(parent), env=env)
    assert result.returncode == 0, result.stderr
    assert (root / "main.py").read_text() == "# in-progress local code\n"
    assert git("branch", "--show-current") == "feature/local"
    assert "--dev" in json.loads(log.read_text())


def test_public_install_failure_does_not_fall_back_to_master(disposable_install):
    parent, root, git, log, env = disposable_install
    head = git("rev-parse", "HEAD")
    curl = Path(env["PATH"].split(":")[0]) / "curl"
    curl.write_text("#!/bin/sh\nexit 22\n")
    result = run_installer("--dir", str(parent), env=env)
    assert result.returncode == 1
    assert "public release" in result.stderr
    assert not log.exists()
    assert git("rev-parse", "HEAD") == head
    assert (root / "main.py").exists()


def local_release_remote(disposable_install, tmp_path):
    _, root, git, _, env = disposable_install
    remote = tmp_path / "published.git"
    git("clone", "--bare", str(root), str(remote))
    executable = shutil.which("git")
    wrapper = Path(env["PATH"].split(":")[0]) / "git"
    wrapper.write_text(
        f"#!{sys.executable}\nimport os, sys\n"
        f"args = [{executable!r}]\n"
        "if 'fetch' in sys.argv or 'clone' in sys.argv:\n"
        f"    args += ['-c', {'url.' + str(remote) + '.insteadOf=https://github.com/ReedSein/aistudio-api-termux.git'!r}]\n"
        f"    args += ['-c', {'url.' + str(remote) + '.insteadOf=https://github.com/ReedSein/aistudio-api.git'!r}]\n"
        "args += sys.argv[1:]\nos.execv(args[0], args)\n"
    )
    wrapper.chmod(0o755)
    return remote


def test_public_fresh_install_uses_release_even_when_master_is_ahead(
    disposable_install, tmp_path
):
    _, root, git, log, env = disposable_install
    release = git("rev-parse", "HEAD")
    (root / "experimental.py").write_text("# testing only\n")
    git("add", "experimental.py")
    git("commit", "-m", "unreleased experiment")
    local_release_remote(disposable_install, tmp_path)
    parent = tmp_path / "public installation"
    result = run_installer("--dir", str(parent), env=env)
    assert result.returncode == 0, result.stderr
    installed = parent / "aistudio-api-termux"
    assert (
        subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=installed, text=True
        ).strip()
        == release
    )
    assert not (installed / "experimental.py").exists()
    assert "--stable" in json.loads(log.read_text())


def test_keep_data_uninstall_then_public_reinstall_reuses_data(
    disposable_install, tmp_path, monkeypatch
):
    parent, root, _, log, env = disposable_install
    local_release_remote(disposable_install, tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", env["XDG_CONFIG_HOME"])
    monkeypatch.setenv("XDG_STATE_HOME", env["XDG_STATE_HOME"])
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))
    installation = Installation.load(root)
    installation.register()
    installation.config_file.write_text("user_setting: preserved\n")
    (root / "data/accounts").mkdir(parents=True)
    account = root / "data/accounts/auth.json"
    account.write_text("fixture account data")
    monkeypatch.setattr("builtins.input", lambda _prompt: "UNINSTALL")
    with operation_lock(installation):
        assert Uninstaller(installation).run()
    assert not (root / "main.py").exists()
    assert not (root / ".git").exists()
    result = run_installer("--dir", str(parent), env=env)
    assert result.returncode == 0, result.stderr
    assert "--stable" in json.loads(log.read_text())
    restored = Installation.load(root)
    restored.register()
    assert restored.config_file.read_text() == "user_setting: preserved\n"
    assert account.read_text() == "fixture account data"
    assert (root / "main.py").is_file()
    assert (Path.home() / ".local/bin/aistudio").is_file()
    assert restored.root / ".git" in Uninstaller(restored).paths()


@pytest.mark.parametrize("development", [True, False])
def test_environment_preparation_preserves_extra_development_tools(
    disposable_install, development
):
    _, root, _, log, env = disposable_install
    script = root / "scripts/setup-termux.sh"
    shutil.copy(INSTALLER.parent / "setup-termux.sh", script)
    (root / "scripts/setup-browser.sh").write_text("exit 0\n")
    result = subprocess.run(
        ["bash", str(script), *(["--dev"] if development else [])],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    sync_arguments = json.loads(log.read_text())
    assert sync_arguments[0] == "sync"
    assert "--locked" in sync_arguments
    assert ("--inexact" in sync_arguments) == development


def test_public_installer_does_not_register_unreleased_local_commits(
    disposable_install, tmp_path
):
    parent, root, git, log, env = disposable_install
    (root / "experimental.py").write_text("# local development\n")
    git("add", "experimental.py")
    git("commit", "-m", "unreleased work")
    head = git("rev-parse", "HEAD")
    local_release_remote(disposable_install, tmp_path)
    result = run_installer("--dir", str(parent), env=env)
    assert result.returncode == 1
    assert "ahead of the public release" in result.stderr
    assert git("rev-parse", "HEAD") == head
    assert (root / "experimental.py").read_text() == "# local development\n"
    assert not log.exists()
