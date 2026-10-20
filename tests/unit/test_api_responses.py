import pytest

from aistudio_api.api.responses import (
    to_gemini_parts,
    to_gemini_usage_metadata,
)
from aistudio_api.application.api_service_gemini import classify_gemini_error_payload
from aistudio_api.domain.errors import (
    AuthError,
    RequestError,
    SessionExpiredError,
    UsageLimitExceeded,
)
from aistudio_api.domain.models import FunctionCall, FunctionResponse, OutputPart


def test_to_gemini_usage_metadata_uses_visible_and_reasoning_tokens():
    assert to_gemini_usage_metadata(
        {
            "prompt_tokens": 9,
            "completion_tokens": 316,
            "total_tokens": 325,
            "completion_tokens_details": {
                "reasoning_tokens": 290,
                "visible_tokens": 26,
            },
        }
    ).model_dump(mode="json") == {
        "promptTokenCount": 9,
        "candidatesTokenCount": 316,
        "thoughtsTokenCount": 290,
        "totalTokenCount": 325,
    }


def _dump(parts):
    return [part.model_dump(mode="json", exclude_none=True) for part in parts]


def test_to_gemini_parts_serializes_each_part_in_order():
    parts = to_gemini_parts(
        [
            OutputPart(text="想一下", thought=True),
            OutputPart(text="先查天气"),
            OutputPart(
                function_call=FunctionCall(
                    name="getWeather", args={"city": "Shanghai"}, id="c1"
                ),
                thought_signature="SIG-FC",
            ),
            OutputPart(
                function_response=FunctionResponse(
                    name="getWeather", response={"temperature": "24C"}, id="c1"
                )
            ),
            OutputPart(inline_data=("image/png", "cG5nLWJ5dGVz")),
            OutputPart(text="", thought_signature="SIG-TEXT"),
        ]
    )

    assert _dump(parts) == [
        {"text": "想一下", "thought": True},
        {"text": "先查天气"},
        {
            "functionCall": {
                "name": "getWeather",
                "args": {"city": "Shanghai"},
                "id": "c1",
            },
            "thoughtSignature": "SIG-FC",
        },
        {
            "functionResponse": {
                "name": "getWeather",
                "response": {"temperature": "24C"},
                "id": "c1",
            }
        },
        {"inlineData": {"mimeType": "image/png", "data": "cG5nLWJ5dGVz"}},
        {"text": "", "thoughtSignature": "SIG-TEXT"},
    ]


def test_to_gemini_parts_emits_code_execution_parts():
    parts = to_gemini_parts(
        [
            OutputPart(executable_code=("PYTHON", "print(1 + 1)")),
            OutputPart(code_execution_result=("OUTCOME_OK", "2\n")),
        ]
    )

    assert _dump(parts) == [
        {"executableCode": {"language": "PYTHON", "code": "print(1 + 1)"}},
        {"codeExecutionResult": {"outcome": "OUTCOME_OK", "output": "2\n"}},
    ]


def test_classify_gemini_error_payload():
    """Verify exception mapping to Gemini HTTP status and reason code."""
    assert classify_gemini_error_payload(SessionExpiredError("expired")) == (
        401,
        "All accounts have expired sessions. Please import fresh cookies.",
        "UNAUTHENTICATED",
    )
    assert classify_gemini_error_payload(AuthError("auth forbidden")) == (
        403,
        "auth forbidden",
        "PERMISSION_DENIED",
    )
    assert classify_gemini_error_payload(UsageLimitExceeded("quota exceeded")) == (
        429,
        "quota exceeded",
        "RESOURCE_EXHAUSTED",
    )
    assert classify_gemini_error_payload(ValueError("invalid input")) == (
        400,
        "invalid input",
        "INVALID_ARGUMENT",
    )
    assert classify_gemini_error_payload(RequestError(429, "rate limit")) == (
        429,
        "HTTP 429: rate limit",
        "RESOURCE_EXHAUSTED",
    )
    # JSPB bracket array error from upstream Google MakerSuite
    jspb_400 = 'HTTP 400: [,[3,"Invalid value (), Unexpected list for single non-message field.",[["type.googleapis.com/google.rpc.BadRequest"]]]]'
    assert classify_gemini_error_payload(RequestError(400, jspb_400)) == (
        400,
        "Invalid value (), Unexpected list for single non-message field.",
        "INVALID_ARGUMENT",
    )


