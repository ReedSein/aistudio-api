"""Tests for the userscript hosting route."""

from __future__ import annotations

import httpx
import pytest

from aistudio_api.api.app import app


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


@pytest.mark.asyncio
async def test_userscript_serves_script_with_rewritten_self_url():
    async with _client() as client:
        resp = await client.get("/userscript/aistudio-cookie.user.js")
    assert resp.status_code == 200
    assert "javascript" in resp.headers["content-type"]
    body = resp.text
    assert "==UserScript==" in body
    assert "__AISTUDIO_SELF_URL__" not in body
    assert "http://test/userscript/aistudio-cookie.user.js" in body


@pytest.mark.asyncio
async def test_userscript_not_swallowed_by_spa_fallback():
    async with _client() as client:
        resp = await client.get(
            "/userscript/aistudio-cookie.user.js", headers={"Accept": "text/html"}
        )
    assert resp.status_code == 200
    assert "<!DOCTYPE html>" not in resp.text
    assert "==UserScript==" in resp.text
