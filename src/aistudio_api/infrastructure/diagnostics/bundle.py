"""Create a local support archive from explicit, bounded, sanitized sources."""

from __future__ import annotations

import hashlib
import importlib.metadata
import io
import json
import os
import platform
import shutil
import socket
import stat
import subprocess
import sys
import tarfile
from pathlib import Path

from .redaction import redact_text, sanitize, sanitized_json
from .store import PROJECT_ROOT, DiagnosticError, DiagnosticStore, LogPolicy, utc_now

_NOTICE = """AI Studio API 诊断包

本包包含运行日志以及已采集的请求、回复原文，可能涉及个人对话；仅发送给信任的排错人员。
认证头、Cookie、API 密钥等凭据已按结构与已知格式脱敏；未打包账号文件或浏览器登录目录。
manifest.json 列出采集截止时间、文件摘要、缺失和清理情况，environment.json 是环境白名单摘要。
logs/ 中 JSONL 每行是一条事件；用 request_id 关联一次请求、session_id 关联一次进程会话。
http_request_body/http_response_body 的 text 按文件时间、行顺序、sequence/part 重组正文。
http_end 区分 completed、client_disconnected、cancelled、timeout、exception 和 incomplete。
缺少 http_end 或 response_complete=false 的请求不能当成完整成功；进程可能被系统强杀。
仅能导出实际采集且仍保留的内容。历史清理、写入失败、未知旧格式或进行中的记录见清单。
"""
_ENV_FIELDS = (
    "AISTUDIO_PORT",
    "AISTUDIO_BROWSER_PORT",
    "AISTUDIO_BROWSER_HEADLESS",
    "AISTUDIO_TIMEOUT_STREAM",
    "AISTUDIO_TIMEOUT_REPLAY",
    "AISTUDIO_BROWSER_IDLE_TIMEOUT",
    "AISTUDIO_PROOT_NAME",
    "AISTUDIO_INSTALL_CHANNEL",
    "AISTUDIO_SILENT",
    "AISTUDIO_LOG_RETENTION_DAYS",
    "AISTUDIO_LOG_MAX_MIB",
    "TZ",
)
_CONFIG_FIELDS = frozenset(
    {
        "level",
        "timeout_stream",
        "timeout_replay",
        "browser_idle_timeout",
        "dump_requests",
        "dump_to_file",
    }
)


def read_regular(path: Path, limit: int) -> bytes:
    """Read a fixed-size snapshot; never follow a symlink or wait on a device/FIFO."""
    fd = os.open(
        path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    )
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise DiagnosticError("诊断来源不是普通文件。")
        if info.st_size > limit:
            raise DiagnosticError("诊断来源超过本次归档容量。")
        return stream.read(info.st_size)


def _probe(command: list[str], root: Path) -> dict[str, object]:
    try:
        completed = subprocess.run(
            command, cwd=root, capture_output=True, text=True, timeout=3, check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"error": type(exc).__name__}
    if completed.returncode:
        return {
            "exit_code": completed.returncode,
            "error": redact_text(completed.stderr)[:2000],
        }
    return {"value": redact_text(completed.stdout.strip())}


