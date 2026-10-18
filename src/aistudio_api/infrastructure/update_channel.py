"""Read-only release channel notice shared with the Termux manager."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

PROJECT_ROOT = Path(__file__).resolve().parents[3]
_SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from termux_release import RELEASE_PREFIX, check_release

WEB_CHECK_TIMEOUT = 4.0


class VersionNotice(BaseModel):
    channel: Literal["development", "stable"]
    state: (
        Literal["newer", "current", "unpublished", "unreachable", "unknown"] | None
    ) = None
    current: str | None = None
    latest: str | None = None
    release_url: str | None = None


def _current_branch(root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=root,
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def install_channel(root: Path = PROJECT_ROOT) -> Literal["development", "stable"]:
    """The manager injects the channel; manual starts fall back to the branch."""
    value = os.getenv("AISTUDIO_INSTALL_CHANNEL")
    if value == "development":
        return "development"
    if value == "stable":
        return "stable"
    return "development" if _current_branch(root) else "stable"


def version_notice(root: Path = PROJECT_ROOT) -> VersionNotice:
    channel = install_channel(root)
    if channel == "development":
        return VersionNotice(channel=channel)
    check = check_release(root, timeout=WEB_CHECK_TIMEOUT)
    release_url = f"{RELEASE_PREFIX}{check.latest}" if check.latest else None
    return VersionNotice(
        channel=channel,
        state=check.state,
        current=check.current,
        latest=check.latest,
        release_url=release_url,
    )
