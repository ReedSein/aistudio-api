"""Tests for system and configuration routes."""

from __future__ import annotations

import httpx
import pytest
import yaml

from aistudio_api.api.app import app


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


@pytest.mark.asyncio
async def test_health_endpoint():
    async with _client() as client:
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


@pytest.mark.asyncio
async def test_auth_check_endpoint():
    async with _client() as client:
        resp = await client.get("/auth/check")
        assert resp.status_code == 200
        data = resp.json()
        assert "auth_enabled" in data


@pytest.mark.asyncio
async def test_spa_endpoints():
    import asyncio

    async with _client() as client:
        responses = await asyncio.gather(
            *[
                client.get(path, headers={"Accept": "text/html"})
                for path in ("/", "/login", "/accounts", "/settings")
            ]
        )
        for resp in responses:
            assert resp.status_code == 200
            assert "<!DOCTYPE html>" in resp.text
            assert "Accept" in resp.headers.get("Vary", "")
            assert "no-store" in resp.headers.get("Cache-Control", "")


@pytest.mark.asyncio
async def test_get_config_endpoint():
    async with _client() as client:
        resp = await client.get("/config")
        assert resp.status_code == 200
        data = resp.json()
        assert "port" in data
        assert "browser_port" in data
        assert "yaml_content" in data


@pytest.mark.asyncio
async def test_update_config_yaml_validation():
    async with _client() as client:
        resp = await client.put(
            "/config/yaml", json={"yaml_content": "invalid: [yaml: broken"}
        )
        assert resp.status_code == 400

        resp_non_dict = await client.put(
            "/config/yaml", json={"yaml_content": "- item1\n- item2"}
        )
        assert resp_non_dict.status_code == 400


@pytest.mark.asyncio
async def test_rotation_status_and_clear_cooldown():
    from unittest.mock import MagicMock

    from aistudio_api.api.state import runtime_state
    from aistudio_api.application.account_rotator import AccountRotator

    mock_rotator = MagicMock(spec=AccountRotator)
    mock_rotator.get_all_stats.return_value = {"acc_1": {"requests": 1}}

    runtime_state.rotator = mock_rotator
    async with _client() as client:
        resp = await client.get("/rotation")
        assert resp.status_code == 200
        data = resp.json()
        assert data["mode"] == "sticky"
        assert "accounts" in data

        resp_clear = await client.post(
            "/rotation/clear-cooldown", json={"account_id": "acc_1"}
        )
        assert resp_clear.status_code == 200
        mock_rotator.clear_cooldown.assert_called_once_with("acc_1", model=None)


@pytest.mark.asyncio
async def test_get_config_includes_runtime_fields():
    async with _client() as client:
        resp = await client.get("/config")
        assert resp.status_code == 200
        data = resp.json()
        assert "timeout_replay" in data
        assert "browser_idle_timeout" in data
        assert "env_overrides" in data


@pytest.mark.asyncio
async def test_update_runtime_config_roundtrip(monkeypatch, tmp_path):
    import os

    from aistudio_api.config import settings

    monkeypatch.setattr(settings, "timeout_stream", settings.timeout_stream)
    monkeypatch.setattr(settings, "timeout_replay", settings.timeout_replay)
    monkeypatch.setattr(settings, "browser_idle_timeout", settings.browser_idle_timeout)

    config_file = tmp_path / "runtime_config.yaml"
    config_file.write_text("api_keys: []\n", encoding="utf-8")
    monkeypatch.setenv("AISTUDIO_CONFIG_FILE", str(config_file))

    async with _client() as client:
        resp = await client.put(
            "/config/runtime",
            json={
                "timeout_stream": 300,
                "timeout_replay": 0,
                "browser_idle_timeout": 600,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["timeout_stream"] == 300
        assert data["timeout_replay"] == 0
        assert data["browser_idle_timeout"] == 600
        expected_env = [
            name
            for name, env_name in (
                ("timeout_stream", "AISTUDIO_TIMEOUT_STREAM"),
                ("timeout_replay", "AISTUDIO_TIMEOUT_REPLAY"),
                ("browser_idle_timeout", "AISTUDIO_BROWSER_IDLE_TIMEOUT"),
            )
            if os.getenv(env_name)
        ]
        assert data["env_overrides"] == expected_env

        written = yaml.safe_load(config_file.read_text(encoding="utf-8"))
        assert written["runtime"]["timeout_stream"] == 300
        assert written["runtime"]["timeout_replay"] == 0
        assert written["runtime"]["browser_idle_timeout"] == 600

        cfg = (await client.get("/config")).json()
        assert cfg["timeout_stream"] == 300
        assert cfg["timeout_replay"] == 0

        # 应用层的 RequestValidationError 处理器把参数校验错误统一转换为 400
        negative = await client.put("/config/runtime", json={"timeout_stream": -1})
        assert negative.status_code == 400

        too_large = await client.put(
            "/config/runtime", json={"browser_idle_timeout": 100000}
        )
        assert too_large.status_code == 400


@pytest.mark.asyncio
async def test_version_endpoint_reports_a_formal_update(monkeypatch):
    from termux_release import ReleaseCheck

    from aistudio_api.infrastructure import update_channel

    monkeypatch.setenv("AISTUDIO_INSTALL_CHANNEL", "stable")
    monkeypatch.setattr(
        update_channel,
        "check_release",
        lambda _root, timeout=30.0: ReleaseCheck(
            "newer", "termux-v1.0.0", "termux-v1.2.0"
        ),
    )
    async with _client() as client:
        resp = await client.get("/version")
        assert resp.status_code == 200
        data = resp.json()
        assert data["channel"] == "stable"
        assert data["state"] == "newer"
        assert data["current"] == "termux-v1.0.0"
        assert data["latest"] == "termux-v1.2.0"
        assert data["release_url"].endswith("/releases/tag/termux-v1.2.0")


@pytest.mark.asyncio
async def test_version_endpoint_reports_development_channel(monkeypatch):
    monkeypatch.setenv("AISTUDIO_INSTALL_CHANNEL", "development")
    async with _client() as client:
        resp = await client.get("/version")
        assert resp.status_code == 200
        data = resp.json()
        assert data["channel"] == "development"
        assert data["state"] is None
        assert data["release_url"] is None
