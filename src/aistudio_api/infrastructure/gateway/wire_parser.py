"""Google Protobuf wire array parser for AI Studio gateway responses.

Reverse-engineered Protobuf-over-JSON array specifications are documented in:
docs/WIRE_SPECIFICATION.md
"""

from __future__ import annotations

import json

from aistudio_api.domain.errors import RequestError
from aistudio_api.domain.models import (
    Candidate,
    FunctionCall,
    FunctionResponse,
    ModelOutput,
    OutputPart,
    append_part,
)
from aistudio_api.infrastructure.gateway.wire_spec import PartIndex
from aistudio_api.infrastructure.utils.common import extract_outer_json

# ==============================================================================
# Google Protobuf-over-JSON Wire Array Specifications (docs/WIRE_SPECIFICATION.md)
# ==============================================================================
# Top-level chunk indexes (WIRE_SPECIFICATION.md §7.1)
WIRE_CHUNK_CANDIDATES_INDEX = 0  # Field 1 candidates container [[candidate, ...]]
WIRE_CHUNK_USAGE_INDEX = 2  # Field 3 usage metadata array
WIRE_RESPONSE_ID_INDEX = 7  # Field 8 response unique ID string

# Candidate part indexes come from the single protocol table in wire_spec.
WIRE_PART_THOUGHT_LEAD_INDEX = PartIndex.THOUGHT_LEAD
WIRE_PART_TEXT_INDEX = PartIndex.TEXT
WIRE_PART_INLINE_DATA_INDEX = PartIndex.INLINE_DATA
WIRE_PART_FUNCTION_CALL_INDEX = PartIndex.FUNCTION_CALL
WIRE_PART_FUNCTION_RESPONSE_INDEX = PartIndex.FUNCTION_RESPONSE
WIRE_PART_FILE_DATA_INDEX = PartIndex.FILE_DATA
WIRE_PART_EXECUTABLE_CODE_INDEX = PartIndex.EXECUTABLE_CODE
WIRE_PART_CODE_EXECUTION_RESULT_INDEX = PartIndex.CODE_RESULT
WIRE_PART_THOUGHT_FLAG_INDEX = PartIndex.THOUGHT_FLAG
WIRE_PART_FUNCTION_CALL_ALT_INDEX = PartIndex.FUNCTION_CALL_ALT
WIRE_PART_FUNCTION_RESPONSE_ALT_INDEX = PartIndex.FUNCTION_RESPONSE_ALT
WIRE_PART_THOUGHT_ALT_FLAG_INDEX = PartIndex.THOUGHT_ALT_FLAG
WIRE_PART_THOUGHT_SIGNATURE_INDEX = PartIndex.THOUGHT_SIGNATURE

# UsageMetadata indexes inside chunk[2] (WIRE_SPECIFICATION.md §7.3)
WIRE_USAGE_PROMPT_TOKENS = 0  # Field 1 prompt token count
WIRE_USAGE_VISIBLE_COMPLETION = 1  # Field 2 visible completion token count
WIRE_USAGE_TOTAL_TOKENS = 2  # Field 3 total token count
WIRE_USAGE_CACHED_TOKENS = 3  # Field 4 cached prompt token count
WIRE_USAGE_PROMPT_DETAILS = 4  # Field 5 prompt tokens breakdown structure
WIRE_USAGE_REASONING_TOKENS = 9  # Field 10 thinking/reasoning token count

# Candidate indexes inside chunk[0][0] (WIRE_SPECIFICATION.md §7.2)
WIRE_CANDIDATE_CONTENT = 0  # Field 1 [parts, role]
WIRE_CANDIDATE_FINISH_REASON = 1  # Field 2 finish reason enum code
WIRE_CANDIDATE_FINISH_MESSAGE = 3  # Field 4 finish message string
WIRE_CANDIDATE_SAFETY_RATINGS = 4  # Field 5 safety ratings array


def _looks_like_response_chunk(value: object) -> bool:
    return isinstance(value, list) and len(value) > 0 and isinstance(value[0], list)


