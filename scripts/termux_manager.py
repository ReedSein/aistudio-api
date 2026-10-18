"""Chinese Termux menu for the maintained aistudio-api installation."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from termux_runtime import Installation, ManagerError, Service, operation_lock
from termux_update import Updater

CHOICES = ("普通启动", "静默启动", "停止服务", "检查更新", "一键更新", "退出")


def menu(installation: Installation) -> None:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ManagerError("请在交互式 Termux 窗口运行 aistudio。")
    if not shutil.which("gum"):
        raise ManagerError("缺少菜单工具 gum，请运行 pkg install gum。")
    service = Service(installation)
    children: list[subprocess.Popen[bytes]] = []
    while True:
        children[:] = [child for child in children if child.poll() is None]
        running = service.running()
        state = "未运行"
        if running:
            state = "静默运行中" if running.mode == "silent" else "运行中"
        header = f"AI Studio API · Termux\n状态：{state}  端口：{installation.port}"
        try:
            selection = subprocess.run(
                ["gum", "choose", "--header", header, *CHOICES],
                stdout=subprocess.PIPE,
                text=True,
                check=False,
            )
            if selection.returncode:
                return
            action = selection.stdout.strip()
            if action == "退出":
                return
            foreground = None
            reload_menu = False
            with operation_lock(installation):
                if action == "普通启动":
                    foreground = service.start("foreground")
                elif action == "静默启动":
                    children.append(service.start("silent"))
                    print("已在后台启动，不保存服务日志。")
                    print(
                        f"控制台地址: http://localhost:{installation.port}/", flush=True
                    )
                elif action == "停止服务":
                    print(
                        "服务已停止。"
                        if service.stop()
                        else "服务未运行；过期记录已清理。"
                    )
                elif action == "检查更新":
                    status = Updater(installation).check()
                    print(
                        f"当前 {status.current[:12]}；远端 {status.available[:12]}；可更新 {status.commits} 个提交。"
                    )
                elif action == "一键更新":
                    reload_menu, foreground = Updater(installation).update()
            if foreground is not None:
                running = service.running()
                if running and running.mode == "foreground":
                    service.foreground(foreground)
                else:
                    children.append(foreground)
            if reload_menu:
                print("更新完成，正在重新加载菜单…", flush=True)
                os.execv(
                    sys.executable,
                    [
                        sys.executable,
                        str(installation.root / "scripts/termux_manager.py"),
                    ],
                )
        except KeyboardInterrupt:
            print("\n操作已取消。")
        except (ManagerError, OSError, subprocess.SubprocessError) as exc:
            print(f"操作失败：{exc}", file=sys.stderr)
        input("按回车返回菜单…")


def main() -> int:
    parser = argparse.ArgumentParser(description="aistudio-api Termux 管理菜单")
    setup = parser.add_mutually_exclusive_group()
    setup.add_argument(
        "--register", action="store_true", help="登记安装并创建 aistudio 命令"
    )
    setup.add_argument("--install", action="store_true", help="准备运行环境并登记安装")
    parser.add_argument("--project-root", type=Path, help="登记的项目路径")
    parser.add_argument("--port", type=int, help="服务端口，默认 11451")
    args = parser.parse_args()
    try:
        installation = Installation.load(args.project_root, args.port)
        if args.register or args.install:
            with operation_lock(installation):
                if args.install:
                    if Service(installation).running():
                        raise ManagerError(
                            "服务正在运行，请先停止或使用菜单中的一键更新。"
                        )
                    Updater(installation).prepare()
                entry = installation.register()
            print(f"已登记 {installation.root}\n启动命令：{entry}")
        else:
            menu(installation)
    except KeyboardInterrupt:
        print("\n已退出菜单。")
    except (ManagerError, OSError, subprocess.SubprocessError) as exc:
        print(f"操作失败：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
