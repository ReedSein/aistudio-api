"""Bounded, private, process-safe JSONL diagnostics for application and scripts."""

from __future__ import annotations

import contextvars
import os
import stat
import sys
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import IO

from .redaction import redact_text, sanitized_json

PROJECT_ROOT = Path(__file__).resolve().parents[4]
SESSION_ID = uuid.uuid4().hex[:16]
request_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "diagnostic_request_id", default=""
)
_thread_lock = threading.RLock()
_stores: dict[tuple[str, int, int], DiagnosticStore] = {}
_closed_directories: set[Path] = set()


class DiagnosticError(RuntimeError):
    """A visible collection or export failure without unsafe payload excerpts."""


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def log_directory(root: Path = PROJECT_ROOT) -> Path:
    configured = os.getenv("AISTUDIO_LOG_DIR")
    if configured:
        return Path(configured).expanduser().absolute()
    if sys.platform == "android" or "com.termux" in os.getenv("PREFIX", ""):
        return (
            Path(os.getenv("XDG_STATE_HOME", str(Path.home() / ".local/state")))
            / "aistudio-termux/logs"
        )
    return root / "server-logs/diagnostics"


def _directory_owned(path: Path) -> bool:
    marker = path / ".aistudio-diagnostics-v1"
    if marker.is_symlink():
        raise DiagnosticError("日志目录所有权标记不能是符号链接。")
    if marker.exists():
        info = marker.stat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_size > 64
            or marker.read_text(encoding="ascii") != "aistudio-api diagnostics v1\n"
        ):
            raise DiagnosticError("日志目录所有权标记不符。")
        return True
    if any(item.name != ".lock" for item in path.iterdir()):
        raise DiagnosticError(
            "现有目录不是本功能管理的日志目录，请指定新的专用子目录。"
        )
    return False


def private_directory(path: Path) -> None:
    if path.resolve() in (Path.home().resolve(), PROJECT_ROOT, Path("/")):
        raise DiagnosticError(
            "日志必须使用专用子目录，不能直接使用家目录、项目根或文件系统根。"
        )
    if path.is_symlink():
        raise DiagnosticError("诊断目录不能是符号链接。")
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    if not path.is_dir():
        raise DiagnosticError("诊断目录不是普通目录。")
    _directory_owned(path)
    path.chmod(0o700)


def open_private(path: Path, mode: str = "a") -> IO[str]:
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    flags |= os.O_EXCL if mode == "x" else os.O_APPEND
    fd = os.open(path, flags, 0o600)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        os.close(fd)
        raise DiagnosticError("拒绝写入非普通诊断文件。")
    os.fchmod(fd, 0o600)
    return os.fdopen(fd, mode, encoding="utf-8")


@dataclass(frozen=True)
class LogPolicy:
    directory: Path
    retention_days: int = 7
    max_bytes: int = 512 * 1024 * 1024

    @classmethod
    def from_environment(cls, root: Path = PROJECT_ROOT) -> LogPolicy:
        directory = log_directory(root)
        previous_days, previous_max = 7, 512 * 1024 * 1024
        snapshot = directory / ".policy"
        if directory.is_symlink() or snapshot.is_symlink():
            raise DiagnosticError("诊断目录和日志策略不能是符号链接。")
        try:
            info = snapshot.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise DiagnosticError("日志策略必须是普通文件。")
            with snapshot.open(encoding="ascii") as stream:
                values = stream.read(128).split()
        except FileNotFoundError:
            pass
        else:
            if len(values) != 2 or not all(value.isdigit() for value in values):
                raise DiagnosticError("保存的日志策略格式错误。")
            previous_days, previous_max = map(int, values)
        try:
            days = int(os.getenv("AISTUDIO_LOG_RETENTION_DAYS", str(previous_days)))
            explicit_mib = os.getenv("AISTUDIO_LOG_MAX_MIB")
            maximum = (
                int(explicit_mib) * 1024 * 1024
                if explicit_mib is not None
                else previous_max
            )
        except ValueError as exc:
            raise DiagnosticError("日志保留天数和容量必须是正整数。") from exc
        if not 1 <= days <= 3650 or not 1 <= maximum <= 32768 * 1024 * 1024:
            raise DiagnosticError("日志保留天数须为 1–3650，容量须为 1–32768 MiB。")
        return cls(directory, days, maximum)


