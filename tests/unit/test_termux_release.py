"""Public release resolution fails explicitly without selecting development code."""

from __future__ import annotations

import subprocess
import sys
import urllib.error
from contextlib import nullcontext
from email.message import Message
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import termux_release
from termux_runtime import ManagerError


@pytest.mark.parametrize(
    "tag", ["termux-v1.2.3", "v0.1.0", "termux-v1.2.3-rc1", "master"]
)
def test_latest_release_requires_maintained_release_tag(monkeypatch, tag):
    response = SimpleNamespace(geturl=lambda: termux_release.RELEASE_PREFIX + tag)
    monkeypatch.setattr(
        termux_release.urllib.request,
        "urlopen",
        lambda *_a, **_kw: nullcontext(response),
    )
    if tag == "termux-v1.2.3":
        assert termux_release.latest_release() == tag
    else:
        with pytest.raises(ManagerError, match="标签"):
            termux_release.latest_release()


@pytest.mark.parametrize("status", [404, 403, 500])
def test_missing_or_unavailable_release_is_an_error(monkeypatch, status):
    def fail(*_args, **_kwargs):
        raise urllib.error.HTTPError(
            termux_release.LATEST_RELEASE, status, "fixture", Message(), None
        )

    monkeypatch.setattr(termux_release.urllib.request, "urlopen", fail)
    with pytest.raises(ManagerError, match=r"尚无正式|获取正式"):
        termux_release.latest_release()


def test_legacy_release_address_is_still_accepted(monkeypatch):
    response = SimpleNamespace(
        geturl=lambda: termux_release.LEGACY_RELEASE_PREFIX + "termux-v1.2.3"
    )
    monkeypatch.setattr(
        termux_release.urllib.request,
        "urlopen",
        lambda *_a, **_kw: nullcontext(response),
    )
    assert termux_release.latest_release() == "termux-v1.2.3"


def _check_with(monkeypatch, current, latest):
    monkeypatch.setattr(termux_release, "current_release_tag", lambda _root: current)
    if isinstance(latest, ManagerError):

        def fail(*_args, **_kwargs):
            raise latest

        monkeypatch.setattr(termux_release, "latest_release", fail)
    else:
        monkeypatch.setattr(
            termux_release, "latest_release", lambda timeout=30.0: latest
        )
    return termux_release.check_release(Path("/nonexistent"))


def test_release_check_reports_an_available_update(monkeypatch):
    check = _check_with(monkeypatch, "termux-v1.0.0", "termux-v1.2.0")
    assert check.state == "newer"
    assert check.current == "termux-v1.0.0"
    assert check.latest == "termux-v1.2.0"


def test_release_check_reports_up_to_date(monkeypatch):
    assert _check_with(monkeypatch, "termux-v1.2.0", "termux-v1.2.0").state == "current"
    assert _check_with(monkeypatch, "termux-v1.3.0", "termux-v1.2.0").state == "current"


def test_release_check_reports_unpublished_and_unreachable(monkeypatch):
    unpublished = _check_with(
        monkeypatch, "termux-v1.0.0", termux_release.UnpublishedRelease("fixture")
    )
    assert unpublished.state == "unpublished"
    assert unpublished.latest is None
    unreachable = _check_with(
        monkeypatch, "termux-v1.0.0", termux_release.ReleaseLookupError("fixture")
    )
    assert unreachable.state == "unreachable"
    assert unreachable.current == "termux-v1.0.0"


def test_release_check_reports_unknown_current_version(monkeypatch):
    check = _check_with(monkeypatch, None, "termux-v1.2.0")
    assert check.state == "unknown"
    assert check.current is None


def test_latest_release_passes_the_timeout(monkeypatch):
    captured = {}

    def fake_urlopen(_url, timeout=None):
        captured["timeout"] = timeout
        response = SimpleNamespace(
            geturl=lambda: termux_release.RELEASE_PREFIX + "termux-v1.0.0"
        )
        return nullcontext(response)

    monkeypatch.setattr(termux_release.urllib.request, "urlopen", fake_urlopen)
    assert termux_release.latest_release(timeout=3.0) == "termux-v1.0.0"
    assert captured["timeout"] == 3.0


def test_current_release_tag_requires_an_exact_tag(tmp_path):
    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=tmp_path, text=True, capture_output=True, check=True
        ).stdout.strip()

    git("init", "--initial-branch=master")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.invalid")
    git("config", "commit.gpgsign", "false")
    (tmp_path / "main.py").write_text("# fixture\n")
    git("add", "main.py")
    git("commit", "-m", "fixture")
    git("tag", "termux-v9.9.9")
    assert termux_release.current_release_tag(tmp_path) == "termux-v9.9.9"
    (tmp_path / "next.py").write_text("# next\n")
    git("add", "next.py")
    git("commit", "-m", "next")
    assert termux_release.current_release_tag(tmp_path) is None
