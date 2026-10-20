"""One generation lifecycle for every public chat protocol."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import aclosing
from typing import Any

from fastapi import HTTPException

from aistudio_api.api.schemas import GeminiGenerateContentRequest
from aistudio_api.api.state import runtime_state
from aistudio_api.application.account_orchestrator import (
    MAX_RETRIES,
    ensure_active_account,
    record_rotator_event,
)
from aistudio_api.application.chat_service import (
    NormalizedGeminiRequest,
    normalize_gemini_request,
)
from aistudio_api.application.generation_errors import (
    classify_gemini_error_payload,
    handle_attempt_exception,
)
from aistudio_api.domain.errors import RequestError
from aistudio_api.domain.models import ModelOutput
from aistudio_api.infrastructure.gateway.client import AIStudioClient
from aistudio_api.infrastructure.gateway.readiness import preparation_budget, preparing
from aistudio_api.infrastructure.utils.logger import get_logger

logger = get_logger("generation")

# Missing, or FINISH_REASON_UNSPECIFIED (0): the upstream never said it finished.
UNFINISHED_REASONS = (None, 0)


def _parameters(req: NormalizedGeminiRequest) -> dict[str, Any]:
    return {
        "model": req.model,
        "capture_prompt": req.capture_prompt,
        "capture_images": req.capture_images,
        "contents": req.contents,
        "system_instruction_content": req.system_instruction,
        "tools": req.tools,
        "tool_config": req.tool_config,
        "safety_settings": req.safety_settings,
        "temperature": req.temperature,
        "top_p": req.top_p,
        "top_k": req.top_k,
        "max_tokens": req.max_tokens,
        "generation_config_overrides": req.generation_config_overrides,
        "sanitize_plain_text": False,
    }


def _success(model: str, usage: dict[str, object] | None) -> None:
    record_rotator_event("success", model=model)
    runtime_state.record(model, "success", usage)
    logger.info("生成完成: model=%s, usage=%s", model, usage)


def _exhausted(last_error: Exception | None) -> HTTPException:
    if isinstance(last_error, HTTPException):
        return last_error
    if last_error is not None:
        code, message, _ = classify_gemini_error_payload(last_error)
        return HTTPException(
            code if 400 <= code <= 599 else 503, detail={"message": message}
        )
    return HTTPException(
        503,
        detail={
            "message": "Generation retry limit reached",
            "type": "service_unavailable",
        },
    )


async def _generate_content(
    model_path: str,
    req: GeminiGenerateContentRequest,
    client: AIStudioClient,
) -> tuple[NormalizedGeminiRequest, ModelOutput]:
    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES):
        normalized = None
        try:
            normalized = normalize_gemini_request(req, model_path)
            async with preparing():
                await ensure_active_account(attempt, model=model_path)
            logger.info(
                "生成请求: model=%s, turns=%d, stream=false, attempt=%d",
                normalized.model,
                len(req.contents),
                attempt + 1,
            )
            output = await client.generate_content(**_parameters(normalized))
            if (
                not output.candidates
                or output.candidates[0].finish_reason in UNFINISHED_REASONS
            ):
                raise RequestError(
                    502, "Upstream response ended without a finish reason"
                )
            _success(normalized.model, output.usage)
            return normalized, output
        except Exception as exc:
            last_error = exc
            if await handle_attempt_exception(
                exc,
                attempt=attempt,
                model_path=model_path,
                normalized_model=normalized.model if normalized else None,
                client=client,
            ):
                continue
            raise
    raise _exhausted(last_error) from last_error


async def _stream_content(
    model_path: str,
    req: GeminiGenerateContentRequest,
    client: AIStudioClient,
) -> AsyncGenerator[tuple[str, object], None]:
    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES):
        normalized = None
        emitted = False
        completed = False
        finished = False
        usage: dict[str, object] | None = None
        try:
            normalized = normalize_gemini_request(req, model_path)
            async with preparing():
                await ensure_active_account(attempt, model=model_path)
            # Internal metadata resets adapter state when an empty attempt retries.
            yield "model", normalized.model
            async with aclosing(
                client.stream_generate_content(
                    **_parameters(normalized), force_refresh_capture=attempt > 0
                )
            ) as events:
                async for kind, value in events:
                    if kind == "done":
                        completed = True
                    elif kind == "finish_reason" and value not in UNFINISHED_REASONS:
                        finished = True
                    if kind == "usage":
                        usage = value if isinstance(value, dict) else None
                    if kind == "parts" and value:
                        emitted = True
                    yield kind, value
            if not completed or not finished:
                raise RequestError(
                    502,
                    "Upstream stream ended without a finish reason or completion marker",
                )
            _success(normalized.model, usage)
            return
        except Exception as exc:
            last_error = exc
            if await handle_attempt_exception(
                exc,
                attempt=attempt,
                model_path=model_path,
                normalized_model=normalized.model if normalized else None,
                client=client,
                has_yielded_data=emitted,
            ):
                continue
            raise
    raise _exhausted(last_error) from last_error


async def generate_content(
    model_path: str,
    req: GeminiGenerateContentRequest,
    client: AIStudioClient,
) -> tuple[NormalizedGeminiRequest, ModelOutput]:
    with preparation_budget():
        return await _generate_content(model_path, req, client)


async def stream_content(
    model_path: str,
    req: GeminiGenerateContentRequest,
    client: AIStudioClient,
) -> AsyncGenerator[tuple[str, object], None]:
    with preparation_budget():
        async with aclosing(_stream_content(model_path, req, client)) as events:
            async for event in events:
                yield event
