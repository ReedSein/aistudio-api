"""Precedence and explicit degradation rules for non-content chat parameters."""

from __future__ import annotations

import math
from typing import Any

from pydantic import ValidationError

from aistudio_api.api.openai_errors import OpenAIInputError
from aistudio_api.api.openai_schemas import OpenAIChatRequest, require_object as _object
from aistudio_api.api.schemas import GeminiGenerationConfig, GeminiTool
from aistudio_api.infrastructure.diagnostics.store import get_store


class ParameterConversion:
    def __init__(self, req: OpenAIChatRequest):
        self.values = req.model_extra or {}
        self.used: set[str] = set()
        self.notes: list[dict[str, str]] = []

    def take(self, name: str) -> Any:
        self.used.add(name)
        return self.values.get(name)

    def note(self, field: str, action: str) -> None:
        self.notes.append({"field": field[:96], "action": action})

    def generation_config(self) -> GeminiGenerationConfig:
        native = self.take("generationConfig")
        alias = self.take("generation_config")
        config = (
            dict(_object(native if native is not None else alias, "generationConfig"))
            if native is not None or alias is not None
            else {}
        )
        for source, destination, low, high, integer in (
            ("temperature", "temperature", 0, 2, False),
            ("top_p", "topP", 0, 1, False),
            ("top_k", "topK", 0, None, True),
            ("presence_penalty", "presencePenalty", -2, 2, False),
            ("frequency_penalty", "frequencyPenalty", -2, 2, False),
        ):
            value = self.take(source)
            if value is None:
                continue
            try:
                number = float(value)
                if (
                    isinstance(value, bool)
                    or not math.isfinite(number)
                    or number < low
                    or (high is not None and number > high)
                    or (integer and not number.is_integer())
                ):
                    raise ValueError
            except (TypeError, ValueError, OverflowError):
                self.note(source, "ignored: unsupported numeric value")
                continue
            config[destination] = int(number) if integer else number
            self.note(source, "converted to " + destination)
        old_limit, new_limit = (
            self.take("max_tokens"),
            self.take("max_completion_tokens"),
        )
        if new_limit is not None and old_limit is not None:
            self.note("max_tokens", "ignored: max_completion_tokens takes precedence")
        limit = new_limit if new_limit is not None else old_limit
        if limit is not None:
            try:
                number = float(limit)
                if (
                    isinstance(limit, bool)
                    or not math.isfinite(number)
                    or number < 1
                    or not number.is_integer()
                ):
                    raise ValueError
                config["maxOutputTokens"] = int(number)
                self.note(
                    "max_completion_tokens" if new_limit is not None else "max_tokens",
                    "converted to maxOutputTokens",
                )
            except (ValueError, TypeError, OverflowError):
                self.note("max_tokens", "ignored: unsupported token limit")
        stop = self.take("stop")
        if isinstance(stop, str):
            stop = [stop]
        if stop is not None:
            if isinstance(stop, list) and all(isinstance(item, str) for item in stop):
                config["stopSequences"] = stop
            else:
                self.note("stop", "ignored: expected text or text array")
        response_format = self.take("response_format")
        if response_format is not None:
            response_format = _object(response_format, "response_format")
            kind = response_format.get("type")
            if kind in ("json_object", "json_schema"):
                config["responseMimeType"] = "application/json"
                if kind == "json_schema":
                    wrapper = _object(
                        response_format.get("json_schema"),
                        "response_format.json_schema",
                    )
                    config["responseSchema"] = _object(
                        wrapper.get("schema"), "response_format.json_schema.schema"
                    )
                    if wrapper.get("strict"):
                        self.note(
                            "response_format.json_schema.strict",
                            "converted: Gemini schema constraints; OpenAI strict parity is not guaranteed",
                        )
            elif kind not in (None, "text"):
                self.note("response_format", "ignored: unsupported format setting")
        effort = self.take("reasoning_effort")
        reasoning, thinking = self.take("reasoning"), self.take("thinking")
        if effort is None and isinstance(reasoning, dict):
            effort = reasoning.get("effort")
        if effort is None and isinstance(thinking, dict):
            effort = thinking.get("thinkingLevel", thinking.get("level"))
            if "budget_tokens" in thinking:
                self.note(
                    "thinking.budget_tokens",
                    "ignored: wire protocol has no verified token-budget mapping",
                )
        levels = {
            "minimal": "MINIMAL",
            "low": "LOW",
            "medium": "MEDIUM",
            "high": "HIGH",
            "xhigh": "HIGH",
            "max": "HIGH",
        }
        if isinstance(effort, str) and effort.lower() in levels:
            config["thinkingConfig"] = {"thinkingLevel": levels[effort.lower()]}
            self.note("reasoning_effort", "converted to thinkingConfig.thinkingLevel")
        elif effort is not None:
            self.note(
                "reasoning_effort", "ignored: unsupported effort; model defaults apply"
            )
        for field, value in (("thinking", thinking), ("reasoning", reasoning)):
            if value is not None:
                self.note(
                    field,
                    "only effort/level is mapped; other reasoning settings are ignored",
                )
        n = self.take("n")
        if n not in (None, 1):
            self.note("n", "normalized to one candidate; no additional generation")
        try:
            return GeminiGenerationConfig.model_validate(config)
        except ValidationError as exc:
            raise OpenAIInputError(
                "Invalid native generationConfig settings", "generationConfig"
            ) from exc

    def tools(self) -> tuple[list[GeminiTool] | None, dict[str, object] | None]:
        tools, functions = self.take("tools"), self.take("functions")
        declarations: list[dict[str, Any]] = []
        if tools is not None:
            if functions is not None:
                self.note("functions", "ignored: tools takes precedence")
            if not isinstance(tools, list):
                raise OpenAIInputError("tools must be an array", "tools")
            for i, raw in enumerate(tools):
                tool = _object(raw, f"tools.{i}")
                if tool.get("type", "function") != "function":
                    raise OpenAIInputError(
                        "Only function tools can be converted", f"tools.{i}.type"
                    )
                declarations.append(
                    _object(tool.get("function"), f"tools.{i}.function")
                )
        elif functions is not None:
            if not isinstance(functions, list):
                raise OpenAIInputError("functions must be an array", "functions")
            declarations = [_object(item, "functions") for item in functions]
        for declaration in declarations:
            if not isinstance(declaration.get("name"), str) or not declaration["name"]:
                raise OpenAIInputError("Function name is required", "tools")
            if declaration.get("strict"):
                self.note(
                    "tools.function.strict", "ignored: Gemini function schema is used"
                )
            if declaration.get("parameters") is not None:
                _object(declaration["parameters"], "tools.function.parameters")
        choice, legacy = self.take("tool_choice"), self.take("function_call")
        choice = choice if choice is not None else legacy
        mode, names = "AUTO", []
        if choice == "none":
            mode = "NONE"
        elif choice == "required":
            mode = "ANY"
        elif isinstance(choice, dict):
            function = choice.get("function", choice)
            name = _object(function, "tool_choice.function").get("name")
            if not isinstance(name, str) or name not in {
                item["name"] for item in declarations
            }:
                raise OpenAIInputError(
                    "tool_choice names an undeclared function", "tool_choice"
                )
            mode, names = "ANY", [name]
        elif choice not in (None, "auto"):
            self.note("tool_choice", "ignored: unsupported choice; AUTO is used")
        if mode == "ANY" and not declarations:
            raise OpenAIInputError(
                "Required tool choice needs function declarations", "tool_choice"
            )
        config: dict[str, object] | None = None
        if choice is not None:
            config = {
                "functionCallingConfig": {"mode": mode, "allowedFunctionNames": names}
            }
        native = self.take("toolConfig")
        if config is None and native is not None:
            config = _object(native, "toolConfig")
        result = (
            [GeminiTool(functionDeclarations=declarations)]
            if declarations
            else ([] if tools is not None or functions is not None else None)
        )
        return result, config

    def finish(self) -> None:
        for field in self.values:
            if field not in self.used:
                self.note(field, "ignored: no equivalent upstream capability")
        get_store().emit("openai_compatibility", decisions=self.notes)
