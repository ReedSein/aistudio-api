"""冷启动竞态回归：请求捕获必须等待会话就绪门。

预热未完成时若直接用占位模板解码，会触发 list index out of range 的 500。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from aistudio_api.infrastructure.gateway.capture import RequestCaptureService
from aistudio_api.infrastructure.gateway.session import BrowserSession


@pytest.mark.asyncio
async def test_capture_waits_for_ready_session_before_capture_template():
    session = MagicMock(spec=BrowserSession)
    ready = False
    ready_context = MagicMock(context_id=1)

    async def fake_ensure_ready(timeout_s: float = 60.0):
        nonlocal ready
        ready = True
        return ready_context

    async def fake_capture_template(model: str, *, context):
        assert ready, "capture_template must run after the session is ready"
        assert context is ready_context
        return {
            "url": "https://example.com/generate",
            "headers": {"content-type": "application/json"},
            "body": '["models/gemini-3.1-flash-lite",[[[[null,"hi"]],"user"]],null,[]]',
        }

    session.ensure_ready = AsyncMock(side_effect=fake_ensure_ready)
    session.capture_template = AsyncMock(side_effect=fake_capture_template)
    session.generate_snapshot = AsyncMock(return_value="!snap")
    session.active_context = MagicMock(context_id=1)

    service = RequestCaptureService(session=session)
    captured = await service.capture(prompt="hi", model="models/gemini-3.1-flash-lite")

    assert captured is not None
    session.ensure_ready.assert_awaited_once()
    session.capture_template.assert_awaited_once()