def test_clean_upstream_error_message():
    from aistudio_api.application.api_service_gemini import clean_upstream_error_message

    assert clean_upstream_error_message("") == ""
    assert clean_upstream_error_message("Normal plain error") == "Normal plain error"
    assert (
        clean_upstream_error_message("HTTP 429: rate limit") == "HTTP 429: rate limit"
    )
    jspb_nested = 'HTTP 400: [,[3,"Please enable tool_config.include_server_side_tool_invocations to use Built-in tools with Function calling.",[["type.googleapis.com/details"]]]]'
    assert (
        clean_upstream_error_message(jspb_nested)
        == "Please enable tool_config.include_server_side_tool_invocations to use Built-in tools with Function calling."
    )
    jspb_404 = 'HTTP 404: [,[5,"Requested entity was not found."]]'
    assert clean_upstream_error_message(jspb_404) == "Requested entity was not found."


@pytest.mark.asyncio
async def test_handle_attempt_exception_retries_ambiguous_rpc_404_in_place():
    from unittest.mock import MagicMock

    from fastapi import HTTPException

    from aistudio_api.application.api_service_gemini import handle_attempt_exception
    from aistudio_api.infrastructure.gateway.client import AIStudioClient

    mock_client = MagicMock(spec=AIStudioClient)
    mock_client.clear_templates = MagicMock()

    # 1. Ambiguous RPC 404 error should be retried in-place without raising
    ambiguous_err = RequestError(
        404,
        "Ambiguous request for service '' and method '/GenerativeService.StreamGenerateContent'. Please use fully qualified (unique) service and method names to call this method.",
    )
    should_retry = await handle_attempt_exception(
        ambiguous_err,
        attempt=0,
        model_path="gemini-3.7-flash",
        normalized_model="models/gemini-3.7-flash",
        client=mock_client,
        has_yielded_data=False,
    )
    assert should_retry is True
    mock_client.clear_templates.assert_called_once()

    # 2. Other legitimate 404 errors (e.g. model not found) MUST NOT be retried
    other_404 = RequestError(404, "Model 'models/nonexistent' not found.")
    with pytest.raises(HTTPException) as exc_info:
        await handle_attempt_exception(
            other_404,
            attempt=0,
            model_path="nonexistent",
            normalized_model="models/nonexistent",
            client=mock_client,
            has_yielded_data=False,
        )
    assert exc_info.value.status_code == 404

    # 3. If data has already been yielded to the client, retry MUST NOT happen
    with pytest.raises(HTTPException):
        await handle_attempt_exception(
            ambiguous_err,
            attempt=0,
            model_path="gemini-3.7-flash",
            normalized_model="models/gemini-3.7-flash",
            client=mock_client,
            has_yielded_data=True,
        )


@pytest.mark.asyncio
async def test_build_gemini_streaming_response_passes_requested_model():
    from unittest.mock import MagicMock

    from aistudio_api.api.schemas import (
        GeminiContent,
        GeminiGenerateContentRequest,
        GeminiPart,
    )
    from aistudio_api.application.api_service_gemini import (
        _build_gemini_streaming_response,
    )
    from aistudio_api.infrastructure.gateway.client import AIStudioClient

    passed_model = None

    async def fake_stream_generate_content(*args, **kwargs):
        nonlocal passed_model
        passed_model = kwargs.get("model")
        yield ("chunk", b"data: test")
        yield ("finish_reason", "STOP")
        yield ("done", None)

    mock_client = MagicMock(spec=AIStudioClient)
    mock_client.stream_generate_content = MagicMock(
        side_effect=fake_stream_generate_content
    )

    req = GeminiGenerateContentRequest(
        contents=[GeminiContent(role="user", parts=[GeminiPart(text="hello")])]
    )
    resp = _build_gemini_streaming_response(
        client=mock_client,
        req=req,
        model_path="gemini-2.5-pro",
    )

    chunks = [chunk async for chunk in resp.body_iterator]
    assert len(chunks) > 0
    assert passed_model == "models/gemini-2.5-pro"


