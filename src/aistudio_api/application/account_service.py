"""账号管理应用服务，协调 account_store 与 Cookie 导入。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from aistudio_api.domain.accounts import TierRefreshError
from aistudio_api.infrastructure.account.benefit_tier import query_benefit_tier

if TYPE_CHECKING:
    from aistudio_api.application.account_rotator import AccountRotator
    from aistudio_api.infrastructure.account.account_store import (
        AccountMeta,
        AccountStore,
    )
    from aistudio_api.infrastructure.gateway.session import BrowserSession
from aistudio_api.infrastructure.diagnostics.store import get_store
from aistudio_api.infrastructure.utils.logger import get_logger

logger = get_logger("account")


class AccountService:
    """账号管理服务。"""

    def __init__(
        self,
        account_store: AccountStore,
        rotator: AccountRotator | None = None,
    ) -> None:
        self._store = account_store
        self._rotator = rotator
        self._activation_lock = asyncio.Lock()

    def list_accounts(self) -> list[AccountMeta]:
        """列出所有账号。"""
        return self._store.list_accounts()

    def get_account(self, account_id: str) -> AccountMeta | None:
        """获取单个账号。"""
        return self._store.get_account(account_id)

    def get_active_account(self) -> AccountMeta | None:
        """获取当前活跃账号。"""
        return self._store.get_active_account()

    def set_active_account(self, account_id: str) -> None:
        """设置当前活跃账号。"""
        self._store.set_active_account(account_id)

    async def activate_account(
        self,
        account_id: str,
        browser_session: BrowserSession,
    ) -> AccountMeta | None:
        """切换到指定账号。"""
        account = self._store.get_account(account_id)
        if account is None:
            return None

        auth_path = self._store.get_auth_path_optional(account_id, require_exists=False)
        if auth_path is None:
            logger.error("账号 %s 的账号目录不存在", account_id)
            return None

        async with self._activation_lock:
            await browser_session.switch_auth(
                str(auth_path),
                publish=lambda: self._store.set_active_account(
                    account_id, expected_version=account.credential_version
                ),
            )
        logger.info("已切换到账号: %s (%s)", account_id, account.name)
        return account

    def delete_account(self, account_id: str) -> bool:
        """删除账号。"""
        return self._store.delete_account(account_id)

    def update_account(self, account_id: str, name: str) -> AccountMeta | None:
        """更新账号名称。"""
        return self._store.update_account(account_id, name)

    def update_account_email(self, account_id: str, email: str) -> bool:
        """更新账号的登录邮箱。"""
        return self._store.update_account_email(account_id, email)

    def save_account_from_cookies(
        self,
        *,
        name: str,
        email: str | None,
        storage_state: dict[str, object],
        account_id: str | None = None,
        auth_user: str = "0",
        cookie_id: str | None = None,
    ) -> AccountMeta:
        """从 Cookie 存储状态保存新账号。"""
        account = self._store.save_account(
            name=name,
            email=email,
            storage_state=storage_state,
            account_id=account_id,
            auth_user=auth_user,
            cookie_id=cookie_id,
        )
        if self._rotator is not None:
            self._rotator.add_account(account.id)
            self._rotator.reset_auth_state(account.id)
        return account

    def batch_import_accounts(
        self,
        probed_list: list[dict[str, object]],
        storage_state: dict[str, object],
        prefix: str = "Google Account",
        cookie_id: str | None = None,
    ) -> list[AccountMeta]:
        """批量导入探活到的账号列表。"""
        imported: list[AccountMeta] = []
        diagnostics = get_store()
        stage = "save"
        diagnostics.emit("account_import_save_start", expected_count=len(probed_list))
        try:
            for index, p in enumerate(probed_list):
                u_idx = str(p.get("auth_user") or "0")
                acc_name = (
                    f"{prefix} (u/{u_idx})"
                    if len(probed_list) > 1 or u_idx != "0"
                    else prefix
                )
                diagnostics.emit("account_import_save_item", index=index)
                account = self.save_account_from_cookies(
                    name=acc_name,
                    email=None,
                    storage_state=storage_state,
                    auth_user=u_idx,
                    cookie_id=cookie_id,
                )
                imported.append(account)
                diagnostics.emit(
                    "account_import_account_saved",
                    account_id=account.id,
                    index=index,
                    saved_count=len(imported),
                )

            stage = "activate"
            if not self.get_active_account() and imported:
                self.set_active_account(imported[0].id)
        except BaseException as exc:
            diagnostics.emit(
                "account_import_save_failed",
                stage=stage,
                saved_count=len(imported),
                expected_count=len(probed_list),
                account_ids=[account.id for account in imported],
                error_type=type(exc).__name__,
            )
            raise
        diagnostics.emit(
            "account_import_saved",
            saved_count=len(imported),
            account_ids=[account.id for account in imported],
        )
        return imported

    def warn_unconfirmed_tiers(self) -> list[AccountMeta]:
        """Log accounts whose tier was never refreshed; paid models refuse them."""
        unconfirmed = [a for a in self._store.list_accounts() if a.tier is None]
        if unconfirmed:
            logger.warning(
                "%d 个账号尚未刷新会员等级，付费模型会拒绝这些账号，"
                "请在控制台「账号管理」点击「刷新等级」: %s",
                len(unconfirmed),
                "、".join(f"{a.name} ({a.id})" for a in unconfirmed),
            )
        return unconfirmed

    def get_account_auth_path(self, account_id: str) -> Path | None:
        """获取指定账号的 auth.json 路径。"""
        return self._store.get_auth_path(account_id)

    async def detect_tier(self, account_id: str) -> AccountMeta:
        """Query once with an immutable credential snapshot, then compare-and-save."""
        try:
            account = self._store.get_account(account_id)
        except (OSError, ValueError):
            raise TierRefreshError("storage") from None
        if account is None:
            raise TierRefreshError("not_found")
        error: TierRefreshError | None = None
        tier = None
        try:
            snapshot = self._store.credential_snapshot(account_id)
            if snapshot is None:
                raise TierRefreshError("stale")
            account, credentials = snapshot
            tier = await query_benefit_tier(credentials, account.auth_user)
        except (OSError, ValueError):
            error = TierRefreshError("credentials")
        except TierRefreshError as exc:
            if exc.reason == "stale":
                raise
            error = exc
        try:
            updated = self._store.save_tier_result(
                account.id,
                account.credential_version,
                tier=tier,
                error=error.reason if error else None,
            )
        except (OSError, ValueError):
            raise TierRefreshError("storage") from None
        if updated is None:
            raise TierRefreshError("stale")
        if error is not None:
            raise error
        return updated