def _iter_response_chunks(outer: object) -> list[list[object]]:
    if isinstance(outer, list) and outer:
        if len(outer) == 1 and isinstance(outer[0], list):
            inner = outer[0]
            nested_chunks = [
                item
                for item in inner
                if _looks_like_response_chunk(item) and isinstance(item, list)
            ]
            if nested_chunks:
                return nested_chunks
        top_level_chunks = [
            item
            for item in outer
            if _looks_like_response_chunk(item) and isinstance(item, list)
        ]
        if len(top_level_chunks) > 1:
            return top_level_chunks

    if _looks_like_response_chunk(outer) and isinstance(outer, list):
        return [outer]

    return []


def _coerce_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
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
    return None


# Wire enum codes for code execution parts, mapped to Gemini enum names.
_WIRE_CODE_LANGUAGES = {0: "LANGUAGE_UNSPECIFIED", 1: "PYTHON"}
_WIRE_CODE_OUTCOMES = {
    0: "OUTCOME_UNSPECIFIED",
    1: "OUTCOME_OK",
    2: "OUTCOME_FAILED",
    3: "OUTCOME_DEADLINE_EXCEEDED",
}


def _slot(raw_part: list[object], index: int) -> object:
    return raw_part[index] if len(raw_part) > index else None


def parse_response_part(raw_part: object) -> OutputPart | None:
    """Decode one wire part; return None when it carries nothing."""
    if not isinstance(raw_part, list):
        return None
    # A content block [parts, role] or nested list is not a part.
    if raw_part and isinstance(raw_part[0], list):
        return None
    if len(raw_part) == 2 and raw_part[1] in ("model", "user", "system", "assistant"):
        return None

    thought_flag = _slot(raw_part, WIRE_PART_THOUGHT_FLAG_INDEX)
    thought_lead = _slot(raw_part, WIRE_PART_THOUGHT_LEAD_INDEX)
    if isinstance(thought_flag, bool):
        thought = thought_flag
    elif isinstance(thought_lead, bool):
        thought = thought_lead
    else:
        thought = _slot(raw_part, WIRE_PART_THOUGHT_ALT_FLAG_INDEX) == 1

    raw_signature = _slot(raw_part, WIRE_PART_THOUGHT_SIGNATURE_INDEX)
    signature = raw_signature if isinstance(raw_signature, str) else ""
    part = OutputPart(thought=thought, thought_signature=signature)

    raw_inline = _slot(raw_part, WIRE_PART_INLINE_DATA_INDEX)
    function_call = _function_call(
        _slot(raw_part, WIRE_PART_FUNCTION_CALL_INDEX)
    ) or _function_call(_slot(raw_part, WIRE_PART_FUNCTION_CALL_ALT_INDEX))
    function_response = _function_response(
        _slot(raw_part, WIRE_PART_FUNCTION_RESPONSE_INDEX)
    ) or _function_response(_slot(raw_part, WIRE_PART_FUNCTION_RESPONSE_ALT_INDEX))
    raw_code = _slot(raw_part, WIRE_PART_EXECUTABLE_CODE_INDEX)
    raw_result = _slot(raw_part, WIRE_PART_CODE_EXECUTION_RESULT_INDEX)
    raw_text = _slot(raw_part, WIRE_PART_TEXT_INDEX)

    if isinstance(raw_inline, list) and len(raw_inline) >= 2:
        part.inline_data = (str(raw_inline[0]), str(raw_inline[1]))
    elif function_call is not None:
        part.function_call = function_call
    elif function_response is not None:
        part.function_response = function_response
    elif raw_code is not None:
        part.executable_code = _coded_pair(raw_code, _WIRE_CODE_LANGUAGES, "code")
    elif raw_result is not None:
        part.code_execution_result = _coded_pair(
            raw_result, _WIRE_CODE_OUTCOMES, "code execution result"
        )
    elif isinstance(raw_text, str) and (raw_text or signature):
        part.text = raw_text
    elif signature or not _is_flag_only(raw_part):
        # A part that carries data we cannot place must not vanish silently.
        raise RequestError(502, f"Unrecognized upstream part: {raw_part!r}")
    else:
        return None
    return part


