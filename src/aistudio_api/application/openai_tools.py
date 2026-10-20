"""Stateless tool-call identity/signature transport across client serialization."""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from typing import Any

from aistudio_api.api.openai_errors import OpenAIInputError
from aistudio_api.domain.models import OutputPart

PREFIX = "call_gemini_"
MAX_CALL_ID = 65536


def encode_call(part: OutputPart, signature: str = "") -> dict[str, Any]:
    """Encode a function-call part; its own signature wins over ``signature``."""
    call = part.function_call
    if call is None:
        raise ValueError("encode_call needs a function-call part")
    native_id = call.id or ""
    signature = part.thought_signature or signature
    payload = json.dumps(
        [native_id, signature, uuid.uuid4().hex], separators=(",", ":")
    ).encode()
    call_id = (
        PREFIX + base64.urlsafe_b64encode(payload).decode().rstrip("=")
        if signature or native_id
        else "call_" + uuid.uuid4().hex
    )
    if len(call_id) > MAX_CALL_ID:
        raise ValueError("Upstream tool signature exceeds the supported call ID size")
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": call.name,
            "arguments": json.dumps(call.args, ensure_ascii=False),
        },
        **({"signature": signature} if signature else {}),
    }


def decode_call(call_id: str, param: str) -> tuple[str | None, str | None]:
    if len(call_id) > MAX_CALL_ID:
        raise OpenAIInputError("Tool call ID is too long", param)
    if not call_id.startswith(PREFIX):
        return call_id, None
    encoded = call_id[len(PREFIX) :]
    try:
        decoded = base64.b64decode(
            encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True
        )
        value = json.loads(decoded)
        if (
            not isinstance(value, list)
            or len(value) not in (2, 3)
            or not all(isinstance(item, str) for item in value)
        ):
            raise ValueError
    except (ValueError, binascii.Error, UnicodeError, RecursionError) as exc:
        raise OpenAIInputError("Malformed Gemini tool call ID", param) from exc
    return value[0] or None, value[1] or None
