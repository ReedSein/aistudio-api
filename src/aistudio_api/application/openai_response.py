"""Chat Completions JSON and incremental SSE adapters."""

from __future__ import annotations

import base64
import json
import time
import uuid
from collections.abc import AsyncGenerator
from contextlib import aclosing
from typing import Any

from aistudio_api.api.openai_errors import error_payload
from aistudio_api.api.schemas import GeminiGenerateContentRequest
from aistudio_api.application.api_service_gemini import to_gemini_finish_reason
from aistudio_api.application.generation_service import generate_content, stream_content
from aistudio_api.application.openai_tools import encode_call
from aistudio_api.domain.errors import RequestError
from aistudio_api.domain.models import GeneratedImage
from aistudio_api.infrastructure.gateway.client import AIStudioClient
from aistudio_api.infrastructure.utils.logger import get_logger

logger = get_logger("api.openai")


def _finish_reason(value: object, *, tools: bool) -> str:
    if isinstance(value, int):
        value = to_gemini_finish_reason(value)
    if value == "MAX_TOKENS":
        return "length"
    if value in (
        "SAFETY",
        "RECITATION",
        "BLOCKLIST",
        "PROHIBITED_CONTENT",
        "SPII",
        "IMAGE_SAFETY",
        "IMAGE_PROHIBITED_CONTENT",
        "IMAGE_RECITATION",
    ):
        return "content_filter"
    if value != "STOP":
        raise RequestError(
            502,
            "Upstream generation did not provide a supported completion finish reason",
        )
    return "tool_calls" if tools else "stop"


def _images(images: list[GeneratedImage]) -> list[dict[str, object]]:
    return [
        {
            "index": index,
            "type": "image_url",
            "image_url": {
                "url": f"data:{image.mime};base64,{base64.b64encode(image.data).decode('ascii')}"
            },
            **(
                {"thought_signature": image.thought_signature}
                if image.thought_signature
                else {}
            ),
        }
        for index, image in enumerate(images)
    ]


def _usage(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict) or not value:
        return None
    result = {
        key: value[key]
        for key in ("prompt_tokens", "completion_tokens", "total_tokens")
        if isinstance(value.get(key), int) and not isinstance(value[key], bool)
    }
    for key in ("prompt_tokens_details", "completion_tokens_details"):
        detail = value.get(key)
        if isinstance(detail, dict):
            result[key] = {
                name: count
                for name, count in detail.items()
                if isinstance(count, int) and not isinstance(count, bool)
            }
    return result or None


def _envelope(model: str, *, stream: bool) -> dict[str, Any]:
    return {
        "id": "chatcmpl-" + uuid.uuid4().hex,
        "object": "chat.completion.chunk" if stream else "chat.completion",
        "created": int(time.time()),
        "model": model.removeprefix("models/"),
    }


async def completion(
    model: str, req: GeminiGenerateContentRequest, client: AIStudioClient
) -> dict[str, Any]:
    normalized, output = await generate_content(model, req, client)
    calls = [
        encode_call(call, output.thought_signature if i == 0 else "")
        for i, call in enumerate(output.function_calls)
    ]
    message: dict[str, Any] = {
        "role": "assistant",
        "content": output.text or (None if calls else ""),
    }
    if output.thinking:
        message["reasoning_content"] = output.thinking
    if calls:
        message["tool_calls"] = calls
    if output.images:
        message["images"] = _images(output.images)
    if output.reasoning_images:
        message["reasoning_images"] = _images(output.reasoning_images)
    if output.thought_signature:
        message["thought_signature"] = output.thought_signature
    if output.function_responses:
        message["function_responses"] = output.function_responses
    reason = _finish_reason(
        output.candidates[0].finish_reason if output.candidates else None,
        tools=bool(calls),
    )
    return {
        **_envelope(normalized.model, stream=False),
        "choices": [
            {"index": 0, "message": message, "finish_reason": reason, "logprobs": None}
        ],
        "usage": _usage(output.usage),
    }


def _sse(value: dict[str, Any]) -> str:
    return "data: " + json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n\n"


async def completion_stream(
    model: str,
    req: GeminiGenerateContentRequest,
    client: AIStudioClient,
    *,
    include_usage: bool,
) -> AsyncGenerator[str, None]:
    envelope = _envelope(model, stream=True)
    started = False
    usage = None
    reason: object = None
    signature = ""
    calls: list[dict[str, object]] = []
    done = False

    def chunk(delta: dict[str, Any], finish: str | None = None) -> str:
        return _sse(
            {
                **envelope,
                "choices": [
                    {
                        "index": 0,
                        "delta": delta,
                        "finish_reason": finish,
                        "logprobs": None,
                    }
                ],
            }
        )

    try:
        async with aclosing(stream_content(model, req, client)) as events:
            async for kind, value in events:
                if kind == "model":
                    envelope["model"] = str(value).removeprefix("models/")
                    usage, reason, signature, calls, done = None, None, "", [], False
                    continue
                if kind == "usage":
                    usage = _usage(value)
                    continue
                if kind == "finish_reason":
                    reason = value
                    continue
                if kind == "done":
                    done = True
                    continue
                if kind == "tool_calls":
                    if not isinstance(value, list) or not all(
                        isinstance(call, dict) for call in value
                    ):
                        raise RequestError(502, "Invalid upstream tool calls")
                    calls.extend(value)
                    continue
                delta: dict[str, Any] = {}
                if kind == "body" and isinstance(value, str):
                    delta["content"] = value
                elif kind == "thinking" and isinstance(value, str):
                    delta["reasoning_content"] = value
                elif kind == "thought_signature" and isinstance(value, str):
                    signature = value
                    delta["thought_signature"] = value
                elif kind in {"images", "reasoning_images"}:
                    if not isinstance(value, list) or not all(
                        isinstance(item, GeneratedImage) for item in value
                    ):
                        raise RequestError(502, "Invalid upstream image data")
                    delta[kind] = _images(value)
                if not delta:
                    continue
                if not started:
                    yield chunk({"role": "assistant", "content": ""})
                    started = True
                yield chunk(delta)
        if not done:
            raise RequestError(502, "Upstream stream ended without completion")
        finish = _finish_reason(reason, tools=bool(calls))
        if not started:
            yield chunk({"role": "assistant", "content": ""})
        # A signature may arrive after a function part. Delay only the tool
        # envelopes so its ID can carry the complete, original signature.
        for index, call in enumerate(calls):
            converted = encode_call(call, signature if index == 0 else "")
            yield chunk({"tool_calls": [{"index": index, **converted}]})
        yield chunk({}, finish)
        if include_usage:
            yield _sse({**envelope, "choices": [], "usage": usage})
        yield "data: [DONE]\n\n"
    except Exception as exc:
        logger.exception("OpenAI stream failed")
        _, payload = error_payload(exc)
        yield _sse(payload)
