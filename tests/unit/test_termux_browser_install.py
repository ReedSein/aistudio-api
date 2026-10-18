"""Browser archive layouts, traversal refusal and version selection."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from unpack_browser import unpack


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
        f'#!{sys.executable}\nimport json, sys\nfrom pathlib import Path\nif sys.argv[1] == "list":\n print("aistudio-api")\nelse:\n Path({str(log)!r}).write_text(json.dumps(sys.argv[1:]))\n'
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
