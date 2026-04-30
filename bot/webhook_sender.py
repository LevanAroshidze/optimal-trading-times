from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)


_DISCORD_WEBHOOK_PATTERN = re.compile(
    r"^https://(?:[a-z]+\.)?discord(?:app)?\.com/api/webhooks/\d+/[A-Za-z0-9_\-]+/?$"
)
DISCORD_MESSAGE_LIMIT = 2000
_MAX_RETRIES_429 = 3


class WebhookError(RuntimeError):
    """Raised when the Discord webhook ultimately rejects a payload."""


@dataclass(frozen=True)
class WebhookPayload:
    """Container for either plain content or an embed payload."""

    content: str = ""
    embeds: list[dict[str, Any]] = field(default_factory=list)


def validate_webhook_url(url: str, env_var: str) -> str:
    cleaned = url.strip()
    if not cleaned:
        raise WebhookError(
            f"Missing required environment variable: {env_var}. "
            f"Set it to a Discord webhook URL."
        )
    if not _DISCORD_WEBHOOK_PATTERN.match(cleaned):
        prefix = cleaned[:32] + "..." if len(cleaned) > 32 else cleaned
        raise WebhookError(
            f"{env_var} is not a valid Discord webhook URL "
            f"(starts with {prefix!r})."
        )
    return cleaned


class DiscordWebhookSender:
    def __init__(
        self,
        webhook_url: str,
        timeout_seconds: float = 10.0,
        max_message_length: int = DISCORD_MESSAGE_LIMIT,
        retry_backoff_seconds: float = 1.0,
    ) -> None:
        self.webhook_url = webhook_url
        self.timeout_seconds = timeout_seconds
        self.max_message_length = max_message_length
        self.retry_backoff_seconds = retry_backoff_seconds

    async def send_message(
        self, content: str | WebhookPayload
    ) -> None:
        if isinstance(content, WebhookPayload):
            await self._send_payload(
                {"content": content.content, "embeds": content.embeds}
            )
            return

        chunks = self._split_message(content, self.max_message_length)
        for chunk in chunks:
            await self._send_payload({"content": chunk})

    @staticmethod
    def _split_message(content: str, limit: int) -> list[str]:
        if len(content) <= limit:
            return [content]

        chunks: list[str] = []
        remaining = content
        while len(remaining) > limit:
            split_at = remaining.rfind("\n", 0, limit)
            if split_at <= 0:
                split_at = limit
            chunks.append(remaining[:split_at].rstrip())
            remaining = remaining[split_at:].lstrip("\n")
        if remaining:
            chunks.append(remaining)
        return chunks

    async def _send_payload(self, payload: dict[str, Any]) -> None:
        if "embeds" in payload and not payload["embeds"]:
            payload.pop("embeds")
        timeout = httpx.Timeout(self.timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout) as client:
            await self._post_with_retries(client, payload)

    async def _post_with_retries(
        self, client: httpx.AsyncClient, payload: dict[str, Any]
    ) -> None:
        attempt = 0
        while attempt <= _MAX_RETRIES_429:
            attempt += 1
            try:
                response = await client.post(self.webhook_url, json=payload)
            except httpx.RequestError as error:
                raise WebhookError("Failed to send Discord webhook message.") from error

            status = response.status_code
            if 200 <= status < 300:
                return

            if status == 429 and attempt <= _MAX_RETRIES_429:
                retry_after = self._parse_retry_after(response)
                logger.warning(
                    "discord_rate_limited attempt=%s retry_after=%.2fs",
                    attempt,
                    retry_after,
                )
                await asyncio.sleep(retry_after)
                continue

            if 500 <= status < 600 and attempt <= _MAX_RETRIES_429:
                backoff = min(self.retry_backoff_seconds * (2 ** (attempt - 1)), 10)
                await asyncio.sleep(backoff)
                continue

            raise WebhookError(
                f"Discord webhook rejected the message with status {status}."
            )

        raise WebhookError(
            "Discord webhook rate-limited and exceeded retry attempts."
        )

    @staticmethod
    def _parse_retry_after(response: httpx.Response) -> float:
        header = response.headers.get("Retry-After")
        if header:
            try:
                return float(header)
            except ValueError:
                pass
        try:
            data = response.json()
        except ValueError:
            return 1.0
        retry_after = data.get("retry_after") if isinstance(data, dict) else None
        if isinstance(retry_after, (int, float)):
            return float(retry_after)
        return 1.0
