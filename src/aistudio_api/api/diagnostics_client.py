"""Strict browser diagnostic input; authentication data is never part of this contract."""

from __future__ import annotations

from typing import Annotated, Literal
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

CLIENT_REPORT_LIMIT = 1024 * 1024
CLIENT_BODY_LIMIT = CLIENT_REPORT_LIMIT + 1024
Identifier = Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")]
Version = Annotated[str, Field(max_length=32, pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")]
CookieName = Literal[
    "SID",
    "HSID",
    "SSID",
    "SAPISID",
    "__Secure-1PAPISID",
    "__Secure-3PAPISID",
    "__Secure-1PSID",
    "__Secure-3PSID",
]


class StrictReportModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class BrowserEvent(StrictReportModel):
    id: Identifier
    session_id: Identifier
    operation_id: Identifier
    timestamp: Annotated[
        str, Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
    ]
    script_version: Version
    sequence: Annotated[int, Field(ge=0, le=2**53 - 1)]
    stage: Literal[
        "script_start",
        "cookie_start",
        "cookie_end",
        "request_start",
        "request_callback",
        "request_settled",
        "result",
        "export_start",
        "export_result",
        "unexpected_error",
    ]
    callback: (
        Literal["load", "error", "timeout", "abort", "ready_state", "progress", "throw"]
        | None
    ) = None
    outcome: (
        Literal[
            "ok",
            "unconfirmed",
            "auth",
            "http",
            "no_cookies",
            "local_only",
            "combined",
            "error",
        ]
        | None
    ) = None
    reason: (
        Literal[
            "network_error",
            "timeout",
            "aborted",
            "invalid_json",
            "invalid_response",
            "unexpected_error",
            "local_export",
            "archive_rejected",
            "download_failed",
        ]
        | None
    ) = None
    elapsed_ms: Annotated[float, Field(ge=0, le=604800000)] | None = None
    status: Annotated[int, Field(ge=0, le=599)] | None = None
    ready_state: Annotated[int, Field(ge=0, le=4)] | None = None
    loaded_bytes: Annotated[int, Field(ge=0, le=2**53 - 1)] | None = None
    response_bytes: Annotated[int, Field(ge=0, le=2**53 - 1)] | None = None
    request_id: Identifier | None = None
    cookie_count: Annotated[int, Field(ge=0, le=10000)] | None = None
    imported_count: Annotated[int, Field(ge=0, le=10000)] | None = None
    missing_cookie_names: Annotated[list[CookieName], Field(max_length=8)] | None = None
    gm_supported: bool | None = None
    gm_failed: bool | None = None
    enhanced: bool | None = None
    error_type: Annotated[str, Field(pattern=r"^[A-Za-z]{1,64}$")] | None = None
    error_stack: (
        Annotated[
            str,
            Field(
                max_length=8192, pattern=r"^(?:at \[script\]:[0-9]+:[0-9]+(?:\n|$))*$"
            ),
        ]
        | None
    ) = None
    duplicate_callback: bool | None = None
    server_origin: Annotated[str, Field(max_length=512)] | None = None

    @field_validator("server_origin")
    @classmethod
    def origin_only(cls, value: str | None) -> str | None:
        if value is not None:
            parts = urlsplit(value)
            if (
                parts.scheme not in ("http", "https")
                or not parts.hostname
                or parts.username
                or parts.password
                or parts.query
                or parts.fragment
                or parts.path not in ("", "/")
            ):
                raise ValueError("Expected a credential-free HTTP origin")
        return value


class BrowserReport(StrictReportModel):
    schema_version: Literal[1]
    script_version: Version
    browser: Annotated[str, Field(max_length=256)]
    manager: Annotated[str, Field(max_length=64)]
    manager_version: Annotated[str, Field(max_length=64)]
    storage_error: bool
    pruned_events: Annotated[int, Field(ge=0, le=2**53 - 1)]
    pruned_bytes: Annotated[int, Field(ge=0, le=2**53 - 1)]
    events: Annotated[list[BrowserEvent], Field(max_length=5000)]


class BrowserExportRequest(StrictReportModel):
    client_report: BrowserReport


async def read_client_report(request: Request) -> BrowserReport | None:
    """Bound the raw body before JSON/schema parsing and never echo rejected input."""
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > CLIENT_BODY_LIMIT:
            raise HTTPException(413, detail="浏览器诊断摘要超过容量限制。")
        body.extend(chunk)
    if not body.strip():
        return None
    try:
        report = BrowserExportRequest.model_validate_json(bytes(body)).client_report
    except ValidationError as exc:
        raise HTTPException(
            400, detail="浏览器诊断摘要格式不合法；未接收原始凭据或未知字段。"
        ) from exc
    if len(report.model_dump_json(exclude_none=True).encode()) > CLIENT_REPORT_LIMIT:
        raise HTTPException(413, detail="浏览器诊断摘要超过容量限制。")
    return report
