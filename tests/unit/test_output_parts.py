"""Model output is forwarded as the ordered native parts the upstream produced."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from aistudio_api.api.openai_schemas import OpenAIChatRequest
from aistudio_api.application.api_service_gemini import format_sse_event
from aistudio_api.application.openai_request import to_gemini_request
from aistudio_api.application.openai_response import completion_stream
from aistudio_api.application.openai_tools import decode_call
from aistudio_api.domain.models import FunctionCall, OutputPart
from aistudio_api.infrastructure.gateway.capture import CapturedRequest
from aistudio_api.infrastructure.gateway.client import AIStudioClient
from aistudio_api.infrastructure.gateway.streaming import StreamingGateway
from aistudio_api.infrastructure.gateway.wire_parser import parse_text_output

THOUGHT = [
    None,
    "想一想",
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    1,
]
CALL = [
    *([None] * 10),
    ["lookup", [[["city", [None, None, "杭州"]]]], "c1"],
    None,
    None,
    None,
    "SIG-FC",
]
TEXT_A = [None, "好的，"]
TEXT_B = [None, "查到了。"]
SIGNATURE = [None, ""] + [None] * 12 + ["SIG-TEXT"]


def _chunk(parts, *, finish=None, usage=None):
    candidate = [[parts, "model"]]
    if finish is not None:
        candidate.append(finish)
    return [[candidate], None, usage, None, None, None, None, "resp-1"]


CHUNKS = [
    _chunk([THOUGHT]),
    _chunk([CALL]),
    _chunk([TEXT_A]),
    _chunk([TEXT_B]),
    _chunk([SIGNATURE], finish=1, usage=[5, 4, 9]),
]


def test_non_stream_keeps_order_and_joins_only_unsigned_text_deltas():
    output = parse_text_output(json.dumps([CHUNKS], ensure_ascii=False))

    assert output.parts == [
        OutputPart(text="想一想", thought=True),
        OutputPart(
            function_call=FunctionCall(name="lookup", args={"city": "杭州"}, id="c1"),
            thought_signature="SIG-FC",
        ),
        OutputPart(text="好的，查到了。"),
        OutputPart(text="", thought_signature="SIG-TEXT"),
    ]


def test_signed_text_part_never_absorbs_following_text():
    signed = [None, "前半", None, None, None, None, None, None, None, None, None]
    signed += [None, None, None, "SIG-1"]
    output = parse_text_output(
        json.dumps([[_chunk([signed]), _chunk([TEXT_B], finish=1)]], ensure_ascii=False)
    )

    assert output.parts == [
        OutputPart(text="前半", thought_signature="SIG-1"),
        OutputPart(text="查到了。"),
    ]


class _FakeSession:
    async def send_streaming_request(self, **_kwargs):
        yield "status", 200
        yield "chunk", json.dumps([CHUNKS], ensure_ascii=False)


async def test_stream_emits_one_parts_event_per_upstream_chunk_without_duplicates():
    gateway = StreamingGateway(session=_FakeSession())  # type: ignore[arg-type]
    captured = CapturedRequest(url="https://upstream.test", headers={}, body="[]")

    events = [
        event async for event in gateway.stream_chat(captured=captured, model="m")
    ]
    part_events = [value for kind, value in events if kind == "parts"]

    assert len(part_events) == len(CHUNKS)
    sse = "".join(format_sse_event("parts", value) or "" for value in part_events)
    assert sse.count("SIG-FC") == 1
    assert sse.count("SIG-TEXT") == 1
    assert ("finish_reason", 1) in events


@pytest.mark.asyncio
async def test_openai_stream_derives_deltas_and_tool_ids_from_parts():
    async def source(**_kwargs):
        yield "parts", [OutputPart(text="想一想", thought=True)]
        yield (
            "parts",
            [
                OutputPart(
                    function_call=FunctionCall(name="lookup", args={}, id="c1"),
                    thought_signature="SIG-FC",
                )
            ],
        )
        yield "parts", [OutputPart(text="好的")]
        yield "parts", [OutputPart(text="", thought_signature="SIG-TEXT")]
        yield "finish_reason", 1
        yield "done", None

    client = MagicMock(spec=AIStudioClient)
    client.stream_generate_content = MagicMock(side_effect=source)
    req = await to_gemini_request(
        OpenAIChatRequest.model_validate(
            {"model": "gemini-test", "messages": [{"role": "user", "content": "hi"}]}
        )
    )

    frames = [
        json.loads(line[len("data: ") :])
        async for line in completion_stream(
            "gemini-test", req, client, include_usage=False
        )
        if line.startswith("data: {")
    ]
    deltas = [frame["choices"][0]["delta"] for frame in frames]

    assert {"reasoning_content": "想一想"} in deltas
    assert {"content": "好的"} in deltas
    assert {"thought_signature": "SIG-TEXT"} in deltas
    calls = [call for delta in deltas for call in delta.get("tool_calls", [])]
    assert len(calls) == 1
    assert decode_call(calls[0]["id"], "id") == ("c1", "SIG-FC")
