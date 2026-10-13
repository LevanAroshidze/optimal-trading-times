from __future__ import annotations

import asyncio

import httpx
import pytest

from bot.webhook_sender import (
    DiscordWebhookSender,
    WebhookError,
    WebhookPayload,
    validate_webhook_url,
)

VALID_URL = "https://discord.com/api/webhooks/123456789012345678/abcDEFghi-_JKL"


def test_validate_webhook_url_accepts_real_format():
    assert validate_webhook_url(VALID_URL, "DISCORD_WEBHOOK_URL") == VALID_URL


def test_validate_webhook_url_rejects_empty():
    with pytest.raises(WebhookError, match="Missing required environment variable"):
        validate_webhook_url("   ", "DISCORD_WEBHOOK_URL")


def test_validate_webhook_url_rejects_other_hosts():
    with pytest.raises(WebhookError, match="not a valid Discord webhook URL"):
        validate_webhook_url("https://example.com/api/webhooks/1/abc", "DISCORD_WEBHOOK_URL")


@pytest.mark.parametrize("invalid", ["secret-token", "https://" + "x" * 200])
def test_validate_webhook_url_does_not_echo_invalid_value(invalid):
    with pytest.raises(WebhookError) as exc_info:
        validate_webhook_url(invalid, "DISCORD_WEBHOOK_URL")
    assert invalid not in str(exc_info.value)


def test_split_message_under_limit():
    chunks = DiscordWebhookSender._split_message("hello", 2000)
    assert chunks == ["hello"]


def test_split_message_splits_on_newlines():
    content = "\n".join(["Line " + str(i) * 50 for i in range(100)])
    chunks = DiscordWebhookSender._split_message(content, 500)
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= 500


def test_split_message_does_not_produce_empty_chunks():
    chunks = DiscordWebhookSender._split_message("\n" * 501 + "message", 500)

    assert chunks == ["message"]


@pytest.mark.parametrize("content", ["", "   ", "\n" * 2001])
def test_send_message_rejects_empty_plain_content(content):
    with pytest.raises(WebhookError, match="empty Discord message"):
        asyncio.run(DiscordWebhookSender(VALID_URL).send_message(content))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"timeout_seconds": float("nan")},
        {"timeout_seconds": float("inf")},
        {"retry_backoff_seconds": float("nan")},
        {"retry_backoff_seconds": float("inf")},
        {"max_message_length": 0},
    ],
)
def test_sender_rejects_values_that_can_hang_or_disable_delivery(kwargs):
    with pytest.raises(ValueError):
        DiscordWebhookSender(VALID_URL, **kwargs)


def test_send_message_success_uses_one_post():
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(204)

    sender = DiscordWebhookSender(VALID_URL, timeout_seconds=1)
    transport = httpx.MockTransport(handler)

    async def run():
        async with httpx.AsyncClient(transport=transport) as client:
            await sender._post_with_retries(client, {"content": "hi"})

    asyncio.run(run())
    assert len(calls) == 1
    assert calls[0].url.params["wait"] == "true"


def test_send_message_suppresses_mentions_in_payload(monkeypatch):
    captured: list[dict] = []

    async def capture_payload(self, client, payload):
        captured.append(payload)

    monkeypatch.setattr(DiscordWebhookSender, "_post_with_retries", capture_payload)

    asyncio.run(DiscordWebhookSender(VALID_URL).send_message("@everyone hello"))

    assert captured == [{"content": "@everyone hello", "allowed_mentions": {"parse": []}}]


def test_send_embed_suppresses_mentions_in_payload(monkeypatch):
    captured: list[dict] = []

    async def capture_payload(self, client, payload):
        captured.append(payload)

    monkeypatch.setattr(DiscordWebhookSender, "_post_with_retries", capture_payload)

    message = WebhookPayload(
        content="@everyone summary",
        embeds=[{"title": "@here calendar", "description": "<@123456789>"}],
    )
    asyncio.run(DiscordWebhookSender(VALID_URL).send_message(message))

    assert captured == [
        {
            "content": "@everyone summary",
            "embeds": [
                {"title": "@here calendar", "description": "<@123456789>"}
            ],
            "allowed_mentions": {"parse": []},
        }
    ]


def test_send_message_retries_on_429_then_succeeds():
    counter = {"calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        counter["calls"] += 1
        if counter["calls"] == 1:
            return httpx.Response(
                429,
                headers={"Retry-After": "0"},
                json={"retry_after": 0.0, "global": False},
            )
        return httpx.Response(204)

    sender = DiscordWebhookSender(VALID_URL, timeout_seconds=1)
    transport = httpx.MockTransport(handler)

    async def run():
        async with httpx.AsyncClient(transport=transport) as client:
            await sender._post_with_retries(client, {"content": "hi"})

    asyncio.run(run())
    assert counter["calls"] == 2


def test_send_message_rejects_long_429_without_sleeping(monkeypatch):
    slept: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "300"})

    async def record_sleep(delay: float) -> None:
        slept.append(delay)

    monkeypatch.setattr("bot.webhook_sender.asyncio.sleep", record_sleep)
    sender = DiscordWebhookSender(VALID_URL, timeout_seconds=1)
    transport = httpx.MockTransport(handler)

    async def run():
        async with httpx.AsyncClient(transport=transport) as client:
            await sender._post_with_retries(client, {"content": "hi"})

    with pytest.raises(WebhookError, match="refusing to block"):
        asyncio.run(run())
    assert slept == []


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(429, headers={"Retry-After": "inf"}),
        httpx.Response(429, content=b'{"retry_after": NaN}'),
        httpx.Response(429, json={"retry_after": -5}),
    ],
)
def test_retry_after_rejects_nonfinite_or_negative_values(response):
    assert DiscordWebhookSender._parse_retry_after(response) == 1.0


