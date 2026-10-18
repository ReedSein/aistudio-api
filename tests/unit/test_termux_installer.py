"""Installer orchestration in disposable repositories, without package changes."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

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


def test_existing_unrelated_directory_is_preserved(tmp_path):
    root = tmp_path / "aistudio-api"
    root.mkdir()
    owned_file = root / "keep.txt"
    owned_file.write_text("keep")
    result = run_installer("--dir", str(tmp_path))
    assert result.returncode == 1
    assert "nothing was overwritten" in result.stderr
    assert owned_file.read_text() == "keep"


@pytest.fixture
def disposable_install(tmp_path):
    parent = tmp_path / "parent with spaces"
    root = parent / "aistudio-api"
    (root / "scripts").mkdir(parents=True)
    (root / "main.py").write_text("# fixture\n")
    (root / "scripts/setup-env.sh").write_text("exit 0\n")

    def git(*arguments):
        return subprocess.run(
            ["git", *arguments], cwd=root, capture_output=True, text=True, check=True
        ).stdout.strip()

    git("init", "--initial-branch=master")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.invalid")
    git("config", "commit.gpgsign", "false")
    git("add", "main.py", "scripts/setup-env.sh")
    git("commit", "-m", "fixture")
    git("remote", "add", "origin", "https://github.com/ReedSein/aistudio-api.git")
    git("update-ref", "refs/remotes/origin/master", "HEAD")
    git("branch", "--set-upstream-to=origin/master", "master")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "uv-arguments.json"
    uv = bin_dir / "uv"
    uv.write_text(
        f"#!{sys.executable}\nimport json, sys\nfrom pathlib import Path\nPath({str(log)!r}).write_text(json.dumps(sys.argv[1:]))\n"
    )
    uv.chmod(0o755)
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}")
    return parent, root, git, log, env


def test_custom_parent_and_repeated_install_reuse_the_checkout(disposable_install):
    parent, root, git, log, env = disposable_install
    head = git("rev-parse", "HEAD")
    for _ in range(2):
        result = run_installer("--dir", str(parent), env=env)
        assert result.returncode == 0, result.stderr
        arguments = json.loads(log.read_text())
        assert arguments[-2:] == ["--project-root", str(root)]
        assert "--install" in arguments
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
    result = run_installer("--dir", str(parent), env=env)
    assert result.returncode == 1
    assert "not ReedSein/aistudio-api" in result.stderr
    assert not log.exists()
