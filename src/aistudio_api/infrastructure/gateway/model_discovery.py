"""动态模型发现与元数据获取服务。"""

from __future__ import annotations

import asyncio
import json
import math
import time
import uuid
from collections.abc import Mapping
from copy import deepcopy
from hashlib import sha256
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from aistudio_api.infrastructure.account.account_store import (
        AccountMeta,
        AccountStore,
    )
    from aistudio_api.infrastructure.browser.cdp_client import CDPPage
    from aistudio_api.infrastructure.gateway.session import BrowserSession

from aistudio_api.domain.accounts import BenefitTier
from aistudio_api.infrastructure.account.benefit_tier import TIER_HEADERS
from aistudio_api.infrastructure.account.cookie_parser import (
    DEFAULT_API_KEY,
    DEFAULT_EXT_BIN,
    DEFAULT_ORIGIN,
    DEFAULT_USER_AGENT,
    calculate_sapisid_hash,
)
from aistudio_api.infrastructure.gateway.session_state import SessionContext
from aistudio_api.infrastructure.gateway.wire_spec import (
    ModelIndex,
    ModelListIndex,
    ModelThinkingIndex,
)
from aistudio_api.infrastructure.utils.logger import get_logger

logger = get_logger("model_discovery")


class ModelAliasAmbiguous(ValueError):
    """More than one catalog entry claims the requested identifier."""


class _PageQueryFailed(RuntimeError):
    def __init__(self, reason: str, status: int | None = None):
        self.reason = reason
        self.status = status
        super().__init__(reason)


def _account_key(account: AccountMeta) -> tuple[str, ...]:
    return (
        "account",
        account.id,
        account.credential_version,
        account.auth_user,
        account.tier or "unknown",
    )


def _find_model(
    models: list[dict[str, object]], identifier: str
) -> Mapping[str, object] | None:
    target = identifier.removeprefix("models/")
    matches = []
    for model in models:
        aliases = model.get("aliases")
        if str(model.get("id") or model.get("name") or "").removeprefix(
            "models/"
        ) == target or (isinstance(aliases, list) and target in aliases):
            matches.append(model)
    if len(matches) > 1:
        raise ModelAliasAmbiguous(
            "Ambiguous model alias; use an unambiguous model identifier"
        )
    return matches[0] if matches else None


def _storage_cookies(state: dict[str, object]) -> dict[str, str]:
    raw = state.get("cookies")
    if not isinstance(raw, list):
        raise ValueError("Model account has no Cookie state")
    cookies: dict[str, str] = {}
    for cookie in raw:
        if (
            not isinstance(cookie, dict)
            or not isinstance(cookie.get("name"), str)
            or not isinstance(cookie.get("value"), str)
        ):
            raise ValueError("Invalid model account Cookie state")
        cookies[cookie["name"]] = cookie["value"]
    return cookies


def _at(values: list, index: int) -> object:
    return values[index] if index < len(values) else None


def _number(value: object) -> int | float | None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or value < 0
        or value > 2**53 - 1
        or (isinstance(value, float) and not math.isfinite(value))
    ):
        return None
    return value


def _rpc_headers(
    cookies: dict[str, str], auth_user: str, tier: BenefitTier | None
) -> dict[str, str]:
    authorization = calculate_sapisid_hash(cookies)
    if not authorization:
        raise ValueError("Model account has no signing Cookie")
    headers = {
        "Content-Type": "application/json+protobuf",
        "X-User-Agent": DEFAULT_USER_AGENT,
        "X-Goog-Api-Key": DEFAULT_API_KEY,
        "X-Goog-AuthUser": auth_user,
        "X-Goog-Ext-519733851-bin": DEFAULT_EXT_BIN,
        "X-AIStudio-Visit-Id": f"v1_{uuid.uuid4()}",
        "Origin": DEFAULT_ORIGIN,
        "Referer": DEFAULT_ORIGIN + "/",
        "Authorization": authorization,
    }
    tier_header = TIER_HEADERS.get(tier) if tier is not None else None
    if tier_header:
        headers["X-AIStudio-G1-Tier"] = tier_header
    return headers


