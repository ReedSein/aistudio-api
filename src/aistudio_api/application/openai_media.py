"""Bounded inline media conversion and credential-free, DNS-pinned image reads."""

from __future__ import annotations

import asyncio
import base64
import binascii
import ipaddress
import socket
from urllib.parse import urlsplit, urlunsplit

from curl_cffi import CurlOpt
from curl_cffi.requests import Session
from curl_cffi.requests.exceptions import RequestException

from aistudio_api.api.openai_errors import OpenAIInputError
from aistudio_api.api.schemas import GeminiInlineData, GeminiPart

MAX_MEDIA_BYTES = 20 * 1024 * 1024
MAX_TOTAL_MEDIA_BYTES = 32 * 1024 * 1024
MAX_ATTACHMENTS = 32


def _image_mime(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _public_address(value: str) -> bool:
    address = ipaddress.ip_address(value)
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    if isinstance(address, ipaddress.IPv6Address) and any(
        address in ipaddress.ip_network(prefix)
        for prefix in ("64:ff9b::/96", "64:ff9b:1::/48", "2002::/16", "2001::/32")
    ):
        return False
    return address.is_global and not (address.is_multicast or address.is_reserved)


async def _resolve_image_url(url: str, param: str) -> tuple[str, int, str]:
    try:
        parts = urlsplit(url)
        host = parts.hostname
        port = parts.port or (443 if parts.scheme == "https" else 80)
        if (
            parts.scheme not in {"http", "https"}
            or not host
            or parts.username is not None
            or parts.password is not None
            or parts.fragment
            or any(ord(c) < 33 or ord(c) == 127 for c in url)
            or "\\" in url
            or "%" in host
        ):
            raise ValueError
        host = host.encode("idna").decode("ascii").rstrip(".")
        if not host:
            raise ValueError
        async with asyncio.timeout(10):
            records = await asyncio.get_running_loop().getaddrinfo(
                host,
                port,
                type=socket.SOCK_STREAM,
            )
        addresses = list(dict.fromkeys(str(record[4][0]) for record in records))
        if not addresses or not all(_public_address(value) for value in addresses):
            raise ValueError
    except (ValueError, UnicodeError, OSError, TimeoutError) as exc:
        raise OpenAIInputError(
            "image_url must resolve only to public HTTP(S) addresses without credentials or redirects",
            param,
        ) from exc
    return host, port, addresses[0]


def _download_image(url: str, host: str, port: int, address: str, param: str) -> bytes:
    parsed = urlsplit(url)
    authority = f"[{host}]" if ":" in host else host
    url = urlunsplit(
        (parsed.scheme, f"{authority}:{port}", parsed.path, parsed.query, "")
    )
    body = bytearray()
    oversized = False

    def receive(chunk: bytes) -> int:
        nonlocal oversized
        if len(body) + len(chunk) > MAX_MEDIA_BYTES:
            oversized = True
            return 0
        body.extend(chunk)
        return len(chunk)

    pinned = f"[{address}]" if ":" in address else address
    # RESOLVE retains the URL host for HTTPS certificate/SNI verification.
    # https://curl.se/libcurl/c/CURLOPT_RESOLVE.html
    options: dict[CurlOpt, object] = {
        CurlOpt.PROXY: b"",
        CurlOpt.NOPROXY: b"*",
        CurlOpt.MAXFILESIZE_LARGE: MAX_MEDIA_BYTES,
    }
    try:
        ipaddress.ip_address(host)
    except ValueError:
        options[CurlOpt.RESOLVE] = [f"{host}:{port}:{pinned}"]
    try:
        with Session(trust_env=False, curl_options=options) as session:
            response = session.get(
                url,
                allow_redirects=False,
                timeout=20,
                content_callback=receive,
                headers={"Accept": "image/png,image/jpeg,image/webp,image/gif"},
                discard_cookies=True,
            )
            if response.status_code != 200:
                raise OpenAIInputError(
                    "image_url did not return HTTP 200; redirects are not followed",
                    param,
                )
    except RequestException as exc:
        message = (
            "image_url exceeds the 20 MiB limit"
            if oversized
            else "image_url download failed or exceeded its limits"
        )
        raise OpenAIInputError(message, param) from exc
    return bytes(body)


class MediaConverter:
    def __init__(self) -> None:
        self.total_bytes = 0
        self.count = 0

    def inline(self, mime: str, encoded: str, param: str) -> GeminiPart:
        if len(encoded) > (MAX_MEDIA_BYTES + 2) // 3 * 4:
            raise OpenAIInputError("Attachment exceeds the 20 MiB limit", param)
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise OpenAIInputError("Attachment is not valid base64", param) from exc
        return self._part(mime, data, param)

    def _part(self, mime: str, data: bytes, param: str) -> GeminiPart:
        self.count += 1
        self.total_bytes += len(data)
        if not data or len(data) > MAX_MEDIA_BYTES:
            raise OpenAIInputError(
                "Attachment is empty or exceeds the 20 MiB limit", param
            )
        if self.total_bytes > MAX_TOTAL_MEDIA_BYTES or self.count > MAX_ATTACHMENTS:
            raise OpenAIInputError(
                "Attachments exceed 32 MiB or 32 items in one request", param
            )
        if mime.startswith("image/"):
            detected = _image_mime(data)
            if detected is None or detected != mime:
                raise OpenAIInputError(
                    "Image type does not match supported PNG/JPEG/GIF/WebP data", param
                )
        elif mime == "application/pdf":
            if not data.startswith(b"%PDF-"):
                raise OpenAIInputError("PDF data is invalid", param)
        elif mime == "text/plain":
            try:
                data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise OpenAIInputError("Text attachment must be UTF-8", param) from exc
        elif mime in {"audio/wav", "audio/mpeg"}:
            valid = (
                data.startswith(b"RIFF") and data[8:12] == b"WAVE"
                if mime == "audio/wav"
                else data.startswith((b"ID3", b"\xff"))
            )
            if not valid:
                raise OpenAIInputError("Audio data does not match its format", param)
        else:
            raise OpenAIInputError("Unsupported inline attachment MIME type", param)
        return GeminiPart(
            inlineData=GeminiInlineData(
                mimeType=mime, data=base64.b64encode(data).decode("ascii")
            )
        )

    async def image(self, url: object, param: str) -> GeminiPart:
        if not isinstance(url, str):
            raise OpenAIInputError("image_url.url must be a string", param)
        if url.startswith("data:"):
            return self.data_url(url, param, image_only=True)
        if self.count >= MAX_ATTACHMENTS or self.total_bytes >= MAX_TOTAL_MEDIA_BYTES:
            raise OpenAIInputError("Attachment limit reached", param)
        host, port, address = await _resolve_image_url(url, param)
        data = await asyncio.to_thread(_download_image, url, host, port, address, param)
        mime = _image_mime(data)
        if mime is None:
            raise OpenAIInputError("image_url did not contain a supported image", param)
        return self._part(mime, data, param)

    def data_url(
        self, value: object, param: str, *, image_only: bool = False
    ) -> GeminiPart:
        if not isinstance(value, str) or not value.startswith("data:"):
            raise OpenAIInputError(
                "Attachment must contain a base64 data URL; file IDs are unavailable",
                param,
            )
        header, separator, encoded = value[5:].partition(",")
        mime, marker, encoding = header.partition(";")
        mime = mime.lower()
        if (
            not separator
            or not marker
            or encoding != "base64"
            or (image_only and not mime.startswith("image/"))
        ):
            raise OpenAIInputError(
                "Attachment must contain a supported base64 data URL", param
            )
        return self.inline(mime, encoded, param)
