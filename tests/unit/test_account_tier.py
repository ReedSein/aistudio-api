"""Benefit queries must not turn unknown failures into Free or cross identities."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from aistudio_api.api.app import app
from aistudio_api.api.state import runtime_state
from aistudio_api.application import account_service as service_module
from aistudio_api.application.account_service import AccountService
from aistudio_api.domain.accounts import BenefitTier, TierRefreshError
from aistudio_api.infrastructure.account import (
    account_store as store_module,
    benefit_tier as benefits,
)
from aistudio_api.infrastructure.account.account_store import AccountStore
from aistudio_api.infrastructure.browser.cdp_client import CDPPage
from aistudio_api.infrastructure.gateway.session import BrowserSession
from aistudio_api.infrastructure.gateway.session_state import SessionContext
from aistudio_api.infrastructure.gateway.transport import _ensure_authorization_header


@pytest.fixture
def account_service(tmp_path):
    store = AccountStore(tmp_path / "accounts")
    account = store.save_account(
        name="fixture",
        email=None,
        auth_user="2",
        storage_state={"cookies": [{"name": "SAPISID", "value": "tier-fixture"}]},
    )
    return store, account, AccountService(store)


@pytest.mark.parametrize(
    "raw, tier",
    [
        ("[]", BenefitTier.FREE),
        ("[null]", BenefitTier.FREE),
        ("[0]", BenefitTier.FREE),
        (")]}'\n[1]", BenefitTier.PRO),
        ("[2]", BenefitTier.ULTRA),
        ("[3]", BenefitTier.PLUS),
    ],
)
def test_known_benefit_values(raw, tier):
    assert benefits.parse_benefit_tier(raw) is tier


@pytest.mark.parametrize(
    "raw", ["", "html fixture", "{}", "[true]", "[1.5]", "[99]", '["0"]', "[[1]]"]
)
def test_unknown_benefit_response_is_a_failure(raw):
    with pytest.raises(TierRefreshError, match="invalid_response"):
        benefits.parse_benefit_tier(raw)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status, reason",
    [
        (503, "unavailable"),
        (429, "rate_limited"),
        (403, "authentication"),
        (302, "authentication"),
        (200, None),
    ],
)
async def test_http_query_is_one_attempt_with_the_target_credentials(
    monkeypatch, status, reason
):
    requests = []

    def receive(request):
        requests.append(request)
        return httpx.Response(
            status, text="[1]", headers={"Content-Type": "application/json+protobuf"}
        )

    original = httpx.AsyncClient
    monkeypatch.setattr(
        benefits.httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(receive)),
    )
    state: dict[str, object] = {
        "cookies": [{"name": "SAPISID", "value": "target-fixture"}]
    }
    if reason:
        with pytest.raises(TierRefreshError, match=reason):
            await benefits.query_benefit_tier(state, "2")
    else:
        assert await benefits.query_benefit_tier(state, "2") is BenefitTier.PRO
    assert len(requests) == 1
    assert requests[0].headers["x-goog-authuser"] == "2"
    assert requests[0].headers["cookie"] == "SAPISID=target-fixture"


@pytest.mark.asyncio
async def test_failed_refresh_retains_last_valid_tier_and_timestamp(
    account_service, monkeypatch
):
    store, account, service = account_service
    previous = store.save_tier_result(
        account.id, account.credential_version, tier=BenefitTier.PRO
    )
    assert previous is not None
    monkeypatch.setattr(
        service_module,
        "query_benefit_tier",
        AsyncMock(side_effect=TierRefreshError("unavailable")),
    )
    with pytest.raises(TierRefreshError, match="unavailable"):
        await service.detect_tier(account.id)
    updated = store.get_account(account.id)
    assert updated is not None
    assert updated.tier is BenefitTier.PRO
    assert updated.tier_checked_at == previous.tier_checked_at
    assert updated.tier_last_error == "unavailable"


@pytest.mark.asyncio
async def test_valid_free_result_can_downgrade_a_paid_tier(
    account_service, monkeypatch
):
    store, account, service = account_service
    store.save_tier_result(account.id, account.credential_version, tier=BenefitTier.PRO)
    monkeypatch.setattr(
        service_module, "query_benefit_tier", AsyncMock(return_value=BenefitTier.FREE)
    )
    updated = await service.detect_tier(account.id)
    assert updated.tier is BenefitTier.FREE and updated.tier_checked_at
    assert updated.tier_last_error is None


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["delete", "reimport"])
async def test_changed_credentials_reject_stale_tier_results(
    account_service, monkeypatch, change
):
    store, account, service = account_service

    async def query(state, auth_user):
        assert auth_user == "2"
        assert state["cookies"][0]["value"] == "tier-fixture"
        if change == "delete":
            store.delete_account(account.id)
        else:
            store.save_account(
                name="new",
                email=None,
                storage_state={"cookies": []},
                account_id=account.id,
            )
        return BenefitTier.ULTRA

    monkeypatch.setattr(service_module, "query_benefit_tier", query)
    with pytest.raises(TierRefreshError, match="stale"):
        await service.detect_tier(account.id)
    current = store.get_account(account.id)
    assert current is None or current.tier is None


@pytest.mark.asyncio
async def test_storage_failure_never_publishes_a_new_tier(account_service, monkeypatch):
    store, account, service = account_service
    monkeypatch.setattr(
        service_module, "query_benefit_tier", AsyncMock(return_value=BenefitTier.PRO)
    )
    monkeypatch.setattr(
        store_module,
        "_atomic_write_json",
        MagicMock(side_effect=OSError("fixture disk full")),
    )
    with pytest.raises(TierRefreshError, match="storage"):
        await service.detect_tier(account.id)
    assert store.get_account(account.id) == account


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tier, expected",
    [
        (None, None),
        (BenefitTier.FREE, None),
        (BenefitTier.PLUS, "TIER0"),
        (BenefitTier.PRO, "TIER1"),
        (BenefitTier.ULTRA, "TIER2"),
    ],
)
async def test_header_uses_only_the_explicit_tier(tier, expected, monkeypatch):
    # Even another globally selected Ultra account must not supply a fallback.
    global_service = MagicMock()
    global_service.get_active_account.return_value.tier = BenefitTier.ULTRA
    monkeypatch.setattr(runtime_state, "account_service", global_service)
    page = MagicMock(spec=CDPPage)
    page._cached_cookies = {"SAPISID": "fixture"}
    headers = await _ensure_authorization_header(
        page, {"x-aistudio-g1-tier": "TIER2"}, auth_user="0", tier=tier
    )
    assert headers.get("X-AIStudio-G1-Tier") == expected
    assert headers["X-Goog-AuthUser"] == "0"


@pytest.mark.asyncio
async def test_refresh_changes_the_next_attempt_without_mutating_an_inflight_snapshot(
    account_service,
):
    store, account, _ = account_service
    paid = store.save_tier_result(
        account.id, account.credential_version, tier=BenefitTier.PRO
    )
    assert paid is not None
    page = MagicMock(spec=CDPPage)
    page.is_closed.return_value = False
    ctx = SessionContext(
        page, "fixture", "2", str(store.get_auth_path(account.id)), {}, account=paid
    )
    session = BrowserSession(9222)
    session.bind_account_store(store)
    session.set_ready_context(ctx)
    async with session.request_scope():
        assert session.account_snapshot(ctx) is paid
        store.save_tier_result(
            account.id, account.credential_version, tier=BenefitTier.FREE
        )
        assert session.account_snapshot(ctx) is paid
    async with session.request_scope():
        snapshot = session.account_snapshot(ctx)
        assert snapshot is not None and snapshot.tier is BenefitTier.FREE


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reason, status",
    [
        ("not_found", 404),
        ("stale", 409),
        ("invalid_response", 502),
        ("unavailable", 503),
        ("storage", 503),
        ("authentication", 502),
        ("rate_limited", 429),
    ],
)
async def test_refresh_api_reports_failures_in_the_existing_error_format(
    monkeypatch, reason, status
):
    service = MagicMock(spec=AccountService)
    service.detect_tier = AsyncMock(side_effect=TierRefreshError(reason))
    monkeypatch.setattr(runtime_state, "account_service", service)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/api/accounts/fixture/detect-tier")
    assert response.status_code == status
    assert isinstance(response.json()["error"]["message"], str)
    service.detect_tier.assert_awaited_once_with("fixture")


@pytest.mark.asyncio
async def test_missing_credentials_record_failure_without_querying(
    account_service, monkeypatch
):
    store, account, service = account_service
    path = store.get_auth_path(account.id)
    assert path is not None
    path.unlink()
    query = AsyncMock()
    monkeypatch.setattr(service_module, "query_benefit_tier", query)
    with pytest.raises(TierRefreshError, match="credentials"):
        await service.detect_tier(account.id)
    updated = store.get_account(account.id)
    assert updated is not None and updated.tier_last_error == "credentials"
    assert updated.tier is None and updated.tier_checked_at is None
    query.assert_not_called()


@pytest.mark.asyncio
async def test_account_reads_do_not_probe_and_manual_refresh_keeps_active_identity(
    account_service, monkeypatch
):
    store, first, service = account_service
    target = store.save_account(
        name="target",
        email=None,
        auth_user="1",
        storage_state={"cookies": [{"name": "SAPISID", "value": "target-fixture"}]},
    )
    query = AsyncMock(return_value=BenefitTier.ULTRA)
    monkeypatch.setattr(service_module, "query_benefit_tier", query)
    monkeypatch.setattr(runtime_state, "account_service", service)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/accounts")
        assert response.status_code == 200
        assert response.json()[0]["tier"] is None
        query.assert_not_called()
        response = await client.post(f"/api/accounts/{target.id}/detect-tier")
    assert response.status_code == 200
    assert response.json()["tier"] == "Ultra"
    assert response.json()["tier_checked_at"]
    assert response.json()["tier_last_error"] is None
    query.assert_awaited_once()
    assert query.call_args.args[1] == "1"
    active = store.get_active_account()
    assert active is not None and active.id == first.id