class DiagnosticStore:
    def __init__(self, policy: LogPolicy, *, policy_error: str | None = None) -> None:
        self.policy = policy
        self.failure: str | None = policy_error
        self.policy_error = policy_error
        self._warned: str | None = None
        self._policy_saved = False
        if policy_error:
            self.report_failure(policy_error)

    @contextmanager
    def locked(self) -> Iterator[None]:
        private_directory(self.policy.directory)
        with _thread_lock, open_private(self.policy.directory / ".lock") as stream:
            if os.name == "posix":
                import fcntl

                fcntl.flock(stream, fcntl.LOCK_EX)
            try:
                marker = self.policy.directory / ".aistudio-diagnostics-v1"
                if not _directory_owned(self.policy.directory):
                    with open_private(marker, "x") as marker_stream:
                        marker_stream.write("aistudio-api diagnostics v1\n")
                if not self._policy_saved:
                    snapshot = self.policy.directory / ".policy"
                    if snapshot.is_symlink():
                        raise DiagnosticError("日志策略不能是符号链接。")
                    temporary = self.policy.directory / f".policy-{uuid.uuid4().hex}"
                    try:
                        with open_private(temporary, "x") as policy_stream:
                            policy_stream.write(
                                f"{self.policy.retention_days} {self.policy.max_bytes}\n"
                            )
                        temporary.replace(snapshot)
                    finally:
                        temporary.unlink(missing_ok=True)
                    self._policy_saved = True
                yield
            finally:
                if os.name == "posix":
                    fcntl.flock(stream, fcntl.LOCK_UN)

    def files(self) -> list[Path]:
        directory = self.policy.directory
        if directory.is_symlink():
            raise DiagnosticError("诊断目录不能是符号链接。")
        if not directory.exists():
            return []
        return sorted(
            (
                path
                for path in directory.iterdir()
                if path.name.startswith(("events-", "script-", "browser-"))
                and path.suffix in (".jsonl", ".log")
                and not path.is_symlink()
                and path.is_file()
            ),
            key=lambda path: path.stat().st_mtime,
        )

    def _prune(self, incoming: int) -> None:
        files = self.files()
        total = sum(path.stat().st_size for path in files)
        cutoff = time.time() - self.policy.retention_days * 86400
        removed = 0
        removed_bytes = 0
        for path in files:
            info = path.stat()
            if info.st_mtime >= cutoff and total + incoming <= self.policy.max_bytes:
                continue
            # Only files in the managed namespace beneath this exact root qualify.
            path.unlink()
            total -= info.st_size
            removed += 1
            removed_bytes += info.st_size
        if removed:
            journal = self.policy.directory / ".retention.jsonl"
            with open_private(journal) as stream:
                stream.write(
                    sanitized_json(
                        {
                            "timestamp": utc_now(),
                            "removed_files": removed,
                            "removed_bytes": removed_bytes,
                            "reason": "age_or_capacity",
                        }
                    )
                    + "\n"
                )
            if journal.stat().st_size > 256 * 1024:
                # Keep the latest cleanup evidence, with an explicit history boundary.
                journal.unlink()
                with open_private(journal, "x") as stream:
                    stream.write(
                        sanitized_json(
                            {
                                "timestamp": utc_now(),
                                "removed_files": removed,
                                "removed_bytes": removed_bytes,
                                "earlier_cleanup_history_expired": True,
                            }
                        )
                        + "\n"
                    )

    def emit(self, event: str, **fields: object) -> bool:
        if self.policy_error or self.policy.directory.resolve() in _closed_directories:
            return False
        record = {
            "timestamp": utc_now(),
            "session_id": SESSION_ID,
            "pid": os.getpid(),
            "request_id": request_id.get(),
            "event": event,
            **fields,
        }
        try:
            line = (sanitized_json(record) + "\n").encode("utf-8")
            if len(line) > self.policy.max_bytes:
                raise DiagnosticError("单条诊断事件超过日志容量，未记录该事件。")
            with self.locked():
                self._prune(len(line))
                files = [
                    path for path in self.files() if path.name.startswith("events-")
                ]
                path = files[-1] if files else None
                limit = min(4 * 1024 * 1024, self.policy.max_bytes)
                if path is None or path.stat().st_size + len(line) > limit:
                    path = (
                        self.policy.directory
                        / f"events-{time.time_ns()}-{uuid.uuid4().hex[:8]}.jsonl"
                    )
                with open_private(path) as stream:
                    stream.write(line.decode("utf-8"))
                    stream.flush()
            return True
        except (OSError, ValueError, TypeError, DiagnosticError) as exc:
            self.report_failure(type(exc).__name__)
            return False

    def report_failure(self, reason: str) -> None:
        self.failure = reason
        if self._warned != reason:
            self._warned = reason
            print(
                f"[diagnostics] 日志采集失败 ({reason})；诊断包可能缺失证据。",
                file=sys.__stderr__ or sys.stderr,
                flush=True,
            )
        if self.policy_error:
            # Invalid/unreadable policy disables collection; never silently replace it.
            return
        try:
            with (
                self.locked(),
                open_private(self.policy.directory / ".capture-failures.log") as stream,
            ):
                stream.write(f"{utc_now()} collection_failed {reason}\n")
        except (OSError, ValueError, DiagnosticError):
            # The terminal warning is the remaining channel when this disk is unwritable.
            return

    def text(self, event: str, text: str, **fields: object) -> None:
        text = redact_text(text)
        for index, start in enumerate(range(0, max(1, len(text)), 16384)):
            self.emit(event, text=text[start : start + 16384], part=index, **fields)

    def status(self) -> dict[str, object]:
        try:
            files = self.files()
            sizes = [path.stat() for path in files]
            return {
                "directory": str(self.policy.directory),
                "retention_days": self.policy.retention_days,
                "max_bytes": self.policy.max_bytes,
                "file_count": len(files),
                "total_bytes": sum(info.st_size for info in sizes),
                "oldest_file_time": datetime.fromtimestamp(
                    min(info.st_mtime for info in sizes), UTC
                ).isoformat()
                if sizes
                else None,
                "collection_error": self.failure,
                "contains_conversation_content": True,
            }
        except (OSError, DiagnosticError) as exc:
            return {
                "directory": str(self.policy.directory),
                "retention_days": self.policy.retention_days,
                "max_bytes": self.policy.max_bytes,
                "file_count": None,
                "total_bytes": None,
                "oldest_file_time": None,
                "collection_error": type(exc).__name__,
                "contains_conversation_content": True,
            }


def close_directory(directory: Path) -> None:
    """A successful clear-data uninstall must not recreate its removed logs."""
    _closed_directories.add(directory.resolve())


def get_store() -> DiagnosticStore:
    try:
        policy = LogPolicy.from_environment()
    except (OSError, ValueError, DiagnosticError) as exc:
        # Logging failure must not turn a business request into an application failure.
        directory = log_directory()
        key = (str(directory), -1, -1)
        with _thread_lock:
            if key not in _stores:
                _stores[key] = DiagnosticStore(
                    LogPolicy(directory), policy_error=type(exc).__name__
                )
            return _stores[key]
    key = (str(policy.directory), policy.retention_days, policy.max_bytes)
    with _thread_lock:
        if key not in _stores:
            _stores[key] = DiagnosticStore(policy)
        return _stores[key]