@pytest.mark.parametrize(
    "error_type", [httpx.ConnectTimeout, httpx.ConnectError, httpx.PoolTimeout]
)
def test_send_message_retries_before_request_is_sent(error_type):
    counter = {"calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        counter["calls"] += 1
        if counter["calls"] == 1:
            raise error_type("request not sent", request=request)
        return httpx.Response(204)

    sender = DiscordWebhookSender(
        VALID_URL,
        timeout_seconds=1,
        retry_backoff_seconds=0,
    )
    transport = httpx.MockTransport(handler)

    async def run():
        async with httpx.AsyncClient(transport=transport) as client:
            await sender._post_with_retries(client, {"content": "hi"})

    asyncio.run(run())
    assert counter["calls"] == 2


@pytest.mark.parametrize(
    "error_type",
    [
        httpx.ReadTimeout,
        httpx.ReadError,
        httpx.WriteTimeout,
        httpx.WriteError,
        httpx.RemoteProtocolError,
        httpx.RequestError,
    ],
)
def test_send_message_does_not_retry_uncertain_delivery(error_type):
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            raise error_type("delivery result unknown", request=request)
        return httpx.Response(200, json={"id": "duplicate"})

    sender = DiscordWebhookSender(VALID_URL, retry_backoff_seconds=0)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await sender._post_with_retries(client, {"content": "hi"})

    with pytest.raises(WebhookError, match="delivery is unconfirmed") as exc_info:
        asyncio.run(run())

    assert len(calls) == 1
    assert isinstance(exc_info.value.__cause__, error_type)


def test_send_message_persistent_connect_error_exhausts_configured_retries():
    counter = {"calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        counter["calls"] += 1
        raise httpx.ConnectError("connection failed", request=request)

    sender = DiscordWebhookSender(
        VALID_URL,
        timeout_seconds=1,
        max_retries=3,
        retry_backoff_seconds=0,
    )
    transport = httpx.MockTransport(handler)

    async def run():
        async with httpx.AsyncClient(transport=transport) as client:
            await sender._post_with_retries(client, {"content": "hi"})

    with pytest.raises(WebhookError, match="after 4 attempts") as exc_info:
        asyncio.run(run())

    assert counter["calls"] == 4
    assert isinstance(exc_info.value.__cause__, httpx.ConnectError)


@pytest.mark.parametrize("first_failure", ["connect_error", "rate_limit"])
def test_safe_retry_stops_as_soon_as_delivery_becomes_uncertain(first_failure):
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            if first_failure == "connect_error":
                raise httpx.ConnectError("connection failed", request=request)
            return httpx.Response(429, headers={"Retry-After": "0"})
        if len(calls) == 2:
            raise httpx.ReadTimeout("confirmation lost", request=request)
        return httpx.Response(200, json={"id": "duplicate"})

    sender = DiscordWebhookSender(VALID_URL, retry_backoff_seconds=0)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await sender._post_with_retries(client, {"content": "hi"})

    with pytest.raises(WebhookError, match="delivery is unconfirmed"):
        asyncio.run(run())
    assert len(calls) == 2


def test_send_message_raises_on_4xx_other_than_429():
    counter = {"calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        counter["calls"] += 1
        return httpx.Response(400, json={"message": "bad"})

    sender = DiscordWebhookSender(VALID_URL, timeout_seconds=1)
    transport = httpx.MockTransport(handler)

    async def run():
        async with httpx.AsyncClient(transport=transport) as client:
            await sender._post_with_retries(client, {"content": "hi"})

    with pytest.raises(WebhookError, match="status 400"):
        asyncio.run(run())
    assert counter["calls"] == 1


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_send_message_does_not_retry_uncertain_server_error(status):
    counter = {"calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        counter["calls"] += 1
        return httpx.Response(status)

    sender = DiscordWebhookSender(
        VALID_URL,
        timeout_seconds=1,
        max_retries=2,
        retry_backoff_seconds=0,
    )
    transport = httpx.MockTransport(handler)

    async def run():
        async with httpx.AsyncClient(transport=transport) as client:
            await sender._post_with_retries(client, {"content": "hi"})

    with pytest.raises(WebhookError, match=f"delivery is unconfirmed.*status {status}"):
        asyncio.run(run())
    assert counter["calls"] == 1


def test_send_message_uses_exponential_backoff_for_connect_errors(monkeypatch):
    counter = {"calls": 0}
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        counter["calls"] += 1
        if counter["calls"] <= 3:
            raise httpx.ConnectError("connection failed", request=request)
        return httpx.Response(204)

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr("bot.webhook_sender.asyncio.sleep", record_sleep)
    sender = DiscordWebhookSender(
        VALID_URL,
        timeout_seconds=1,
        max_retries=3,
        retry_backoff_seconds=1,
    )
    transport = httpx.MockTransport(handler)

    async def run():
        async with httpx.AsyncClient(transport=transport) as client:
            await sender._post_with_retries(client, {"content": "hi"})

    asyncio.run(run())

    assert counter["calls"] == 4
    assert delays == [1, 2, 4]