_FLAG_SLOTS = {
    PartIndex.THOUGHT_LEAD,
    PartIndex.TEXT,
    PartIndex.THOUGHT_FLAG,
    PartIndex.THOUGHT_ALT_FLAG,
}


def _is_flag_only(raw_part: list[object]) -> bool:
    """True when only an empty text slot or thought flags are set."""
    return all(
        value is None or (index in _FLAG_SLOTS and value in ("", True, False, 1, 0))
        for index, value in enumerate(raw_part)
    )


def _coded_pair(raw: object, names: dict[int, str], label: str) -> tuple[str, str]:
    if (
        isinstance(raw, list)
        and len(raw) >= 2
        and isinstance(raw[0], int)
        and isinstance(raw[1], str)
    ):
        return names.get(raw[0], names[0]), raw[1]
    raise RequestError(502, f"Unrecognized upstream {label} part: {raw!r}")


def _function_call(raw: object) -> FunctionCall | None:
    payload = _coerce_wire_payload(raw)
    if payload is None:
        return None
    name, value, call_id = payload
    return FunctionCall(name=name, args=value, id=call_id)


def _function_response(raw: object) -> FunctionResponse | None:
    payload = _coerce_wire_payload(raw)
    if payload is None:
        return None
    name, value, call_id = payload
    return FunctionResponse(name=name, response=value, id=call_id)


def _coerce_wire_payload(raw: object) -> tuple[str, object, str | None] | None:
    """Decode a wire ``[name, struct, id]`` (or dict) payload."""
    if isinstance(raw, dict):
        name = raw.get("name")
        value = raw.get("args", raw.get("response", {}))
        call_id = raw.get("id")
    elif isinstance(raw, list) and raw and isinstance(raw[0], str):
        name = raw[0]
        second = raw[1] if len(raw) > 1 else None
        if isinstance(second, list):
            value = decode_wire_struct(second)
        elif isinstance(second, str) and second.strip().startswith(("{", "[")):
            try:
                value = json.loads(second)
            except json.JSONDecodeError:
                value = second
        else:
            value = second if second is not None else {}
        call_id = raw[2] if len(raw) > 2 else None
    else:
        return None
    if not isinstance(name, str) or not name:
        raise RequestError(502, f"Upstream function part has no name: {raw!r}")
    return name, value, call_id if isinstance(call_id, str) and call_id else None


def _is_wire_value(val: object) -> bool:
    if isinstance(val, list) and len(val) > 0:
        return val[0] is None or (val[0] == 0 and len(val) == 1)
    return False


def decode_wire_struct(raw_struct: object) -> dict[str, object]:
    if isinstance(raw_struct, dict):
        return raw_struct
    if not isinstance(raw_struct, list) or not raw_struct:
        return {}

    # Single unboxed key-value pair [key, wire_value]
    if len(raw_struct) >= 2 and isinstance(raw_struct[0], str):
        return {raw_struct[0]: _decode_wire_value(raw_struct[1])}
    entries: object = raw_struct
    while (
        isinstance(entries, list)
        and len(entries) == 1
        and isinstance(entries[0], list)
        and not (len(entries[0]) >= 2 and isinstance(entries[0][0], str))
    ):
        entries = entries[0]

    if not isinstance(entries, list):
        return {}

    result: dict[str, object] = {}
    for item in entries:
        if isinstance(item, list) and len(item) >= 2 and isinstance(item[0], str):
            result[item[0]] = _decode_wire_value(item[1])
    return result


def _decode_wire_list(raw_list: object) -> list[object]:
    if not isinstance(raw_list, list) or not raw_list:
        return []

    if (
        len(raw_list) == 1
        and isinstance(raw_list[0], list)
        and not _is_wire_value(raw_list[0])
    ):
        items = raw_list[0]
    else:
        items = raw_list

    return [_decode_wire_value(item) for item in items]


