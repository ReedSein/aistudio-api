"""Remove managed installation files while preserving local development work."""

from __future__ import annotations

import shlex
import shutil
from pathlib import Path

from termux_diagnostics import get_store
from termux_runtime import (
    ENTRY_MARKER,
    Installation,
    ManagerError,
    Service,
    read_object,
)
from termux_update import Updater

from aistudio_api.infrastructure.diagnostics.store import close_directory


class Uninstaller:
    """Called under operation_lock, including the user's confirmation."""

    def __init__(self, installation: Installation, *, clear_data: bool = False) -> None:
        self.installation = installation
        self.clear_data = clear_data
        self.entry = Path.home() / ".local/bin/aistudio"

    def paths(self) -> list[Path]:
        installation = self.installation
        root = installation.root
        record = installation.config_dir / "installation.json"
        if read_object(record).get("project_root") != str(root):
            raise ManagerError("卸载目录与安装登记不符，未删除任何文件。")
        if self.entry.is_symlink() or (
            self.entry.exists()
            and (
                ENTRY_MARKER not in self.entry.read_text()
                or shlex.quote(str(root)) not in self.entry.read_text()
            )
        ):
            raise ManagerError(f"命令入口不属于此安装：{self.entry}")
        paths = [root / ".venv", root / ".cloakbrowser", self.entry]
        if not installation.development:
            updater = Updater(installation)
            updater.verify_checkout()
            if updater.git(
                "rev-list",
                "--max-count=1",
                "--all",
                "HEAD",
                "--not",
                "--remotes",
                "--tags",
            ):
                raise ManagerError(
                    "本地有仅保存在此处的提交或暂存工作，卸载已停止；请先登记为开发用法或保存这些工作。"
                )
            # Only Git-owned program files are removed; unknown local files stay.
            for name in updater.git("ls-files", "-z").split("\0"):
                if not name:
                    continue
                relative = Path(name)
                if relative.is_absolute() or ".." in relative.parts:
                    raise ManagerError("程序文件路径超出安装目录，未执行卸载。")
                if relative.parts[0] in ("data", "dumps", "server-logs", ".env"):
                    continue
                paths.append(root / relative)
            paths.append(root / ".git")
        if self.clear_data:
            paths.extend(
                root / name for name in ("data", "dumps", "server-logs", ".env")
            )
            paths.extend([installation.config_file, record])
            logs = installation.state_dir / "logs"
            if logs.is_symlink():
                paths.append(logs)
            elif logs.exists():
                marker = logs / ".aistudio-diagnostics-v1"
                if (
                    marker.is_symlink()
                    or not marker.is_file()
                    or marker.read_text() != "aistudio-api diagnostics v1\n"
                ):
                    raise ManagerError("无法确认诊断日志目录归属，未执行卸载。")
                paths.append(logs)
        paths.extend(
            installation.state_dir / name
            for name in ("service.json", "last-update.json", "previous-uv.lock")
        )
        for path in paths:
            for parent in path.parents:
                if (
                    parent == root
                    or parent == installation.config_dir
                    or parent == installation.state_dir
                    or parent == self.entry.parent
                ):
                    break
                if parent.is_symlink():
                    raise ManagerError(f"删除路径经过符号链接，未执行卸载：{parent}")
        return paths

    def run(self) -> bool:
        get_store().emit("uninstall_start", clear_data=self.clear_data)
        paths = self.paths()
        installation = self.installation
        if installation.development:
            print(f"开发源码和 Git 记录保留：{installation.root}")
        else:
            print(
                f"删除程序文件和 Git 目录：{installation.root}（保留未接管的本地文件）"
            )
        print(
            f"删除运行环境、浏览器缓存及命令入口：{installation.root / '.venv'}、{installation.root / '.cloakbrowser'}、{self.entry}"
        )
        if self.clear_data:
            print("清除以下配置与数据，删除后无法恢复：")
            for path in (
                installation.config_file,
                installation.config_dir / "installation.json",
                installation.state_dir / "logs",
                *(
                    installation.root / name
                    for name in ("data", "dumps", "server-logs", ".env")
                ),
            ):
                print(f"  {path}")
        else:
            print(
                f"保留配置：{installation.config_dir}\n保留账号与统计：{installation.root / 'data'}"
            )
        print("共享系统工具、浏览器容器和自定义外部数据目录保留。")
        if input("输入 UNINSTALL 确认卸载，其他输入取消：").strip() != "UNINSTALL":
            get_store().emit("uninstall_cancelled")
            print("已取消卸载。")
            return False
        Service(installation).stop()
        get_store().emit("uninstall_confirmed", target_count=len(paths))
        for path in paths:
            if path.is_symlink() or not path.is_dir():
                path.unlink(missing_ok=True)
            else:
                shutil.rmtree(path)
            if path == installation.state_dir / "logs":
                close_directory(path)
        print("卸载完成。")
        get_store().emit("uninstall_completed", clear_data=self.clear_data)
        return True