def environment_summary(
    root: Path, config_file: Path | None = None
) -> dict[str, object]:
    versions: dict[str, object] = {}
    for package in (
        "fastapi",
        "starlette",
        "uvicorn",
        "pydantic",
        "curl_cffi",
        "httpx",
        "websockets",
        "PyYAML",
    ):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = {"error": "not_installed_in_export_interpreter"}
    summary: dict[str, object] = {
        "python": sys.version,
        "architecture": platform.machine(),
        "system": platform.system(),
        "kernel": platform.release(),
        "dependencies": versions,
        "commit": _probe(["git", "rev-parse", "HEAD"], root),
        "release": _probe(["git", "describe", "--tags", "--exact-match", "HEAD"], root),
        "uv": _probe(["uv", "--version"], root),
        "runtime_environment": {
            name: redact_text(os.environ[name])
            for name in _ENV_FIELDS
            if name in os.environ
        },
        "proxy_configured": any(
            os.getenv(name)
            for name in (
                "AISTUDIO_PROXY",
                "AISTUDIO_PROXY_URL",
                "HTTPS_PROXY",
                "HTTP_PROXY",
                "ALL_PROXY",
            )
        ),
        "web_password_configured": bool(
            os.getenv("AISTUDIO_WEB_PASSWORD") or os.getenv("AISTUDIO_ADMIN_PASSWORD")
        ),
    }
    if shutil.which("getprop"):
        summary["android_release"] = _probe(
            ["getprop", "ro.build.version.release"], root
        )
        summary["android_sdk"] = _probe(["getprop", "ro.build.version.sdk"], root)
    try:
        usage = shutil.disk_usage(root)
        summary["disk"] = {"total": usage.total, "free": usage.free}
    except OSError as exc:
        summary["disk"] = {"error": type(exc).__name__}
    browser = root / "scripts/cloakbrowser.version"
    try:
        summary["expected_browser_version"] = (
            read_regular(browser, 1024).decode().strip()
        )
    except (OSError, DiagnosticError, UnicodeError) as exc:
        summary["expected_browser_version"] = {"error": type(exc).__name__}
    config = config_file or Path(
        os.getenv("AISTUDIO_CONFIG_FILE", str(root / "config.yaml"))
    )
    try:
        text = read_regular(config, 1024 * 1024).decode("utf-8")
        selected: dict[str, str] = {}
        for line in text.splitlines():
            key, sep, value = line.strip().partition(":")
            if sep and key in _CONFIG_FIELDS:
                value = value.partition("#")[0].strip()
                if (
                    value.upper()
                    in {
                        "DEBUG",
                        "INFO",
                        "WARNING",
                        "ERROR",
                        "CRITICAL",
                        "TRUE",
                        "FALSE",
                    }
                    or value.isdigit()
                ):
                    selected[key] = value
        summary["configuration"] = {
            "selected_fields": selected,
            "scope": "logging_and_runtime_allowlist",
        }
    except (OSError, DiagnosticError, UnicodeError) as exc:
        summary["configuration"] = {"error": type(exc).__name__}
    state = (
        Path(os.getenv("XDG_STATE_HOME", str(Path.home() / ".local/state")))
        / "aistudio-termux"
    )
    try:
        value: object = json.loads(read_regular(state / "service.json", 65536))
        if isinstance(value, dict):
            summary["service_record"] = {
                key: value.get(key) for key in ("pid", "port", "mode", "start_ticks")
            }
    except FileNotFoundError:
        summary["service_record"] = None
    except (OSError, ValueError, DiagnosticError) as exc:
        summary["service_record"] = {"error": type(exc).__name__}
    ports: dict[str, object] = {}
    for name, default in (
        ("AISTUDIO_PORT", "11451"),
        ("AISTUDIO_BROWSER_PORT", "9222"),
    ):
        try:
            port = int(os.getenv(name, default))
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                ports[str(port)] = "listening"
        except (OSError, ValueError, OverflowError):
            ports[os.getenv(name, default)] = "unreachable_or_invalid"
    summary["local_ports"] = ports
    return summary


def _safe_source(path: Path, data: bytes) -> tuple[bytes, list[str]]:
    issues: list[str] = []
    if path.suffix == ".jsonl":
        records: list[str] = []
        for number, line in enumerate(data.splitlines(), 1):
            if not line.strip():
                continue
            try:
                value: object = json.loads(line.decode("utf-8"))
            except (ValueError, UnicodeError) as exc:
                issues.append(f"line {number}: {type(exc).__name__}")
                continue
            records.append(sanitized_json(value))
        return ("\n".join(records) + "\n").encode(), issues
    text = data.decode("utf-8", errors="strict")
    if path.suffix == ".json":
        value = json.loads(text)
        if not isinstance(value, dict) or not (
            {"req_id", "status_code"} <= value.keys()
        ):
            raise DiagnosticError("未知旧转储格式，无法保证凭据脱敏。")
        return (sanitized_json(value) + "\n").encode(), issues
    return redact_text(text).encode(), issues


