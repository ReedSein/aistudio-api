"""One request never retries an account it has already failed on."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from aistudio_api.api.state import runtime_state
from aistudio_api.application.account_rotator import AccountRotator
from aistudio_api.application.account_service import AccountService
from aistudio_api.application.generation_errors import (
    RequestAccountFailures,
    handle_attempt_exception,
)
from aistudio_api.domain.accounts import BenefitTier
from aistudio_api.domain.errors import AuthError, PermissionDeniedError, classify_error
from aistudio_api.infrastructure.account.account_store import AccountMeta, AccountStore


@pytest.fixture
def pool(tmp_path, monkeypatch):
    store = AccountStore(tmp_path / "accounts")
    rotator = AccountRotator(store)
    service = AccountService(store, rotator)
    accounts = [
        service.save_account_from_cookies(
            name=name, email=f"{name}@example.invalid", storage_state={"cookies": []}
        )
        for name in ("a", "b", "c")
    ]
    store.set_active_account(accounts[0].id)

    async def switch_auth(_path, *, publish):
        publish()

    session = MagicMock()
    session.switch_auth = AsyncMock(side_effect=switch_auth)
    client = MagicMock()
    client.session = session
    monkeypatch.setattr(runtime_state, "account_service", service)
    monkeypatch.setattr(runtime_state, "rotator", rotator)
    monkeypatch.setattr(runtime_state, "client", client)
    return store, rotator, service, client, accounts


def with_tier(store: AccountStore, account: AccountMeta, tier: BenefitTier):
    updated = store.save_tier_result(
        account.id, account.credential_version, tier=tier, error=None
    )
    assert updated is not None
    return updated


def message(exc: HTTPException) -> str:
    return str(exc.detail)


def test_permission_denied_is_its_own_auth_error_kind():
    assert isinstance(classify_error(403, ""), PermissionDeniedError)
    assert isinstance(classify_error(200, '[,[7,"denied"]]'), PermissionDeniedError)
    unauthenticated = classify_error(401, "")
    assert isinstance(unauthenticated, AuthError)
    assert not isinstance(unauthenticated, PermissionDeniedError)


@pytest.mark.asyncio
async def test_rotator_never_returns_an_excluded_account_even_in_fallback(pool):
    _, rotator, _, _, (a, b, c) = pool
    for account in (a, b, c):
        rotator.record_auth_error(account.id, model="m")
    chosen = await rotator.get_next_account(
        model="m",
        current_account_id=c.id,
        failed_account_id=c.id,
        excluded_account_ids=frozenset({a.id, c.id}),
    )
    assert chosen is not None and chosen.id == b.id
    assert (
        await rotator.get_next_account(
            model="m",
            current_account_id=c.id,
            failed_account_id=c.id,
            excluded_account_ids=frozenset({a.id, b.id, c.id}),
        )
        is None
    )


@pytest.mark.asyncio
async def test_known_tier_denials_try_each_account_once_then_summarize(pool):
    store, _, service, client, accounts = pool
    for account in accounts:
        with_tier(store, account, BenefitTier.PRO)
    failures = RequestAccountFailures()
    visited = []
    with pytest.raises(HTTPException) as raised:
        for attempt in range(5):
            current = service.get_active_account()
            assert current is not None
            visited.append(current.id)
            assert await handle_attempt_exception(
                PermissionDeniedError("禁止访问: denied"),
                attempt=attempt,
                model_path="m",
                normalized_model="m",
                client=client,
                attempt_account=current,
                failures=failures,
            )
    assert visited == [account.id for account in accounts]
    assert raised.value.status_code == 403
    summary = message(raised.value)
    assert all(account.id in summary for account in accounts)


@pytest.mark.asyncio
async def test_unknown_tier_denial_stops_without_switching_or_cooldown(pool):
    _, rotator, service, client, (a, _, _) = pool
    with pytest.raises(HTTPException) as raised:
        await handle_attempt_exception(
            PermissionDeniedError("禁止访问: The caller does not have permission"),
            attempt=0,
            model_path="m",
            normalized_model="m",
            client=client,
            attempt_account=service.get_account(a.id),
            failures=RequestAccountFailures(),
        )
    assert raised.value.status_code == 403
    assert "刷新等级" in message(raised.value)
    active = service.get_active_account()
    assert active is not None and active.id == a.id
    stats = rotator.get_account_stats(a.id)
    assert stats is None or stats.auth_cooldown == 0
    client.session.switch_auth.assert_not_called()


@pytest.mark.asyncio
async def test_unknown_tier_authentication_failure_still_switches(pool):
    _, _, service, client, (a, _, _) = pool
    assert await handle_attempt_exception(
        AuthError("认证失败: expired"),
        attempt=0,
        model_path="m",
        normalized_model="m",
        client=client,
        attempt_account=service.get_account(a.id),
        failures=RequestAccountFailures(),
    )
    active = service.get_active_account()
    assert active is not None and active.id != a.id


class DenyingClient:
    """Fake gateway: every attempt reports its account, then upstream says 403."""

    def __init__(self, service: AccountService, client: MagicMock):
        self._service = service
        self.session = client.session
        self.clear_templates = MagicMock()
        self.calls: list[str] = []

    def _deny(self, on_account) -> None:
        account = self._service.get_active_account()
        assert account is not None
        self.calls.append(account.id)
        on_account(account)
        raise PermissionDeniedError("禁止访问: The caller does not have permission")

    async def generate_content(self, *, on_account, **_):
        self._deny(on_account)

    async def stream_generate_content(self, *, on_account, **_):
        self._deny(on_account)
        yield "done", None


async def run_generation(client, stream: bool):
    from aistudio_api.api.schemas import GeminiGenerateContentRequest
    from aistudio_api.application.generation_service import (
        generate_content,
        stream_content,
    )

    req = GeminiGenerateContentRequest.model_validate(
        {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]}
    )
    if stream:
        async for _ in stream_content("models/fixture", req, client):
            pass
    else:
        await generate_content("models/fixture", req, client)


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_shared_generation_loop_stops_at_first_unknown_tier_denial(pool, stream):
    _, _, service, client, _ = pool
    fake = DenyingClient(service, client)
    with pytest.raises(HTTPException) as raised:
        await run_generation(fake, stream)
    assert len(fake.calls) == 1
    assert raised.value.status_code == 403
    assert "刷新等级" in message(raised.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_shared_generation_loop_never_revisits_a_failed_account(pool, stream):
    store, _, service, client, accounts = pool
    for account in accounts:
        with_tier(store, account, BenefitTier.ULTRA)
    fake = DenyingClient(service, client)
    with pytest.raises(HTTPException) as raised:
        await run_generation(fake, stream)
    assert sorted(fake.calls) == sorted(account.id for account in accounts)
    assert len(fake.calls) == len(set(fake.calls))
    assert raised.value.status_code == 403


def test_startup_warning_lists_only_accounts_without_a_confirmed_tier(pool):
    store, _, service, _, (a, b, c) = pool
    with_tier(store, b, BenefitTier.FREE)
    assert {account.id for account in service.warn_unconfirmed_tiers()} == {
        a.id,
        c.id,
    }
