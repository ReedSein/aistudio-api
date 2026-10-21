"""Account registration must survive failed writes, concurrency and old metadata."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock, MagicMock

import pytest

from aistudio_api.api.state import runtime_state
from aistudio_api.application.account_orchestrator import try_switch_account
from aistudio_api.application.account_rotator import AccountRotator
from aistudio_api.application.account_service import AccountService
from aistudio_api.application.generation_errors import handle_attempt_exception
from aistudio_api.domain.errors import AuthError, SessionExpiredError
from aistudio_api.infrastructure.account import account_store as module
from aistudio_api.infrastructure.account.account_store import AccountStore
from aistudio_api.infrastructure.gateway.session import BrowserSession


def save(store, suffix="one"):
    return store.save_account(
        name=suffix,
        email=f"{suffix}@example.invalid",
        storage_state={"cookies": [{"name": "fixture", "value": suffix}]},
    )


def active(store: AccountStore):
    account = store.get_active_account()
    assert account is not None
    return account


@pytest.mark.parametrize(
    "operation", ["rename", "email", "activate", "import", "delete", "legacy"]
)
def test_failed_write_preserves_registry_and_credentials(
    tmp_path, monkeypatch, operation
):
    store = AccountStore(tmp_path / "accounts")
    account = (
        store.save_account(name="one", email=None, storage_state={"cookies": []})
        if operation == "legacy"
        else save(store)
    )
    directory = store._accounts_dir
    before_registry = (directory / "registry.json").read_bytes()
    path = store.get_auth_path(account.id)
    assert path is not None
    before_auth = json.loads(path.read_text())
    if operation == "legacy":
        # Old versions may leave the only known email in meta.json.
        (path.parent / "meta.json").write_text(
            json.dumps({"email": "legacy@example.invalid"})
        )
    original = module._atomic_write_json

    def fail_registry(target, data):
        if target.name == "registry.json":
            raise OSError("fixture disk full")
        return original(target, data)

    monkeypatch.setattr(module, "_atomic_write_json", fail_registry)
    with pytest.raises(OSError, match="disk full"):
        if operation == "rename":
            store.update_account(account.id, "changed")
        elif operation == "email":
            store.update_account_email(account.id, "changed@example.invalid")
        elif operation == "activate":
            store.set_active_account(account.id)
        elif operation == "import":
            save(store)
        elif operation == "delete":
            store.delete_account(account.id)
        else:
            store.reconcile_legacy_metadata()
    assert store.get_account(account.id) == account
    assert (directory / "registry.json").read_bytes() == before_registry
    assert json.loads(path.read_text()) == before_auth


def test_corrupt_registry_is_never_replaced_with_an_empty_one(tmp_path):
    store = AccountStore(tmp_path / "accounts")
    path = store._accounts_dir / "registry.json"
    path.write_text("invalid fixture JSON")
    with pytest.raises(ValueError, match="Cannot parse"):
        save(store)
    assert path.read_text() == "invalid fixture JSON"


def test_concurrent_imports_keep_all_registrations(tmp_path):
    store = AccountStore(tmp_path / "accounts")
    with ThreadPoolExecutor(max_workers=4) as pool:
        accounts = list(pool.map(lambda i: save(store, str(i)), range(12)))
    assert {a.id for a in store.list_accounts()} == {a.id for a in accounts}
    assert (
        len(json.loads((store._accounts_dir / "registry.json").read_text())["accounts"])
        == 12
    )


def test_reads_are_pure_and_legacy_reconciliation_is_explicit(tmp_path):
    store = AccountStore(tmp_path / "accounts")
    account = store.save_account(name="old", email=None, storage_state={"cookies": []})
    path = store.get_auth_path(account.id)
    assert path is not None
    (path.parent / "meta.json").write_text(
        json.dumps(
            {"email": "legacy@example.invalid", "name": "stale name", "auth_user": "9"}
        )
    )
    before = (store._accounts_dir / "registry.json").read_bytes()
    assert active(store).email is None
    store.get_active_account()
    assert (store._accounts_dir / "registry.json").read_bytes() == before
    assert store.reconcile_legacy_metadata() == 1
    updated = store.get_account(account.id)
    assert updated is not None
    assert updated.email == "legacy@example.invalid"
    assert updated.name == "old" and updated.auth_user == "0"
    assert store.reconcile_legacy_metadata() == 0


def test_reimport_registers_immediately_without_resetting_quotas(tmp_path):
    store = AccountStore(tmp_path / "accounts")
    rotator = AccountRotator(store)
    service = AccountService(store, rotator)
    account = service.save_account_from_cookies(
        name="one", email="one@example.invalid", storage_state={"cookies": []}
    )
    assert rotator.is_account_available(account.id)
    rotator.record_rate_limited(account.id, model="fixture", scope="day")
    rotator.record_session_expired(account.id)
    stats = rotator.get_account_stats(account.id)
    assert stats is not None
    before = dict(stats.model_requests)
    previous_version = account.credential_version
    account = service.save_account_from_cookies(
        name="one", email="one@example.invalid", storage_state={"cookies": []}
    )
    assert account.credential_version != previous_version
    assert not stats.session_expired and stats.auth_cooldown == 0
    assert stats.model_requests == before
    assert not rotator.is_account_available(account.id, model="fixture")
    assert (
        store.save_refreshed_cookies(
            account.id, previous_version, [{"name": "stale", "value": "fixture"}]
        )
        is False
    )


@pytest.mark.asyncio
async def test_activation_failure_never_publishes_failed_account(tmp_path):
    store = AccountStore(tmp_path / "accounts")
    old, target = save(store), save(store, "two")
    service = AccountService(store)
    session = MagicMock(spec=BrowserSession)
    session.switch_auth = AsyncMock(
        side_effect=RuntimeError("fixture preparation failed")
    )
    with pytest.raises(RuntimeError, match="preparation failed"):
        await service.activate_account(target.id, session)
    assert active(store).id == old.id


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [AuthError, SessionExpiredError])
async def test_old_auth_failure_cannot_lock_reimported_credentials(
    tmp_path, monkeypatch, error
):
    store = AccountStore(tmp_path / "accounts")
    rotator = AccountRotator(store)
    service = AccountService(store, rotator)
    old = service.save_account_from_cookies(
        name="one", email="one@example.invalid", storage_state={"cookies": []}
    )
    service.save_account_from_cookies(
        name="one", email="one@example.invalid", storage_state={"cookies": []}
    )
    client = MagicMock()
    monkeypatch.setattr(runtime_state, "account_service", service)
    monkeypatch.setattr(runtime_state, "rotator", rotator)
    monkeypatch.setattr(runtime_state, "client", client)
    assert await handle_attempt_exception(
        error("fixture"),
        attempt=0,
        model_path="fixture",
        normalized_model="fixture",
        client=client,
        attempt_account=old,
    )
    assert await try_switch_account(
        model="fixture",
        failed_account_id=old.id,
        is_session_expired=True,
        failed_credential_version=old.credential_version,
    )
    stats = rotator.get_account_stats(old.id)
    assert stats is not None and stats.auth_cooldown == 0 and not stats.session_expired


@pytest.mark.asyncio
async def test_activation_publishes_only_after_preparation_and_rejects_stale_import(
    tmp_path,
):
    store = AccountStore(tmp_path / "accounts")
    old, target = save(store), save(store, "two")
    service = AccountService(store)
    session = MagicMock(spec=BrowserSession)

    async def prepare(path, *, publish):
        assert active(store).id == old.id
        save(store, "two")
        publish()

    session.switch_auth = AsyncMock(side_effect=prepare)
    with pytest.raises(ValueError, match="credentials changed"):
        await service.activate_account(target.id, session)
    assert active(store).id == old.id
