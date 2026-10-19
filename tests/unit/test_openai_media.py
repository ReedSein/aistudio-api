"""Media fidelity and SSRF/credential boundaries without external network I/O."""

from __future__ import annotations

import asyncio
import base64
import socket
from unittest.mock import AsyncMock

import pytest
from curl_cffi import CurlOpt
from curl_cffi.requests.exceptions import RequestException

from aistudio_api.api.openai_errors import OpenAIInputError
from aistudio_api.application import openai_media as media

PNG = b"\x89PNG\r\n\x1a\n" + b"test-image-data"


def dns(addresses):
    return [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))
        for address in addresses
    ]


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://127.0.0.1/picture",
        "http://localhost/picture",
        "http://169.254.169.254/picture",
        "http://192.168.1.1/picture",
        "http://[::ffff:127.0.0.1]/picture",
        "https://user:password@example.com/picture",
        "https://example.com/picture\r\nX-Evil: 1",
        "http://example.com/picture#fragment",
    ],
)
async def test_private_or_credentialed_urls_are_rejected_before_download(
    monkeypatch, url
):
    monkeypatch.setattr(
        asyncio.get_running_loop(),
        "getaddrinfo",
        AsyncMock(return_value=dns(["127.0.0.1"])),
    )
    download = AsyncMock()
    monkeypatch.setattr(media.asyncio, "to_thread", download)
    with pytest.raises(OpenAIInputError):
        await media.MediaConverter().image(url, "messages.0.content.0.image_url")
    download.assert_not_awaited()


async def test_mixed_public_private_dns_is_rejected(monkeypatch):
    monkeypatch.setattr(
        asyncio.get_running_loop(),
        "getaddrinfo",
        AsyncMock(return_value=dns(["93.184.216.34", "10.0.0.2"])),
    )
    with pytest.raises(OpenAIInputError):
        await media._resolve_image_url("https://example.com/p", "image_url")


async def test_download_pins_dns_and_never_inherits_credentials_or_proxy(monkeypatch):
    observed = {}

    class Session:
        def __init__(self, **kwargs):
            observed.update(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, **kwargs):
            observed["url"] = url
            observed.update(kwargs)
            assert kwargs["content_callback"](PNG) == len(PNG)
            return type("Response", (), {"status_code": 200})()

    monkeypatch.setattr(media, "Session", Session)
    monkeypatch.setattr(
        asyncio.get_running_loop(),
        "getaddrinfo",
        AsyncMock(return_value=dns(["93.184.216.34"])),
    )
    result = await media.MediaConverter().image(
        "https://ExAmPle.com./picture", "image_url"
    )
    assert result.inlineData is not None
    assert base64.b64decode(result.inlineData.data) == PNG
    assert result.inlineData.mimeType == "image/png"
    assert observed["trust_env"] is False
    assert observed["url"] == "https://example.com:443/picture"
    assert observed["curl_options"][CurlOpt.RESOLVE] == [
        "example.com:443:93.184.216.34"
    ]
    assert observed["curl_options"][CurlOpt.PROXY] == b""
    assert observed["allow_redirects"] is False and observed["discard_cookies"] is True
    assert set(observed["headers"]) == {"Accept"}


def test_download_stream_limit_and_redirect_are_explicit(monkeypatch):
    class Session:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, **kwargs):
            assert kwargs["content_callback"](b"x" * 20) == 0
            raise RequestException("write aborted")

    monkeypatch.setattr(media, "MAX_MEDIA_BYTES", 16)
    monkeypatch.setattr(media, "Session", Session)
    with pytest.raises(OpenAIInputError) as exc:
        media._download_image(
            "https://example.com/p", "example.com", 443, "93.184.216.34", "image_url"
        )
    assert "limit" in str(exc.value.detail)


def test_inline_types_and_total_budget(monkeypatch):
    encoded = base64.b64encode(PNG).decode()
    converter = media.MediaConverter()
    part = converter.data_url("data:image/png;base64," + encoded, "image_url")
    assert part.inlineData is not None
    assert part.inlineData.data == encoded
    with pytest.raises(OpenAIInputError):
        converter.data_url("data:image/jpeg;base64," + encoded, "image_url")
    with pytest.raises(OpenAIInputError):
        converter.data_url(
            "data:image/svg+xml;base64," + base64.b64encode(b"<svg/>").decode(),
            "image_url",
        )
    monkeypatch.setattr(media, "MAX_TOTAL_MEDIA_BYTES", len(PNG))
    with pytest.raises(OpenAIInputError):
        converter.data_url("data:image/png;base64," + encoded, "image_url")


def test_transition_addresses_cannot_bypass_private_network_rejection():
    assert not media._public_address("64:ff9b::a00:1")
    assert not media._public_address("2002:a00:1::")
    assert not media._public_address("::ffff:127.0.0.1")
    assert media._public_address("93.184.216.34")
    assert media._public_address("2001:4860:4860::8888")
