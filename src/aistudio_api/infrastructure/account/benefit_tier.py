"""Explicit account benefit query, independent from browser preparation."""

from __future__ import annotations

import json
import uuid

import httpx

from aistudio_api.config import settings
from aistudio_api.domain.accounts import BenefitTier, TierRefreshError
from aistudio_api.infrastructure.account.cookie_parser import (
    DEFAULT_API_KEY,
    DEFAULT_EXT_BIN,
    DEFAULT_ORIGIN,
    DEFAULT_USER_AGENT,
    calculate_sapisid_hash,
)
from aistudio_api.infrastructure.gateway.wire_spec import BenefitTierIndex

BENEFIT_TIER_RPC = "https://alkalimakersuite-pa.clients6.google.com/$rpc/google.internal.alkali.applications.makersuite.v1.MakerSuiteService/GetAiStudioBenefitTier"
TIER_CODES = {
    0: BenefitTier.FREE,
    1: BenefitTier.PRO,
    2: BenefitTier.ULTRA,
    3: BenefitTier.PLUS,
}
TIER_HEADERS = {
    BenefitTier.PLUS: "TIER0",
    BenefitTier.PRO: "TIER1",
    BenefitTier.ULTRA: "TIER2",
}


def parse_benefit_tier(raw: str) -> BenefitTier:
    text = raw.strip().removeprefix(")]}'").strip()
    if text.startswith("[,"):
        text = "[null," + text[2:]
    try:
        payload = json.loads(text)
    except ValueError:
        raise TierRefreshError("invalid_response") from None
    if not isinstance(payload, list):
        raise TierRefreshError("invalid_response")
    # JSPB omits a default scalar enum (0) as null or a trailing empty slot.
    value = payload[BenefitTierIndex.TIER] if payload else None
    code = value if value is not None else 0
    if isinstance(code, bool) or not isinstance(code, int) or code not in TIER_CODES:
        raise TierRefreshError("invalid_response")
    return TIER_CODES[code]


async def query_benefit_tier(
    storage_state: dict[str, object], auth_user: str
) -> BenefitTier:
    raw_cookies = storage_state.get("cookies")
    if not isinstance(raw_cookies, list):
        raise TierRefreshError("credentials")
    cookies = {}
    for item in raw_cookies:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("name"), str)
            or not isinstance(item.get("value"), str)
        ):
            raise TierRefreshError("credentials")
        cookies[item["name"]] = item["value"]
    authorization = calculate_sapisid_hash(cookies)
    if not authorization:
        raise TierRefreshError("credentials")
    headers = {
        "Content-Type": "application/json+protobuf",
        "X-User-Agent": DEFAULT_USER_AGENT,
        "X-Goog-Api-Key": DEFAULT_API_KEY,
        "X-Goog-AuthUser": auth_user,
        "X-Goog-Ext-519733851-bin": DEFAULT_EXT_BIN,
        "X-AIStudio-Visit-Id": f"v1_{uuid.uuid4()}",
        "Origin": DEFAULT_ORIGIN,
        "Referer": DEFAULT_ORIGIN + "/",
        "Cookie": "; ".join(f"{name}={value}" for name, value in cookies.items()),
        "Authorization": authorization,
    }
    try:
        async with httpx.AsyncClient(timeout=8.0, proxy=settings.proxy_url) as client:
            response = await client.post(
                BENEFIT_TIER_RPC, headers=headers, content="[]"
            )
    except httpx.HTTPError:
        raise TierRefreshError("unavailable") from None
    if response.status_code == 429:
        raise TierRefreshError("rate_limited")
    if response.status_code in (401, 403) or response.is_redirect:
        raise TierRefreshError("authentication")
    if response.status_code != 200:
        raise TierRefreshError("unavailable")
    if response.headers.get("content-type", "").split(";", 1)[0].lower() not in (
        "application/json+protobuf",
        "application/json",
    ):
        raise TierRefreshError("invalid_response")
    return parse_benefit_tier(response.text)
