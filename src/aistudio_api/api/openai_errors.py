"""OpenAI error envelopes, scoped to the /v1 API."""

from __future__ import annotations

from fastapi import HTTPException

from aistudio_api.application.generation_errors import classify_gemini_error_payload
from aistudio_api.infrastructure.diagnostics.redaction import sanitize


class OpenAIInputError(HTTPException):
    def __init__(self, message: str, param: str | None = None, status: int = 400):
        super().__init__(status, detail={"message": message, "param": param})


def is_openai_path(path: str) -> bool:
    return path == "/v1" or path.startswith("/v1/")


def error_payload(exc: Exception) -> tuple[int, dict[str, object]]:
    status, message, _ = classify_gemini_error_payload(exc)
    if not 400 <= status <= 599:
        status = 502
    kind = {
        401: "authentication_error",
        403: "permission_error",
        429: "rate_limit_error",
    }.get(status, "server_error" if status >= 500 else "invalid_request_error")
    code = {
        401: "invalid_api_key",
        404: "model_not_found",
        413: "request_too_large",
        429: "rate_limit_exceeded",
    }.get(status, kind)
    if status >= 500:
        message = "The upstream generation service could not complete the request. See the server diagnostics for details."
    param = None
    if isinstance(exc, HTTPException) and isinstance(exc.detail, dict):
        param = exc.detail.get("param")
    return status, {
        "error": {
            "message": sanitize(message),
            "type": kind,
            "param": param,
            "code": code,
        }
    }
