"""Convert Chat Completions to the existing Gemini content contract."""

from __future__ import annotations

import json
from contextlib import suppress
from typing import Any

from pydantic import ValidationError

from aistudio_api.api.openai_errors import OpenAIInputError
from aistudio_api.api.openai_schemas import (
    OpenAIChatRequest,
    require_object as _object,
    require_string as _string,
    strict_json_loads,
)
from aistudio_api.api.schemas import (
    GeminiContent,
    GeminiFunctionCall,
    GeminiFunctionResponse,
    GeminiGenerateContentRequest,
    GeminiPart,
)
from aistudio_api.application.openai_media import MediaConverter
from aistudio_api.application.openai_parameters import ParameterConversion
from aistudio_api.application.openai_tools import decode_call


async def _content(
    value: object, media: MediaConverter, param: str
) -> list[GeminiPart]:
    if value is None:
        return []
    if isinstance(value, str):
        return [GeminiPart(text=value)]
    if not isinstance(value, list):
        raise OpenAIInputError("Message content must be text or content parts", param)
    parts: list[GeminiPart] = []
    for i, raw in enumerate(value):
        key = f"{param}.{i}"
        item = _object(raw, key)
        kind = _string(item.get("type"), key + ".type")
        if kind in {"text", "input_text", "output_text"}:
            parts.append(GeminiPart(text=_string(item.get("text"), key + ".text")))
        elif kind == "refusal":
            parts.append(
                GeminiPart(text=_string(item.get("refusal"), key + ".refusal"))
            )
        elif kind in {"image_url", "input_image"}:
            image = item.get("image_url")
            url = image.get("url") if isinstance(image, dict) else image
            parts.append(await media.image(url, key + ".image_url"))
        elif kind == "input_audio":
            audio = _object(item.get("input_audio"), key + ".input_audio")
            mime = {"wav": "audio/wav", "mp3": "audio/mpeg"}.get(
                str(audio.get("format"))
            )
            if mime is None:
                raise OpenAIInputError("Only inline wav/mp3 audio is supported", key)
            parts.append(
                media.inline(mime, _string(audio.get("data"), key + ".data"), key)
            )
        elif kind in {"file", "input_file"}:
            file = _object(item.get("file", item), key + ".file")
            if file.get("file_id") or file.get("file_url"):
                raise OpenAIInputError(
                    "Remote file IDs/URLs cannot be read; send inline file_data", key
                )
            parts.append(media.data_url(file.get("file_data"), key + ".file_data"))
        else:
            raise OpenAIInputError("Unsupported message content type", key + ".type")
    return parts


def _signature(call: dict[str, Any], param: str) -> str | None:
    extra = call.get("extra_content")
    google = extra.get("google") if isinstance(extra, dict) else None
    value = call.get(
        "signature", call.get("thought_signature", call.get("thoughtSignature"))
    )
    if value is None and isinstance(google, dict):
        value = google.get("thought_signature")
    if value is not None:
        return _string(value, param)
    return None


def _tool_calls(message: dict[str, Any], index: int) -> list[dict[str, Any]]:
    raw = message.get("tool_calls")
    if raw is None and message.get("function_call") is not None:
        raw = [
            {
                "id": f"legacy_{index}",
                "type": "function",
                "function": message["function_call"],
            }
        ]
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise OpenAIInputError(
            "tool_calls must be an array", f"messages.{index}.tool_calls"
        )
    return [_object(item, f"messages.{index}.tool_calls") for item in raw]


def _arguments(value: object, param: str) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = strict_json_loads(value)
        except (ValueError, RecursionError) as exc:
            raise OpenAIInputError(
                "Function arguments must be a JSON object", param
            ) from exc
    return _object(value, param)


