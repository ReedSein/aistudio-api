"""Client-facing protocol, content fidelity and streaming lifecycle checks."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from aistudio_api.api.app import app
from aistudio_api.api.openai_schemas import OpenAIChatRequest
from aistudio_api.api.state import runtime_state
from aistudio_api.application import generation_service
from aistudio_api.application.openai_request import to_gemini_request
from aistudio_api.application.openai_response import completion_stream
from aistudio_api.config import settings
from aistudio_api.domain.errors import RequestError
from aistudio_api.domain.models import Candidate, ModelOutput
from aistudio_api.infrastructure.gateway.client import AIStudioClient


@pytest.fixture
def gateway(monkeypatch):
    client = MagicMock(spec=AIStudioClient)
    client._session = None
    client.generate_content = AsyncMock(
        return_value=ModelOutput(
            candidates=[
                Candidate(text="你好，测试回复。", thinking="简短思考", finish_reason=1)
            ],
            usage={"prompt_tokens": 7, "completion_tokens": 5, "total_tokens": 12},
        )
    )
    monkeypatch.setattr(runtime_state, "client", client)
    monkeypatch.setattr(runtime_state, "account_service", None)
    monkeypatch.setattr(runtime_state, "rotator", None)
    monkeypatch.setattr(settings, "api_keys", set())
    monkeypatch.setattr(settings, "web_password", None)
    monkeypatch.setattr(generation_service, "ensure_active_account", AsyncMock())
    return client


@pytest.fixture
async def http(gateway):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


def request(**extra):
    return {
        "model": "gemini-test",
        "messages": [{"role": "user", "content": "你好"}],
        **extra,
    }


async def test_normal_reply_and_native_route_share_generation(http, gateway):
    reply = await http.post("/v1/chat/completions", json=request())
    assert reply.status_code == 200
    result = reply.json()
    assert result["object"] == "chat.completion"
    assert result["model"] == "gemini-test"
    assert result["choices"][0]["message"]["content"] == "你好，测试回复。"
    assert result["choices"][0]["message"]["reasoning_content"] == "简短思考"
    assert result["choices"][0]["finish_reason"] == "stop"
    assert result["usage"]["total_tokens"] == 12
    native = await http.post(
        "/v1beta/models/gemini-test:generateContent",
        json={"contents": [{"role": "user", "parts": [{"text": "你好"}]}]},
    )
    assert native.status_code == 200
    assert (
        native.json()["candidates"][0]["content"]["parts"][-1]["text"]
        == "你好，测试回复。"
    )
    assert gateway.generate_content.await_count == 2


async def test_client_parameters_zero_aliases_unknown_and_names(http, gateway):
    messages = [
        {"role": "system", "content": "系统A"},
        {"role": "developer", "content": "系统B"},
        {"role": "user", "name": "Alice", "content": "第一句"},
        {"role": "assistant", "content": "第二句"},
        {"role": "user", "content": "第三句"},
    ]
    reply = await http.post(
        "/v1/chat/completions",
        json=request(
            messages=messages,
            temperature=0,
            top_p=0,
            top_k=0,
            max_tokens=44,
            max_completion_tokens=77,
            presence_penalty=0,
            frequency_penalty=0,
            stop="结束",
            seed=1,
            logit_bias={"12": 80},
            n=4,
            reasoning_effort="minimal",
            tools=[],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "out",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {"ok": {"type": "boolean"}},
                    },
                },
            },
        ),
    )
    assert reply.status_code == 200
    assert len(reply.json()["choices"]) == 1
    args = gateway.generate_content.call_args.kwargs
    assert args["temperature"] == args["top_p"] == args["top_k"] == 0
    assert args["max_tokens"] == 77
    assert args["tools"] == []
    assert [part.text for part in args["system_instruction_content"].parts] == [
        "系统A",
        "系统B",
    ]
    assert "Alice" in args["contents"][0].parts[0].text
    assert args["contents"][0].parts[-1].text == "第一句"
    assert args["contents"][-1].parts[0].text == "第三句"
    overrides = args["generation_config_overrides"]
    assert overrides["stop_sequences"] == ["结束"]
    assert overrides["presence_penalty"] == 0
    assert overrides["thinking_config"][-1] == 4
    assert overrides["response_mime_type"] == "application/json"


async def test_luker_minimal_tool_history_retains_native_signature(http, gateway):
    gateway.generate_content.return_value = ModelOutput(
        candidates=[
            Candidate(
                function_calls=[
                    {
                        "name": "lookup",
                        "args": {"city": "杭州"},
                        "call_id": "native-7",
                        "thought_signature": "real-upstream-signature",
                    }
                ],
                finish_reason=1,
            )
        ]
    )
    first = (
        await http.post(
            "/v1/chat/completions",
            json=request(
                tools=[
                    {
                        "type": "function",
                        "function": {
                            "name": "lookup",
                            "parameters": {"type": "object"},
                        },
                    }
                ]
            ),
        )
    ).json()
    call = first["choices"][0]["message"]["tool_calls"][0]
    assert first["choices"][0]["finish_reason"] == "tool_calls"
    # Custom clients reconstruct only these fields; arbitrary extensions vanish.
    reconstructed = {key: call[key] for key in ("id", "type", "function")}
    gateway.generate_content.return_value = ModelOutput(
        candidates=[Candidate(text="杭州晴", finish_reason=1)]
    )
    second = await http.post(
        "/v1/chat/completions",
        json=request(
            messages=[
                {"role": "user", "content": "杭州天气"},
                {"role": "assistant", "content": None, "tool_calls": [reconstructed]},
                {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": '{"weather":"晴"}',
                },
            ]
        ),
    )
    assert second.status_code == 200
    contents = gateway.generate_content.call_args.kwargs["contents"]
    assert contents[1].parts[0].function_call == (
        "lookup",
        {"city": "杭州"},
        "native-7",
    )
    assert contents[1].parts[0].thought_signature == "real-upstream-signature"
    assert contents[2].parts[0].function_response == (
        "lookup",
        {"weather": "晴"},
        "native-7",
    )


async def test_legacy_functions_and_named_choice(gateway):
    converted = await to_gemini_request(
        OpenAIChatRequest.model_validate(
            request(
                functions=[{"name": "lookup", "parameters": {"type": "object"}}],
                function_call={"name": "lookup"},
                messages=[
                    {"role": "user", "content": "查一下"},
                    {
                        "role": "assistant",
                        "function_call": {"name": "lookup", "arguments": "{}"},
                    },
                    {"role": "function", "name": "lookup", "content": "done"},
                ],
            )
        )
    )
    assert converted.tools is not None
    declarations = converted.tools[0].functionDeclarations
    assert declarations is not None and declarations[0]["name"] == "lookup"
    assert converted.toolConfig is not None
    calling_config = converted.toolConfig["functionCallingConfig"]
    assert isinstance(calling_config, dict)
    assert calling_config["allowedFunctionNames"] == ["lookup"]
    tool_result = converted.contents[-1].parts[0].functionResponse
    assert tool_result is not None
    assert tool_result.response == {"result": "done"}


@pytest.mark.parametrize(
    "message",
    [
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64,broken!"},
                }
            ],
        },
        {
            "role": "user",
            "content": [{"type": "video", "video_url": "https://example.com/v"}],
        },
        {
            "role": "user",
            "content": [{"type": "file", "file": {"file_id": "file-unavailable"}}],
        },
        {"role": "tool", "tool_call_id": "orphan", "content": "result"},
        {"role": ["user"], "content": "invalid role"},
        {"role": "user", "content": [{"type": {"unexpected": True}}]},
    ],
)
async def test_unconvertible_content_never_reaches_model(http, gateway, message):
    reply = await http.post("/v1/chat/completions", json=request(messages=[message]))
    assert reply.status_code == 400
    assert reply.json()["error"]["type"] == "invalid_request_error"
    assert reply.json()["error"]["param"].startswith("messages")
    gateway.generate_content.assert_not_awaited()


async def test_auth_models_and_spa_do_not_intercept_api(http, monkeypatch):
    from aistudio_api.api.routes_openai import model_discovery

    monkeypatch.setattr(settings, "api_keys", {"fixture-api-key"})
    missing = await http.get("/v1/models")
    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "invalid_api_key"
    assert missing.headers["www-authenticate"] == "Bearer"
    monkeypatch.setattr(
        model_discovery,
        "get_models",
        AsyncMock(return_value=[{"id": "models/gemini-test"}]),
    )
    headers = {"Authorization": "Bearer fixture-api-key", "Accept": "text/html"}
    listing = await http.get("/v1/models", headers=headers)
    assert listing.json()["data"][0]["id"] == "gemini-test"
    assert listing.json()["data"][0]["owned_by"] == "google"
    monkeypatch.setattr(
        model_discovery, "get_model_by_id", AsyncMock(return_value=None)
    )
    unknown = await http.get("/v1/models/missing", headers=headers)
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "model_not_found"


async def test_body_limits_and_validation_do_not_echo_input(http, gateway, monkeypatch):
    import aistudio_api.api.routes_openai as routes

    invalid = await http.post(
        "/v1/chat/completions", json={"model": 42, "messages": "private-content"}
    )
    assert invalid.status_code == 400
    assert "private-content" not in invalid.text
    monkeypatch.setattr(routes, "MAX_REQUEST_BYTES", 24)

    async def chunks():
        yield b'{"model":"gemini-test",'
        yield b'"messages":[]}'

    oversized = await http.post("/v1/chat/completions", content=chunks())
    assert oversized.status_code == 413
    gateway.generate_content.assert_not_awaited()


async def test_incremental_sse_usage_and_cancellation(gateway):
    waiting = asyncio.Event()
    closed = asyncio.Event()

    async def source(**kwargs):
        try:
            yield "body", "第一段"
            await waiting.wait()
            yield "body", "第二段"
            yield "finish_reason", 1
            yield (
                "usage",
                {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
            )
            yield "done", None
        finally:
            closed.set()

    gateway.stream_generate_content = MagicMock(side_effect=source)
    req = await to_gemini_request(OpenAIChatRequest.model_validate(request()))
    stream = completion_stream("gemini-test", req, gateway, include_usage=True)
    first = await anext(stream)
    assert '"role": "assistant"' in first
    assert "第一段" in await anext(stream)
    assert not waiting.is_set()
    waiting.set()
    tail = [part async for part in stream]
    assert "第二段" in tail[0]
    assert tail[-1] == "data: [DONE]\n\n"
    frames = [json.loads(part[6:]) for part in [first, *tail[:-1]]]
    assert len({frame["id"] for frame in frames}) == 1
    assert frames[-1]["choices"] == [] and frames[-1]["usage"]["total_tokens"] == 5
    assert closed.is_set()
    closed.clear()
    stream = completion_stream("gemini-test", req, gateway, include_usage=False)
    await anext(stream)
    await stream.aclose()
    assert closed.is_set()


async def test_sse_partial_failure_is_not_retried_or_marked_done(http, gateway):
    async def source(**kwargs):
        yield "body", "partial-text"
        raise RequestError(204, "connection interrupted")

    gateway.stream_generate_content = MagicMock(side_effect=source)
    reply = await http.post("/v1/chat/completions", json=request(stream=True))
    assert "partial-text" in reply.text
    assert '"error"' in reply.text
    assert "[DONE]" not in reply.text
    assert gateway.stream_generate_content.call_count == 1


async def test_sse_metadata_only_retry_resets_usage_and_finishes(http, gateway):
    attempts = 0

    async def source(**kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            yield "response_id", "aborted-id"
            yield "usage", {"total_tokens": 9000}
            raise RequestError(204, "empty upstream")
        yield "body", "recovered"
        yield "finish_reason", 1
        yield "done", None

    gateway.stream_generate_content = MagicMock(side_effect=source)
    reply = await http.post(
        "/v1/chat/completions",
        json=request(stream=True, stream_options={"include_usage": True}),
    )
    assert attempts == 2
    assert "recovered" in reply.text and "[DONE]" in reply.text
    assert "9000" not in reply.text and '"usage": null' in reply.text


async def test_full_openai_dialogue_is_captured_with_request_id(
    http, tmp_path, monkeypatch
):
    logs = tmp_path / "openai-logs"
    monkeypatch.setenv("AISTUDIO_LOG_DIR", str(logs))
    prompt = "完整对话" * 2000
    response = await http.post(
        "/v1/chat/completions",
        json=request(messages=[{"role": "user", "content": prompt}]),
        headers={"Authorization": "Bearer fixture-never-export"},
    )
    assert response.status_code == 200
    rows = [
        json.loads(line)
        for file in sorted(logs.glob("events-*.jsonl"))
        for line in file.read_text().splitlines()
    ]
    rows = [
        row for row in rows if row.get("request_id") == response.headers["x-request-id"]
    ]
    raw = "".join(row["text"] for row in rows if row["event"] == "http_request_body")
    assert json.loads(raw)["messages"][0]["content"] == prompt
    assert "你好，测试回复。" in "".join(
        row["text"] for row in rows if row["event"] == "http_response_body"
    )
    assert "fixture-never-export" not in json.dumps(rows)


@pytest.mark.parametrize("missing", ["finish_reason", "done"])
async def test_missing_stream_completion_is_an_error_in_both_protocols(
    http, gateway, missing
):
    async def source(**kwargs):
        yield "body", "unfinished"
        if missing != "finish_reason":
            yield "finish_reason", 1
        if missing != "done":
            yield "done", None

    gateway.stream_generate_content = MagicMock(side_effect=source)
    openai = await http.post("/v1/chat/completions", json=request(stream=True))
    native = await http.post(
        "/v1beta/models/gemini-test:streamGenerateContent",
        json={"contents": [{"parts": [{"text": "hi"}]}]},
    )
    assert '"error"' in openai.text and "[DONE]" not in openai.text
    assert '"error"' in native.text and "finishReason" not in native.text


async def test_late_stream_tool_signature_survives_client_reconstruction(http, gateway):
    from aistudio_api.application.openai_tools import decode_call

    async def source(**kwargs):
        yield (
            "tool_calls",
            [{"name": "lookup", "args": {"x": 1}, "call_id": "native-8"}],
        )
        yield "thought_signature", "late-signature"
        yield "finish_reason", 1
        yield "done", None

    gateway.stream_generate_content = MagicMock(side_effect=source)
    reply = await http.post("/v1/chat/completions", json=request(stream=True))
    frames = [
        json.loads(frame[6:])
        for frame in reply.text.split("\n\n")
        if frame.startswith("data: {")
    ]
    calls = [
        call
        for frame in frames
        for choice in frame.get("choices", [])
        for call in choice["delta"].get("tool_calls", [])
    ]
    assert len(calls) == 1 and calls[0]["index"] == 0
    assert decode_call(calls[0]["id"], "id") == ("native-8", "late-signature")
    assert frames[-1]["choices"][0]["finish_reason"] == "tool_calls"


async def test_catalog_internal_error_has_openai_envelope(http, monkeypatch):
    from aistudio_api.api.routes_openai import model_discovery

    monkeypatch.setattr(
        model_discovery,
        "get_models",
        AsyncMock(side_effect=RuntimeError("private internal detail")),
    )
    reply = await http.get("/v1/models")
    assert reply.status_code == 500
    assert reply.json()["error"]["type"] == "server_error"
    assert "private internal detail" not in reply.text


@pytest.mark.parametrize("status", [429, 503])
async def test_account_preflight_keeps_its_http_status(
    http, gateway, monkeypatch, status
):
    from fastapi import HTTPException

    monkeypatch.setattr(
        generation_service,
        "ensure_active_account",
        AsyncMock(side_effect=HTTPException(status, detail="No usable account")),
    )
    openai = await http.post("/v1/chat/completions", json=request())
    native = await http.post(
        "/v1beta/models/gemini-test:generateContent",
        json={"contents": [{"parts": [{"text": "hi"}]}]},
    )
    assert openai.status_code == native.status_code == status
    assert openai.json()["error"]["code"] == (
        "rate_limit_exceeded" if status == 429 else "server_error"
    )
    assert native.json()["error"]["code"] == status
    gateway.generate_content.assert_not_awaited()
