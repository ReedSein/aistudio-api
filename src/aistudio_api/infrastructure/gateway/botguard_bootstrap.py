"""BotGuard WAA bootstrap probe and base request template interception."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from urllib.parse import urlsplit

from aistudio_api.domain.errors import SessionExpiredError
from aistudio_api.infrastructure.browser.cdp_client import CDPPage
from aistudio_api.infrastructure.browser.scripts import (
    DIALOG_CLEANUP_JS,
    DOM_GC_CLEANUP_JS,
    INSTALL_HOOKS_JS,
    STOP_GENERATION_JS,
)
from aistudio_api.infrastructure.diagnostics.store import get_store
from aistudio_api.infrastructure.gateway.readiness import preparing
from aistudio_api.infrastructure.gateway.session_auth import is_login_page_url
from aistudio_api.infrastructure.utils.logger import get_logger

log = get_logger("session.botguard")

BOTGUARD_BOOTSTRAP_PROMPT = "say '1'"
GENERATE_CONTENT_RPC = "https://alkalimakersuite-pa.clients6.google.com/$rpc/google.internal.alkali.applications.makersuite.v1.MakerSuiteService/GenerateContent"

OPEN_EDITOR_JS = """(() => {
    if (document.querySelector('textarea')) return;
    const clickable = Array.from(document.querySelectorAll('button, mat-card, a, [role="button"], [role="tab"]'));
    for (const term of ['New chat', 'Chat prompt', 'Start building', 'Create prompt', 'Code and Chat', 'Playground']) {
        const target = clickable.find(el => !el.disabled && (el.innerText || el.textContent || '').includes(term));
        if (target) { target.click(); return; }
    }
})()"""


class EditorNotReady(RuntimeError):
    """The expected AI Studio editor never became usable."""


async def install_page_hooks(page: CDPPage) -> str:
    """Install WAA service hooks on page and extract snapshot function key."""
    if is_login_page_url(page.url):
        raise SessionExpiredError("Cookie 认证失败，页面处于登录页")
    result = await page.evaluate(INSTALL_HOOKS_JS)
    if result == "already_hooked":
        snap_key = await page.evaluate("() => window.__AISTUDIO__?.snapKey || ''")
        if snap_key and isinstance(snap_key, str):
            return snap_key
        return "snapKey"
    if isinstance(result, str) and result.startswith("hooked:"):
        return result.split(":", 1)[1]

    for _ in range(3):
        await page.wait_for_timeout(1500)
        result = await page.evaluate(INSTALL_HOOKS_JS)
        if result == "already_hooked":
            snap_key = await page.evaluate("() => window.__AISTUDIO__?.snapKey || ''")
            if snap_key and isinstance(snap_key, str):
                return snap_key
            return "snapKey"
        if isinstance(result, str) and result.startswith("hooked:"):
            return result.split(":", 1)[1]

    page_url = getattr(page, "url", "(unknown)")
    page_title = await page.title()
    raise RuntimeError(
        f"Hook install failed: {result} (url={page_url}, title={page_title!r})"
    )


async def enable_temporary_chat(page: CDPPage) -> bool:
    """Enable Temporary Chat mode in AI Studio to prevent polluting user history."""
    try:
        return bool(
            await page.evaluate(
                """(() => {
                    const clickable = Array.from(document.querySelectorAll('button, [role="button"], mat-slide-toggle, a'));
                    const tempBtn = clickable.find(el => {
                        const s = ((el.innerText || '') + ' ' + (el.getAttribute('aria-label') || '') + ' ' + (el.getAttribute('mattooltip') || '')).toLowerCase();
                        return (s.includes('temporary chat') || s.includes('temporary mode') || s.includes('turn on temporary'))
                            && !s.includes('data use policy');
                    });
                    if (tempBtn) {
                        tempBtn.click();
                        return true;
                    }

                    const moreBtn = clickable.find(el => {
                        const aria = (el.getAttribute('aria-label') || '').toLowerCase();
                        const txt = (el.innerText || '').trim();
                        return aria === 'view more actions' || txt === 'more_vert';
                    });
                    if (moreBtn && moreBtn.getAttribute('aria-expanded') !== 'true') {
                        moreBtn.click();
                        const menuItem = Array.from(document.querySelectorAll('[role="menuitem"], .mat-mdc-menu-item')).find(item => {
                            const s = ((item.innerText || '') + ' ' + (item.getAttribute('aria-label') || '')).toLowerCase();
                            return s.includes('temporary');
                        });
                        if (menuItem) {
                            menuItem.click();
                            return true;
                        }
                        moreBtn.click();
                    }
                    return false;
                })()"""
            )
        )
    except Exception as e:
        log.debug("尝试激活 Temporary chat 异常: %s", e)
        return False


async def click_run_button(page: CDPPage) -> bool:
    """Find and trigger Run / Build button or dispatch Control+Enter shortcut."""
    clicked = await page.evaluate(
        """(() => {
            const buttons = Array.from(document.querySelectorAll('button'));
            const runBtn = buttons.find(b => {
                const t = (b.innerText || b.textContent || '').trim();
                return t === 'Run' || t.startsWith('Run\\n') || t.startsWith('Run Ctrl')
                    || t === 'Build' || t.startsWith('Build\\n') || t.startsWith('BuildCtrl')
                    || b.classList.contains('build-button')
                    || b.classList.contains('ctrl-enter-submits');
            });
            if (runBtn) {
                runBtn.click();
                return true;
            }
            return false;
        })()"""
    )
    if clicked:
        return True

    if await page.send_control_enter("textarea"):
        return True

    if await page.click("button.build-button"):
        return True
    if await page.click("button.ctrl-enter-submits"):
        return True
    if await page.click("button:has-text('Build')"):
        return True
    if await page.click("button:has-text('Run')"):
        return True
    return await page.click("button:has(mat-icon)")


async def _wait_for_prompt(page: CDPPage, timeout_s: float = 15.0) -> None:
    deadline = time.monotonic() + timeout_s
    while True:
        if is_login_page_url(page.url):
            raise SessionExpiredError("Cookie 认证失败，等待输入框时返回登录页")
        if "available-regions" in page.url:
            raise RuntimeError("Google AI Studio 地区限制")
        await page.evaluate(DIALOG_CLEANUP_JS)
        await page.evaluate(OPEN_EDITOR_JS)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            await page.wait_for_selector("textarea", timeout_s=min(1.0, remaining))
            usable = await page.evaluate(
                "() => { const el = document.querySelector('textarea'); return !!(el && !el.disabled && !el.readOnly && el.getClientRects().length); }"
            )
            if usable is True:
                return
            await page.wait_for_timeout(100)
        except TimeoutError:
            await page.evaluate(DIALOG_CLEANUP_JS)
    state = await page.evaluate(
        "() => ({ready: document.readyState, textareas: document.querySelectorAll('textarea').length, script: !!window.default_MakerSuite})"
    )
    fields: dict[str, object] = {}
    if isinstance(state, dict):
        if state.get("ready") in ("loading", "interactive", "complete"):
            fields["page_ready"] = state["ready"]
        count = state.get("textareas")
        if (
            isinstance(count, int)
            and not isinstance(count, bool)
            and 0 <= count <= 1000
        ):
            fields["textarea_count"] = count
        fields["script_ready"] = state.get("script") is True
    get_store().emit("browser_preparation_failed", stage="input", **fields)
    raise EditorNotReady("textarea not found while capturing BotGuardService")


async def bootstrap_botguard_and_template(
    page: CDPPage,
    reload_cookies_cb: Callable[[CDPPage], Awaitable[bool]] | None = None,
    is_proc_alive_cb: Callable[[], bool] | None = None,
    max_wait_seconds: int = 45,
) -> tuple[str, dict[str, object]]:
    started = time.monotonic()
    async with preparing():
        for attempt in range(2):
            try:
                result = await _bootstrap_attempt(
                    page, is_proc_alive_cb, max_wait_seconds
                )
                get_store().emit(
                    "browser_preparation_completed",
                    stage="capture",
                    elapsed_seconds=round(time.monotonic() - started, 3),
                )
                return result
            except (SessionExpiredError, EditorNotReady) as exc:
                if (
                    attempt
                    or reload_cookies_cb is None
                    or not await reload_cookies_cb(page)
                ):
                    raise
                get_store().emit(
                    "browser_preparation_recovery",
                    stage="login" if isinstance(exc, SessionExpiredError) else "input",
                )
    raise RuntimeError("BotGuard login recovery exhausted")


async def _bootstrap_attempt(
    page: CDPPage,
    is_proc_alive_cb: Callable[[], bool] | None = None,
    max_wait_seconds: int = 45,
) -> tuple[str, dict[str, object]]:
    """Execute 'say 1' bootstrap probe to capture BotGuardService and baseline wire template."""
    current_url = getattr(page, "url", "") or ""
    if "available-regions" in current_url:
        raise RuntimeError(
            "Google AI Studio 地区限制: 访问被重定向至 available-regions"
        )

    await _wait_for_prompt(page)
    snap_key = await install_page_hooks(page)

    t0 = time.monotonic()
    async with page.capture_blocked_request(GENERATE_CONTENT_RPC + "*") as captured:
        return await _capture_probe(
            page, captured, is_proc_alive_cb, max_wait_seconds, snap_key, t0
        )


async def _capture_probe(
    page: CDPPage,
    captured: asyncio.Future[dict[str, object]],
    is_proc_alive_cb: Callable[[], bool] | None,
    max_wait_seconds: int,
    snap_key: str,
    t0: float,
) -> tuple[str, dict[str, object]]:
    original_text = ""
    try:
        await page.evaluate(DIALOG_CLEANUP_JS)
        original_text = str(
            (
                await page.evaluate(
                    "() => document.querySelector('textarea')?.value || ''"
                )
            )
            or ""
        )
        await enable_temporary_chat(page)
        await page.fill("textarea", BOTGUARD_BOOTSTRAP_PROMPT)
        await page.wait_for_timeout(600)
        await page.evaluate(DIALOG_CLEANUP_JS)

        if not await click_run_button(page):
            raise RuntimeError(
                "failed to trigger run button while capturing BotGuardService"
            )

        for i in range(max_wait_seconds):
            if captured.done():
                captured.result()
            if is_proc_alive_cb and not is_proc_alive_cb():
                raise RuntimeError("Browser process died during BotGuard capture")
            if page.is_closed():
                raise RuntimeError("Browser page closed during BotGuard capture")

            curr_url = getattr(page, "url", "") or ""
            if is_login_page_url(curr_url):
                log.warning(
                    "BotGuard 捕获期间检测到登录页重定向: %s，尝试重新注入 Cookie...",
                    curr_url,
                )
                raise SessionExpiredError(
                    f"Cookie 认证失效，已跳转至登录页: {curr_url}"
                )

            await page.wait_for_timeout(1000)
            if await page.evaluate("() => !!window.__AISTUDIO__?.service"):
                with suppress(Exception):
                    await page.evaluate(STOP_GENERATION_JS)
                if not captured.done():
                    for _ in range(25):
                        if captured.done():
                            break
                        await page.wait_for_timeout(100)

                await page.fill("textarea", original_text)
                with suppress(Exception):
                    await page.evaluate(DOM_GC_CLEANUP_JS)

                if not captured.done():
                    raise RuntimeError(
                        "BotGuard service captured but wire template missing"
                    )

                request = captured.result()
                url = request.get("url")
                expected = urlsplit(GENERATE_CONTENT_RPC)
                if (
                    not isinstance(url, str)
                    or urlsplit(url)._replace(query="", fragment="") != expected
                ):
                    raise RuntimeError(
                        "BotGuard wire template has an unexpected RPC URL"
                    )
                body = request.get("post_data")
                if not isinstance(body, str):
                    raise RuntimeError("BotGuard wire template body is missing")
                try:
                    payload = json.loads(body)
                except (ValueError, TypeError) as exc:
                    raise RuntimeError("BotGuard wire template is not JSON") from exc
                if (
                    not isinstance(payload, list)
                    or not payload
                    or not isinstance(payload[0], str)
                ):
                    raise RuntimeError("BotGuard wire template is not a model request")

                log.info(
                    "BotGuard 握手成功并截获模板，耗时 %d 秒 (累计 %.1f 秒)",
                    i + 1,
                    time.monotonic() - t0,
                )
                return snap_key, {
                    "url": request["url"],
                    "headers": request["headers"],
                    "body": body,
                }

        raise RuntimeError(f"BotGuardService capture timeout after {max_wait_seconds}s")
    finally:
        with suppress(Exception):
            await page.evaluate(STOP_GENERATION_JS)
        with suppress(Exception):
            await page.fill("textarea", original_text)
