"""Gemini-compatible HTTP/SSE response helpers."""

from __future__ import annotations

from aistudio_api.api.response_models import (
    GeminiCodeExecutionResultPayload,
    GeminiExecutableCodePayload,
    GeminiFunctionCallPayload,
    GeminiFunctionResponsePayload,
    GeminiInlineDataResponse,
    GeminiPartResponse,
    GeminiUsageMetadata,
)
from aistudio_api.domain.models import OutputPart


def _coerce_usage_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped and (
            stripped.isdigit() or (stripped[0] in "+-" and stripped[1:].isdigit())
        ):
            return int(stripped)
    return 0


def to_gemini_usage_metadata(
    usage: dict[str, object] | None = None,
) -> GeminiUsageMetadata:
    usage = usage or {}
    completion_details = usage.get("completion_tokens_details")
    if not isinstance(completion_details, dict):
        completion_details = {}
    reasoning_tokens = _coerce_usage_int(completion_details.get("reasoning_tokens"))
    visible_tokens = _coerce_usage_int(completion_details.get("visible_tokens"))
    completion_tokens = _coerce_usage_int(usage.get("completion_tokens"))
    if completion_tokens == 0:
        completion_tokens = visible_tokens + reasoning_tokens
    candidates_tokens = completion_tokens
    prompt_tokens = _coerce_usage_int(usage.get("prompt_tokens"))
    total_tokens = _coerce_usage_int(usage.get("total_tokens"))
    if total_tokens == 0 and (prompt_tokens or candidates_tokens):
        total_tokens = prompt_tokens + candidates_tokens
    return GeminiUsageMetadata(
        promptTokenCount=prompt_tokens,
        candidatesTokenCount=candidates_tokens,
        thoughtsTokenCount=reasoning_tokens,
        totalTokenCount=total_tokens,
    )


def to_gemini_part(part: OutputPart) -> GeminiPartResponse:
    """Serialize one model output part without reordering or merging."""
    response = GeminiPartResponse(
        thought=True if part.thought else None,
        thoughtSignature=part.thought_signature or None,
    )
    if part.inline_data is not None:
        mime, data = part.inline_data
        response.inlineData = GeminiInlineDataResponse(mimeType=mime, data=data)
    elif part.function_call is not None:
        call = part.function_call
        response.functionCall = GeminiFunctionCallPayload(
            name=call.name, args=call.args, id=call.id
        )
    elif part.function_response is not None:
        result = part.function_response
        response.functionResponse = GeminiFunctionResponsePayload(
            name=result.name, response=result.response, id=result.id
        )
    elif part.executable_code is not None:
        language, code = part.executable_code
        response.executableCode = GeminiExecutableCodePayload(
            language=language, code=code
        )
    elif part.code_execution_result is not None:
        outcome, output = part.code_execution_result
        response.codeExecutionResult = GeminiCodeExecutionResultPayload(
            outcome=outcome, output=output
        )
    else:
        response.text = part.text or ""
    return response


def to_gemini_parts(parts: list[OutputPart]) -> list[GeminiPartResponse]:
    return [to_gemini_part(part) for part in parts]
