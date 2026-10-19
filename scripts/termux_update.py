"""Shared fast-forward updates for public releases and local development."""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass

from termux_release import ACCEPTED_ORIGINS, REPOSITORY, latest_release
from termux_runtime import (
    Installation,
    ManagerError,
    Service,
    atomic_write,
    write_object,
)

BRANCH = "master"


@dataclass(frozen=True)
class UpdateStatus:
    current: str
    available: str
    commits: int
    version: str


class Updater:
    def __init__(
        self, installation: Installation, repository: str = REPOSITORY
    ) -> None:
        self.installation = installation
        self.repository = repository

    def git(self, *args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=self.installation.root,
            text=True,
            capture_output=True,
            timeout=60,
            check=False,
        )
        if result.returncode:
            raise ManagerError(
                f"Git 操作失败 ({' '.join(args[:2])})：\n{result.stderr.strip()}"
            )
        return result.stdout.strip()

    def verify_checkout(self) -> None:
        urls = self.git("remote", "get-url", "--all", "origin").splitlines()
        allowed = {self.repository}
        if self.repository == REPOSITORY:
            allowed = set(ACCEPTED_ORIGINS)
        if len(urls) != 1 or urls[0] not in allowed:
            raise ManagerError(
                "更新来源不符：此版本仅从 ReedSein/aistudio-api-termux 更新，请检查 origin。"
            )
        branch = self.git("branch", "--show-current")
        if self.installation.development and branch != BRANCH:
            raise ManagerError("当前不是 master 使用分支，请在开发分支中手动管理更新。")
        if (
            self.installation.development
            and self.git("rev-parse", "--abbrev-ref", "@{upstream}") != "origin/master"
        ):
            raise ManagerError("master 未跟踪自有 fork 的 origin/master。")
        if self.git("status", "--porcelain", "--untracked-files=no"):
            raise ManagerError("源码有未提交修改，操作已停止；请先处理这些修改。")

    def check(self) -> UpdateStatus:
        self.verify_checkout()
        version = BRANCH if self.installation.development else latest_release()
        reference = (
            BRANCH
            if self.installation.development
            else f"refs/tags/{version}:refs/tags/{version}"
        )
        self.git("fetch", "--no-tags", "origin", reference)
        current = self.git("rev-parse", "HEAD")
        available = self.git("rev-parse", "FETCH_HEAD^{commit}")
        counts = self.git(
            "rev-list", "--left-right", "--count", f"HEAD...{available}"
        ).split()
        if int(counts[0]):
            raise ManagerError("本地领先于目标版本或分支已分叉，不能自动更新。")
        return UpdateStatus(current, available, int(counts[1]), version)

    def prepare(self) -> None:
        command = ["bash", "scripts/setup-termux.sh"]
        if self.installation.development:
            command.append("--dev")
        environment = dict(os.environ)
        environment["AISTUDIO_PROOT_NAME"] = self.installation.proot_name
        result = subprocess.run(
            command, cwd=self.installation.root, env=environment, check=False
        )
        if result.returncode:
            raise ManagerError(
                f"准备运行环境失败：setup-termux.sh，退出码 {result.returncode}。"
            )

    def update(self) -> tuple[bool, subprocess.Popen[bytes] | None]:
        status = self.check()
        if not status.commits:
            print("已经是我们的最新版本。")
            return False, None
        service = Service(self.installation)
        running = service.running()
        inherited_env = service.capture_environment(running) if running else None
        journal: dict[str, object] = {
            "previous_commit": status.current,
            "target_commit": status.available,
            "stage": "preparing",
            "time": int(time.time()),
        }
        journal_path = self.installation.state_dir / "last-update.json"
        write_object(journal_path, journal)
        lockfile = self.installation.root / "uv.lock"
        if lockfile.is_file():
            atomic_write(
                self.installation.state_dir / "previous-uv.lock", lockfile.read_bytes()
            )
        try:
            if running:
                print("更新会短暂停止服务，正在正常停止…", flush=True)
                service.stop()
            journal["stage"] = "code"
            write_object(journal_path, journal)
            # Git silently replaces ignored local files that the update adds;
            # stop instead so user data and local notes survive.
            self.git("merge", "--ff-only", "--no-overwrite-ignore", status.available)
            journal["stage"] = "environment"
            write_object(journal_path, journal)
            self.prepare()
            self.installation.register()
            journal["stage"] = "restart"
            write_object(journal_path, journal)
            process = (
                service.start(running.mode, inherited_env=inherited_env)
                if running
                else None
            )
            journal["stage"] = "complete"
            write_object(journal_path, journal)
            return True, process
        except (ManagerError, OSError, subprocess.SubprocessError, KeyboardInterrupt):
            print(
                f"更新停在 {journal['stage']} 阶段。更新前版本：{status.current[:12]}。\n恢复记录：{journal_path}；旧浏览器及用户数据已保留。",
                flush=True,
            )
            raise
