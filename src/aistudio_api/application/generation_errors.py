"""Shared upstream error classification and bounded account recovery."""

from __future__ import annotations

import re

from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError

from aistudio_api.api.state import runtime_state
from aistudio_api.application.account_orchestrator import (
    MAX_RETRIES,
    record_rotator_event,
    try_switch_account,
)
from aistudio_api.domain.errors import (
    AistudioError,
    AuthError,
    PreparationTimeout,
    RequestError,
    SessionExpiredError,
    UsageLimitExceeded,
    is_browser_session_hang_error,
    is_transient_rpc_error,
    parse_upstream_grpc_error,
)
from aistudio_api.infrastructure.gateway.client import AIStudioClient
from aistudio_api.infrastructure.gateway.readiness import preparing
from aistudio_api.infrastructure.utils.logger import get_logger

logger = get_logger("generation")


async def _switch_account(
    *,
    model: str,
    failed_account_id: str | None,
    is_auth_error: bool = False,
    is_session_expired: bool = False,
) -> bool:
    async with preparing():
        return await try_switch_account(
            model=model,
            failed_account_id=failed_account_id,
            is_auth_error=is_auth_error,
            is_session_expired=is_session_expired,
        )


def clean_upstream_error_message(raw_msg: str) -> str:
    """Extract a clean, readable error message from Google/JSPB upstream error strings."""
    if not raw_msg:
        return ""
    text = raw_msg.strip()
    if "[,[" not in text and "[null,[" not in text and not text.startswith("[,"):
        return text
    inner = text
    while True:
        m = re.match(r"^HTTP\s+\d+:\s*(.*)$", inner)
        if m:
            inner = m.group(1).strip()
        else:
            break
    _, msg = parse_upstream_grpc_error(inner)
    return msg or inner


async def _classify_attempt_exception(
    exc: Exception,
    *,
    attempt: int,
    model_path: str,
    normalized_model: str | None,
    client: AIStudioClient,
    has_yielded_data: bool = False,
) -> bool:
    """Handle per-attempt error classification and account switching.

    Returns True if the operation should be retried, or raises HTTPException / re-raises.
    """
    target_model = normalized_model or model_path
    account_svc = runtime_state.account_service
    active_acc = account_svc.get_active_account() if account_svc else None
    failed_id = active_acc.id if active_acc else None

    if isinstance(exc, HTTPException):
        raise exc

    if isinstance(exc, ValueError):
        raise HTTPException(
            400, detail={"message": str(exc), "type": "bad_request"}
        ) from exc

    if isinstance(exc, SessionExpiredError):
        logger.warning("账号会话失效，已被重定向到登录页: %s", exc)
        client.clear_templates()
        if not has_yielded_data and await _switch_account(
            model=target_model,
            failed_account_id=failed_id,
            is_auth_error=True,
            is_session_expired=True,
        ):
            logger.info(
                "已切换至其他可用健康账号重试 (%d/%d)", attempt + 1, MAX_RETRIES
            )
            return True
        raise HTTPException(
            401,
            detail={
                "message": "All accounts encountered login redirect or expired sessions. Please import fresh cookies.",
                "type": "auth_error",
            },
        ) from exc

    if isinstance(exc, AuthError):
        logger.warning("账号 403 权限拒绝: %s", exc)
        client.clear_templates()
        record_rotator_event("auth_error", model=target_model)
        if not has_yielded_data and await _switch_account(
            model=target_model, failed_account_id=failed_id, is_auth_error=True
        ):
            logger.info("切换至可用账号重试 (%d/%d)", attempt + 1, MAX_RETRIES)
            return True
        raise HTTPException(
            403, detail={"message": str(exc), "type": "auth_error"}
        ) from exc

    if isinstance(exc, UsageLimitExceeded):
        runtime_state.record(target_model, "rate_limited")
        record_rotator_event("rate_limited", model=target_model, scope=exc.scope)
        if not has_yielded_data and await _switch_account(
            model=target_model, failed_account_id=failed_id
        ):
            logger.info(
                "429 触发限额，切换账号 (%d/%d): model=%s",
                attempt + 1,
                MAX_RETRIES,
                target_model,
            )
            return True
        logger.warning("全部账号在模型 %s 上均处于冷却状态", target_model)
        raise HTTPException(
            429,
            detail={
                "message": rate_limit_message(exc, target_model, failed_id),
                "type": "rate_limit_exceeded",
            },
        ) from exc

    if (
        isinstance(exc, RequestError)
        and exc.status == 204
        and attempt == 0
        and not has_yielded_data
    ):
        logger.warning("Gemini 收到 204，清理模板缓存后重试一次")
        client.clear_templates()
        return True

    # 针对 Google 上游偶发且完全随机的 RPC 路由 404 (Ambiguous request for service '' and method '/GenerativeService.*')：
    # 此报错为上游端点偶发抖动，在未产生输出时当场立即重试（严禁切号以防浪费配额，其他 404 严格按原样报错抛出）
    if (
        is_transient_rpc_error(exc)
        and not has_yielded_data
        and attempt < MAX_RETRIES - 1
    ):
        logger.warning(
            "检测到 Google 上游偶发 404 (Ambiguous RPC method)，当场立即重试 (%d/%d): %s",
            attempt + 1,
            MAX_RETRIES,
            clean_upstream_error_message(str(exc)),
        )
        client.clear_templates()
        return True

    if is_browser_session_hang_error(exc):
        logger.warning(
            "模板或 BotGuard 捕获超时 (%d/%d): %s，切换账号重试",
            attempt + 1,
            MAX_RETRIES,
            exc,
        )
        client.clear_templates()
        if not has_yielded_data and attempt >= MAX_RETRIES - 1:
            raise HTTPException(
                503, detail={"message": str(exc), "type": "service_unavailable"}
            ) from exc
        if not has_yielded_data and await _switch_account(
            model=target_model, failed_account_id=failed_id, is_auth_error=False
        ):
            logger.info("已切换账号重试 (%d/%d)", attempt + 1, MAX_RETRIES)
            return True
        if attempt < 2 and not has_yielded_data:
            if client._session is not None:
                async with preparing():
                    await client._session.recover()
            return True
        if not has_yielded_data:
            raise HTTPException(
                503, detail={"message": str(exc), "type": "service_unavailable"}
            ) from exc
    if isinstance(exc, RequestError):
        clean_msg = clean_upstream_error_message(str(exc))
        status_code = exc.status if exc.status > 0 else 500
        error_type = (
            "bad_request"
            if status_code == 400
            else "not_found"
            if status_code == 404
            else "upstream_error"
        )
        logger.warning("Gemini 上游请求错误 (%d): %s", status_code, clean_msg)
        raise HTTPException(
            status_code, detail={"message": clean_msg, "type": error_type}
        ) from exc

    if isinstance(exc, AistudioError):
        runtime_state.record(target_model, "errors")
        record_rotator_event("error", model=target_model)
        logger.warning("Gemini 服务端异常: %s", exc)
        raise HTTPException(
            500, detail={"message": str(exc), "type": "server_error"}
        ) from exc

    runtime_state.record(target_model, "errors")
    record_rotator_event("error", model=target_model)
    logger.error("Gemini 未知严重异常: %s", exc)
    logger.debug("Gemini 异常堆栈详情:", exc_info=True)
    raise HTTPException(
        500, detail={"message": str(exc), "type": "server_error"}
    ) from exc


