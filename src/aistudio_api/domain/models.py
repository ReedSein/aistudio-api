"""Typed domain response models.

A candidate is an ordered list of the parts the model produced. Every
aggregate view (visible text, thinking, tool calls, images, signature) is
derived from that list, so callers that need the native shape and callers
that need a flattened shape read the same source.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FunctionCall:
    name: str
    args: object
    id: str | None = None


@dataclass
class FunctionResponse:
    name: str
    response: object
    id: str | None = None


@dataclass
class OutputPart:
    """One model output part, kept in the order the upstream produced it."""

    text: str | None = None
    thought: bool = False
    thought_signature: str = ""
    # (mime type, base64 data) exactly as received.
    inline_data: tuple[str, str] | None = None
    function_call: FunctionCall | None = None
    function_response: FunctionResponse | None = None
    # (language, code) and (outcome, output) using Gemini enum names.
    executable_code: tuple[str, str] | None = None
    code_execution_result: tuple[str, str] | None = None

    @property
    def is_plain_text(self) -> bool:
        return (
            self.text is not None
            and not self.thought_signature
            and self.inline_data is None
            and self.function_call is None
            and self.function_response is None
            and self.executable_code is None
            and self.code_execution_result is None
        )


def append_part(parts: list[OutputPart], part: OutputPart) -> None:
    """Append ``part``, joining it to the previous part when both are text deltas.

    Only unsigned plain text of the same kind (thought or visible) is joined.
    A signed part never absorbs or donates text, because a signature belongs
    to the exact part it arrived on.
    """
    previous = parts[-1] if parts else None
    if (
        previous is not None
        and previous.is_plain_text
        and part.is_plain_text
        and previous.thought == part.thought
    ):
        previous.text = (previous.text or "") + (part.text or "")
        return
    parts.append(part)


@dataclass
class Candidate:
    parts: list[OutputPart] = field(default_factory=list)
    finish_reason: int | None = None
    finish_message: str = ""
    safety_ratings: list[dict[str, object]] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "".join(
            p.text for p in self.parts if p.text is not None and not p.thought
        )

    @property
    def thinking(self) -> str:
        return "".join(p.text for p in self.parts if p.text is not None and p.thought)

    @property
    def function_calls(self) -> list[OutputPart]:
        return [p for p in self.parts if p.function_call is not None]

    @property
    def function_responses(self) -> list[FunctionResponse]:
        return [p.function_response for p in self.parts if p.function_response]

    @property
    def images(self) -> list[OutputPart]:
        return [p for p in self.parts if p.inline_data is not None and not p.thought]

    @property
    def reasoning_images(self) -> list[OutputPart]:
        return [p for p in self.parts if p.inline_data is not None and p.thought]

    @property
    def thought_signature(self) -> str:
        """The last signature carried by a text part (including signature-only parts)."""
        signatures = [
            p.thought_signature
            for p in self.parts
            if p.thought_signature and p.text is not None
        ]
        return signatures[-1] if signatures else ""


@dataclass
class ModelOutput:
    candidates: list[Candidate] = field(default_factory=list)
    model: str = ""
    raw_response: str = ""
    response_id: str = ""
    usage: dict[str, object] = field(default_factory=dict)

    @property
    def candidate(self) -> Candidate:
        return self.candidates[0] if self.candidates else Candidate()

    @property
    def parts(self) -> list[OutputPart]:
        return self.candidate.parts

    @property
    def text(self) -> str:
        return self.candidate.text

    @property
    def thinking(self) -> str:
        return self.candidate.thinking


__all__ = [
    "Candidate",
    "FunctionCall",
    "FunctionResponse",
    "ModelOutput",
    "OutputPart",
    "append_part",
]