def _decode_wire_value(value: object) -> object:
    if not isinstance(value, list):
        return value
    if len(value) == 0:
        return []
    if len(value) == 1 and value[0] == 0:
        return None
    if (
        len(value) >= 2
        and isinstance(value[1], (int, float))
        and not isinstance(value[1], bool)
    ):
        return value[1]
    if len(value) >= 3 and isinstance(value[2], str):
        return value[2]
    if len(value) >= 4 and value[3] is not None:
        if isinstance(value[3], bool):
            return value[3]
        if value[3] in (0, 1):
            return bool(value[3])
    if len(value) >= 5 and value[4] is not None:
        return decode_wire_struct(value[4])
    if len(value) >= 6 and value[5] is not None:
        return _decode_wire_list(value[5])
    if len(value) >= 3 and value[2] is not None:
        return value[2]
    return [_decode_wire_value(item) for item in value]


def parse_usage_metadata(raw_usage: object) -> dict[str, object]:
    if not isinstance(raw_usage, list):
        return {}
    prompt_tokens = _coerce_int(
        raw_usage[WIRE_USAGE_PROMPT_TOKENS]
        if len(raw_usage) > WIRE_USAGE_PROMPT_TOKENS
        else None
    )
    visible_completion_tokens = _coerce_int(
        raw_usage[WIRE_USAGE_VISIBLE_COMPLETION]
        if len(raw_usage) > WIRE_USAGE_VISIBLE_COMPLETION
        else None
    )
    total_tokens = _coerce_int(
        raw_usage[WIRE_USAGE_TOTAL_TOKENS]
        if len(raw_usage) > WIRE_USAGE_TOTAL_TOKENS
        else None
    )
    cached_tokens = _coerce_int(
        raw_usage[WIRE_USAGE_CACHED_TOKENS]
        if len(raw_usage) > WIRE_USAGE_CACHED_TOKENS
        else None
    )
    reasoning_tokens = _coerce_int(
        raw_usage[WIRE_USAGE_REASONING_TOKENS]
        if len(raw_usage) > WIRE_USAGE_REASONING_TOKENS
        else None
    )
    completion_tokens = visible_completion_tokens
    if isinstance(visible_completion_tokens, int) and isinstance(reasoning_tokens, int):
        completion_tokens = visible_completion_tokens + reasoning_tokens
    elif completion_tokens is None:
        completion_tokens = reasoning_tokens
    if (
        total_tokens is None
        and isinstance(prompt_tokens, int)
        and isinstance(completion_tokens, int)
    ):
        total_tokens = prompt_tokens + completion_tokens
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "cached_tokens": cached_tokens,
        "prompt_tokens_details": (
            raw_usage[WIRE_USAGE_PROMPT_DETAILS]
            if len(raw_usage) > WIRE_USAGE_PROMPT_DETAILS
            else None
        ),
        "completion_tokens_details": {
            "reasoning_tokens": reasoning_tokens or 0,
            "visible_tokens": visible_completion_tokens or 0,
        },
    }


def parse_chunk_usage(chunk: object) -> dict[str, object]:
    if not isinstance(chunk, list):
        return {}
    return parse_usage_metadata(
        chunk[WIRE_CHUNK_USAGE_INDEX] if len(chunk) > WIRE_CHUNK_USAGE_INDEX else None
    )