def _integer(value: object) -> int | None:
    number = _number(value)
    return int(number) if number is not None and float(number).is_integer() else None


def _int_list(value: object) -> list[int] | None:
    if not isinstance(value, list) or any(
        isinstance(item, bool) or not isinstance(item, int) for item in value
    ):
        return None
    return list(dict.fromkeys(value))


def _aliases(value: object) -> list[str] | None:
    if not isinstance(value, list):
        return None
    result = []
    for item in value:
        name = item[0] if isinstance(item, list) and item else item
        if not isinstance(name, str) or not name.strip():
            return None
        clean_name = name.strip().removeprefix("models/")
        if not clean_name:
            return None
        result.append(clean_name)
    return list(dict.fromkeys(result))


# Labels from the upstream decoder are informational until sample verification.
# They never grant access or decide whether a request may use a tool.
CAPABILITY_LABELS = {
    1: "chat_model",
    9: "code_execution",
    10: "function_declarations",
    12: "google_search",
    13: "url_context",
    20: "video_route",
    21: "image_route",
    25: "thinking",
    26: "live_route",
    35: "thinking_budget",
    37: "speech_route",
    43: "media_resolution",
    46: "speech_translation",
    47: "aspect_ratio",
    48: "code_assistant",
    49: "output_resolution",
    52: "thinking_level",
    53: "music_route",
    54: "image_search",
    58: "google_maps",
    59: "interaction_route",
    85: "speech_metadata",
    91: "output_continuation",
}

# 本地保底默认模型列表（当网络未就绪或未配置账号时使用）
_FALLBACK_MODELS = [
    {
        "id": "gemini-3.8-flash",
        "displayName": "Gemini 3.8 Flash",
        "description": "Latest Gemini 3.8 Flash model",
    },
    {
        "id": "gemini-3.7-flash",
        "displayName": "Gemini 3.7 Flash",
        "description": "Gemini 3.7 Flash default text model",
    },
    {
        "id": "gemini-3.5-flash-lite",
        "displayName": "Gemini 3.5 Flash Lite",
        "description": "Ultra fast and lightweight model",
    },
    {
        "id": "gemini-3.1-pro-preview",
        "displayName": "Gemini 3.1 Pro Preview",
        "description": "Pro capabilities with complex reasoning",
    },
    {
        "id": "gemini-3.6-flash",
        "displayName": "Gemini 3.6 Flash",
        "description": "High throughput flash model",
    },
    {
        "id": "gemini-3.5-flash",
        "displayName": "Gemini 3.5 Flash",
        "description": "Balanced high-performance flash model",
    },
    {
        "id": "gemini-3.1-flash-image",
        "displayName": "Nano Banana 2",
        "description": "Image generation and multimodal editing",
    },
    {
        "id": "gemini-3-pro-image",
        "displayName": "Nano Banana Pro",
        "description": "High-fidelity multimodal generation",
    },
    {
        "id": "gemma-4-31b-it",
        "displayName": "Gemma 4 31B IT",
        "description": "Open weights flagship text model",
    },
    {
        "id": "gemma-4-26b-a4b-it",
        "displayName": "Gemma 4 26B A4B IT",
        "description": "Open weights instruction tuned model",
    },
    {
        "id": "veo-3.1-generate-preview",
        "displayName": "Veo 3.1",
        "description": "Video generation preview model",
    },
    {
        "id": "gemini-pro-latest",
        "displayName": "Gemini Pro Latest",
        "description": "Latest stable Gemini Pro alias",
    },
    {
        "id": "gemini-flash-latest",
        "displayName": "Gemini Flash Latest",
        "description": "Latest stable Gemini Flash alias",
    },
    {
        "id": "gemini-flash-lite-latest",
        "displayName": "Gemini Flash-Lite Latest",
        "description": "Latest stable Gemini Flash Lite alias",
    },
]


