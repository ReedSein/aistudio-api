"""Recovery, cancellation and preparation budget regressions."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from aistudio_api.api.schemas import GeminiGenerateContentRequest
from aistudio_api.api.state import runtime_state
from aistudio_api.application import account_orchestrator, generation_service
from aistudio_api.domain.errors import PreparationTimeout
from aistudio_api.infrastructure.browser.cdp_client import CDPPage
from aistudio_api.infrastructure.gateway import botguard_bootstrap as bg
from aistudio_api.infrastructure.gateway.readiness import preparation_budget, preparing
from aistudio_api.infrastructure.gateway.session import BrowserSession
from aistudio_api.infrastructure.gateway.session_state import SessionContext


@pytest.mark.asyncio
@pytest.mark.parametrize("recovery_fails", [False, True])
@pytest.mark.parametrize("persistent", [False, True])
async def test_generation_survives_transient_recovery_failure(
    monkeypatch, recovery_fails, persistent
):
    account = SimpleNamespace(id="fixture-account")
    service = MagicMock()
    service.get_active_account.return_value = account

    async def activate(*args, **kwargs):
        if recovery_fails:
            raise RuntimeError("BotGuard capture timeout")
        return account

    service.activate_account = AsyncMock(side_effect=activate)
    rotator = MagicMock()
    rotator.get_next_account = AsyncMock(return_value=account)
    rotator.is_account_available.return_value = True
    client = MagicMock()
    client.session = client._session = SimpleNamespace(recover=AsyncMock())
    calls = 0

    async def upstream(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1 or persistent:
            raise RuntimeError("textarea not found while capturing BotGuardService")
        yield "parts", [{"text": "ok"}]
        yield "finish_reason", 1
        yield "done", None

    client.stream_generate_content = upstream
    monkeypatch.setattr(runtime_state, "client", client)
    monkeypatch.setattr(runtime_state, "account_service", service)
    monkeypatch.setattr(runtime_state, "rotator", rotator)
    monkeypatch.setattr(generation_service, "_success", MagicMock())
    monkeypatch.setattr(account_orchestrator, "_last_switch_time", 0.0)
    req = GeminiGenerateContentRequest.model_validate(
        {"contents": [{"role": "user", "parts": [{"text": "fixture"}]}]}
    )
    if persistent:
        with pytest.raises(HTTPException) as result:
            _ = [
                event
                async for event in generation_service.stream_content(
                    "gemini-test", req, client
                )
            ]
        assert result.value.status_code == 503
        assert calls == 5
        assert service.activate_account.await_count <= 4
    else:
        events = [
            event
            async for event in generation_service.stream_content(
                "gemini-test", req, client
            )
        ]
        assert ("done", None) in events
        assert calls == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("initial_login", [False, True])
@pytest.mark.parametrize("query", ["", "?fixture=1"])
async def test_login_recovery_reinstalls_and_captures(
    monkeypatch, initial_login, query
):
    page = MagicMock(spec=CDPPage)
    page.url = (
        "https://accounts.google.com/ServiceLogin"
        if initial_login
        else "https://aistudio.google.com/prompts/new_chat"
    )
    page.is_closed.return_value = False
    page.wait_for_selector = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    page.fill = AsyncMock()
    captured = None

    @asynccontextmanager
    async def intercept(pattern):
        nonlocal captured
        assert pattern == bg.GENERATE_CONTENT_RPC + "*"
        captured = asyncio.get_running_loop().create_future()
        yield captured

    page.capture_blocked_request = intercept

    async def evaluate(expr, *args, **kwargs):
        return (
            True
            if "!!window.__AISTUDIO__?.service" in expr or "getClientRects" in expr
            else ""
        )

    page.evaluate = AsyncMock(side_effect=evaluate)

    async def reload_page(p):
        p.url = "https://aistudio.google.com/prompts/new_chat"
        return True

    async def click(p):
        assert captured is not None
        captured.set_result(
            {
                "url": bg.GENERATE_CONTENT_RPC + query,
                "headers": {},
                "post_data": '["models/gemini-fixture",[]]',
            }
        )
        return True

    reload = AsyncMock(side_effect=reload_page)
    hooks = AsyncMock(return_value="fixture-key")
    monkeypatch.setattr(bg, "install_page_hooks", hooks)
    monkeypatch.setattr(bg, "enable_temporary_chat", AsyncMock(return_value=True))
    monkeypatch.setattr(bg, "click_run_button", click)
    key, template = await bg.bootstrap_botguard_and_template(
        page, reload_cookies_cb=reload
    )
    assert key == "fixture-key"
    assert template["body"] == '["models/gemini-fixture",[]]'
    assert reload.await_count == int(initial_login)
    hooks.assert_awaited_once()


@pytest.mark.asyncio
async def test_budget_charges_preparation_only_and_is_not_renewed():
    with preparation_budget(0.05) as budget:
        async with preparing():
            await asyncio.sleep(0.02)
        before_generation = budget.remaining
        await asyncio.sleep(0.06)
        assert budget.remaining == before_generation
        with pytest.raises(PreparationTimeout):
            async with preparing():
                await asyncio.sleep(0.06)
        assert budget.remaining == 0
        with pytest.raises(PreparationTimeout):
            async with preparing():
                pytest.fail("exhausted preparation must not start")


@pytest.mark.asyncio
async def test_one_cancelled_waiter_does_not_cancel_shared_preparation():
    session = BrowserSession(9222)
    started = asyncio.Event()
    release = asyncio.Event()
    page = MagicMock(spec=CDPPage)
    page.is_closed.return_value = False
    context = SessionContext(page, "key", "0", None, {})

    async def transition(*args, **kwargs):
        started.set()
        await release.wait()
        session.set_ready_context(context)
        return context

    session.transition_to_account = AsyncMock(side_effect=transition)
    first = asyncio.create_task(session.ensure_ready())
    await started.wait()
    second = asyncio.create_task(session.ensure_ready())
    await asyncio.sleep(0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert (
        session._preparation_task is not None and not session._preparation_task.done()
    )
    release.set()
    assert await second is context
    session.transition_to_account.assert_awaited_once()


@pytest.mark.asyncio
async def test_last_cancelled_waiter_cleans_up_preparation():
    session = BrowserSession(9222)
    started = asyncio.Event()
    cleaned = asyncio.Event()

    async def transition(*args, **kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    session.transition_to_account = AsyncMock(side_effect=transition)
    waiter = asyncio.create_task(session.ensure_ready())
    await started.wait()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert cleaned.is_set()
    assert session._preparation_task is None


@pytest.mark.asyncio
async def test_new_waiter_never_inherits_a_cancelled_preparation():
    session = BrowserSession(9222)
    started = asyncio.Event()
    cleaning = asyncio.Event()
    release = asyncio.Event()
    page = MagicMock(spec=CDPPage)
    page.is_closed.return_value = False
    context = SessionContext(page, "key", "0", None, {})
    calls = 0

    async def transition(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                await release.wait()
        session.set_ready_context(context)
        return context

    session.transition_to_account = AsyncMock(side_effect=transition)
    first = asyncio.create_task(session.ensure_ready())
    await started.wait()
    first.cancel()
    await cleaning.wait()
    second = asyncio.create_task(session.ensure_ready())
    try:
        assert await asyncio.wait_for(second, 1) is context
        assert calls == 2
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await first


@pytest.mark.asyncio
async def test_idle_reaper_rechecks_activity_inside_transition_lock(monkeypatch):
    from aistudio_api.config import settings

    monkeypatch.setattr(settings, "browser_idle_timeout", 1)
    session = BrowserSession(9222)
    session._proc = MagicMock()
    session._last_activity_time = 0
    session._close_internal = AsyncMock()
    async with session._transition_lock:
        reap = asyncio.create_task(session.check_idle_timeout())
        await asyncio.sleep(0)
        session._in_flight = 1
    assert not await reap
    session._close_internal.assert_not_awaited()
