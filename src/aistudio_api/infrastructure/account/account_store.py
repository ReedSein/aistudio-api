"""账号存储层，管理多 Google 账号的注册表和 storage state。"""

from __future__ import annotations

import contextlib
import json
import os
import secrets
import shutil
import threading
import time
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from os.path import abspath, basename, join
from pathlib import Path
from typing import ClassVar

from aistudio_api.domain.accounts import BenefitTier
from aistudio_api.infrastructure.utils.logger import get_logger

log = get_logger("account.store")


def _atomic_write_json(path: Path, data: object) -> None:
    """原子写入 JSON 文件（写唯一临时文件后原子替换，防止写穿或损坏）。"""
    target_path = path.resolve()
    target_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target_path.with_suffix(f".tmp.{os.getpid()}_{time.time_ns()}")
    try:
        with os.fdopen(
            os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600),
            "w",
            encoding="utf-8",
        ) as stream:
            json.dump(data, stream, ensure_ascii=False, separators=(",", ":"))
        tmp_path.replace(target_path)
    except Exception:
        if tmp_path.exists():
            with contextlib.suppress(OSError):
                tmp_path.unlink()
        raise


# 默认搜索路径（与 config.py 保持一致）
_SEARCH_ROOTS: list[Path] = [
    Path.cwd(),
    Path(__file__)
    .resolve()
    .parents[4],  # src/aistudio_api/infrastructure/account -> 项目根
]


def _safe_account_dir(accounts_dir: Path, account_id: str) -> Path:
    """验证并返回安全的账号子目录路径，防止路径遍历攻击。"""
    raw = str(account_id or "").strip()
    clean_id = "".join(c for c in raw if c.isalnum() or c in ("-", "_", ".", "@"))
    safe_name = basename(clean_id)
    if not safe_name or safe_name != account_id or ".." in safe_name:
        raise ValueError(f"Invalid or unsafe account_id: {account_id!r}")
    base_str = abspath(str(accounts_dir.resolve()))
    target_str = abspath(join(base_str, safe_name))
    prefix = base_str if base_str.endswith(os.sep) else base_str + os.sep
    if not target_str.startswith(prefix) and target_str != base_str:
        raise ValueError(f"Account path escapes base directory: {account_id!r}")
    return Path(target_str)


def _resolve_accounts_dir() -> Path:
    """发现 accounts 目录，默认为 data/accounts。"""
    env = os.getenv("AISTUDIO_ACCOUNTS_DIR")
    if env:
        return Path(env).resolve()
    for root in _SEARCH_ROOTS:
        candidate = root / "data" / "accounts"
        if candidate.is_dir():
            return candidate
    # 默认在第一个搜索根下创建
    return (_SEARCH_ROOTS[0] / "data" / "accounts").resolve()


def _resolve_legacy_auth_file() -> Path | None:
    """查找遗留的 data/auth.json 文件。"""
    for root in _SEARCH_ROOTS:
        candidate = root / "data" / "auth.json"
        if candidate.is_file():
            return candidate
    return None


def _generate_account_id() -> str:
    """生成 acc_ 前缀的随机 ID。"""
    return f"acc_{secrets.token_hex(4)}"


@dataclass(frozen=True)
class AccountMeta:
    """账号元数据。"""

    id: str
    name: str
    email: str | None
    created_at: str
    last_used: str | None = None
    auth_user: str = "0"
    cookie_id: str | None = None
    credential_version: str = ""
    tier: BenefitTier | None = None
    tier_checked_at: str | None = None
    tier_last_error: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> AccountMeta:
        for key in ("id", "name", "created_at"):
            if not isinstance(data.get(key), str):
                raise ValueError("Invalid account metadata")
        for key in (
            "email",
            "last_used",
            "cookie_id",
            "credential_version",
            "tier_checked_at",
            "tier_last_error",
        ):
            if data.get(key) is not None and not isinstance(data[key], str):
                raise ValueError("Invalid account metadata field")
        auth_user = data.get("auth_user", "0")
        if not isinstance(auth_user, str) or not auth_user.isdecimal():
            raise ValueError("Invalid account sub-account index")
        return cls(
            id=str(data.get("id") or ""),
            name=str(data.get("name") or ""),
            email=str(data["email"]) if data.get("email") is not None else None,
            created_at=str(data.get("created_at") or ""),
            last_used=str(data["last_used"])
            if data.get("last_used") is not None
            else None,
            auth_user=str(data.get("auth_user") or "0"),
            cookie_id=str(data["cookie_id"])
            if data.get("cookie_id") is not None
            else None,
            credential_version=str(data.get("credential_version") or ""),
            tier=next(
                (tier for tier in BenefitTier if tier.value == data.get("tier")), None
            ),
            tier_checked_at=str(data["tier_checked_at"])
            if data.get("tier_checked_at") is not None
            else None,
            tier_last_error=str(data["tier_last_error"])
            if data.get("tier_last_error") is not None
            else None,
        )


