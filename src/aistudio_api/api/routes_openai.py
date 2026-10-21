"""OpenAI-compatible Chat Completions and model catalog routes."""

from __future__ import annotations

from collections.abc import Callable, Coroutine, Mapping
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import Response, StreamingResponse
from fastapi.routing import APIRoute
from pydantic import ValidationError

from aistudio_api.application.openai_request import to_gemini_request
from aistudio_api.application.openai_response import completion, completion_stream
from aistudio_api.infrastructure.gateway.client import AIStudioClient
from aistudio_api.infrastructure.gateway.model_discovery import (
    ModelAliasAmbiguous,
    model_discovery,
)
from aistudio_api.infrastructure.utils.logger import get_logger

from .dependencies import get_client, get_runtime_state
from .openai_errors import OpenAIInputError
from .openai_schemas import MAX_REQUEST_BYTES, OpenAIChatRequest, strict_json_loads
from .state import RuntimeState

logger = get_logger("routes.openai")


class OpenAIRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handle(request: Request) -> Response:
            try:
                return await original(request)
            except (HTTPException, RequestValidationError):
                raise
            except Exception as exc:
                logger.exception("OpenAI route failed")
                raise HTTPException(500, detail="OpenAI request failed") from exc

        return handle


router = APIRouter(route_class=OpenAIRoute)


async def _read_request(request: Request) -> OpenAIChatRequest:
    raw_length = request.headers.get("content-length")
    if raw_length is not None:
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise OpenAIInputError("Invalid Content-Length header") from exc
        if length < 0 or length > MAX_REQUEST_BYTES:
            raise OpenAIInputError("Request exceeds the 64 MiB limit", status=413)
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_REQUEST_BYTES:
            raise OpenAIInputError("Request exceeds the 64 MiB limit", status=413)
        body.extend(chunk)
    try:
        value = strict_json_loads(body)
        return OpenAIChatRequest.model_validate(value)
    except (ValueError, RecursionError) as exc:
        if isinstance(exc, ValidationError):
            first = exc.errors(include_input=False, include_context=False)[0]
            param = ".".join(str(item) for item in first["loc"])
            raise OpenAIInputError(
                "Invalid request field: " + first["type"], param or None
            ) from exc
        raise OpenAIInputError("Request body must be valid JSON") from exc


@router.post(
    "/v1/chat/completions",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {"schema": OpenAIChatRequest.model_json_schema()}
            },
        }
    },
)
async def chat_completions(
    request: Request, client: AIStudioClient = Depends(get_client)
):
    req = await _read_request(request)
    converted = await to_gemini_request(req)
    if req.stream:
        return StreamingResponse(
            completion_stream(
                req.model,
                converted,
                client,
                include_usage=bool((req.stream_options or {}).get("include_usage")),
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    return await completion(req.model, converted, client)


def _model(data: Mapping[str, object]) -> dict[str, object]:
    identifier = str(data.get("id") or data.get("name") or "").removeprefix("models/")
    # The discovery source supplies no creation timestamp; zero means unknown.
    return {"id": identifier, "object": "model", "created": 0, "owned_by": "google"}


@router.get("/v1/models")
async def list_openai_models(state: RuntimeState = Depends(get_runtime_state)):
    session = state.client.session if state.client else None
    models = await model_discovery.get_models(session=session)
    return {"object": "list", "data": [_model(item) for item in models]}


@router.get("/v1/models/{model_id:path}")
async def get_openai_model(
    model_id: str, state: RuntimeState = Depends(get_runtime_state)
):
    session = state.client.session if state.client else None
    try:
        model = await model_discovery.get_model_by_id(
            model_id.removeprefix("models/"), session=session
        )
    except ModelAliasAmbiguous as exc:
        raise HTTPException(409, detail=str(exc)) from None
    if model is None:
        raise HTTPException(404, detail="Model not found")
    return _model(model)
