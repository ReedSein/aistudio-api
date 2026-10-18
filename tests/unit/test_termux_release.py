"""Public release resolution fails explicitly without selecting development code."""

from __future__ import annotations

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