async def handle_attempt_exception(
    exc: Exception,
    *,
    attempt: int,
    model_path: str,
    normalized_model: str | None,
    client: AIStudioClient,
    has_yielded_data: bool = False,
) -> bool:
    """Keep transient recovery failures inside the caller's bounded attempt loop."""
    try:
        if isinstance(exc, PreparationTimeout):
            raise exc
        return await _classify_attempt_exception(
            exc,
            attempt=attempt,
            model_path=model_path,
            normalized_model=normalized_model,
            client=client,
            has_yielded_data=has_yielded_data,
        )
    except PreparationTimeout as timeout:
        raise HTTPException(
            503, detail={"message": str(timeout), "type": "service_unavailable"}
        ) from timeout
    except (RuntimeError, TimeoutError) as recovery_error:
        if (
            not has_yielded_data
            and attempt < MAX_RETRIES - 1
            and is_browser_session_hang_error(recovery_error)
        ):
            logger.warning(
                "会话恢复失败，计入当前尝试 (%d/%d): %s",
                attempt + 1,
                MAX_RETRIES,
                recovery_error,
            )
            return True
        raise


def rate_limit_message(
    exc: UsageLimitExceeded, model: str, account_id: str | None
) -> str:
    """Describe a 429 by the quota window the upstream actually named."""
    if exc.scope == "day":
        return (
            f"All accounts reached daily quota for model '{model}'. "
            "Quota resets at 00:00 PST."
        )
    rotator = runtime_state.rotator
    stats = rotator.get_account_stats(account_id) if rotator and account_id else None
    wait = round(stats.get_cooldown_remaining(model)) if stats else None
    retry = f" Retry in about {wait} seconds." if wait else ""
    if exc.scope == "minute":
        return f"Requests-per-minute limit reached for model '{model}'.{retry}"
    upstream = clean_upstream_error_message(str(exc))
    return f"Upstream rate limit for model '{model}': {upstream}.{retry}"


def classify_gemini_error_payload(exc: Exception) -> tuple[int, str, str]:
    """Extract standard Gemini HTTP status code, message, and status string from an exception."""
    if isinstance(exc, SessionExpiredError):
        return (
            401,
            "All accounts have expired sessions. Please import fresh cookies.",
            "UNAUTHENTICATED",
        )
    if isinstance(exc, AuthError):
        return 403, str(exc), "PERMISSION_DENIED"
    if isinstance(exc, UsageLimitExceeded):
        return 429, str(exc), "RESOURCE_EXHAUSTED"
    if isinstance(exc, (ValueError, RequestValidationError)):
        return 400, str(exc), "INVALID_ARGUMENT"
    if isinstance(exc, HTTPException):
        status = exc.status_code
        detail = exc.detail
        raw_msg = detail.get("message") if isinstance(detail, dict) else detail
        msg = str(raw_msg) if raw_msg is not None else ""
        clean_msg = clean_upstream_error_message(msg)
        status_map = {
            400: "INVALID_ARGUMENT",
            401: "UNAUTHENTICATED",
            403: "PERMISSION_DENIED",
            404: "NOT_FOUND",
            429: "RESOURCE_EXHAUSTED",
            500: "INTERNAL",
            503: "UNAVAILABLE",
        }
        return (
            status,
            clean_msg,
            status_map.get(status, "INTERNAL" if status >= 500 else "UNKNOWN"),
        )
    if isinstance(exc, RequestError):
        status = exc.status if exc.status > 0 else 500
        clean_msg = clean_upstream_error_message(str(exc))
        status_map = {
            400: "INVALID_ARGUMENT",
            401: "UNAUTHENTICATED",
            403: "PERMISSION_DENIED",
            404: "NOT_FOUND",
            429: "RESOURCE_EXHAUSTED",
            500: "INTERNAL",
            503: "UNAVAILABLE",
        }
        return (
            status,
            clean_msg,
            status_map.get(status, "INTERNAL" if status >= 500 else "UNKNOWN"),
        )
    if isinstance(exc, AistudioError):
        return 500, str(exc), "INTERNAL"

    return 500, str(exc), "INTERNAL"