@dataclass
class Registry:
    """账号注册表。"""

    accounts: dict[str, AccountMeta]
    active_account_id: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "accounts": {k: v.to_dict() for k, v in self.accounts.items()},
            "active_account_id": self.active_account_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> Registry:
        raw_accounts = data.get("accounts")
        if not isinstance(raw_accounts, dict):
            raise ValueError("Invalid account registry")
        accounts_dict: dict[str, object] = raw_accounts
        accounts = {
            k: AccountMeta.from_dict(v)
            for k, v in accounts_dict.items()
            if isinstance(v, dict)
        }
        raw_active_id = data.get("active_account_id")
        if len(accounts) != len(accounts_dict) or any(
            k != v.id for k, v in accounts.items()
        ):
            raise ValueError("Invalid account registry entries")
        return cls(
            accounts=accounts,
            active_account_id=str(raw_active_id) if raw_active_id is not None else None,
        )


class AccountStore:
    """账号存储管理器（线程安全单例，支持原子文件替换）。"""

    _instances: ClassVar[dict[Path, AccountStore]] = {}
    _singleton_lock = threading.Lock()
    _initialized: bool = False

    def __new__(cls, accounts_dir: Path | None = None) -> AccountStore:
        resolved_dir = (accounts_dir or _resolve_accounts_dir()).resolve()
        with cls._singleton_lock:
            if resolved_dir not in cls._instances:
                instance = super().__new__(cls)
                cls._instances[resolved_dir] = instance
            return cls._instances[resolved_dir]

    def __init__(self, accounts_dir: Path | None = None) -> None:
        if getattr(self, "_initialized", False):
            return
        self._accounts_dir = (accounts_dir or _resolve_accounts_dir()).resolve()
        self._registry_path = self._accounts_dir / "registry.json"
        self._registry: Registry | None = None
        self._lock = threading.RLock()
        self._ensure_dirs()
        self._migrate_legacy_if_needed()
        self._initialized = True

    def _ensure_dirs(self) -> None:
        """确保目录存在。"""
        self._accounts_dir.mkdir(parents=True, exist_ok=True)

    def _migrate_legacy_if_needed(self) -> None:
        """如果 accounts 目录为空且存在 data/auth.json，自动迁移。"""
        if self._registry_path.exists():
            return  # 已有注册表，无需迁移
        legacy = _resolve_legacy_auth_file()
        if legacy is None:
            return
        # 创建一个迁移账号
        account_id = "acc_migrated"
        now = datetime.now(UTC).isoformat()
        meta = AccountMeta(
            id=account_id,
            name="迁移的账号",
            email=None,
            created_at=now,
            last_used=now,
        )
        account_dir = self._accounts_dir / account_id
        account_dir.mkdir(parents=True, exist_ok=True)
        # 复制 auth.json
        shutil.copy2(legacy, account_dir / "auth.json")
        # 写入 meta.json
        _atomic_write_json(account_dir / "meta.json", meta.to_dict())
        # 创建注册表
        registry = Registry(
            accounts={account_id: meta},
            active_account_id=account_id,
        )
        self._save_registry(registry)

    def _load_registry(self) -> Registry:
        """加载注册表（线程安全）。"""
        with self._lock:
            if self._registry is not None:
                return self._registry
            if not self._registry_path.exists():
                self._registry = Registry(accounts={})
                return self._registry
            try:
                data = json.loads(self._registry_path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    raise ValueError("Invalid account registry")
                self._registry = Registry.from_dict(data)
            except (ValueError, TypeError, KeyError):
                raise ValueError("Cannot parse account registry") from None
            return self._registry

    def _save_registry(self, registry: Registry) -> None:
        """保存注册表（线程安全+原子写入）。"""
        with self._lock:
            _atomic_write_json(self._registry_path, registry.to_dict())
            self._registry = registry

    def reconcile_legacy_metadata(self) -> int:
        """Explicit startup compatibility pass; ordinary reads never write metadata."""
        with self._lock:
            registry = self._load_registry()
            accounts = dict(registry.accounts)
            raw = (
                json.loads(self._registry_path.read_text(encoding="utf-8"))
                if self._registry_path.exists()
                else {"accounts": {}}
            )
            changed = 0
            for account_id, account in registry.accounts.items():
                path = _safe_account_dir(self._accounts_dir, account_id) / "meta.json"
                if not path.is_file():
                    continue
                try:
                    legacy = json.loads(path.read_text(encoding="utf-8"))
                except ValueError:
                    raise ValueError("Cannot parse legacy account metadata") from None
                if not isinstance(legacy, dict):
                    raise ValueError("Invalid legacy account metadata")
                email = legacy.get("email")
                updated = account
                if account.email is None and isinstance(email, str) and "@" in email:
                    updated = replace(updated, email=email)
                registered = raw["accounts"].get(account_id, {})
                for key in ("auth_user", "cookie_id"):
                    value = legacy.get(key)
                    if (
                        key not in registered
                        and isinstance(value, str)
                        and value
                        and (key != "auth_user" or value.isdecimal())
                    ):
                        updated = replace(updated, **{key: value})
                if updated != account:
                    accounts[account_id] = updated
                    changed += 1
            if changed:
                self._save_registry(replace(registry, accounts=accounts))
            return changed

    def list_accounts(self) -> list[AccountMeta]:
        """列出所有账号。"""
        registry = self._load_registry()
        return list(registry.accounts.values())

    def get_account(self, account_id: str) -> AccountMeta | None:
        """获取单个账号。"""
        registry = self._load_registry()
        return registry.accounts.get(account_id)

    def get_active_account(self) -> AccountMeta | None:
        """获取当前活跃账号；旧登记没有选择项时只读返回首个账号。"""
        registry = self._load_registry()
        if (
            registry.active_account_id
            and registry.active_account_id in registry.accounts
        ):
            return registry.accounts[registry.active_account_id]
        if registry.accounts:
            first_id = next(iter(registry.accounts))
            return registry.accounts[first_id]
        return None

    def get_active_auth_path(self) -> Path | None:
        """获取当前活跃账号的 auth.json 路径。"""
        account = self.get_active_account()
        if account is None:
            return None
        return _safe_account_dir(self._accounts_dir, account.id) / "auth.json"

    def set_active_account(
        self, account_id: str, *, expected_version: str | None = None
    ) -> AccountMeta | None:
        """设置活跃账号，返回账号元数据或 None（如果不存在）。"""
        with self._lock:
            registry = self._load_registry()
            account = registry.accounts.get(account_id)
            if expected_version is not None and (
                account is None or account.credential_version != expected_version
            ):
                raise ValueError("Account credentials changed during activation")
            if account is None:
                return None
            updated = replace(account, last_used=datetime.now(UTC).isoformat())
            accounts = {**registry.accounts, account_id: updated}
            self._save_registry(Registry(accounts, account_id))
            return updated

    def save_account(
        self,
        name: str,
        email: str | None,
        storage_state: dict[str, object],
        account_id: str | None = None,
        auth_user: str = "0",
        cookie_id: str | None = None,
    ) -> AccountMeta:
        """保存新账号。"""
        if not auth_user.isdecimal():
            raise ValueError("Invalid account sub-account index")
        with self._lock:
            registry = self._load_registry()
            if account_id is None and email:
                account_id = next(
                    (
                        a.id
                        for a in registry.accounts.values()
                        if a.email == email and a.auth_user == auth_user
                    ),
                    None,
                )
            account_id = _generate_account_id() if account_id is None else account_id
            previous = registry.accounts.get(account_id)
            now = datetime.now(UTC).isoformat()
            meta = (
                replace(
                    previous,
                    name=name,
                    email=email or previous.email,
                    auth_user=auth_user,
                    cookie_id=cookie_id or previous.cookie_id,
                    credential_version=secrets.token_hex(16),
                )
                if previous
                else AccountMeta(
                    id=account_id,
                    name=name,
                    email=email,
                    created_at=now,
                    last_used=now,
                    auth_user=auth_user,
                    cookie_id=cookie_id,
                    credential_version=secrets.token_hex(16),
                )
            )
            auth_path = _safe_account_dir(self._accounts_dir, account_id) / "auth.json"
            previous_auth = (
                json.loads(auth_path.read_text(encoding="utf-8"))
                if auth_path.exists()
                else None
            )
            _atomic_write_json(auth_path, storage_state)
            try:
                self._save_registry(
                    Registry(
                        {**registry.accounts, account_id: meta},
                        registry.active_account_id or account_id,
                    )
                )
            except BaseException:
                if previous_auth is None:
                    auth_path.unlink()
                else:
                    _atomic_write_json(auth_path, previous_auth)
                raise
            return meta

    def delete_account(self, account_id: str) -> bool:
        """删除账号，返回是否成功。"""
        with self._lock:
            registry = self._load_registry()
            if account_id not in registry.accounts:
                return False
            account_dir = _safe_account_dir(self._accounts_dir, account_id)
            accounts = {k: v for k, v in registry.accounts.items() if k != account_id}
            active_id = registry.active_account_id
            if active_id == account_id:
                active_id = next(iter(accounts), None)
            self._save_registry(Registry(accounts, active_id))
            if account_dir.is_dir():
                try:
                    shutil.rmtree(account_dir)
                except OSError as exc:
                    log.warning(
                        "账号已取消登记，残留文件清理失败: %s", type(exc).__name__
                    )
            return True

    def update_account(self, account_id: str, name: str) -> AccountMeta | None:
        """更新账号名称。"""
        with self._lock:
            registry = self._load_registry()
            account = registry.accounts.get(account_id)
            if account is None:
                return None
            updated = replace(account, name=name)
            self._save_registry(
                replace(registry, accounts={**registry.accounts, account_id: updated})
            )
            return updated

    def update_account_email(
        self, account_id: str, email: str, *, expected_version: str | None = None
    ) -> bool:
        """更新账号邮箱；版本限定可阻止旧页面覆盖重新导入的资料。"""
        with self._lock:
            registry = self._load_registry()
            account = registry.accounts.get(account_id)
            if expected_version is not None and (
                account is None or account.credential_version != expected_version
            ):
                return False
            if account is None or account.email == email:
                return False
            updated = replace(account, email=email)
            self._save_registry(
                replace(registry, accounts={**registry.accounts, account_id: updated})
            )
            return True

    def get_auth_path(self, account_id: str) -> Path | None:
        """获取指定账号的 auth.json 路径。"""
        return self.get_auth_path_optional(account_id, require_exists=True)

    def credential_snapshot(
        self, account_id: str
    ) -> tuple[AccountMeta, dict[str, object]] | None:
        with self._lock:
            account = self.get_account(account_id)
            if account is None:
                return None
            path = _safe_account_dir(self._accounts_dir, account_id) / "auth.json"
            state = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(state, dict):
                raise ValueError("Invalid account credential state")
            return account, state

    def save_tier_result(
        self,
        account_id: str,
        expected_version: str,
        *,
        tier: BenefitTier | None = None,
        error: str | None = None,
    ) -> AccountMeta | None:
        with self._lock:
            registry = self._load_registry()
            account = registry.accounts.get(account_id)
            if account is None or account.credential_version != expected_version:
                return None
            if error is not None:
                updated = replace(account, tier_last_error=error)
            else:
                if tier is None:
                    raise ValueError("A successful tier result must be known")
                updated = replace(
                    account,
                    tier=tier,
                    tier_checked_at=datetime.now(UTC).isoformat(),
                    tier_last_error=None,
                )
            self._save_registry(
                replace(registry, accounts={**registry.accounts, account_id: updated})
            )
            return updated

    def save_refreshed_cookies(
        self, account_id: str, credential_version: str, cookies: list[dict[str, object]]
    ) -> bool:
        """Never overwrite a reimported credential with an older page's Cookie state."""
        with self._lock:
            account = self.get_account(account_id)
            if (
                account is None
                or account.credential_version != credential_version
                or not cookies
            ):
                return False
            path = _safe_account_dir(self._accounts_dir, account_id) / "auth.json"
            state = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(state, dict):
                raise ValueError("Invalid account credential state")
            _atomic_write_json(path, {**state, "cookies": cookies})
            return True

    def get_auth_path_optional(
        self, account_id: str, *, require_exists: bool = False
    ) -> Path | None:
        """获取指定账号的 auth.json 路径，可选是否要求文件已存在。"""
        registry = self._load_registry()
        if account_id not in registry.accounts:
            return None
        path = _safe_account_dir(self._accounts_dir, account_id) / "auth.json"
        if require_exists and not path.exists():
            return None
        return path

    def get_profile_path(self, account_id: str) -> Path | None:
        """获取指定账号的 profile 目录路径。"""
        registry = self._load_registry()
        if account_id not in registry.accounts:
            return None
        return _safe_account_dir(self._accounts_dir, account_id) / "profile"
