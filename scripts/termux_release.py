"""Resolve the maintainer-selected public release, independently of master."""

from __future__ import annotations

import re
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from termux_runtime import ManagerError

REPOSITORY = "https://github.com/ReedSein/aistudio-api-termux.git"
LATEST_RELEASE = "https://github.com/ReedSein/aistudio-api-termux/releases/latest"
RELEASE_PREFIX = "https://github.com/ReedSein/aistudio-api-termux/releases/tag/"
LEGACY_REPOSITORY = "https://github.com/ReedSein/aistudio-api.git"
LEGACY_RELEASE_PREFIX = "https://github.com/ReedSein/aistudio-api/releases/tag/"
ACCEPTED_ORIGINS = (
    REPOSITORY,
    "git@github.com:ReedSein/aistudio-api-termux.git",
    LEGACY_REPOSITORY,
    "git@github.com:ReedSein/aistudio-api.git",
)
TAG_PATTERN = re.compile(r"termux-v([0-9]+)\.([0-9]+)\.([0-9]+)")


class UnpublishedRelease(ManagerError):
    """The maintainer has not published a formal release yet."""


class ReleaseLookupError(ManagerError):
    """The release lookup failed at the network or HTTP level."""


def latest_release(timeout: float = 30.0) -> str:
    try:
        with urllib.request.urlopen(LATEST_RELEASE, timeout=timeout) as response:
            url = response.geturl()
    except urllib.error.HTTPError as exc:
        exc.close()
        if exc.code == 404:
            raise UnpublishedRelease("尚无正式发布版本，请等待维护者发布。") from exc
        raise ReleaseLookupError(f"获取正式版本失败：HTTP {exc.code}。") from exc
    except (OSError, urllib.error.URLError) as exc:
        raise ReleaseLookupError(f"获取正式版本失败：{exc}。") from exc
    if url.startswith(RELEASE_PREFIX):
        tag = url.removeprefix(RELEASE_PREFIX)
    elif url.startswith(LEGACY_RELEASE_PREFIX):
        tag = url.removeprefix(LEGACY_RELEASE_PREFIX)
    else:
        tag = ""
    if not re.fullmatch(r"termux-v[0-9]+\.[0-9]+\.[0-9]+", tag):
        raise ManagerError("正式发布必须使用 termux-vX.Y.Z 标签。")
    return tag


def current_release_tag(root: Path) -> str | None:
    """Installed release tag when HEAD sits exactly on one, otherwise None."""
    try:
        result = subprocess.run(
            ["git", "describe", "--tags", "--exact-match", "HEAD"],
            cwd=root,
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    tag = result.stdout.strip()
    if result.returncode or not re.fullmatch(r"termux-v[0-9]+\.[0-9]+\.[0-9]+", tag):
        return None
    return tag


@dataclass(frozen=True)
class ReleaseCheck:
    state: Literal["newer", "current", "unpublished", "unreachable", "unknown"]
    current: str | None
    latest: str | None


def check_release(root: Path, timeout: float = 30.0) -> ReleaseCheck:
    """Shared release judgment for the menu notice and the web console."""
    current = current_release_tag(root)
    try:
        latest = latest_release(timeout=timeout)
    except UnpublishedRelease:
        return ReleaseCheck("unpublished", current, None)
    except ManagerError:
        return ReleaseCheck("unreachable", current, None)
    if current is None:
        return ReleaseCheck("unknown", None, latest)
    if _version_tuple(current) < _version_tuple(latest):
        return ReleaseCheck("newer", current, latest)
    return ReleaseCheck("current", current, latest)


def _version_tuple(tag: str) -> tuple[int, int, int]:
    match = TAG_PATTERN.fullmatch(tag)
    if match is None:
        raise ManagerError(f"无法解析版本标签：{tag}")
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))
