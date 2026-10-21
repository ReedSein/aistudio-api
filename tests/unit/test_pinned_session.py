"""A switch may publish a new page while an older attempt is being signed."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from aistudio_api.infrastructure.browser.cdp_client import CDPPage
from aistudio_api.infrastructure.gateway.capture import RequestCaptureService
from aistudio_api.infrastructure.gateway.replay import RequestReplayService
from aistudio_api.infrastructure.gateway.session import BrowserSession
from aistudio_api.infrastructure.gateway.session_state import SessionContext


def context(name: str, user: str):
    page = MagicMock(spec=CDPPage)
    page.is_closed.return_value = False
    return SessionContext(
        page,
        name,
        user,
        f"/fixture/{name}/auth.json",
        {
            "url": f"https://{name}.invalid/generate",
            "headers": {},
            "body": '["models/fixture",[[[[null,"hi"]],"user"]],null,[null,null,null,128],"!original"]',
        },
    )


@pytest.mark.asyncio
async def test_template_snapshot_and_replay_keep_the_same_identity_after_switch():
    session = BrowserSession(9222)
    old, new = context("old", "0"), context("new", "1")
    session.set_ready_context(old)

    async def snapshot(*args, **kwargs):
        session.set_ready_context(new)
        return "!fixture-snapshot"

    old.page.evaluate = AsyncMock(side_effect=snapshot)
    session._transport.send_hooked_request = AsyncMock(return_value=(200, b"fixture"))
    service = RequestCaptureService(session)
    async with session.request_scope() as pinned:
        assert pinned is old
        captured = await service.capture("fixture", model="fixture")
        assert captured is not None and captured.context is old
        assert session.active_context is new
        assert await session.ensure_ready() is old
        assert await RequestReplayService(session).replay(captured, captured.body) == (
            200,
            b"fixture",
        )
        call = session._transport.send_hooked_request.call_args
        assert call.args[0] is old.page
        assert call.kwargs["auth_user"] == "0"
        assert call.kwargs["url"] == "https://old.invalid/generate"
        assert old.in_flight == 1
    assert old.in_flight == 0 and old.drain_event.is_set()
    assert await session.ensure_ready() is new


@pytest.mark.asyncio
async def test_waiting_template_capture_rechecks_context_inside_cache_lock():
    session = BrowserSession(9222)
    old, new = context("old", "0"), context("new", "1")
    entered, release = asyncio.Event(), asyncio.Event()

    async def template(model, *, context):
        if context is new:
            entered.set()
            await release.wait()
        return context.base_template

    session.capture_template = AsyncMock(side_effect=template)
    service = RequestCaptureService(session)
    service._active_ctx_id = old.context_id
    first = asyncio.create_task(service._ensure_template("fixture", new))
    await entered.wait()
    second = asyncio.create_task(service._ensure_template("fixture", old))
    await asyncio.sleep(0)
    release.set()
    new_template, old_template = await asyncio.gather(first, second)
    assert new_template.url == "https://new.invalid/generate"
    assert old_template.url == "https://old.invalid/generate"
