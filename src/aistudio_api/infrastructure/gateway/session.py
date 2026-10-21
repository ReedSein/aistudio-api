"""Shared browser session management for gateway operations."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager, suppress
from contextvars import Context, ContextVar
from hashlib import sha256
from pathlib import Path

from aistudio_api.config import settings
from aistudio_api.domain.accounts import BenefitTier
from aistudio_api.domain.errors import SessionExpiredError
from aistudio_api.infrastructure.account.account_store import AccountMeta, AccountStore
from aistudio_api.infrastructure.browser.browser_engine import (
    ChromiumProcess,
    launch_chromium_process,
)
from aistudio_api.infrastructure.browser.cdp_client import CDPClient, CDPPage
from aistudio_api.infrastructure.browser.scripts import (
    DOM_GC_CLEANUP_JS,
    SNAPSHOT_GENERATE_JS,
)
from aistudio_api.infrastructure.gateway.botguard_bootstrap import (
    BOTGUARD_BOOTSTRAP_PROMPT,
    GENERATE_CONTENT_RPC,
    bootstrap_botguard_and_template,
)
from aistudio_api.infrastructure.gateway.readiness import preparing
from aistudio_api.infrastructure.gateway.session_auth import (
    bootstrap_google_session,
    derive_profile_dir,
    extract_account_email,
    get_auth_user_from_meta,
    is_login_page_url,
    load_cookies_to_page,
    navigate_to_aistudio,
    save_browser_cookies,
    verify_account_identity,
)
from aistudio_api.infrastructure.gateway.session_state import (
    SessionContext,
    SessionReadinessGate,
    SessionState,
)
from aistudio_api.infrastructure.gateway.transport import XHRStreamTransport
from aistudio_api.infrastructure.gateway.wire_types import AistudioContent
from aistudio_api.infrastructure.utils.logger import get_logger

log = get_logger("session")

_is_login_page_url = is_login_page_url

DEFAULT_BOOTSTRAP_TEMPLATE: dict[str, object] = {
    "url": GENERATE_CONTENT_RPC,
    "headers": {
        "Content-Type": "application/json+protobuf",
        "X-User-Agent": "grpc-web-javascript/0.1",
        "X-Goog-Api-Key": "AIzaSyDdP816MREB3SkjZO04QXbjsigfcI0GWOs",
        "X-Goog-AuthUser": "0",
        "Referer": "https://aistudio.google.com/",
    },
    "body": '["models/gemini-3.7-flash"]',
}


class BrowserSession:
    """Coordinates browser lifecycle, readiness gate, and blue-green tab transitions."""

    def __init__(self, port: int):
        self.port = port
        self._auth_file: str | None = settings.auth_file or None
        self._auth_user: str | None = None
        self._account_store: AccountStore | None = None
        self._request_context: ContextVar[SessionContext | None] = ContextVar(
            "request_session_context", default=None
        )
        self._request_account: ContextVar[AccountMeta | None] = ContextVar(
            "request_account_snapshot", default=None
        )
        self._profile_dir = derive_profile_dir(self._auth_file)
        self._proc: ChromiumProcess | None = None
        self._cdp_client: CDPClient | None = None

        self._gate = SessionReadinessGate()
        self._transition_lock = asyncio.Lock()
        self._preparation_task: asyncio.Task[SessionContext] | None = None
        self._preparation_waiters = 0
        self._snapshot_lock = asyncio.Lock()
        self._transport = XHRStreamTransport()
        self._retiring_contexts: list[SessionContext] = []
        self._background_tasks: set[asyncio.Task[object]] = set()
        self._in_flight: int = 0
        self._last_activity_time: float = time.time()
        self._stream_cleanup_count: int = 0

    @property
    def state(self) -> SessionState:
        return self._gate.state

    @property
    def is_ready(self) -> bool:
        return self._gate.is_ready()

    @property
    def active_context(self) -> SessionContext | None:
        return self._gate.active_context

    @property
    def page(self) -> CDPPage | None:
        return self._gate.active_context.page if self._gate.active_context else None

    @property
    def snap_key(self) -> str | None:
        return self._gate.active_context.snap_key if self._gate.active_context else None

    @property
    def base_template(self) -> dict[str, object] | None:
        return (
            self._gate.active_context.base_template
            if self._gate.active_context
            else None
        )

    def set_ready_context(self, context: SessionContext) -> None:
        """Set active ready context (used by transitions and test fixtures)."""
        self._gate.set_ready(context)

    def bind_account_store(self, store: AccountStore) -> None:
        """Bind registered identity before warmup; registry is the authority."""
        self._account_store = store
        active = store.get_active_account()
        if active is not None:
            path = store.get_auth_path(active.id)
            if path is not None:
                self._auth_file = str(path)
                self._auth_user = active.auth_user
                self._profile_dir = derive_profile_dir(self._auth_file)

    def _account_for_auth(self, auth_file: str | None) -> AccountMeta | None:
        if self._account_store is None or auth_file is None:
            return None
        account_id = Path(auth_file).parent.name
        path = self._account_store.get_auth_path(account_id)
        if path is not None and path.resolve() == Path(auth_file).resolve():
            return self._account_store.get_account(account_id)
        raise RuntimeError("Auth file is not registered in the bound account store")

    def _context_is_current(self, context: SessionContext) -> bool:
        if self._account_store is None or context.account_id is None:
            return True
        account = self._account_store.get_account(context.account_id)
        return (
            account is not None
            and account.credential_version == context.credential_version
        )

    def account_snapshot(self, context: SessionContext) -> AccountMeta | None:
        if self._request_context.get() is context:
            return self._request_account.get()
        if self._account_store is not None and context.account_id is not None:
            account = self._account_store.get_account(context.account_id)
            if (
                account is not None
                and account.credential_version == context.credential_version
            ):
                return account
        return context.account

    def get_current_auth_user(self) -> str:
        """获取当前活跃账号的 auth_user 编号（0, 1, 2...）。"""
        if self._auth_user is not None:
            return self._auth_user
        if self._gate.active_context is not None:
            return self._gate.active_context.auth_user
        return get_auth_user_from_meta(self._auth_file)

    @asynccontextmanager
    async def request_scope(self):
        """追踪活动请求并更新活跃时间戳。"""
        self._in_flight += 1
        self._last_activity_time = time.time()
        ctx: SessionContext | None = None
        token = None
        account_token = None
        try:
            ctx = await self.ensure_ready()
            ctx.incr_in_flight()
            account_token = self._request_account.set(self.account_snapshot(ctx))
            token = self._request_context.set(ctx)
            yield ctx
        finally:
            if token is not None:
                self._request_context.reset(token)
            if account_token is not None:
                self._request_account.reset(account_token)
            self._in_flight = max(0, self._in_flight - 1)
            if ctx is not None:
                ctx.decr_in_flight()
            self._last_activity_time = time.time()

    async def ensure_ready(self, timeout_s: float | None = None) -> SessionContext:
        """Share preparation without letting one waiter cancel another's work."""
        pinned = self._request_context.get()
        if pinned is not None:
            if not pinned.is_usable():
                raise RuntimeError("Pinned browser session closed")
            return pinned
        async with preparing():
            if (
                self._gate.is_ready()
                and self._gate.active_context is not None
                and self._context_is_current(self._gate.active_context)
            ):
                return self._gate.active_context
            task = self._preparation_task
            if task is None or task.done():
                task = asyncio.create_task(
                    self._prepare_selected_account(),
                    name="browser_preparation",
                    context=Context(),
                )
                self._preparation_task = task
            self._preparation_waiters += 1
            try:
                async with asyncio.timeout(timeout_s):
                    return await asyncio.shield(task)
            finally:
                self._preparation_waiters -= 1
                if self._preparation_waiters == 0:
                    if self._preparation_task is task:
                        self._preparation_task = None
                    if not task.done():
                        task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

    async def _prepare_selected_account(
        self, *, force_refresh: bool = False
    ) -> SessionContext:
        # Resolve after an ongoing manual switch, so a queued request cannot
        # switch back to the account selected before it acquired this lock.
        async with self._transition_lock:
            if (
                not force_refresh
                and self._gate.is_ready()
                and self.active_context is not None
                and self._context_is_current(self.active_context)
            ):
                assert self.active_context is not None
                return self.active_context
            auth_file = self._auth_file
            if self._account_store is not None:
                path = self._account_store.get_active_auth_path()
                auth_file = str(path) if path is not None else None
        return await self.transition_to_account(auth_file, force_refresh=force_refresh)

    async def ensure_context(self) -> CDPPage:
        """Ensure active verified CDPPage is available, auto-reconnecting if dropped."""
        page = self.page
        if page is not None and not page.is_closed():
            if await page.is_alive(timeout_s=1.0):
                return page
            log.warning("浏览器页面无响应，重新连接...")
        await self.recover()
        return (await self.ensure_ready()).page

    async def recover(self) -> None:
        """Refresh through the transition lock; callers retain their retry budget."""
        async with preparing():
            await self._prepare_selected_account(force_refresh=True)

    async def is_alive(self, timeout_s: float = 1.5) -> bool:
        if self._proc is not None and not self._proc.is_alive():
            return False
        page = self.page
        if page is None or page.is_closed():
            return False
        return await page.is_alive(timeout_s=timeout_s)

    async def check_idle_timeout(self) -> bool:
        idle_timeout = getattr(settings, "browser_idle_timeout", 0)
        if idle_timeout <= 0:
            return False
        async with self._transition_lock:
            if (
                self._in_flight > 0
                or self._proc is None
                or self.state == SessionState.PREPARING
            ):
                return False
            idle_duration = time.time() - self._last_activity_time
            if idle_duration >= idle_timeout:
                log.info(
                    "浏览器已空闲 %.1f 秒 (阈值=%d 秒)，回收进程释放内存",
                    idle_duration,
                    idle_timeout,
                )
                await self._close_internal()
                return True
        return False

    async def switch_auth(
        self, auth_file: str | None, *, publish: Callable[[], object] | None = None
    ) -> None:
        """Switch auth using blue-green transition without killing in-flight streams."""
        await self.transition_to_account(auth_file, force_refresh=True, publish=publish)

    async def transition_to_account(
        self,
        auth_file: str | None,
        force_refresh: bool = False,
        *,
        publish: Callable[[], object] | None = None,
    ) -> SessionContext:
        async with preparing():
            return await self._transition_to_account(
                auth_file, force_refresh, publish=publish
            )

    async def _transition_to_account(
        self,
        auth_file: str | None,
        force_refresh: bool = False,
        *,
        publish: Callable[[], object] | None = None,
    ) -> SessionContext:
        """Execute atomic session transition to target auth file."""
        async with self._transition_lock:
            if (
                not force_refresh
                and self._gate.is_ready()
                and self._gate.active_context is not None
                and self._gate.active_context.auth_file == auth_file
                and self._context_is_current(self._gate.active_context)
            ):
                return self._gate.active_context

            account = self._account_for_auth(auth_file)
            target_auth_user = (
                account.auth_user if account else get_auth_user_from_meta(auth_file)
            )
            self._last_activity_time = time.time()
            self._gate.set_preparing()
            old_context = self._gate.active_context

            new_page: CDPPage | None = None
            try:
                # Real browser mode
                if self._proc is None or not self._proc.is_alive():
                    await self._close_internal(reset_gate=False)
                    self._profile_dir = derive_profile_dir(auth_file)
                    await self._ensure_browser_cdp()

                assert self._cdp_client is not None

                new_page = await self._cdp_client.create_new_page(block_assets=True)
                new_page.set_liveness_checker(
                    lambda: self._proc is None or self._proc.is_alive()
                )

                loaded = await load_cookies_to_page(new_page, auth_file)
                if auth_file and not loaded:
                    raise SessionExpiredError("无法载入所选账号的登录凭据")
                recovered = False

                async def _verify(page: CDPPage) -> bool:
                    verified = await verify_account_identity(
                        page, auth_file, account=account
                    )
                    if (
                        verified
                        and account is not None
                        and account.email is None
                        and self._account_store is not None
                    ):
                        email = await extract_account_email(page)
                        if email:
                            self._account_store.update_account_email(
                                account.id,
                                email,
                                expected_version=account.credential_version,
                            )
                    return verified

                async def _save(**_kwargs: object) -> None:
                    assert new_page is not None
                    if account is not None and self._account_store is not None:
                        cookies = await new_page.get_cookies()
                        self._account_store.save_refreshed_cookies(
                            account.id, account.credential_version, cookies
                        )
                    else:
                        await save_browser_cookies(new_page, auth_file)

                async def _reload_cb(page: CDPPage) -> bool:
                    nonlocal recovered
                    if recovered:
                        return False
                    recovered = True
                    if not await load_cookies_to_page(page, auth_file):
                        return False
                    await navigate_to_aistudio(
                        page,
                        auth_user=target_auth_user,
                        auth_file=auth_file,
                        verify_identity_cb=_verify,
                        save_cookies_cb=_save,
                        allow_cookie_reload=False,
                    )
                    return True

                await navigate_to_aistudio(
                    new_page,
                    auth_user=target_auth_user,
                    auth_file=auth_file,
                    verify_identity_cb=_verify,
                    save_cookies_cb=_save,
                    reload_cookies_cb=_reload_cb,
                )

                snap_key, base_template = await bootstrap_botguard_and_template(
                    new_page,
                    reload_cookies_cb=_reload_cb,
                    is_proc_alive_cb=lambda: (
                        self._proc is None or self._proc.is_alive()
                    ),
                )

                new_context = SessionContext(
                    page=new_page,
                    snap_key=snap_key,
                    auth_user=target_auth_user,
                    auth_file=auth_file,
                    base_template=base_template,
                    account=account,
                )

                if not self._context_is_current(new_context):
                    raise RuntimeError(
                        "Browser preparation aborted: account credentials changed"
                    )
                if publish is not None:
                    publish()
                self._auth_file = auth_file
                self._auth_user = target_auth_user

                if (
                    old_context is not None
                    and old_context.page.target_id != new_page.target_id
                    and not old_context.page.is_closed()
                ):
                    self._retiring_contexts.append(old_context)
                    drain_task = asyncio.create_task(
                        self._drain_retiring_context(old_context),
                        name=f"drain_{old_context.auth_user}",
                    )
                    self._background_tasks.add(drain_task)
                    drain_task.add_done_callback(self._background_tasks.discard)
                self._gate.set_ready(new_context)
                log.info(
                    "会话蓝绿平滑热切成功: auth_file=%s, u/%s (活跃在途=%d)",
                    auth_file,
                    target_auth_user,
                    self._in_flight,
                )
                return new_context

            except BaseException as exc:
                if new_page is not None and (
                    old_context is None or new_page is not old_context.page
                ):
                    if self._cdp_client is not None:
                        with suppress(Exception):
                            await self._cdp_client.close_page(new_page)
                    else:
                        await new_page.close()
                if (
                    old_context is not None
                    and old_context.is_usable()
                    and self._context_is_current(old_context)
                ):
                    self._gate.set_ready(old_context)
                    raise
                if isinstance(exc, asyncio.CancelledError):
                    self._gate.set_failed(RuntimeError("Browser preparation cancelled"))
                    raise
                assert isinstance(exc, Exception)
                self._gate.set_failed(exc)
                log.error("会话切换失败: %s", exc)
                raise

    async def _drain_retiring_context(
        self,
        context: SessionContext,
    ) -> None:
        if context.in_flight > 0:
            await context.drain_event.wait()
        try:
            if self._cdp_client is not None:
                await self._cdp_client.close_page(context.page)
            else:
                await context.page.close()
        finally:
            if context in self._retiring_contexts:
                self._retiring_contexts.remove(context)

    async def ensure_botguard_service(self, force_refresh: bool = False) -> CDPPage:
        page = self.page
        curr_url = page.url if page else ""
        if "available-regions" in curr_url:
            raise RuntimeError(
                f"Google AI Studio 地区限制: 访问被重定向至 available-regions ({curr_url})"
            )
        if force_refresh:
            await self.transition_to_account(
                self._auth_file, force_refresh=force_refresh
            )
        return (await self.ensure_ready()).page

    async def capture_template(
        self, model: str, *, context: SessionContext | None = None
    ) -> dict[str, object]:
        if self._proc is not None and not self._proc.is_alive():
            raise RuntimeError("Browser process died during template capture")

        ctx = context or await self.ensure_ready()
        base = dict(ctx.base_template)
        normalized_model = model if model.startswith("models/") else f"models/{model}"
        try:
            body_val = base.get("body")
            if (
                isinstance(body_val, str)
                and body_val.startswith('["models/')
                and "," in body_val
            ):
                first_comma = body_val.find(",")
                base["body"] = f'["{normalized_model}"' + body_val[first_comma:]
            elif isinstance(body_val, str) and body_val.startswith("["):
                body_list = json.loads(body_val)
                if isinstance(body_list, list) and len(body_list) > 0:
                    body_list[0] = normalized_model
                    base["body"] = json.dumps(body_list, separators=(",", ":"))
        except Exception as e:
            log.debug("Failed to adapt model into base template body: %s", e)
        return base

    async def generate_snapshot(
        self, contents: list[AistudioContent], *, context: SessionContext | None = None
    ) -> str:
        ctx = context or await self.ensure_ready()
        # Same per-part mapping as the web client: only text, inline data and
        # file references contribute; every other part type hashes as "".
        hash_parts: list[str] = []
        for content in contents:
            for part in content.parts:
                if part.text is not None:
                    hash_parts.append(str(part.text))
                elif part.inline_data is not None:
                    hash_parts.append(str(part.inline_data[1]))
                elif part.file_id is not None:
                    hash_parts.append(str(part.file_id))
                else:
                    hash_parts.append("")

        content_hash = sha256(" ".join(hash_parts).encode("utf-8")).hexdigest()

        async with self._snapshot_lock:
            for attempt in range(3):
                try:
                    snapshot = await ctx.page.evaluate(
                        SNAPSHOT_GENERATE_JS, args=content_hash, timeout_s=10.0
                    )
                    if snapshot and isinstance(snapshot, str) and len(snapshot) > 0:
                        return snapshot
                except Exception as e:
                    log.debug("快照计算第 %d 次尝试失败: %s", attempt + 1, e)
                    if attempt < 2:
                        await ctx.page.wait_for_timeout(100)

        raise RuntimeError(
            f"Snapshot generation failed for content hash {content_hash[:8]}"
        )

    async def send_hooked_request(
        self,
        *,
        body: str,
        timeout_ms: int,
        url: str | None = None,
        headers: dict[str, str] | None = None,
        context: SessionContext | None = None,
        tier: BenefitTier | None = None,
    ) -> tuple[int, bytes]:
        ctx = context or await self.ensure_ready()
        if not ctx.is_usable():
            raise RuntimeError("Captured browser session closed")
        captured_url = url or str(ctx.base_template.get("url") or "")
        hdrs_val = ctx.base_template.get("headers")
        captured_headers = headers or (
            {str(k): str(v) for k, v in hdrs_val.items()}
            if isinstance(hdrs_val, dict)
            else {}
        )
        ctx.incr_in_flight()
        try:
            return await self._transport.send_hooked_request(
                ctx.page,
                url=captured_url,
                headers=captured_headers,
                body=body,
                timeout_ms=timeout_ms,
                auth_user=ctx.auth_user,
                tier=tier,
            )
        finally:
            ctx.decr_in_flight()

    async def send_streaming_request(
        self,
        *,
        body: str,
        timeout_ms: int,
        url: str | None = None,
        headers: dict[str, str] | None = None,
        context: SessionContext | None = None,
        tier: BenefitTier | None = None,
    ) -> AsyncGenerator[tuple[str, object], None]:
        ctx = context or await self.ensure_ready()
        if not ctx.is_usable():
            raise RuntimeError("Captured browser session closed")
        captured_url = url or str(ctx.base_template.get("url") or "")
        hdrs_val = ctx.base_template.get("headers")
        captured_headers = headers or (
            {str(k): str(v) for k, v in hdrs_val.items()}
            if isinstance(hdrs_val, dict)
            else {}
        )
        ctx.incr_in_flight()
        try:
            async for event in self._transport.send_streaming_request(
                ctx.page,
                url=captured_url,
                headers=captured_headers,
                body=body,
                timeout_ms=timeout_ms,
                auth_user=ctx.auth_user,
                tier=tier,
            ):
                yield event
        finally:
            ctx.decr_in_flight()
            await self.cleanup_stream_page(ctx.page)

    async def cleanup_stream_page(self, page: CDPPage | None = None) -> None:
        self._stream_cleanup_count += 1
        target_page = page or (
            self.active_context.page if self.active_context else None
        )
        if (
            target_page is not None
            and not target_page.is_closed()
            and (self._in_flight <= 0 or (self._stream_cleanup_count % 5 == 0))
        ):
            with suppress(Exception):
                await target_page.evaluate(DOM_GC_CLEANUP_JS)
            if self._in_flight <= 0 and (self._stream_cleanup_count % 10 == 0):
                with suppress(Exception):
                    await target_page.collect_garbage()
                import gc

                gc.collect()

    async def import_cookies(
        self, cookie_string: str, auth_file: str | None = None
    ) -> int:
        from aistudio_api.infrastructure.account.cookie_refresher import (
            load_cookies_from_string,
        )

        pw_cookies = load_cookies_from_string(cookie_string)
        target_auth = auth_file or self._auth_file
        if not target_auth:
            return len(pw_cookies)

        await self._ensure_browser_cdp()
        assert self._cdp_client is not None
        account = self._account_for_auth(target_auth)
        page = await self._cdp_client.create_new_page(block_assets=False)
        try:
            await page.set_cookies(pw_cookies)
            with suppress(Exception):
                await bootstrap_google_session(page)
            browser_cookies = await page.get_cookies()
            final_cookies = browser_cookies or pw_cookies
            if account is not None and self._account_store is not None:
                self._account_store.save_refreshed_cookies(
                    account.id, account.credential_version, final_cookies
                )
            else:
                await save_browser_cookies(
                    page, auth_file=target_auth, cookies=final_cookies
                )
            return len(final_cookies)
        finally:
            await self._cdp_client.close_page(page)

    async def extract_account_email(self, page: CDPPage | None = None) -> str | None:
        target = page or (self.active_context.page if self.active_context else None)
        return await extract_account_email(target)

    async def reload_current_account_cookies(self, page: CDPPage | None = None) -> bool:
        target = page or (self.active_context.page if self.active_context else None)
        return bool(target and await load_cookies_to_page(target, self._auth_file))

    async def _ensure_browser_cdp(self) -> None:
        if self._proc is not None and self._proc.is_alive() and self._cdp_client:
            return

        profile_dir = self._profile_dir
        if profile_dir:
            Path(profile_dir).mkdir(parents=True, exist_ok=True)

        self._last_activity_time = time.time()
        self._proc = launch_chromium_process(
            port=self.port,
            user_data_dir=profile_dir,
            headless=settings.browser_headless,
        )

        self._cdp_client = CDPClient(port=self.port)
        await self._cdp_client.wait_until_ready()

    async def close(self) -> None:
        task = self._preparation_task
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        async with self._transition_lock:
            await self._close_internal()

    async def _close_internal(self, *, reset_gate: bool = True) -> None:
        if reset_gate:
            self._gate.reset()
        for task in list(self._background_tasks):
            task.cancel()
        await asyncio.gather(*self._background_tasks, return_exceptions=True)
        self._background_tasks.clear()
        self._retiring_contexts.clear()

        if self._cdp_client is not None:
            with suppress(Exception):
                await self._cdp_client.close()
            self._cdp_client = None

        if self._proc is not None:
            with suppress(Exception):
                self._proc.terminate()
            self._proc = None


__all__ = [
    "BOTGUARD_BOOTSTRAP_PROMPT",
    "DEFAULT_BOOTSTRAP_TEMPLATE",
    "BrowserSession",
    "_is_login_page_url",
]
