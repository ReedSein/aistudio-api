"""Gemini-compatible response adapters over the shared generation lifecycle."""

from __future__ import annotations

import json
from contextlib import aclosing

from fastapi.responses import StreamingResponse

from aistudio_api.api.response_models import (
    GeminiCandidateResponse,
    GeminiContentResponse,
    GeminiGenerateContentResponse,
    GeminiPartResponse,
)
from aistudio_api.api.responses import (
    to_gemini_part,
    to_gemini_parts,
    to_gemini_usage_metadata,
)
from aistudio_api.api.schemas import GeminiGenerateContentRequest
from aistudio_api.application.generation_errors import (
    classify_gemini_error_payload as classify_gemini_error_payload,
    clean_upstream_error_message as clean_upstream_error_message,
    handle_attempt_exception as handle_attempt_exception,
)
from aistudio_api.application.generation_service import generate_content, stream_content
from aistudio_api.domain.models import OutputPart
from aistudio_api.infrastructure.gateway.client import AIStudioClient

WIRE_TO_GEMINI_FINISH_REASON: dict[int, str] = {
    0: "FINISH_REASON_UNSPECIFIED",
    1: "STOP",
    2: "MAX_TOKENS",
    3: "SAFETY",
    4: "RECITATION",
    5: "LANGUAGE",
    6: "OTHER",
    7: "BLOCKLIST",
    8: "PROHIBITED_CONTENT",
    9: "SPII",
    10: "MALFORMED_FUNCTION_CALL",
    11: "IMAGE_SAFETY",
    12: "UNEXPECTED_TOOL_CALL",
    13: "TOO_MANY_TOOL_CALLS",
    14: "IMAGE_PROHIBITED_CONTENT",
    15: "NO_IMAGE",
    16: "IMAGE_RECITATION",
    17: "IMAGE_OTHER",
}


def to_gemini_finish_reason(wire_code: int | None) -> str:
    if wire_code is None:
        return "STOP"
    return WIRE_TO_GEMINI_FINISH_REASON.get(wire_code, "FINISH_REASON_UNSPECIFIED")


async def handle_gemini_generate_content(
    model_path: str,
    req: GeminiGenerateContentRequest,
    client: AIStudioClient,
    *,
    stream: bool,
):
    if stream:
        return _build_gemini_streaming_response(
            client=client, req=req, model_path=model_path
        )
    normalized, output = await generate_content(model_path, req, client)
    return GeminiGenerateContentResponse(
        candidates=[
            GeminiCandidateResponse(
                content=GeminiContentResponse(
                    # An empty candidate still carries one part for clients
                    # that index parts[0].
                    parts=to_gemini_parts(output.parts) or [GeminiPartResponse(text="")]
                ),
                finishReason=to_gemini_finish_reason(
                    output.candidates[0].finish_reason if output.candidates else None
                ),
                index=0,
            )
        ],
        usageMetadata=to_gemini_usage_metadata(output.usage),
        modelVersion=normalized.model,
        responseId=output.response_id or None,
    )


def format_sse_event(
    event_type: str,
    value: object,
    *,
    model_version: str | None = None,
    response_id: str | None = None,
) -> str | None:
    """Format one upstream chunk's parts as a Gemini SSE chunk."""
    if event_type != "parts" or not isinstance(value, list) or not value:
        return None
    parts = [
        to_gemini_part(part).model_dump(mode="json", exclude_none=True)
        for part in value
        if isinstance(part, OutputPart)
    ]
    payload: dict[str, object] = {
        "candidates": [{"content": {"role": "model", "parts": parts}, "index": 0}]
    }
    if model_version:
        payload["modelVersion"] = model_version
    if response_id:
        payload["responseId"] = response_id
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


def format_sse_usage(
    final_usage: dict[str, object] | None,
    *,
    finish_reason: str = "STOP",
    model_version: str | None = None,
    response_id: str | None = None,
) -> str:
    """Format final completion usage metadata into SSE chunk string."""
    effective_usage = final_usage or {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }
    usage_dict = to_gemini_usage_metadata(effective_usage).model_dump(
        mode="json", exclude_none=True
    )
    payload: dict[str, object] = {
        "candidates": [
            {
                "content": {"role": "model", "parts": []},
                "finishReason": finish_reason,
                "index": 0,
            }
        ],
        "usageMetadata": usage_dict,
    }
    if model_version:
        payload["modelVersion"] = model_version
    if response_id:
        payload["responseId"] = response_id
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


def format_sse_error(exc: Exception) -> str:
    """Format exception into standard Gemini SSE error chunk."""
    code, msg, status_str = classify_gemini_error_payload(exc)
    return (
        "data: "
        + json.dumps(
            {
                "error": {
                    "code": code,
                    "message": msg,
                    "status": status_str,
                }
            },
            ensure_ascii=False,
        )
        + "\n\n"
    )


def _build_gemini_streaming_response(
    *,
    client: AIStudioClient,
    req: GeminiGenerateContentRequest,
    model_path: str,
) -> StreamingResponse:
    async def stream_response():
        model = model_path
        usage = None
        reason = "STOP"
        response_id = None
        try:
            async with aclosing(stream_content(model_path, req, client)) as events:
                async for kind, value in events:
                    if kind == "model":
                        model, usage, reason, response_id = (
                            str(value),
                            None,
                            "STOP",
                            None,
                        )
                    elif kind == "usage":
                        usage = value if isinstance(value, dict) else None
                    elif kind == "finish_reason":
                        reason = (
                            to_gemini_finish_reason(value)
                            if isinstance(value, int)
                            else str(value)
                        )
                    elif kind == "response_id":
                        response_id = str(value)
                    else:
                        chunk = format_sse_event(
                            kind, value, model_version=model, response_id=response_id
                        )
                        if chunk:
                            yield chunk
            yield format_sse_usage(
                usage,
                finish_reason=reason,
                model_version=model,
                response_id=response_id,
            )
        except Exception as exc:
            yield format_sse_error(exc)

    return StreamingResponse(
        stream_response(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