def _reason(exc: Exception) -> str:
    if isinstance(exc, DiagnosticError):
        return str(exc)
    if isinstance(exc, OSError):
        return f"{type(exc).__name__}: errno={exc.errno}"
    return type(exc).__name__


def _coverage(data: bytes, suffix: str) -> tuple[str | None, str | None]:
    if suffix != ".jsonl":
        return None, None
    first = last = None
    for line in data.splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        if isinstance(value, dict) and isinstance(value.get("timestamp"), str):
            stamp = value["timestamp"]
            first = stamp if first is None or stamp < first else first
            last = stamp if last is None or stamp > last else last
    return first, last


def export_bundle(
    output: Path,
    *,
    root: Path = PROJECT_ROOT,
    policy: LogPolicy | None = None,
    config_file: Path | None = None,
    legacy_dirs: tuple[Path, ...] = (),
) -> dict[str, object]:
    from contextlib import ExitStack

    policy = policy or LogPolicy.from_environment(root)
    store = DiagnosticStore(policy)
    inventory: list[dict[str, object]] = []
    omissions: list[dict[str, str]] = []
    manifest: dict[str, object] = {
        "schema_version": 1,
        "created_at": utc_now(),
        "contains_conversation_content": True,
        "cutoff": None,
        "retention_days": policy.retention_days,
        "max_bytes": policy.max_bytes,
        "coverage_start": None,
        "coverage_end": None,
        "limits": [
            "Only recorded and retained evidence is available.",
            "Requests without http_end may be in progress or interrupted.",
            "Legacy opaque wire dumps are excluded when credential slots are unknown.",
            "Script outputs use a bounded read; their last line may still be in progress.",
        ],
    }
    environment = environment_summary(root, config_file)
    output.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(
        output,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        with (
            os.fdopen(fd, "wb") as destination,
            tarfile.open(fileobj=destination, mode="w:gz") as archive,
        ):
            coverage_start: str | None = None
            coverage_end: str | None = None

            def add(
                name: str,
                content: bytes,
                *,
                source: Path | None = None,
                source_mtime: float | None = None,
            ) -> None:
                nonlocal coverage_start, coverage_end
                member = tarfile.TarInfo(name)
                member.size, member.mode = len(content), 0o600
                archive.addfile(member, io.BytesIO(content))
                item: dict[str, object] = {
                    "name": name,
                    "bytes": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                }
                if source is not None:
                    first, last = _coverage(content, source.suffix)
                    item.update(
                        first_timestamp=first,
                        last_timestamp=last,
                        source_mtime=source_mtime,
                    )
                    if first is not None:
                        coverage_start = (
                            first
                            if coverage_start is None or first < coverage_start
                            else coverage_start
                        )
                    if last is not None:
                        coverage_end = (
                            last
                            if coverage_end is None or last > coverage_end
                            else coverage_end
                        )
                inventory.append(item)

            add("README.txt", _NOTICE.encode())
            add("environment.json", sanitized_json(environment).encode())
            budget = policy.max_bytes
            # Snapshot open descriptors and byte lengths under the writers' lock.
            # Append/rotation can then continue while sanitization and gzip run.
            with ExitStack() as handles:
                snapshots = []
                with store.locked():
                    manifest["cutoff"] = utc_now()
                    sources = store.files()
                    sources.extend(
                        path
                        for name in (".retention.jsonl", ".capture-failures.log")
                        if (path := policy.directory / name).exists()
                    )
                    manifest["logs_pruned"] = (
                        policy.directory / ".retention.jsonl"
                    ).exists()
                    manifest["collection_failures_recorded"] = (
                        policy.directory / ".capture-failures.log"
                    ).exists()
                    for path in policy.directory.iterdir():
                        if (
                            path.name.startswith(("events-", "script-", "browser-"))
                            and path.is_symlink()
                        ):
                            omissions.append(
                                {
                                    "source": path.name,
                                    "reason": "symlink_source_refused",
                                }
                            )
                    if not sources:
                        omissions.append(
                            {"source": "logs", "reason": "no_retained_logs"}
                        )
                    for path in sources:
                        try:
                            if len(snapshots) >= 1024:
                                raise DiagnosticError(
                                    "归档来源超过 1024 个，未打开额外文件。"
                                )
                            descriptor = os.open(
                                path,
                                os.O_RDONLY
                                | getattr(os, "O_NOFOLLOW", 0)
                                | getattr(os, "O_NONBLOCK", 0),
                            )
                            stream = handles.enter_context(os.fdopen(descriptor, "rb"))
                            info = os.fstat(stream.fileno())
                            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                                raise DiagnosticError("拒绝非普通或多重硬链接来源。")
                            snapshots.append(
                                (path, stream, info.st_size, info.st_mtime)
                            )
                        except (OSError, DiagnosticError) as exc:
                            omissions.append(
                                {"source": path.name, "reason": _reason(exc)}
                            )
                for path, stream, size, modified in snapshots:
                    try:
                        if size > budget:
                            raise DiagnosticError("来源超过本次归档剩余容量。")
                        raw = stream.read(size)
                        if len(raw) != size:
                            omissions.append(
                                {
                                    "source": path.name,
                                    "reason": "source_shortened_during_snapshot",
                                }
                            )
                        data, issues = _safe_source(path, raw)
                        for reason in issues:
                            omissions.append({"source": path.name, "reason": reason})
                        budget -= len(data)
                        add(
                            "logs/" + path.name.lstrip("."),
                            data,
                            source=path,
                            source_mtime=modified,
                        )
                    except (OSError, ValueError, UnicodeError, DiagnosticError) as exc:
                        omissions.append({"source": path.name, "reason": _reason(exc)})

            roots = tuple(dict.fromkeys((root / "dumps", *legacy_dirs)))
            for index, directory in enumerate(roots):
                if directory.is_symlink():
                    omissions.append(
                        {
                            "source": f"legacy-{index}",
                            "reason": "symlink_directory_refused",
                        }
                    )
                    continue
                if not directory.is_dir():
                    continue
                for path in sorted(directory.glob("*.json")):
                    try:
                        data, issues = _safe_source(
                            path, read_regular(path, max(0, budget))
                        )
                        budget -= len(data)
                        add(f"legacy-{index}/" + path.name, data)
                    except (OSError, ValueError, UnicodeError, DiagnosticError) as exc:
                        omissions.append(
                            {
                                "source": f"legacy-{index}/" + path.name,
                                "reason": _reason(exc),
                            }
                        )
            state = (
                Path(os.getenv("XDG_STATE_HOME", str(Path.home() / ".local/state")))
                / "aistudio-termux"
            )
            journal = state / "last-update.json"
            if journal.exists():
                try:
                    value = sanitize(json.loads(read_regular(journal, 65536)))
                    add("last-update.json", sanitized_json(value).encode())
                except (OSError, ValueError, DiagnosticError) as exc:
                    omissions.append(
                        {"source": "last-update.json", "reason": _reason(exc)}
                    )
            manifest.update(
                files=inventory,
                file_count=len(inventory),
                omissions=omissions,
                has_omissions=bool(omissions)
                or bool(manifest.get("collection_failures_recorded")),
                coverage_start=coverage_start,
                coverage_end=coverage_end,
            )
            content = json.dumps(manifest, ensure_ascii=False, indent=2).encode()
            member = tarfile.TarInfo("manifest.json")
            member.size, member.mode = len(content), 0o600
            archive.addfile(member, io.BytesIO(content))
        return manifest
    except BaseException:
        output.unlink(missing_ok=True)
        raise
