"""Browser archive layouts, traversal refusal and version selection."""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from unpack_browser import unpack


@pytest.fixture
def browser_install(tmp_path):
    root = tmp_path / "first install"
    source = Path(__file__).resolve().parents[2] / "scripts"
    shutil.copytree(
        source, root / "scripts", ignore=shutil.ignore_patterns("__pycache__")
    )
    (root / "scripts/cloakbrowser.version").write_text("9.8.7.6.5\n")
    archive = tmp_path / "browser.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        for name in ("chrome", "icudtl.dat"):
            content = b"fixture"
            info = tarfile.TarInfo(name)
            info.size = len(content)
            bundle.addfile(info, io.BytesIO(content))
    binary = tmp_path / "bin"
    binary.mkdir()
    scripts = {
        "proot-distro": """import os, sys
from pathlib import Path
p = Path(os.environ["TEST_ROOT"])
args = sys.argv[1:]
state, broken = p / "container", p / "broken-container"
if args[0] == "list":
    if state.exists(): print("aistudio-api-termux")
elif args[0] == "install":
    state.touch()
    if os.getenv("TEST_CONTAINER_FAIL") and not (p / "failed-once").exists():
        (p / "failed-once").touch()
        broken.touch()
        sys.exit(1)
elif args[0] == "remove":
    with (p / "removed").open("a") as log: log.write(args[1] + "\\n")
    state.unlink()
    broken.unlink(missing_ok=True)
elif args[0] == "login":
    if broken.exists(): sys.exit(1)
    if "--version" in args:
        chrome = Path(os.environ["TEST_PROJECT"]) / ".cloakbrowser/chromium-9.8.7.6.5/chrome"
        if not chrome.exists() or chrome.read_bytes() == b"broken": sys.exit(1)
        print("Chromium 9.8.7.6")
""",
        "curl": """import os, sys
from pathlib import Path
p = Path(os.environ["TEST_ROOT"])
with (p / "downloads").open("a") as log: log.write("download\\n")
Path(sys.argv[sys.argv.index("-o") + 1]).write_bytes((p / "browser.tar.gz").read_bytes())
""",
        "uv": """import os, sys
os.execv(sys.executable, [sys.executable, *sys.argv[sys.argv.index("python") + 1:]])
""",
    }
    for name, body in scripts.items():
        tool = binary / name
        tool.write_text(f"#!{sys.executable}\n{body}")
        tool.chmod(0o755)
    (tmp_path / "container").touch()
    env = {
        name: os.environ[name]
        for name in ("PATH", "TMPDIR", "PREFIX", "LD_PRELOAD", "LD_LIBRARY_PATH")
        if name in os.environ
    }
    env.update(
        PATH=f"{binary}:{os.environ['PATH']}",
        TEST_ROOT=str(tmp_path),
        TEST_PROJECT=str(root),
    )
    return root, tmp_path, env


def prepare_browser(root, env):
    return subprocess.run(
        ["bash", str(root / "scripts/setup-browser.sh")],
        env=env,
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )


def test_first_install_replaces_a_partial_browser_then_reuses_the_complete_cache(
    browser_install,
):
    root, state, env = browser_install
    cache = root / ".cloakbrowser/chromium-9.8.7.6.5"
    cache.mkdir(parents=True)
    (cache / "chrome").write_bytes(b"broken")
    (cache / "chrome").chmod(0o755)
    for _ in range(2):
        result = prepare_browser(root, env)
        assert result.returncode == 0, result.stderr
    assert (cache / "icudtl.dat").is_file()
    assert (state / "downloads").read_text().splitlines() == ["download"]


def test_first_install_resumes_its_own_interrupted_container(browser_install):
    root, state, env = browser_install
    (state / "container").unlink()
    env["TEST_CONTAINER_FAIL"] = "1"
    first = prepare_browser(root, env)
    assert first.returncode != 0
    marker = root / ".cloakbrowser/.proot-installing"
    assert marker.is_file()
    second = prepare_browser(root, env)
    assert second.returncode == 0, second.stderr
    assert (state / "removed").read_text().splitlines() == ["aistudio-api-termux"]
    assert not marker.exists()


