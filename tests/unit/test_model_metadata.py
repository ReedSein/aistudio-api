"""Model metadata is optional and cached only for its originating identity."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from aistudio_api.api.app import app
from aistudio_api.api.routes_models import _to_gemini_model
from aistudio_api.domain.accounts import BenefitTier
from aistudio_api.infrastructure.account.account_store import AccountStore
from aistudio_api.infrastructure.browser.cdp_client import CDPPage
from aistudio_api.infrastructure.gateway.model_discovery import (
    ModelAliasAmbiguous,
    ModelDiscoveryService,
    model_discovery,
)
from aistudio_api.infrastructure.gateway.session import BrowserSession
from aistudio_api.infrastructure.gateway.session_state import SessionContext
from aistudio_api.infrastructure.gateway.wire_codec import build_tools_from_names


def raw_model(identifier="fixture") -> list[object]:
    entry: list[object] = [None] * 83
    entry[:11] = [
        f"models/{identifier}",
        None,
        "v-fixture",
        "Fixture",
        "Fixture model",
        1000,
        200,
        ["generateContent"],
        0.25,
        0.8,
        32,
    ]
    entry[56] = [["models/fixture-latest"]]
    entry[64] = [9, 12, 999]
    entry[71] = [None, None, None, None, None, 3, [1, 3]]
    return entry


def test_extended_metadata_keeps_unknown_access_and_unverified_capabilities():
    model = ModelDiscoveryService()._parse_raw_models([[raw_model()]])[0]
    assert model["version"] == "v-fixture" and model["aliases"] == ["fixture-latest"]
    assert model["temperature"] == 0.25 and model["topP"] == 0.8 and model["topK"] == 32
    assert model["defaultThinkingLevel"] == 3 and model["thinkingLevels"] == [1, 3]
    assert model["capabilityCodes"] == [9, 12, 999]
    assert model["capabilitySource"] == "unverified_mapping"
    assert (
        model["paid"] is None
        and model["paidHint"] is None
        and model["accessModes"] is None
    )
    response = _to_gemini_model(model)
    assert response.version == "v-fixture" and response.temperature == 0.25
    assert response.aliases == ["fixture-latest"]


def test_invalid_optional_fields_remain_unknown_instead_of_false_or_free():
    entry = raw_model()
    entry[5] = True
    entry[8] = float("nan")
    entry[9] = 2.0
    entry[10] = 10**400
    entry[56] = [True]
    entry[64] = [True]
    entry[71] = [None] * 5 + [True, "unknown"]
    entry[77] = True
    entry[82] = "Free"
    model = ModelDiscoveryService()._parse_raw_models([[entry]])[0]
    for name in (
        "inputTokenLimit",
        "temperature",
        "topP",
        "topK",
        "aliases",
        "capabilityCodes",
        "defaultThinkingLevel",
        "thinkingLevels",
        "paid",
        "paidHint",
        "accessModes",
    ):
        assert model[name] is None, name
    assert model["capabilitySource"] == "unknown"


@pytest.mark.asyncio
async def test_cache_never_returns_another_accounts_catalog(tmp_path):
    store = AccountStore(tmp_path / "accounts")

    def save(name):
        return store.save_account(
            name=name,
            email=f"{name}@example.invalid",
            storage_state={"cookies": [{"name": "SAPISID", "value": name}]},
        )

    first, second = save("first"), save("second")
    service = ModelDiscoveryService()
    service.bind_account_store(store)

    async def fetch(cookies, user, **kwargs):
        return [{"id": cookies["SAPISID"]}]

    service._fetch_via_http = AsyncMock(side_effect=fetch)
    assert (await service.get_models())[0]["id"] == "first"
    store.set_active_account(second.id)
    assert (await service.get_models())[0]["id"] == "second"
    assert service._fetch_via_http.await_count == 2
    assert (await service.get_models())[0]["id"] == "second"
    assert service._fetch_via_http.await_count == 2
    save("second")
    await service.get_models()
    assert service._fetch_via_http.await_count == 3
    current = store.get_account(second.id)
    assert current is not None
    store.save_tier_result(second.id, current.credential_version, tier=BenefitTier.PRO)
    await service.get_models()
    assert service._fetch_via_http.await_count == 4
    assert service._fetch_via_http.call_args.kwargs["tier"] is BenefitTier.PRO
    store.set_active_account(first.id)
    service._fetch_via_http.side_effect = RuntimeError("fixture offline")
    models = await service.get_models(force_refresh=True)
    assert all(model["id"] != "second" for model in models)
    assert all(model["metadataSource"] == "fallback" for model in models)


@pytest.mark.asyncio
async def test_failed_refresh_retains_only_same_identity_and_returns_defensive_copies():
    service = ModelDiscoveryService()
    service._fetch_via_http = AsyncMock(
        return_value=[{"id": "fixture", "aliases": ["alias"]}]
    )
    rows = await service.get_models(cookies={"SAPISID": "fixture"})
    aliases = rows[0]["aliases"]
    assert isinstance(aliases, list)
    aliases.append("corruption")
    assert (await service.get_models(cookies={"SAPISID": "fixture"}))[0]["aliases"] == [
        "alias"
    ]
    service._fetch_via_http.side_effect = RuntimeError("fixture offline")
    stale = await service.get_models(cookies={"SAPISID": "fixture"}, force_refresh=True)
    assert stale[0]["id"] == "fixture" and stale[0]["metadataSource"] == "stale"
    assert (await service.get_models(cookies={"SAPISID": "fixture"}))[0][
        "metadataSource"
    ] == "stale"
    other = await service.get_models(cookies={"SAPISID": "other"})
    assert all(row["id"] != "fixture" for row in other)


@pytest.mark.asyncio
async def test_public_session_page_uses_its_own_sub_account_and_remains_leased():
    page = MagicMock(spec=CDPPage)
    page.is_closed.return_value = False
    page.get_cookies = AsyncMock(return_value=[{"name": "SAPISID", "value": "fixture"}])
    context = SessionContext(page, "fixture", "2", None, {})
    session = BrowserSession(9222)
    session.set_ready_context(context)

    async def evaluate(script, *, args, **kwargs):
        assert context.in_flight == 1
        assert args["headers"]["X-Goog-AuthUser"] == "2"
        assert "Authorization" in args["headers"]
        return {"ok": True, "data": [[raw_model()]]}

    page.evaluate = AsyncMock(side_effect=evaluate)
    models = await ModelDiscoveryService().get_models(session=session)
    assert models[0]["id"] == "fixture" and models[0]["metadataSource"] == "rpc"
    assert context.in_flight == 0 and context.drain_event.is_set()


@pytest.mark.asyncio
async def test_aliases_resolve_once_and_conflicts_fail_explicitly():
    service = ModelDiscoveryService()
    service.get_models = AsyncMock(
        return_value=[{"id": "canonical", "aliases": ["alias"]}]
    )
    found = await service.get_model_by_id("models/alias")
    assert found is not None and found["id"] == "canonical"
    service.get_models.return_value.append({"id": "other", "aliases": ["alias"]})
    with pytest.raises(ModelAliasAmbiguous):
        await service.get_model_by_id("alias")


@pytest.mark.asyncio
async def test_alias_conflicts_use_the_same_contract_for_both_model_apis(monkeypatch):
    monkeypatch.setattr(
        model_discovery,
        "get_models",
        AsyncMock(
            return_value=[
                {"id": "one", "aliases": ["alias"]},
                {"id": "two", "aliases": ["alias"]},
            ]
        ),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        for path in ("/v1beta/models/alias", "/v1/models/alias"):
            response = await client.get(path)
            assert response.status_code == 409
        response = await client.get("/v1/models")
    assert set(response.json()["data"][0]) == {"id", "object", "created", "owned_by"}


def test_wire_encoding_does_not_mutate_shared_tool_templates():
    first = build_tools_from_names(["google_search"])
    first[0].clear()
    assert build_tools_from_names(["google_search"]) == [
        [None, None, None, [None, [[]]]]
    ]


def test_empty_model_identifiers_and_aliases_are_not_accepted():
    entry = raw_model()
    entry[0] = "models/"
    service = ModelDiscoveryService()
    assert service._parse_raw_models([[entry]]) == []
    entry[0] = "models/fixture"
    entry[56] = ["models/"]
    assert service._parse_raw_models([[entry]])[0]["aliases"] is None
