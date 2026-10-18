"""Fast-forward updates from the maintained Termux fork only."""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass

from termux_runtime import (
    Installation,
    ManagerError,
    Service,
    atomic_write,
    write_object,
)

REPOSITORY = "https://github.com/ReedSein/aistudio-api.git"
BRANCH = "master"


@dataclass(frozen=True)
class UpdateStatus:
    current: str
    available: str
    commits: int


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
            allowed.add("git@github.com:ReedSein/aistudio-api.git")
        if len(urls) != 1 or urls[0] not in allowed:
            raise ManagerError(
                "更新来源不符：此版本仅从 ReedSein/aistudio-api 更新，请检查 origin。"
            )
        if self.git("branch", "--show-current") != BRANCH:
            raise ManagerError("当前不是 master 使用分支，请在开发分支中手动管理更新。")
        if self.git("rev-parse", "--abbrev-ref", "@{upstream}") != "origin/master":
            raise ManagerError("master 未跟踪自有 fork 的 origin/master。")
        if self.git("status", "--porcelain", "--untracked-files=no"):
            raise ManagerError("源码有未提交修改，更新已停止；请先处理这些修改。")

    def check(self) -> UpdateStatus:
        self.verify_checkout()
        self.git("fetch", "--no-tags", "origin", BRANCH)
        current = self.git("rev-parse", "HEAD")
        available = self.git("rev-parse", "origin/master")
        counts = self.git(
            "rev-list", "--left-right", "--count", "HEAD...origin/master"
        ).split()
        if int(counts[0]):
            raise ManagerError("本地存在未发布提交或分支已分叉，不能自动更新。")
        return UpdateStatus(current, available, int(counts[1]))

    def prepare(self) -> None:
        command = ["bash", "scripts/setup-termux.sh"]
        result = subprocess.run(command, cwd=self.installation.root, check=False)
        if result.returncode:
            raise ManagerError(
                f"更新环境失败：setup-termux.sh，退出码 {result.returncode}。"
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
            self.git("merge", "--ff-only", status.available)
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
