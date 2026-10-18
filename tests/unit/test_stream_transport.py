"""Unit tests for XHRStreamTransport and CDP native stream binding."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from aistudio_api.infrastructure.browser.cdp_client import CDPPage
from aistudio_api.infrastructure.browser.scripts import (
    STREAM_CLEANUP_JS,
    STREAMING_INIT_JS,
)
from aistudio_api.infrastructure.gateway.transport import XHRStreamTransport


def test_streaming_scripts_defined_and_non_empty():
    """Verify streaming initialization and cleanup scripts are properly loaded."""
    assert len(STREAMING_INIT_JS.strip()) > 0
    assert len(STREAM_CLEANUP_JS.strip()) > 0
    assert "__AISTUDIO__" in STREAMING_INIT_JS
    assert "__AISTUDIO__" in STREAM_CLEANUP_JS


@pytest.mark.asyncio
async def test_transport_native_cdp_binding_stream_push():
    """Verify realtime stream event dispatch via CDP Runtime.bindingCalled."""
    transport = XHRStreamTransport()
    page = MagicMock(spec=CDPPage)
    binding_listener = None

    def mock_on_binding(name: str, callback):
        nonlocal binding_listener
        if name == "__aistudio_stream_push__":
            binding_listener = callback

    page.on_binding = MagicMock(side_effect=mock_on_binding)
    page.add_binding = AsyncMock()

    captured_rid = None

    async def fake_evaluate(expr, args=None, *a, **kw):
        nonlocal captured_rid
        if isinstance(args, dict) and "rid" in args:
            captured_rid = args["rid"]
        return

    page.evaluate = AsyncMock(side_effect=fake_evaluate)

    async def push_events():
        while captured_rid is None or binding_listener is None:
            await asyncio.sleep(0.01)

        binding_listener(
            json.dumps({"rid": captured_rid, "type": "status", "status": 200})
        )
        binding_listener(
            json.dumps({"rid": captured_rid, "type": "chunk", "text": "Hello "})
        )
        binding_listener(
            json.dumps({"rid": captured_rid, "type": "chunk", "text": "CDP Binding!"})
        )
        binding_listener(json.dumps({"rid": captured_rid, "type": "done"}))

    push_task = asyncio.create_task(push_events())

    collected = []
    async for tag, payload in transport.send_streaming_request(
        page,
        url="http://test.com",
        headers={},
        body="{}",
        timeout_ms=3000,
    ):
        collected.append((tag, payload))

    await push_task

    assert ("status", 200) in collected
    assert ("chunk", b"Hello ") in collected
    assert ("chunk", b"CDP Binding!") in collected


@pytest.mark.asyncio
async def test_transport_streaming_timeout_raises():
    """Verify stream timeout before completion explicitly raises TimeoutError."""
    transport = XHRStreamTransport()
    page = MagicMock(spec=CDPPage)
    binding_listener = None

    def mock_on_binding(name: str, callback):
        nonlocal binding_listener
        if name == "__aistudio_stream_push__":
            binding_listener = callback

    page.on_binding = MagicMock(side_effect=mock_on_binding)
    page.add_binding = AsyncMock()

    captured_rid = None

    async def fake_evaluate(expr, args=None, *a, **kw):
        nonlocal captured_rid
        if isinstance(args, dict) and "rid" in args:
            captured_rid = args["rid"]
        return

    page.evaluate = AsyncMock(side_effect=fake_evaluate)

    async def push_status_only():
        while captured_rid is None or binding_listener is None:
            await asyncio.sleep(0.01)
        binding_listener(
            json.dumps({"rid": captured_rid, "type": "status", "status": 200})
        )

    push_task = asyncio.create_task(push_status_only())
    with pytest.raises(
        TimeoutError, match="streaming response timed out before completion"
    ):
        async for _ in transport.send_streaming_request(
            page,
            url="http://test.com",
            headers={},
            body="{}",
            timeout_ms=100,
        ):
            pass
    await push_task


@pytest.mark.asyncio
async def test_transport_abort_stream_by_rid():
    """Verify abort_stream targets specific active stream by rid without disturbing other streams."""
    transport = XHRStreamTransport()
    page = MagicMock(spec=CDPPage)
    page.on_binding = MagicMock()
    page.add_binding = AsyncMock()

    captured_rids: list[str] = []

    async def fake_evaluate(expr, args=None, *a, **kw):
        if isinstance(args, dict) and "rid" in args:
            captured_rids.append(args["rid"])
        return

    page.evaluate = AsyncMock(side_effect=fake_evaluate)

    assert transport.in_flight_count == 0

    async def run_stream():
        async for _ in transport.send_streaming_request(
            page,
            url="http://test.com",
            headers={},
            body="{}",
            timeout_ms=5000,
        ):
            pass

    task1 = asyncio.create_task(run_stream())
    task2 = asyncio.create_task(run_stream())
    await asyncio.sleep(0.05)
    assert transport.in_flight_count == 2
    assert len(captured_rids) == 2

    # Abort unknown rid returns False
    assert transport.abort_stream("non_existent_rid") is False
    assert transport.in_flight_count == 2

    # Abort stream 1 specifically
    rid1 = captured_rids[0]
    assert transport.abort_stream(rid1, "client_disconnected") is True

    with pytest.raises(
        RuntimeError, match="streaming request failed: client_disconnected"
    ):
        await task1

    assert transport.in_flight_count == 1

    # Close transport interrupts remaining stream 2
    transport.close("transport_closed")
    with pytest.raises(
        RuntimeError, match="streaming request failed: transport_closed"
    ):
        await task2

    assert transport.in_flight_count == 0


@pytest.mark.asyncio
async def test_transport_unlimited_streaming_ignores_deadline():
    """timeout_ms=0 表示不超时：静默超过 1 秒仍继续等待，直到 done 到达。"""
    transport = XHRStreamTransport()
    page = MagicMock(spec=CDPPage)
    binding_listener = None

    def mock_on_binding(name: str, callback):
        nonlocal binding_listener
        if name == "__aistudio_stream_push__":
            binding_listener = callback

    page.on_binding = MagicMock(side_effect=mock_on_binding)
    page.add_binding = AsyncMock()

    captured_rid = None
    captured_timeout = None

    async def fake_evaluate(expr, args=None, *a, **kw):
        nonlocal captured_rid, captured_timeout
        if isinstance(args, dict) and "rid" in args:
            captured_rid = args["rid"]
            captured_timeout = args.get("timeout")
        return

    page.evaluate = AsyncMock(side_effect=fake_evaluate)

    async def push_events():
        while captured_rid is None or binding_listener is None:
            await asyncio.sleep(0.01)
        await asyncio.sleep(1.2)
        binding_listener(
            json.dumps({"rid": captured_rid, "type": "status", "status": 200})
        )
        binding_listener(
            json.dumps({"rid": captured_rid, "type": "chunk", "text": "deep thinking"})
        )
        binding_listener(json.dumps({"rid": captured_rid, "type": "done"}))

    push_task = asyncio.create_task(push_events())

    collected = []
    async for tag, payload in transport.send_streaming_request(
        page,
        url="http://test.com",
        headers={},
        body="{}",
        timeout_ms=0,
    ):
        collected.append((tag, payload))
    await push_task

    assert captured_timeout == 0.0
    assert ("status", 200) in collected
    assert ("chunk", b"deep thinking") in collected


@pytest.mark.asyncio
async def test_transport_unlimited_hooked_request_disables_timers():
    """timeout_ms=0 时 JS 侧收到 0（不设计时器），Python 侧 evaluate 不设截止时间。"""
    transport = XHRStreamTransport()
    page = MagicMock(spec=CDPPage)
    captured: dict = {}

    async def fake_evaluate(expr, args=None, *a, **kw):
        captured["args"] = args
        captured.update(kw)
        return {"status": 200, "body": "ok"}

    page.evaluate = AsyncMock(side_effect=fake_evaluate)

    status, raw = await transport.send_hooked_request(
        page,
        url="http://test.com",
        headers={},
        body="{}",
        timeout_ms=0,
    )

    assert status == 200
    assert raw == b"ok"
    assert captured["args"]["timeout"] == 0.0
    assert captured["timeout_s"] == float("inf")
