"""Request-stage interception must never release an unidentified probe."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from aistudio_api.infrastructure.browser.cdp_client import CDPConnection, CDPPage


@pytest.fixture
def interception_page():
    connection = MagicMock(spec=CDPConnection)
    connection._closed = False
    listeners = {}

    def on(event, callback):
        listeners[event] = callback
        return lambda: listeners.pop(event, None)

    connection.on.side_effect = on
    connection.send = AsyncMock(return_value={})
    connection.close = AsyncMock()
    return CDPPage(connection, "fixture-page"), connection, listeners


def request(body: str | None = "[]") -> dict:
    return {
        "requestId": "intercepted",
        "request": {
            "url": "https://example.invalid/probe",
            "method": "POST",
            "headers": {"X-Fixture": "yes"},
            "postData": body,
        },
    }


@pytest.mark.asyncio
async def test_capture_is_published_after_abort_and_all_matches_are_blocked(
    interception_page,
):
    page, connection, listeners = interception_page
    async with page.capture_blocked_request(
        "https://example.invalid/probe"
    ) as captured:
        listeners["Fetch.requestPaused"](request("first"))
        result = await asyncio.wait_for(captured, 1)
        assert result["post_data"] == "first"
        assert result["headers"] == {"X-Fixture": "yes"}
        assert connection.send.call_args.args[0] == "Fetch.failRequest"
        listeners["Fetch.requestPaused"](request("automatic retry"))
        await asyncio.sleep(0)
    methods = [c.args[0] for c in connection.send.call_args_list]
    assert methods == [
        "Fetch.enable",
        "Fetch.failRequest",
        "Fetch.failRequest",
        "Fetch.disable",
    ]
    assert not listeners
    assert not page.is_closed()


@pytest.mark.asyncio
async def test_cors_preflight_does_not_replace_or_release_the_post(interception_page):
    page, connection, listeners = interception_page
    async with page.capture_blocked_request("*") as captured:
        event = request(None)
        event["request"]["method"] = "OPTIONS"
        event["request"]["headers"] = {
            "Origin": "https://example.invalid",
            "Access-Control-Request-Method": "POST",
        }
        listeners["Fetch.requestPaused"](event)
        await asyncio.sleep(0)
        assert not captured.done()
        assert connection.send.call_args.args[0] == "Fetch.continueRequest"
        listeners["Fetch.requestPaused"](request("actual POST"))
        assert (await asyncio.wait_for(captured, 1))["post_data"] == "actual POST"
    assert [call.args[0] for call in connection.send.call_args_list] == [
        "Fetch.enable",
        "Fetch.continueRequest",
        "Fetch.failRequest",
        "Fetch.disable",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("method", [None, "GET"])
async def test_unrecognized_options_is_aborted_instead_of_continued(
    interception_page, method
):
    page, connection, listeners = interception_page
    with pytest.raises(RuntimeError, match="supported POST body"):
        async with page.capture_blocked_request("*") as captured:
            event = request(None)
            event["request"]["method"] = "OPTIONS"
            event["request"]["headers"] = {
                "Origin": "https://example.invalid",
                "Access-Control-Request-Method": method,
            }
            listeners["Fetch.requestPaused"](event)
            await asyncio.wait_for(captured, 1)
    assert "Fetch.continueRequest" not in [
        call.args[0] for call in connection.send.call_args_list
    ]
    assert page.is_closed()


@pytest.mark.asyncio
async def test_omitted_post_data_is_read_before_aborting(interception_page):
    page, connection, listeners = interception_page
    connection.send.side_effect = lambda method, *args, **kwargs: (
        {"postData": "full body"} if method == "Network.getRequestPostData" else {}
    )
    async with page.capture_blocked_request("*") as captured:
        event = request(None)
        event["networkId"] = "network-request"
        event["request"]["hasPostData"] = True
        listeners["Fetch.requestPaused"](event)
        assert (await asyncio.wait_for(captured, 1))["post_data"] == "full body"
    methods = [c.args[0] for c in connection.send.call_args_list]
    assert methods.index("Network.getRequestPostData") < methods.index(
        "Fetch.failRequest"
    )


@pytest.mark.asyncio
async def test_unsupported_body_is_aborted_and_page_is_closed(interception_page):
    page, connection, listeners = interception_page
    with pytest.raises(RuntimeError, match="supported POST body"):
        async with page.capture_blocked_request("*") as captured:
            listeners["Fetch.requestPaused"](request(None))
            await asyncio.wait_for(captured, 1)
    methods = [c.args[0] for c in connection.send.call_args_list]
    assert "Fetch.failRequest" in methods
    assert "Fetch.disable" not in methods
    assert "Fetch.continueRequest" not in methods
    assert page.is_closed()
    assert not listeners


@pytest.mark.asyncio
async def test_failed_interception_setup_closes_page(interception_page):
    page, connection, listeners = interception_page

    async def send(method, *args, **kwargs):
        if method == "Fetch.enable":
            assert "Fetch.requestPaused" in listeners
            raise RuntimeError("fixture protocol failure")
        return {}

    connection.send.side_effect = send
    with pytest.raises(RuntimeError, match="protocol failure"):
        async with page.capture_blocked_request("*"):
            pytest.fail("setup failure must not start a probe")
    assert page.is_closed()
    assert not listeners


@pytest.mark.asyncio
async def test_cancelled_command_dispatch_releases_future():
    connection = CDPConnection("ws://127.0.0.1:9222/fixture")
    socket = AsyncMock()
    connection.ws = socket
    started = asyncio.Event()

    async def send(payload):
        started.set()
        await asyncio.Event().wait()

    socket.send.side_effect = send
    command = asyncio.create_task(connection.send("Runtime.evaluate"))
    await started.wait()
    command.cancel()
    with pytest.raises(asyncio.CancelledError):
        await command
    assert not connection._futures


@pytest.mark.asyncio
async def test_cancelled_probe_closes_page_instead_of_releasing_requests(
    interception_page,
):
    page, connection, listeners = interception_page
    started = asyncio.Event()

    async def prepare():
        async with page.capture_blocked_request("*"):
            started.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(prepare())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert page.is_closed()
    assert "Fetch.disable" not in [c.args[0] for c in connection.send.call_args_list]
    assert not listeners
