"""Tool identities remain usable across independent invocation batches."""

import pytest

from aistudio_api.api.openai_errors import OpenAIInputError
from aistudio_api.api.openai_schemas import OpenAIChatRequest
from aistudio_api.application.openai_request import to_gemini_request
from aistudio_api.application.openai_tools import decode_call, encode_call


def call(arguments="{}"):
    return {
        "id": "reused",
        "type": "function",
        "function": {"name": "lookup", "arguments": arguments},
    }


def test_native_repeated_ids_produce_unique_round_trippable_ids():
    a = encode_call({"name": "lookup", "call_id": "0", "args": {}}, "signature")
    b = encode_call({"name": "lookup", "call_id": "0", "args": {}}, "signature")
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