def test_broken_container_with_unknown_owner_is_preserved(browser_install):
    root, state, env = browser_install
    (state / "broken-container").touch()
    result = prepare_browser(root, env)
    assert result.returncode != 0
    assert not (state / "removed").exists()


def test_unknown_browser_cache_is_preserved(browser_install):
    root, state, env = browser_install
    cache = root / ".cloakbrowser/chromium-9.8.7.6.5"
    cache.mkdir(parents=True)
    sentinel = cache / "keep.txt"
    sentinel.touch()
    result = prepare_browser(root, env)
    assert result.returncode != 0
    assert sentinel.exists()
    assert not (state / "downloads").exists()


def test_browser_cache_symlink_is_not_followed(browser_install):
    root, state, env = browser_install
    external = state / "external"
    external.mkdir()
    sentinel = external / "keep.txt"
    sentinel.touch()
    (root / ".cloakbrowser").symlink_to(external, target_is_directory=True)
    result = prepare_browser(root, env)
    assert result.returncode != 0
    assert sentinel.exists()


@pytest.mark.parametrize("prefix", ["", "chromium-build/"])
def test_archive_layout_is_normalized_without_replacing_old_browser(tmp_path, prefix):
    archive = tmp_path / "browser.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        for name in ("chrome", "icudtl.dat", "locales/en-US.pak"):
            content = b"fixture"
            info = tarfile.TarInfo(prefix + name)
            info.size = len(content)
            bundle.addfile(info, io.BytesIO(content))
    destination = tmp_path / "cache/chromium-1.2.3"
    unpack(archive, destination)
    assert (destination / "chrome").is_file()
    assert os.access(destination / "chrome", os.X_OK)
    assert (destination / "locales/en-US.pak").is_file()
    with pytest.raises(ValueError, match="已存在"):
        unpack(archive, destination)
    assert (destination / "chrome").read_bytes() == b"fixture"


def test_archive_cannot_escape_destination(tmp_path):
    archive = tmp_path / "bad.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        info = tarfile.TarInfo("../escaped")
        info.size = 4
        bundle.addfile(info, io.BytesIO(b"bad!"))
    destination = tmp_path / "cache/chromium-1.2.3"
    with pytest.raises(tarfile.FilterError):
        unpack(archive, destination)
    assert not destination.exists()
    assert not (tmp_path / "escaped").exists()


def test_launcher_uses_declared_version_and_handles_spaces(tmp_path):
    root = tmp_path / "project with spaces"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts/cloakbrowser.version").write_text("2.0.0\n")
    for version in ("1.0.0", "2.0.0"):
        chrome = root / f".cloakbrowser/chromium-{version}/chrome"
        chrome.parent.mkdir(parents=True)
        chrome.touch(mode=0o755)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "arguments.json"
    fake = bin_dir / "proot-distro"
    fake.write_text(
        f'#!{sys.executable}\nimport json, sys\nfrom pathlib import Path\nif sys.argv[1] == "list":\n print("aistudio-api-termux")\nelse:\n Path({str(log)!r}).write_text(json.dumps(sys.argv[1:]))\n'
    )
    fake.chmod(0o755)
    launcher = (
        Path(__file__).resolve().parents[2]
        / "scripts/cloakbrowser_termux/run-chrome.sh"
    )
    env = dict(
        os.environ,
        PATH=f"{bin_dir}:{os.environ['PATH']}",
        CLOAKBROWSER_PROJECT_DIR=str(root),
    )
    subprocess.run(["bash", str(launcher), "--version"], env=env, check=True)
    arguments = json.loads(log.read_text())
    assert f"{root}/.cloakbrowser/chromium-2.0.0:/opt/cloakbrowser" in arguments
    assert all("chromium-1.0.0" not in argument for argument in arguments)


def test_silent_logging_cannot_be_reenabled_by_runtime_settings(monkeypatch):
    from aistudio_api.infrastructure.utils import logger

    monkeypatch.setenv("AISTUDIO_SILENT", "1")
    monkeypatch.setenv("DEBUG", "1")
    monkeypatch.setenv("AISTUDIO_DUMP_FILE", "1")
    monkeypatch.setattr(logger, "_dump_requests_override", True)
    monkeypatch.setattr(logger, "_dump_to_file_override", True)
    assert not logger.is_dump_requests_enabled()
    assert not logger.is_dump_to_file_enabled()