def parse_response_chunk(chunk: list[object]) -> Candidate:
    candidate = Candidate()
    if not isinstance(chunk, list) or len(chunk) <= WIRE_CHUNK_CANDIDATES_INDEX:
        return candidate

    candidates_container = chunk[WIRE_CHUNK_CANDIDATES_INDEX]
    if not isinstance(candidates_container, list) or not candidates_container:
        return candidate

    raw_candidate = candidates_container[0]
    if not isinstance(raw_candidate, list):
        return candidate

    raw_content = _slot(raw_candidate, WIRE_CANDIDATE_CONTENT)
    raw_parts = (
        raw_content[0] if isinstance(raw_content, list) and len(raw_content) > 0 else []
    )
    for raw_part in raw_parts if isinstance(raw_parts, list) else []:
        part = parse_response_part(raw_part)
        if part is not None:
            append_part(candidate.parts, part)

    finish_reason = _slot(raw_candidate, WIRE_CANDIDATE_FINISH_REASON)
    candidate.finish_reason = finish_reason if isinstance(finish_reason, int) else None
    finish_message = _slot(raw_candidate, WIRE_CANDIDATE_FINISH_MESSAGE)
    candidate.finish_message = finish_message if isinstance(finish_message, str) else ""
    raw_ratings = _slot(raw_candidate, WIRE_CANDIDATE_SAFETY_RATINGS)
    candidate.safety_ratings = raw_ratings if isinstance(raw_ratings, list) else []
    return candidate


def parse_text_output(raw: str) -> ModelOutput:
    output = ModelOutput(raw_response=raw)

    parts = extract_outer_json(raw)
    if not parts:
        return output

    chunks = _iter_response_chunks(parts[0])
    if not chunks:
        return output

    merged = Candidate()
    for chunk in chunks:
        parsed = parse_response_chunk(chunk)
        for part in parsed.parts:
            append_part(merged.parts, part)
        if parsed.finish_reason is not None:
            merged.finish_reason = parsed.finish_reason
        if parsed.finish_message:
            merged.finish_message = parsed.finish_message
        if parsed.safety_ratings:
            merged.safety_ratings = parsed.safety_ratings

    last_chunk = chunks[-1]
    output.usage = parse_usage_metadata(_slot(last_chunk, WIRE_CHUNK_USAGE_INDEX))
    raw_rid = _slot(last_chunk, WIRE_RESPONSE_ID_INDEX)
    output.response_id = raw_rid if isinstance(raw_rid, str) else ""
    output.candidates = [merged]
    return output


__all__ = [
    "WIRE_CANDIDATE_CONTENT",
    "WIRE_CANDIDATE_FINISH_MESSAGE",
    "WIRE_CANDIDATE_FINISH_REASON",
    "WIRE_CANDIDATE_SAFETY_RATINGS",
    "WIRE_CHUNK_CANDIDATES_INDEX",
    "WIRE_CHUNK_USAGE_INDEX",
    "WIRE_PART_CODE_EXECUTION_RESULT_INDEX",
    "WIRE_PART_EXECUTABLE_CODE_INDEX",
    "WIRE_PART_FILE_DATA_INDEX",
    "WIRE_PART_FUNCTION_CALL_ALT_INDEX",
    "WIRE_PART_FUNCTION_CALL_INDEX",
    "WIRE_PART_FUNCTION_RESPONSE_ALT_INDEX",
    "WIRE_PART_FUNCTION_RESPONSE_INDEX",
    "WIRE_PART_INLINE_DATA_INDEX",
    "WIRE_PART_TEXT_INDEX",
    "WIRE_PART_THOUGHT_ALT_FLAG_INDEX",
    "WIRE_PART_THOUGHT_FLAG_INDEX",
    "WIRE_PART_THOUGHT_LEAD_INDEX",
    "WIRE_PART_THOUGHT_SIGNATURE_INDEX",
    "WIRE_RESPONSE_ID_INDEX",
    "WIRE_USAGE_CACHED_TOKENS",
    "WIRE_USAGE_PROMPT_DETAILS",
    "WIRE_USAGE_PROMPT_TOKENS",
    "WIRE_USAGE_REASONING_TOKENS",
    "WIRE_USAGE_TOTAL_TOKENS",
    "WIRE_USAGE_VISIBLE_COMPLETION",
    "decode_wire_struct",
    "parse_chunk_usage",
    "parse_response_chunk",
    "parse_response_part",
    "parse_text_output",
    "parse_usage_metadata",
]
