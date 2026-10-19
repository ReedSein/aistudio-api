"""Dependency-free installation metadata and owned service lifecycle."""

from __future__ import annotations

import fcntl
import http.client
import json
import os
import re
import shlex
import shutil
import signal
import socket
import subprocess
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from termux_diagnostics import get_store

Mode = Literal["foreground", "silent"]
ENTRY_MARKER = "# Managed by aistudio-api Termux manager."
DEFAULT_PROOT_NAME = "aistudio-api-termux"
LEGACY_PROOT_NAME = "aistudio-api"
PROOT_NAME_PATTERN = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]*")


class ManagerError(RuntimeError):
    """An actionable installation or service error."""


def read_object(path: Path) -> dict[str, object]:
    try:
        value: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ManagerError(f"无法读取管理记录：{path} ({type(exc).__name__})") from exc
    if not isinstance(value, dict) or not all(isinstance(k, str) for k in value):
        raise ManagerError(f"管理记录格式错误：{path}")
    return value


def atomic_write(path: Path, content: bytes, mode: int = 0o600) -> None:
    """Manager metadata must remain readable before application imports work."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise ManagerError(f"拒绝覆盖符号链接：{path}")
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def write_object(path: Path, value: dict[str, object]) -> None:
    atomic_write(
        path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()
    )


@dataclass(frozen=True)
class Installation:
    root: Path
    config_dir: Path
    state_dir: Path
    port: int = 11451
    development: bool = False
    proot_name: str = DEFAULT_PROOT_NAME

    @classmethod
    def load(
        cls,
        root: Path | None = None,
        port: int | None = None,
        development: bool | None = None,
    ) -> Installation:
        config_dir = (
            Path(os.getenv("XDG_CONFIG_HOME", str(Path.home() / ".config")))
            / "aistudio-termux"
        )
        state_dir = (
            Path(os.getenv("XDG_STATE_HOME", str(Path.home() / ".local/state")))
            / "aistudio-termux"
        )
        record = config_dir / "installation.json"
        saved = read_object(record) if record.exists() else {}
        saved_root = saved.get("project_root")
        saved_port = saved.get("port", 11451)
        saved_proot = saved.get("proot_name")
        if saved_proot is not None and (
            not isinstance(saved_proot, str)
            or not PROOT_NAME_PATTERN.fullmatch(saved_proot)
        ):
            raise ManagerError("安装记录中的浏览器环境名称格式错误。")
        if saved_proot is None:
            override = os.getenv("AISTUDIO_PROOT_NAME")
            if override and not PROOT_NAME_PATTERN.fullmatch(override):
                raise ManagerError("AISTUDIO_PROOT_NAME 名称格式错误。")
            if override:
                saved_proot = override
            elif record.exists():
                # Registrations written before container tracking belong to the
                # original default container; never redirect them to the new name.
                saved_proot = LEGACY_PROOT_NAME
            else:
                saved_proot = DEFAULT_PROOT_NAME
        if development is None:
            saved_development = saved.get("development", False)
            if type(saved_development) is not bool:
                raise ManagerError("安装记录中的开发模式格式错误。")
            development = saved_development
        if root is None:
            if not isinstance(saved_root, str):
                raise ManagerError("尚未登记安装目录，请先运行 Termux 安装脚本。")
            root = Path(saved_root)
        if port is None:
            if type(saved_port) is not int:
                raise ManagerError("安装记录中的端口格式错误。")
            port = saved_port
        if not 1 <= port <= 65535:
            raise ManagerError("端口必须在 1–65535 之间。")
        root = root.expanduser().resolve(strict=True)
        if not (root / "main.py").is_file():
            raise ManagerError(f"不是 aistudio-api 安装目录：{root}")
        return cls(
            root,
            config_dir.resolve(),
            state_dir.resolve(),
            port,
            development,
            saved_proot,
        )

    @property
    def config_file(self) -> Path:
        return self.config_dir / "config.yaml"

    @property
    def process_file(self) -> Path:
        return self.state_dir / "service.json"

    def register(self) -> Path:
        record = self.config_dir / "installation.json"
        if record.exists() and read_object(record).get("project_root") != str(
            self.root
        ):
            raise ManagerError("已登记另一处安装；请使用原安装目录，避免覆盖现有入口。")
        if record.exists():
            previous = Installation.load()
            if previous.port != self.port and Service(previous).running():
                raise ManagerError("请先停止服务，再更改端口。")
        entry = Path.home() / ".local/bin/aistudio"
        if entry.is_symlink() or (
            entry.exists() and ENTRY_MARKER not in entry.read_text()
        ):
            raise ManagerError(f"已有其他命令占用：{entry}")
        uv = shutil.which("uv")
        shell = shutil.which("sh")
        if not uv or not shell:
            raise ManagerError("找不到 uv 或 sh，请先运行环境准备脚本。")
        if not self.config_file.exists():
            source = Path(
                os.getenv("AISTUDIO_CONFIG_FILE", str(self.root / "config.yaml"))
            )
            atomic_write(self.config_file, source.read_bytes())
        command = shlex.join(
            [
                uv,
                "run",
                "--no-sync",
                "--no-python-downloads",
                "--project",
                str(self.root),
                "python",
                str(self.root / "scripts/termux_manager.py"),
            ]
        )
        atomic_write(
            entry,
            (
                f"#!{shell}\n{ENTRY_MARKER}\n"
                'if [ "${1:-}" = "--export-logs" ]; then\n'
                "  shift\n"
                f'  exec {shlex.quote(shutil.which("bash") or "bash")} {shlex.quote(str(self.root / "scripts/export-diagnostics.sh"))} "$@"\n'
                "fi\n"
                f'exec {command} "$@"\n'
            ).encode(),
            0o755,
        )
        write_object(
            record,
            {
                "project_root": str(self.root),
                "port": self.port,
                "development": self.development,
                "proot_name": self.proot_name,
            },
        )
        return entry


@contextmanager
def operation_lock(installation: Installation) -> Iterator[None]:
    installation.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (installation.state_dir / "operation.lock").open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ManagerError(
                "另一个启动、停止或更新操作正在进行，请稍后重试。"
            ) from exc
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


@dataclass(frozen=True)
class ProcessRecord:
    pid: int
    start_ticks: str
    root: str
    port: int
    mode: Mode

    @classmethod
    def load(cls, path: Path) -> ProcessRecord:
        value = read_object(path)
        pid, ticks = value.get("pid"), value.get("start_ticks")
        root, port, mode = value.get("root"), value.get("port"), value.get("mode")
        if (
            type(pid) is not int
            or pid <= 1
            or not isinstance(ticks, str)
            or not ticks.isdigit()
        ):
            raise ManagerError("服务进程记录格式错误；未发送任何停止信号。")
        if (
            not isinstance(root, str)
            or type(port) is not int
            or mode not in ("foreground", "silent")
        ):
            raise ManagerError("服务进程记录格式错误；未发送任何停止信号。")
        return cls(pid, ticks, root, port, mode)


def process_identity(pid: int) -> tuple[str, Path, list[str], int] | None:
    proc = Path("/proc") / str(pid)
    try:
        fields = (proc / "stat").read_text().rsplit(")", 1)[1].split()
        if fields[0] == "Z":
            return None
        args = (proc / "cmdline").read_bytes().decode().rstrip("\0").split("\0")
        return fields[19], (proc / "cwd").resolve(strict=True), args, proc.stat().st_uid
    except (FileNotFoundError, ProcessLookupError):
        return None


def owns_process(record: ProcessRecord, installation: Installation) -> bool:
    identity = process_identity(record.pid)
    if identity is None:
        return False
    ticks, cwd, args, uid = identity
    try:
        group = os.getpgid(record.pid)
    except ProcessLookupError:
        return False
    return (
        ticks == record.start_ticks
        and cwd == installation.root
        and uid == os.getuid()
        and record.root == str(installation.root)
        and record.port == installation.port
        and "main.py" in args
        and "server" in args
        and "--port" in args
        and str(record.port) in args
        and group == record.pid
    )


class Service:
    """Lifecycle operations are serialized by the caller's operation_lock."""

    def __init__(self, installation: Installation) -> None:
        self.installation = installation

    def running(self) -> ProcessRecord | None:
        path = self.installation.process_file
        if not path.exists():
            return None
        record = ProcessRecord.load(path)
        return record if owns_process(record, self.installation) else None

    def capture_environment(self, record: ProcessRecord) -> dict[str, str]:
        if not owns_process(record, self.installation):
            raise ManagerError("服务身份已变化，未读取或停止该进程。")
        try:
            raw = (Path("/proc") / str(record.pid) / "environ").read_bytes()
        except OSError as exc:
            raise ManagerError(
                "无法读取原启动环境；服务未停止，请先手动停止后更新。"
            ) from exc
        if not owns_process(record, self.installation):
            raise ManagerError("服务身份已变化，更新已停止。")
        # Authentication/proxy variables may only exist in the original shell.
        # Keep this snapshot in memory, never in the update journal or logs.
        return dict(
            os.fsdecode(item).split("=", 1) for item in raw.split(b"\0") if item
        )

    def environment(
        self, mode: Mode, inherited: dict[str, str] | None = None
    ) -> dict[str, str]:
        env = os.environ.copy() if inherited is None else inherited.copy()
        env["AISTUDIO_CONFIG_FILE"] = str(self.installation.config_file)
        env["AISTUDIO_PORT"] = str(self.installation.port)
        if not env.get("AISTUDIO_LOG_DIR"):
            env["AISTUDIO_LOG_DIR"] = str(self.installation.state_dir / "logs")
        env["AISTUDIO_BROWSER_EXECUTABLE"] = str(
            self.installation.root / "scripts/cloakbrowser_termux/run-chrome.sh"
        )
        env["AISTUDIO_PROOT_NAME"] = self.installation.proot_name
        env["AISTUDIO_INSTALL_CHANNEL"] = (
            "development" if self.installation.development else "stable"
        )
        if mode == "silent":
            env.update(
                dict.fromkeys(
                    (
                        "DEBUG",
                        "AISTUDIO_DEBUG",
                        "AISTUDIO_DUMP_REQUESTS",
                        "AISTUDIO_DUMP_FILE",
                        "AISTUDIO_DUMP_TO_FILE",
                        "AISTUDIO_DUMP_RAW_RESPONSE",
                    ),
                    "0",
                )
            )
            env["AISTUDIO_SILENT"] = "1"
        else:
            env.pop("AISTUDIO_SILENT", None)
        return env

    def healthy(self) -> bool:
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.installation.port, timeout=1
        )
        try:
            connection.request("GET", "/health")
            response = connection.getresponse()
            value: object = json.loads(response.read(65536))
            return (
                response.status == 200
                and isinstance(value, dict)
                and value.get("status") == "ok"
            )
        except (OSError, http.client.HTTPException, ValueError):
            return False
        finally:
            connection.close()

    def start(
        self,
        mode: Mode,
        timeout: float = 30,
        *,
        inherited_env: dict[str, str] | None = None,
    ) -> subprocess.Popen[bytes]:
        get_store().emit(
            "service_start_requested", mode=mode, port=self.installation.port
        )
        if self.running():
            raise ManagerError("服务已经运行，无需重复启动。")
        try:
            browser_port = int(
                self.environment(mode, inherited_env).get(
                    "AISTUDIO_BROWSER_PORT", "9222"
                )
            )
        except ValueError as exc:
            raise ManagerError("浏览器调试端口须为整数。") from exc
        if not 1 <= browser_port <= 65535 or browser_port == self.installation.port:
            raise ManagerError("浏览器调试端口须在 1–65535 之间，并与服务端口不同。")
        for port in (self.installation.port, browser_port):
            with socket.socket() as probe:
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    probe.bind(("127.0.0.1", port))
                except OSError as exc:
                    raise ManagerError(f"端口 {port} 已被占用，未启动新服务。") from exc
        interpreter = self.installation.root / ".venv/bin/python"
        if not interpreter.is_file():
            raise ManagerError("缺少 uv 管理的项目环境，请重新运行安装脚本。")
        process = subprocess.Popen(
            [
                shutil.which("bash") or "bash",
                str(Path(__file__).with_name("install-termux.sh")),
                "--diagnostics-exec",
                str(interpreter),
                "main.py",
                "server",
                "--port",
                str(self.installation.port),
            ],
            cwd=self.installation.root,
            env=self.environment(mode, inherited_env),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL if mode == "silent" else None,
            stderr=subprocess.DEVNULL if mode == "silent" else None,
            start_new_session=True,
        )
        identity = process_identity(process.pid)
        if identity is None:
            process.wait()
            raise ManagerError(
                f"服务启动失败，退出码 {process.returncode}；可用普通启动查看日志。"
            )
        record = ProcessRecord(
            process.pid,
            identity[0],
            str(self.installation.root),
            self.installation.port,
            mode,
        )
        try:
            write_object(self.installation.process_file, asdict(record))
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise ManagerError(
                        f"服务启动失败，退出码 {process.returncode}；可用普通启动查看日志。"
                    )
                if self.healthy():
                    get_store().emit(
                        "service_ready",
                        child_pid=process.pid,
                        mode=mode,
                        port=self.installation.port,
                    )
                    return process
                time.sleep(0.2)
            raise ManagerError("服务未在规定时间内就绪；可用普通启动查看日志。")
        except BaseException:
            # The child PID comes directly from Popen and is not yet reaped.
            # Clean it up even when persisting its identity failed.
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=20)
            self.installation.process_file.unlink(missing_ok=True)
            raise

    def stop(self, timeout: float = 20) -> bool:
        record = self.running()
        if record is None:
            self.installation.process_file.unlink(missing_ok=True)
            return False
        try:
            get_store().emit("service_stop_requested", child_pid=record.pid)
            os.killpg(record.pid, signal.SIGTERM)
        except ProcessLookupError:
            self.installation.process_file.unlink(missing_ok=True)
            return True
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not owns_process(record, self.installation):
                get_store().emit("service_stopped", child_pid=record.pid)
                self.installation.process_file.unlink(missing_ok=True)
                return True
            time.sleep(0.1)
        raise ManagerError(
            f"进程 {record.pid} 尚未正常退出；保留进程记录，请稍后重试。"
        )

    def foreground(self, process: subprocess.Popen[bytes]) -> None:
        print("服务已启动，按 Ctrl+C 停止并返回菜单。", flush=True)
        try:
            process.wait()
        except KeyboardInterrupt:
            print("\n正在停止服务…", flush=True)
            with operation_lock(self.installation):
                self.stop()
            process.wait(timeout=20)
        finally:
            if process.poll() is not None:
                with operation_lock(self.installation):
                    if not self.running():
                        self.installation.process_file.unlink(missing_ok=True)
