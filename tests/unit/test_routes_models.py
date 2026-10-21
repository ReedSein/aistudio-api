import httpx
import pytest

from aistudio_api.api.app import app
from aistudio_api.config import settings


def _build_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


@pytest.mark.asyncio
async def test_list_models_returns_gemini_format(monkeypatch):
    monkeypatch.setattr(settings, "api_keys", frozenset())
    async with _build_client() as client:
        response = await client.get("/v1beta/models")
        assert response.status_code == 200
        data = response.json()
        assert "models" in data
        assert isinstance(data["models"], list)
        assert len(data["models"]) > 0

        first = data["models"][0]
        assert first["name"].startswith("models/")
        assert "displayName" in first
        assert "supportedGenerationMethods" in first
        assert "generateContent" in first["supportedGenerationMethods"]


@pytest.mark.asyncio
async def test_get_single_model_returns_model_object(monkeypatch):
    monkeypatch.setattr(settings, "api_keys", frozenset())
    async with _build_client() as client:
        response = await client.get("/v1beta/models/gemini-3.7-flash")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "models/gemini-3.7-flash"
        assert "displayName" in data
        assert "supportedGenerationMethods" in data


@pytest.mark.asyncio
async def test_v1_models_returns_openai_catalog(monkeypatch):
    monkeypatch.setattr(settings, "api_keys", frozenset())
    async with _build_client() as client:
        response = await client.get("/v1/models")
        assert response.status_code == 200
        payload = response.json()
        assert payload["object"] == "list"
        assert payload["data"]
        assert payload["data"][0]["object"] == "model"
        assert not payload["data"][0]["id"].startswith("models/")


@pytest.mark.asyncio
async def test_models_endpoint_accepts_query_param_key(monkeypatch):
    monkeypatch.setattr(settings, "api_keys", frozenset({"my-key"}))
    async with _build_client() as client:
        # Unauthorized without key
        res_unauth = await client.get("/v1beta/models")
        assert res_unauth.status_code == 401

        # Authorized with ?key=
        res_auth = await client.get("/v1beta/models?key=my-key")
        assert res_auth.status_code == 200
        assert "models" in res_auth.json()


@pytest.mark.asyncio
async def test_get_single_model_not_found_returns_404(monkeypatch):
    monkeypatch.setattr(settings, "api_keys", frozenset())
    async with _build_client() as client:
        response = await client.get("/v1beta/models/non-existent-unknown-model-12345")
        assert response.status_code == 404
        data = response.json()
        assert "error" in data
        assert data["error"]["code"] == 404
        assert data["error"]["message"] == "Model not found"
        assert data["error"]["status"] == "NOT_FOUND"


@pytest.mark.asyncio
async def test_model_discovery_uses_registered_credentials_when_page_is_not_ready(
    tmp_path,
):
    from unittest.mock import AsyncMock, MagicMock

    from aistudio_api.infrastructure.account.account_store import AccountStore
    from aistudio_api.infrastructure.gateway.model_discovery import (
        ModelDiscoveryService,
    )

    store = AccountStore(tmp_path / "accounts")
    store.save_account(
        name="fixture",
        email=None,
        auth_user="2",
        storage_state={"cookies": [{"name": "SAPISID", "value": "test_sapisid"}]},
    )
    service = ModelDiscoveryService()
    service.bind_account_store(store)
    session = MagicMock()
    session.active_context = None
    rows = [
        {
            "id": "dynamic-gemini-test",
            "displayName": "Dynamic Gemini Test",
            "supportedGenerationMethods": ["generateContent"],
        }
    ]
    service._fetch_via_http = AsyncMock(return_value=rows)
    models = await service.get_models(session=session, force_refresh=True)
    assert models == [{**rows[0], "metadataSource": "rpc"}]
    call_cookies, call_auth_user = service._fetch_via_http.call_args.args
    assert call_cookies["SAPISID"] == "test_sapisid"
    assert call_auth_user == "2"