class ModelDiscoveryService:
    """动态获取 Google AI Studio 支持的模型列表（带缓存与保底）。"""

    def __init__(self, cache_ttl_seconds: int = 600) -> None:
        self._cache_ttl = cache_ttl_seconds
        self._cached_models: list[dict[str, object]] = []
        self._last_fetched_at = 0.0
        self._cache_key: tuple[str, ...] | None = None
        self._account_store: AccountStore | None = None
        self._lock = asyncio.Lock()

    def bind_account_store(self, store: AccountStore) -> None:
        if self._account_store is not store:
            self._account_store = store
            self._cached_models = []
            self._cache_key = None

    def _scope(
        self,
        session: BrowserSession | None,
        cookies: dict[str, str] | None,
        auth_user: str,
    ) -> tuple[tuple[str, ...], SessionContext | None, AccountMeta | None]:
        context = session.active_context if session is not None else None
        if not isinstance(context, SessionContext):
            context = None
        if cookies is not None:
            digest = sha256(json.dumps(sorted(cookies.items())).encode()).hexdigest()
            return ("cookies", digest, auth_user), None, None
        if self._account_store is not None:
            account = self._account_store.get_active_account()
            if account is not None:
                return _account_key(account), context, account
            return ("fallback",), None, None
        if context is not None:
            return (
                ("context", str(context.context_id), context.auth_user),
                context,
                context.account,
            )
        return ("fallback",), None, None

    def _fresh(self, key: tuple[str, ...]) -> bool:
        return (
            self._cache_key == key
            and bool(self._cached_models)
            and time.monotonic() - self._last_fetched_at < self._cache_ttl
        )

    async def get_models(
        self,
        *,
        force_refresh: bool = False,
        cookies: dict[str, str] | None = None,
        auth_user: str = "0",
        session: BrowserSession | None = None,
    ) -> list[dict[str, object]]:
        key, _, _ = self._scope(session, cookies, auth_user)
        if not force_refresh and self._fresh(key):
            return deepcopy(self._cached_models)
        async with self._lock:
            key, context, account = self._scope(session, cookies, auth_user)
            if not force_refresh and self._fresh(key):
                return deepcopy(self._cached_models)
            models: list[dict[str, object]] = []
            try:
                matching_page = (
                    context is not None
                    and context.is_usable()
                    and (
                        account is None
                        or (
                            context.account_id == account.id
                            and context.credential_version == account.credential_version
                        )
                    )
                )
                if matching_page and context is not None:
                    context.incr_in_flight()
                    try:
                        models = await self._fetch_via_page(
                            context.page,
                            auth_user=context.auth_user,
                            tier=account.tier if account else None,
                        )
                    finally:
                        context.decr_in_flight()
                else:
                    effective_cookies = cookies
                    if account is not None and self._account_store is not None:
                        snapshot = self._account_store.credential_snapshot(account.id)
                        if snapshot is None:
                            raise ValueError("Model account changed before discovery")
                        account, state = snapshot
                        key = _account_key(account)
                        auth_user = account.auth_user
                        effective_cookies = _storage_cookies(state)
                    if effective_cookies:
                        models = await self._fetch_via_http(
                            effective_cookies,
                            auth_user,
                            tier=account.tier if account else None,
                        )
            except (
                OSError,
                ValueError,
                RuntimeError,
                TimeoutError,
                httpx.HTTPError,
            ) as exc:
                status = (
                    exc.response.status_code
                    if isinstance(exc, httpx.HTTPStatusError)
                    else (exc.status if isinstance(exc, _PageQueryFailed) else None)
                )
                reason = (
                    exc.reason
                    if isinstance(exc, _PageQueryFailed)
                    else type(exc).__name__
                )
                logger.warning("模型列表查询失败: %s (status=%s)", reason, status)
            if models:
                self._cached_models = [
                    {**model, "metadataSource": "rpc"} for model in models
                ]
                self._cache_key = key
                self._last_fetched_at = time.monotonic()
                return deepcopy(self._cached_models)
            if self._cache_key == key and self._cached_models:
                self._cached_models = [
                    {
                        **model,
                        "metadataSource": "stale"
                        if model.get("metadataSource") in ("rpc", "stale")
                        else "fallback",
                    }
                    for model in self._cached_models
                ]
                return deepcopy(self._cached_models)
            self._cached_models = [
                {**model, "metadataSource": "fallback"} for model in _FALLBACK_MODELS
            ]
            self._cache_key = key
            self._last_fetched_at = time.monotonic()
            return deepcopy(self._cached_models)

    async def get_model_by_id(
        self, model_id: str, session: BrowserSession | None = None
    ) -> Mapping[str, object] | None:
        discovered = await self.get_models(session=session)
        found = _find_model(discovered, model_id)
        if found is not None:
            return found
        return _find_model(
            [{**model, "metadataSource": "fallback"} for model in _FALLBACK_MODELS],
            model_id,
        )

    async def _fetch_via_page(
        self, page: CDPPage, *, auth_user: str = "0", tier: BenefitTier | None = None
    ) -> list[dict[str, object]]:
        """在 CDP Page 环境中直接执行 XHR 拉取 ListModels（自动带齐浏览器完整凭据）。"""
        script = """
        (args) => {
            return new Promise((resolve) => {
                const xhr = new XMLHttpRequest();
                xhr.open('POST', 'https://alkalimakersuite-pa.clients6.google.com/$rpc/google.internal.alkali.applications.makersuite.v1.MakerSuiteService/ListModels');
                for (const [key, value] of Object.entries(args.headers)) xhr.setRequestHeader(key, value);
                xhr.withCredentials = true;
                xhr.timeout = 10000;
                xhr.onload = () => {
                    if (xhr.status === 200) {
                        try {
                            let text = xhr.responseText.trim();
                            if (text.startsWith(")]}'")) text = text.slice(4).trim();
                            resolve({ok: true, data: JSON.parse(text)});
                        } catch(e) {
                            resolve({ok: false, error: 'invalid_response'});
                        }
                    } else {
                        resolve({ok: false, status: xhr.status});
                    }
                };
                xhr.onerror = () => resolve({ok: false, error: 'network_error'});
                xhr.ontimeout = () => resolve({ok: false, error: 'timeout'});
                xhr.send('[]');
            });
        }
        """
        cookies = _storage_cookies({"cookies": await page.get_cookies()})
        headers = _rpc_headers(cookies, auth_user, tier)
        headers = {
            key: value
            for key, value in headers.items()
            if key.lower() not in ("origin", "referer")
        }
        res = await page.evaluate(script, args={"headers": headers}, timeout_s=12.0)
        if not isinstance(res, dict) or not res.get("ok"):
            status = res.get("status") if isinstance(res, dict) else None
            error = res.get("error") if isinstance(res, dict) else None
            reason = (
                error
                if error in ("network_error", "timeout", "invalid_response")
                else "http_failure"
            )
            raise _PageQueryFailed(
                str(reason),
                status
                if isinstance(status, int) and not isinstance(status, bool)
                else None,
            )
        raw_data = res.get("data")
        return self._parse_raw_models(raw_data)

    async def _fetch_via_http(
        self,
        cookies: dict[str, str],
        auth_user: str,
        *,
        tier: BenefitTier | None = None,
    ) -> list[dict[str, object]]:
        """通过 HTTP 客户端携带 SAPISIDHASH 拉取。"""
        headers = _rpc_headers(cookies, auth_user, tier)
        headers["Cookie"] = "; ".join(
            f"{key}={value}" for key, value in cookies.items()
        )

        from aistudio_api.config import settings

        url = "https://alkalimakersuite-pa.clients6.google.com/$rpc/google.internal.alkali.applications.makersuite.v1.MakerSuiteService/ListModels"
        async with httpx.AsyncClient(timeout=10.0, proxy=settings.proxy_url) as client:
            resp = await client.post(url, headers=headers, content="[]")
            resp.raise_for_status()
            return self._parse_raw_models(
                json.loads(resp.text.strip().removeprefix(")]}'").strip())
            )

    def _parse_raw_models(self, raw_data: object) -> list[dict[str, object]]:
        """Decode optional model fields without interpreting access eligibility."""
        entries = (
            _at(raw_data, ModelListIndex.MODELS) if isinstance(raw_data, list) else None
        )
        if not isinstance(entries, list):
            return []
        models: list[dict[str, object]] = []
        for item in entries:
            if not isinstance(item, list):
                continue
            raw_id = _at(item, ModelIndex.NAME)
            if not isinstance(raw_id, str) or not raw_id.strip():
                continue
            model_id = raw_id.strip().removeprefix("models/")
            if not model_id:
                continue
            methods_value = _at(item, ModelIndex.METHODS)
            methods = (
                methods_value
                if isinstance(methods_value, list)
                and all(isinstance(method, str) for method in methods_value)
                else None
            )
            if "generateContent" not in (methods or []) and not any(
                kind in model_id for kind in ("image", "veo", "lyria")
            ):
                continue
            is_image = "image" in model_id or "imagen" in model_id
            is_video = "veo" in model_id
            is_audio = "lyria" in model_id or "tts" in model_id
            category = "flagship"
            if is_image:
                category = "image"
            elif is_video:
                category = "video"
            elif is_audio:
                category = "audio"
            elif any(kind in model_id for kind in ("lite", "8b", "flash")):
                category = "flash"
            elif "gemma" in model_id:
                category = "gemma"
            version = _at(item, ModelIndex.VERSION)
            display = _at(item, ModelIndex.DISPLAY_NAME)
            description = _at(item, ModelIndex.DESCRIPTION)
            codes = _int_list(_at(item, ModelIndex.CAPABILITIES))
            thinking = _at(item, ModelIndex.THINKING_CONFIG)
            thinking = thinking if isinstance(thinking, list) else []
            paid_hint = _at(item, ModelIndex.PAID_HINT)
            paid_hint = (
                paid_hint
                if isinstance(paid_hint, int) and not isinstance(paid_hint, bool)
                else None
            )
            top_p = _number(_at(item, ModelIndex.TOP_P))
            if top_p is not None and top_p > 1:
                top_p = None
            models.append(
                {
                    "id": model_id,
                    "name": raw_id,
                    "displayName": display.strip()
                    if isinstance(display, str) and display.strip()
                    else model_id,
                    "version": version.strip()
                    if isinstance(version, str) and version.strip()
                    else None,
                    "description": description if isinstance(description, str) else "",
                    "category": category,
                    "inputTokenLimit": _integer(
                        _at(item, ModelIndex.INPUT_TOKEN_LIMIT)
                    ),
                    "outputTokenLimit": _integer(
                        _at(item, ModelIndex.OUTPUT_TOKEN_LIMIT)
                    ),
                    "supportedGenerationMethods": methods,
                    "temperature": _number(_at(item, ModelIndex.TEMPERATURE)),
                    "topP": top_p,
                    "topK": _integer(_at(item, ModelIndex.TOP_K)),
                    "aliases": _aliases(_at(item, ModelIndex.ALIASES)),
                    "capabilityCodes": codes,
                    "capabilities": [
                        CAPABILITY_LABELS[code]
                        for code in codes
                        if code in CAPABILITY_LABELS
                    ]
                    if codes is not None
                    else None,
                    "capabilitySource": "unverified_mapping"
                    if codes is not None
                    else "unknown",
                    "defaultThinkingLevel": _integer(
                        _at(thinking, ModelThinkingIndex.DEFAULT_LEVEL)
                    ),
                    "thinkingLevels": _int_list(
                        _at(thinking, ModelThinkingIndex.LEVELS)
                    ),
                    "paid": True if paid_hint == 2 else None,
                    "paidHint": paid_hint,
                    "accessModes": _int_list(_at(item, ModelIndex.ACCESS_MODES)),
                    "is_image_model": is_image,
                    "is_video_model": is_video,
                    "is_audio_model": is_audio,
                }
            )
        return models


# 单例实例
model_discovery = ModelDiscoveryService()
