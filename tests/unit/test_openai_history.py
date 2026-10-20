"""Tool identities remain usable across independent invocation batches."""

import pytest

from aistudio_api.api.openai_errors import OpenAIInputError
from aistudio_api.api.openai_schemas import OpenAIChatRequest
from aistudio_api.application.openai_request import to_gemini_request
from aistudio_api.application.openai_tools import decode_call, encode_call
from aistudio_api.domain.models import FunctionCall, OutputPart


def call(arguments="{}"):
    return {
        "id": "reused",
        "type": "function",
        "function": {"name": "lookup", "arguments": arguments},
    }


def test_native_repeated_ids_produce_unique_round_trippable_ids():
    part = OutputPart(function_call=FunctionCall(name="lookup", args={}, id="0"))
    a = encode_call(part, "signature")
    b = encode_call(part, "signature")
    assert a["id"] != b["id"]
    assert (
        decode_call(a["id"], "id") == decode_call(b["id"], "id") == ("0", "signature")
    )


async def test_external_client_can_reuse_id_after_answering_it():
    messages = [{"role": "user", "content": "question"}]
    for number in range(2):
        messages += [
            {"role": "assistant", "tool_calls": [call()]},
            {"role": "tool", "tool_call_id": "reused", "content": str(number)},
        ]
    req = OpenAIChatRequest(model="gemini-test", messages=messages)
    converted = await to_gemini_request(req)
    calls = [
        part.functionCall
        for content in converted.contents
        for part in content.parts
        if part.functionCall is not None
    ]
    assert len(calls) == 2
    assert [item.id for item in calls] == ["reused", "reused"]


@pytest.mark.parametrize("calls", [[call(), call()], [call('{"x":NaN}')]])
async def test_duplicate_batch_ids_and_invalid_json_are_rejected(calls):
    req = OpenAIChatRequest(
        model="gemini-test",
        messages=[
            {"role": "assistant", "tool_calls": calls},
            {"role": "tool", "tool_call_id": "reused", "content": "result"},
        ],
    )
    with pytest.raises(OpenAIInputError):
        await to_gemini_request(req)


async def _convert(messages):
    return await to_gemini_request(
        OpenAIChatRequest(model="gemini-test", messages=messages)
    )


def _shape(converted):
    return [
        (content.role, [part.text for part in content.parts])
        for content in converted.contents
    ]


async def test_adjacent_same_role_messages_keep_their_boundaries():
    converted = await _convert(
        [
            {"role": "user", "content": "镜子"},
            {"role": "user", "content": "1"},
            {"role": "assistant", "content": "迷宫"},
            {"role": "assistant", "content": "回声"},
        ]
    )

    assert _shape(converted) == [
        ("user", ["镜子"]),
        ("user", ["1"]),
        ("model", ["迷宫"]),
        ("model", ["回声"]),
    ]


async def test_leading_system_is_instruction_and_later_system_stays_in_place():
    converted = await _convert(
        [
            {"role": "system", "content": "角色卡"},
            {"role": "developer", "content": "规则"},
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好呀"},
            {"role": "system", "content": "接下来进入战斗场景"},
            {"role": "user", "content": "拔剑"},
        ]
    )

    assert converted.systemInstruction is not None
    assert [part.text for part in converted.systemInstruction.parts] == [
        "角色卡",
        "规则",
    ]
    assert _shape(converted) == [
        ("user", ["你好"]),
        ("model", ["你好呀"]),
        ("user", ["接下来进入战斗场景"]),
        ("user", ["拔剑"]),
    ]


async def test_consecutive_tool_results_share_one_turn():
    converted = await _convert(
        [
            {"role": "user", "content": "两地天气"},
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "a",
                        "type": "function",
                        "function": {"name": "w", "arguments": "{}"},
                    },
                    {
                        "id": "b",
                        "type": "function",
                        "function": {"name": "w", "arguments": "{}"},
                    },
                ],
            },
            {"role": "tool", "tool_call_id": "a", "content": "晴"},
            {"role": "tool", "tool_call_id": "b", "content": "雨"},
            {"role": "user", "content": "谢谢"},
        ]
    )

    roles = [content.role for content in converted.contents]
    assert roles == ["user", "model", "user", "user"]
    results = converted.contents[2].parts
    assert [
        part.functionResponse.name for part in results if part.functionResponse
    ] == [
        "w",
        "w",
    ]


async def test_system_between_calls_and_results_is_rejected():
    with pytest.raises(OpenAIInputError):
        await _convert(
            [
                {"role": "user", "content": "天气"},
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "a",
                            "type": "function",
                            "function": {"name": "w", "arguments": "{}"},
                        }
                    ],
                },
                {"role": "system", "content": "插话"},
                {"role": "tool", "tool_call_id": "a", "content": "晴"},
            ]
        )
