"""Observe the ASGI exchange without buffering delivery or changing its messages."""

from __future__ import annotations

import asyncio
import codecs
import re
import time
import traceback
import uuid
from urllib.parse import parse_qsl

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from aistudio_api.infrastructure.diagnostics.redaction import redact_url, sanitize
from aistudio_api.infrastructure.diagnostics.store import (
    get_store,
    operation_id,
    request_id,
)
from aistudio_api.infrastructure.utils.logger import (
    dump_request_exchange,
    get_logger,
    is_dump_requests_enabled,
)


class DiagnosticsMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if path.startswith(("/assets/", "/static/")) or path == "/favicon.ico":
            await self.app(scope, receive, send)
            return
        store = get_store()
        identifier = uuid.uuid4().hex
        context = request_id.set(identifier)
        start = time.perf_counter()
        generation = (path.startswith("/v1beta/") and ":" in path) or path.rstrip(
            "/"
        ) == "/v1/chat/completions"
        legacy_enabled = generation and is_dump_requests_enabled()
        legacy_body: str | None = None
        legacy_response: list[bytes] = []
        legacy_response_headers: dict[str, str] = {}
        body = bytearray()
        request_complete = False
        response_complete = False
        disconnected = False
        response_bytes = 0
        chunks = 0
        status = 500
        first_byte_ms: float | None = None
        outcome = "incomplete"
        streaming = False
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        pending = ""
        sequence = 0
        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in scope.get("headers", [])
        }
        supplied_operation = headers.get("x-aistudio-operation-id", "")
        client_operation = (
            supplied_operation
            if re.fullmatch(r"[a-f0-9]{32}", supplied_operation)
            else ""
        )
        operation_context = operation_id.set(client_operation)
        if supplied_operation and not client_operation:
            headers["x-aistudio-operation-id"] = "[invalid operation ID omitted]"
        safe_headers = sanitize(headers)
        query = scope.get("query_string", b"").decode("latin-1")
        safe_query = sanitize(
            dict(parse_qsl(query, keep_blank_values=True)), credential_keys=True
        )
        store.emit(
            "http_start",
            method=scope["method"],
            path=path,
            url=redact_url(path + ("?" + query if query else "")),
            headers=safe_headers,
            query=safe_query,
            client=scope.get("client"),
            body_capture=generation,
        )

        def save_response(final: bool = False) -> None:
            nonlocal pending, sequence
            if not generation:
                return
            if streaming:
                frames = pending.replace("\r\n", "\n").split("\n\n")
                pending = frames.pop()
                for frame in frames:
                    store.text(
                        "http_response_body",
                        frame + "\n\n",
                        sequence=sequence,
                        elapsed_ms=(time.perf_counter() - start) * 1000,
                    )
                    sequence += 1
            if final and pending:
                cleaned = sanitize(pending)
                store.text(
                    "http_response_body",
                    str(cleaned),
                    sequence=sequence,
                    elapsed_ms=(time.perf_counter() - start) * 1000,
                )
                sequence += 1
                pending = ""

        async def observed_receive() -> Message:
            nonlocal request_complete, disconnected, legacy_body
            message = await receive()
            if message["type"] == "http.disconnect":
                disconnected = True
            elif message["type"] == "http.request":
                if generation:
                    body.extend(message.get("body", b""))
                if not message.get("more_body", False):
                    request_complete = True
                    if generation:
                        safe_body = sanitize(body.decode("utf-8", errors="replace"))
                        if legacy_enabled:
                            legacy_body = str(safe_body)
                        store.text("http_request_body", str(safe_body), bytes=len(body))
                        body.clear()
            return message

        async def observed_send(message: Message) -> None:
            nonlocal \
                status, \
                streaming, \
                response_bytes, \
                chunks, \
                first_byte_ms, \
                response_complete, \
                pending
            if message["type"] == "http.response.start":
                status = message["status"]
                response_headers = list(message.get("headers", []))
                legacy_response_headers.update(
                    {
                        k.decode("latin-1"): v.decode("latin-1")
                        for k, v in response_headers
                    }
                )
                streaming = any(
                    k.lower() == b"content-type" and b"text/event-stream" in v
                    for k, v in response_headers
                )
                # Correlation is generated here rather than trusting a client-supplied ID.
                response_headers.append((b"x-request-id", identifier.encode("ascii")))
                message = {**message, "headers": response_headers}
                store.emit(
                    "http_response_start",
                    status=status,
                    headers=sanitize(
                        {
                            k.decode("latin-1"): v.decode("latin-1")
                            for k, v in response_headers
                        }
                    ),
                )
            await send(message)
            if message["type"] == "http.response.body":
                chunk = message.get("body", b"")
                if legacy_enabled:
                    legacy_response.append(chunk)
                response_bytes += len(chunk)
                chunks += bool(chunk)
                if chunk and first_byte_ms is None:
                    first_byte_ms = (time.perf_counter() - start) * 1000
                final = not message.get("more_body", False)
                if generation:
                    pending += decoder.decode(chunk, final=final)
                    save_response(final)
                response_complete = final

        try:
            await self.app(scope, observed_receive, observed_send)
            outcome = "completed" if response_complete else "incomplete"
            if disconnected and not response_complete:
                outcome = "client_disconnected"
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        except TimeoutError:
            outcome = "timeout"
            store.text("http_exception", traceback.format_exc())
            raise
        except OSError:
            outcome = "client_disconnected"
            store.text("http_exception", traceback.format_exc())
            raise
        except Exception:
            outcome = "exception"
            store.text("http_exception", traceback.format_exc())
            raise
        finally:
            try:
                if generation:
                    save_response(True)
                    if body:
                        store.text(
                            "http_request_body",
                            str(sanitize(body.decode("utf-8", errors="replace"))),
                            partial=True,
                        )
                store.emit(
                    "http_end",
                    status=status,
                    outcome=outcome,
                    elapsed_ms=(time.perf_counter() - start) * 1000,
                    first_byte_ms=first_byte_ms,
                    response_bytes=response_bytes,
                    response_chunks=chunks,
                    request_complete=request_complete,
                    response_complete=response_complete,
                )
                get_logger("http").info(
                    "HTTP %s %s -> %d (%.1fms, %s)",
                    scope["method"],
                    redact_url(path),
                    status,
                    (time.perf_counter() - start) * 1000,
                    outcome,
                )
                if legacy_enabled:
                    dump_request_exchange(
                        req_id=identifier,
                        method=scope["method"],
                        url=redact_url(path),
                        client=str(scope.get("client", "unknown")),
                        headers=headers,
                        query_params=dict(parse_qsl(query, keep_blank_values=True)),
                        body_text=legacy_body,
                        status_code=status,
                        elapsed_ms=(time.perf_counter() - start) * 1000,
                        response_headers=legacy_response_headers,
                        response_text=b"".join(legacy_response).decode(
                            "utf-8", errors="replace"
                        ),
                        is_stream=streaming,
                    )
            finally:
                operation_id.reset(operation_context)
                request_id.reset(context)
