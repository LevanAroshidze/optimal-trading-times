from __future__ import annotations

import asyncio

import httpx
import pytest

from bot.webhook_sender import (
    DiscordWebhookSender,
    WebhookError,
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


def test_validate_webhook_url_truncates_long_invalid_value_in_error():
    long = "https://" + "x" * 200
    with pytest.raises(WebhookError) as exc_info:
        validate_webhook_url(long, "DISCORD_WEBHOOK_URL")
    assert "..." in str(exc_info.value)
    assert long not in str(exc_info.value)


def test_split_message_under_limit():
    chunks = DiscordWebhookSender._split_message("hello", 2000)
    assert chunks == ["hello"]


def test_split_message_splits_on_newlines():
    content = "\n".join(["Line " + str(i) * 50 for i in range(100)])
    chunks = DiscordWebhookSender._split_message(content, 500)
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= 500


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


def test_send_message_raises_on_4xx_other_than_429():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"message": "bad"})

    sender = DiscordWebhookSender(VALID_URL, timeout_seconds=1)
    transport = httpx.MockTransport(handler)

    async def run():
        async with httpx.AsyncClient(transport=transport) as client:
            await sender._post_with_retries(client, {"content": "hi"})

    with pytest.raises(WebhookError, match="status 400"):
        asyncio.run(run())


def test_send_message_retries_on_5xx_then_gives_up():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    sender = DiscordWebhookSender(VALID_URL, timeout_seconds=1, retry_backoff_seconds=0)
    transport = httpx.MockTransport(handler)

    async def run():
        async with httpx.AsyncClient(transport=transport) as client:
            await sender._post_with_retries(client, {"content": "hi"})

    with pytest.raises(WebhookError, match="status 503"):
        asyncio.run(run())