@pytest.mark.asyncio
async def test_streaming_metadata_event_does_not_block_failover():
    """验证在仅接收到 response_id 等内部元数据事件时发生异常，不会误判为已发送数据而阻断重试。"""
    from unittest.mock import MagicMock

    from aistudio_api.api.schemas import (
        GeminiContent,
        GeminiGenerateContentRequest,
        GeminiPart,
    )
    from aistudio_api.application.api_service_gemini import (
        _build_gemini_streaming_response,
    )
    from aistudio_api.domain.errors import RequestError
    from aistudio_api.infrastructure.gateway.client import AIStudioClient

    attempt_count = 0

    async def fake_stream_generate_content(*args, **kwargs):
        nonlocal attempt_count
        attempt_count += 1
        if attempt_count == 1:
            # 首次尝试：仅产生内部元数据事件，尚未发出实际正文 chunk，随即遇到 204/抖动异常
            yield ("response_id", "resp_early")
            raise RequestError(204, "No content")
        # 第二次尝试：正常流式输出
        yield ("parts", [OutputPart(text="Hello world after retry")])
        yield ("finish_reason", "STOP")
        yield ("done", None)

    mock_client = MagicMock(spec=AIStudioClient)
    mock_client.stream_generate_content = MagicMock(
        side_effect=fake_stream_generate_content
    )
    mock_client.clear_templates = MagicMock()

    req = GeminiGenerateContentRequest(
        contents=[GeminiContent(role="user", parts=[GeminiPart(text="hello")])]
    )
    resp = _build_gemini_streaming_response(
        client=mock_client,
        req=req,
        model_path="gemini-3.7-flash",
    )

    chunks = [chunk async for chunk in resp.body_iterator]
    # 验证重试成功完成
    assert attempt_count == 2
    assert any("Hello world after retry" in str(c) for c in chunks)


@pytest.mark.asyncio
async def test_streaming_emits_thought_signature_as_own_chunk():
    """验证流式路径把「空文本 + 签名」作为独立部件原样下发（正文之后、usage 之前）。"""
    from unittest.mock import MagicMock

    from aistudio_api.api.schemas import (
        GeminiContent,
        GeminiGenerateContentRequest,
        GeminiPart,
    )
    from aistudio_api.application.api_service_gemini import (
        _build_gemini_streaming_response,
    )
    from aistudio_api.infrastructure.gateway.client import AIStudioClient

    async def fake_stream_generate_content(*args, **kwargs):
        yield ("parts", [OutputPart(text="Hello")])
        yield ("parts", [OutputPart(text="", thought_signature="SIG-9")])
        yield ("finish_reason", "STOP")
        yield ("done", None)

    mock_client = MagicMock(spec=AIStudioClient)
    mock_client.stream_generate_content = MagicMock(
        side_effect=fake_stream_generate_content
    )

    req = GeminiGenerateContentRequest(
        contents=[GeminiContent(role="user", parts=[GeminiPart(text="hello")])]
    )
    resp = _build_gemini_streaming_response(
        client=mock_client,
        req=req,
        model_path="gemini-3.1-flash-lite",
    )

    chunks = [chunk async for chunk in resp.body_iterator]
    body_idx = next(i for i, c in enumerate(chunks) if '"Hello"' in str(c))
    sig_idx = next(i for i, c in enumerate(chunks) if "SIG-9" in str(c))
    usage_idx = next(i for i, c in enumerate(chunks) if "finishReason" in str(c))
    assert body_idx < sig_idx < usage_idx
    assert '"text": ""' in str(chunks[sig_idx])