async def _messages(
    req: OpenAIChatRequest,
) -> tuple[list[GeminiContent], list[GeminiPart]]:
    media = MediaConverter()
    contents: list[GeminiContent] = []
    system: list[GeminiPart] = []
    # External ID -> (function name, original upstream ID).
    pending: dict[str, tuple[str, str | None]] = {}
    # Consecutive tool results answer one batch of calls and share one turn.
    tool_turn_open = False
    for index, message in enumerate(req.messages):
        key = f"messages.{index}"
        role = message.get("role")
        if not isinstance(role, str) or role not in {
            "system",
            "developer",
            "user",
            "assistant",
            "tool",
            "function",
        }:
            raise OpenAIInputError("Unsupported message role", key + ".role")
        if role != "assistant" and (
            message.get("tool_calls") or message.get("function_call")
        ):
            raise OpenAIInputError(
                "Only assistant messages can contain function calls", key
            )
        for field in ("attachments", "audio", "video", "function_responses"):
            if message.get(field):
                raise OpenAIInputError(
                    "This message attachment cannot be converted; use inline content parts",
                    key + "." + field,
                )
        parts = await _content(message.get("content"), media, key + ".content")
        images = message.get("images")
        if images:
            parts.extend(await _content(images, media, key + ".images"))
        if message.get("refusal") is not None:
            parts.append(GeminiPart(text=_string(message["refusal"], key + ".refusal")))
        if role in {"system", "developer"}:
            if any(part.inlineData is not None for part in parts):
                raise OpenAIInputError(
                    "System/developer messages must contain text", key
                )
            if message.get("name") is not None:
                parts.insert(
                    0,
                    GeminiPart(
                        text="[name: "
                        + json.dumps(
                            _string(message["name"], key + ".name"), ensure_ascii=False
                        )
                        + "]\n"
                    ),
                )
            if not contents:
                # Leading instructions become the system instruction.
                system.extend(parts)
                continue
            # Later instructions keep their position as an unlabeled user turn;
            # Gemini contents have no system role.
            if pending:
                raise OpenAIInputError(
                    "Function calls must have their results before the next conversation turn",
                    key,
                )
            target_role = "user"
        elif role in {"tool", "function"}:
            external_id = message.get("tool_call_id")
            if role == "function":
                matches = [
                    identifier
                    for identifier, (name, _) in pending.items()
                    if name == message.get("name")
                ]
                external_id = matches[0] if len(matches) == 1 else None
            if not isinstance(external_id, str) or external_id not in pending:
                raise OpenAIInputError(
                    "Tool result has no matching unanswered function call",
                    key + ".tool_call_id",
                )
            name, native_id = pending.pop(external_id)
            text = "\n".join(part.text for part in parts if part.text is not None)
            response: object = text
            # Plain-text tool results remain verbatim when they are not JSON.
            with suppress(ValueError):
                response = strict_json_loads(text)
            response = response if isinstance(response, dict) else {"result": response}
            parts = [
                GeminiPart(
                    functionResponse=GeminiFunctionResponse(
                        name=name,
                        id=native_id,
                        response=response,
                        parts=[part for part in parts if part.inlineData is not None]
                        or None,
                    )
                )
            ]
            target_role = "user"
        else:
            if pending:
                raise OpenAIInputError(
                    "Function calls must have their results before the next conversation turn",
                    key,
                )
            if message.get("name") is not None:
                parts.insert(
                    0,
                    GeminiPart(
                        text="[name: "
                        + json.dumps(
                            _string(message["name"], key + ".name"), ensure_ascii=False
                        )
                        + "]\n"
                    ),
                )
            calls = _tool_calls(message, index)
            if role != "assistant" and calls:
                raise OpenAIInputError(
                    "Only assistant messages can contain tool calls", key
                )
            if role == "assistant":
                reasoning = message.get("reasoning_content", message.get("reasoning"))
                if reasoning is not None:
                    parts.insert(
                        0,
                        GeminiPart(
                            text=_string(reasoning, key + ".reasoning_content"),
                            thought=True,
                        ),
                    )
                signature = _signature(message, key + ".signature")
                if signature:
                    parts.append(GeminiPart(text="", thoughtSignature=signature))
                for call in calls:
                    if call.get("type", "function") != "function":
                        raise OpenAIInputError(
                            "Only function tool calls can be converted",
                            key + ".tool_calls",
                        )
                    external_id = _string(call.get("id"), key + ".tool_calls.id")
                    if not external_id or external_id in pending:
                        raise OpenAIInputError(
                            "Tool call IDs must be nonempty and unique within an unanswered batch",
                            key + ".tool_calls.id",
                        )
                    native_id, encoded_signature = decode_call(
                        external_id, key + ".tool_calls.id"
                    )
                    explicit_signature = _signature(call, key + ".tool_calls.signature")
                    if (
                        encoded_signature
                        and explicit_signature
                        and encoded_signature != explicit_signature
                    ):
                        raise OpenAIInputError(
                            "Conflicting tool signatures", key + ".tool_calls.signature"
                        )
                    function = _object(
                        call.get("function"), key + ".tool_calls.function"
                    )
                    name = _string(
                        function.get("name"), key + ".tool_calls.function.name"
                    )
                    if not name:
                        raise OpenAIInputError("Function name is required", key)
                    args = _arguments(
                        function.get("arguments", {}),
                        key + ".tool_calls.function.arguments",
                    )
                    parts.append(
                        GeminiPart(
                            functionCall=GeminiFunctionCall(
                                name=name, args=args, id=native_id
                            ),
                            thoughtSignature=encoded_signature or explicit_signature,
                        )
                    )
                    pending[external_id] = name, native_id
            target_role = "model" if role == "assistant" else "user"
        if not parts:
            parts = [GeminiPart(text="")]
        is_tool_result = role in {"tool", "function"}
        if is_tool_result and tool_turn_open:
            contents[-1].parts.extend(parts)
        else:
            # Every other message keeps its own boundary, even next to the same role.
            contents.append(GeminiContent(role=target_role, parts=parts))
        tool_turn_open = is_tool_result
    if pending:
        raise OpenAIInputError("Function calls are missing their results", "messages")
    if not contents:
        raise OpenAIInputError(
            "At least one conversation message is required", "messages"
        )
    return contents, system


async def to_gemini_request(req: OpenAIChatRequest) -> GeminiGenerateContentRequest:
    contents, system = await _messages(req)
    params = ParameterConversion(req)
    config = params.generation_config()
    tools, tool_config = params.tools()
    safety = params.take("safetySettings")
    safety_alias = params.take("safety_settings")
    try:
        converted = GeminiGenerateContentRequest(
            contents=contents,
            systemInstruction=GeminiContent(parts=system) if system else None,
            generationConfig=config,
            tools=tools,
            toolConfig=tool_config,
            safetySettings=safety if safety is not None else safety_alias,
        )
    except ValidationError as exc:
        raise OpenAIInputError(
            "Invalid native safety settings", "safetySettings"
        ) from exc
    params.finish()
    return converted
