"""Resolve the maintainer-selected public release, independently of master."""

from __future__ import annotations

import re
import urllib.error
import urllib.request

from termux_runtime import ManagerError

REPOSITORY = "https://github.com/ReedSein/aistudio-api.git"
LATEST_RELEASE = "https://github.com/ReedSein/aistudio-api/releases/latest"
RELEASE_PREFIX = "https://github.com/ReedSein/aistudio-api/releases/tag/"


def latest_release() -> str:
    try:
        with urllib.request.urlopen(LATEST_RELEASE, timeout=30) as response:
            url = response.geturl()
    except urllib.error.HTTPError as exc:
        exc.close()
        if exc.code == 404:
            raise ManagerError("尚无正式发布版本，请等待维护者发布。") from exc
        raise ManagerError(f"获取正式版本失败：HTTP {exc.code}。") from exc
    except (OSError, urllib.error.URLError) as exc:
        raise ManagerError(f"获取正式版本失败：{exc}。") from exc
    tag = url.removeprefix(RELEASE_PREFIX)
    if not url.startswith(RELEASE_PREFIX) or not re.fullmatch(
        r"termux-v[0-9]+\.[0-9]+\.[0-9]+", tag
    ):
        raise ManagerError("正式发布必须使用 termux-vX.Y.Z 标签。")
    return tag
