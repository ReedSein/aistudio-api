"""Extensible Chat Completions envelope; content is validated by its adapter."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .openai_errors import OpenAIInputError


class OpenAIChatRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str = Field(min_length=1, max_length=256)
    messages: list[dict[str, Any]] = Field(min_length=1)
    stream: bool = False
    stream_options: dict[str, Any] | None = None


MAX_REQUEST_BYTES = 64 * 1024 * 1024


def require_object(value: object, param: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise OpenAIInputError("Expected an object", param)
    return value


def require_string(value: object, param: str) -> str:
    if not isinstance(value, str):
        raise OpenAIInputError("Expected a string", param)
    return value


def _invalid_constant(_value: str) -> None:
    raise ValueError("Non-finite JSON number")


def strict_json_loads(value: str | bytes | bytearray) -> Any:
    return json.loads(value, parse_constant=_invalid_constant)
