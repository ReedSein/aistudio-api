"""Browser-backed AI Studio client facade."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from aistudio_api.config import (
    DEFAULT_BROWSER_PORT,
    DEFAULT_TEXT_MODEL,
    settings,
)
from aistudio_api.domain.errors import RequestError, classify_error
from aistudio_api.domain.models import ModelOutput
from aistudio_api.infrastructure.diagnostics.redaction import (
    REDACTED,
    sanitize,
    sanitized_json,
)
from aistudio_api.infrastructure.diagnostics.store import get_store, open_private
from aistudio_api.infrastructure.gateway.capture import (
    CapturedRequest,
    RequestCaptureService,
)
from aistudio_api.infrastructure.gateway.replay import RequestReplayService
from aistudio_api.infrastructure.gateway.session import BrowserSession
from aistudio_api.infrastructure.gateway.streaming import StreamingGateway
from aistudio_api.infrastructure.gateway.wire_parser import parse_text_output
from aistudio_api.infrastructure.gateway.wire_spec import TopLevelIndex
from aistudio_api.infrastructure.gateway.wire_types import AistudioContent
from aistudio_api.infrastructure.utils.logger import get_logger

logger = get_logger("client")


class AIStudioClient:
    def __init__(self, port: int = DEFAULT_BROWSER_PORT):
        self.port = port
        self._captured: CapturedRequest | None = None
        self._session = BrowserSession(port=port)
        self._capture_service = RequestCaptureService(self._session)
        self._replay_service = RequestReplayService(session=self._session)

        self._streaming_gateway = StreamingGateway(session=self._session)

    @property
    def session(self) -> BrowserSession:
        """Expose active browser session facade."""
        return self._session

    async def warmup(self) -> None:
        """预热浏览器后端并加载 AI Studio 页面及捕获 BotGuard 服务。"""
        if self._session is not None:
            await self._session.ensure_botguard_service()
            logger.info("浏览器预热完成")

    async def switch_auth(self, auth_file: str | None) -> None:
        """切换账号的 auth 文件并清空模板缓存。"""
        self.clear_templates()
        if self._session is not None:
            await self._session.switch_auth(auth_file)

    def clear_templates(self) -> None:
        """清除捕获的请求模板。"""
        if getattr(self, "_capture_service", None) is not None:
            self._capture_service.clear_templates()

    async def close(self) -> None:
        """关闭浏览器后端。"""
        if self._session is not None:
            await self._session.close()

    def _dump_raw_exchange(
        self,
        *,
        kind: str,
        model: str,
        capture_prompt: str,
        modified_body: str,
        raw_response: str,
    ) -> None:
        get_store().text("upstream_response_body", raw_response, kind=kind, model=model)
        if not settings.dump_raw_response:
            return

        out_dir = Path(settings.dump_raw_response_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        safe_model = model.replace("/", "_")
        timestamp = str(__import__("time").time_ns())
        safe_body = json.loads(modified_body)
        safe_body[TopLevelIndex.SNAPSHOT] = REDACTED
        payload = {
            "kind": kind,
            "model": model,
            "capture_prompt": capture_prompt,
            "modified_body": safe_body,
            "raw_response": raw_response,
        }
        path = out_dir / f"aistudio_{kind}_{safe_model}_{timestamp}.json"
        with open_private(path, "x") as stream:
            stream.write(sanitized_json(sanitize(payload)))
        logger.info("已落盘原始请求/响应: %s", path)

    async def capture_request(
        self,
        prompt: str,
        model: str = DEFAULT_TEXT_MODEL,
        images: list[str | tuple[str, str]] | None = None,
        contents: list[AistudioContent] | None = None,
        system_instruction: str | None = None,
        system_instruction_content: AistudioContent | None = None,
        tools: list[list] | None = None,
        tool_config: list[object] | None = None,
        safety_settings: list[list] | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        top_k: int | None = None,
        max_tokens: int | None = None,
        generation_config_overrides: dict | None = None,
        sanitize_plain_text: bool = True,
        force_refresh: bool = False,
    ) -> CapturedRequest | None:
        return await self._capture_service.capture(
            prompt=prompt,
            model=model,
            images=images,
            contents=contents,
            system_instruction=system_instruction,
            system_instruction_content=system_instruction_content,
            tools=tools,
            tool_config=tool_config,
            safety_settings=safety_settings,
            temperature=temperature,
            top_p=top_p,
            top_k=top_k,
            max_tokens=max_tokens,
            generation_config_overrides=generation_config_overrides,
            sanitize_plain_text=sanitize_plain_text,
            force_refresh=force_refresh,
        )

    async def replay(self, body: str, timeout: int | None = None) -> tuple[int, bytes]:
        return await self._replay_service.replay(
            self._captured, body=body, timeout=timeout
        )

    async def stream_generate_content(
        self,
        *,
        model: str = DEFAULT_TEXT_MODEL,
        capture_prompt: str,
        capture_images: list[str | tuple[str, str]] | None = None,
        contents: list[AistudioContent] | None = None,
        system_instruction_content: AistudioContent | None = None,
        tools: list[list] | None = None,
        tool_config: list[object] | None = None,
        safety_settings: list[list] | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        top_k: int | None = None,
        max_tokens: int | None = None,
        generation_config_overrides: dict | None = None,
        sanitize_plain_text: bool = True,
        force_refresh_capture: bool = False,
        on_account: Callable[[str | None, str], None] | None = None,
    ):
        async with self._session.request_scope() as ctx:
            if on_account is not None:
                on_account(ctx.account_id, ctx.credential_version)
            captured = await self.capture_request(
                prompt=capture_prompt,
                model=model,
                images=capture_images,
                contents=contents,
                system_instruction_content=system_instruction_content,
                tools=tools,
                tool_config=tool_config,
                safety_settings=safety_settings,
                temperature=temperature,
                top_p=top_p,
                top_k=top_k,
                max_tokens=max_tokens,
                generation_config_overrides=generation_config_overrides,
                sanitize_plain_text=sanitize_plain_text,
                force_refresh=force_refresh_capture,
            )
            if not captured:
                raise RequestError(0, "无法拦截请求")
            async for event in self._streaming_gateway.stream_chat(
                captured=captured,
                model=model,
            ):
                yield event

    async def generate_content(
        self,
        *,
        model: str = DEFAULT_TEXT_MODEL,
        capture_prompt: str,
        capture_images: list[str | tuple[str, str]] | None = None,
        contents: list[AistudioContent] | None = None,
        system_instruction_content: AistudioContent | None = None,
        tools: list[list] | None = None,
        tool_config: list[object] | None = None,
        safety_settings: list[list] | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        top_k: int | None = None,
        max_tokens: int | None = None,
        generation_config_overrides: dict | None = None,
        sanitize_plain_text: bool = True,
        on_account: Callable[[str | None, str], None] | None = None,
    ) -> ModelOutput:
        async with self._session.request_scope() as ctx:
            if on_account is not None:
                on_account(ctx.account_id, ctx.credential_version)
            logger.info("拦截请求: %r", f"{capture_prompt[:20]}...")
            captured = await self.capture_request(
                prompt=capture_prompt,
                model=model,
                images=capture_images,
                contents=contents,
                system_instruction_content=system_instruction_content,
                tools=tools,
                tool_config=tool_config,
                safety_settings=safety_settings,
                temperature=temperature,
                top_p=top_p,
                top_k=top_k,
                max_tokens=max_tokens,
                generation_config_overrides=generation_config_overrides,
                sanitize_plain_text=sanitize_plain_text,
            )
            if not captured:
                raise RequestError(0, "无法拦截请求")

            modified_body = captured.body

            status, raw = await self._replay_service.replay(
                captured, body=modified_body
            )
            raw_text = raw.decode("utf-8", errors="replace")
            self._dump_raw_exchange(
                kind="generate_content",
                model=model,
                capture_prompt=capture_prompt,
                modified_body=modified_body,
                raw_response=raw_text,
            )
            if status != 200:
                raise classify_error(status, raw_text)
            output = parse_text_output(raw_text)
            output.model = model
            return output


__all__ = ["AIStudioClient", "CapturedRequest"]
