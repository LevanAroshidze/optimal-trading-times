from __future__ import annotations

import asyncio
import logging
import math
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)


_DISCORD_WEBHOOK_PATTERN = re.compile(
    r"^https://(?:[a-z]+\.)?discord(?:app)?\.com/api/webhooks/\d+/[A-Za-z0-9_\-]+/?$"
)
DISCORD_MESSAGE_LIMIT = 2000
_MAX_BACKOFF_SECONDS = 10.0
_MAX_IMMEDIATE_RETRY_AFTER_SECONDS = 60.0
_MAX_TIMEOUT_SECONDS = 60.0
_MAX_RETRIES = 5


class WebhookError(RuntimeError):
    """Raised when Discord delivery fails or cannot be confirmed."""


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
        raise WebhookError(f"{env_var} is not a valid Discord webhook URL.")
    return cleaned


class DiscordWebhookSender:
    def __init__(
        self,
        webhook_url: str,
        timeout_seconds: float = 10.0,
        max_message_length: int = DISCORD_MESSAGE_LIMIT,
        max_retries: int = 3,
        retry_backoff_seconds: float = 1.0,
    ) -> None:
        if (
            not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= _MAX_TIMEOUT_SECONDS
        ):
            raise ValueError("timeout_seconds must be finite and between 0 and 60")
        if (
            isinstance(max_retries, bool)
            or not 0 <= max_retries <= _MAX_RETRIES
        ):
            raise ValueError("max_retries must be between 0 and 5")
        if (
            not math.isfinite(retry_backoff_seconds)
            or retry_backoff_seconds < 0
        ):
            raise ValueError("retry_backoff_seconds must be finite and non-negative")
        if (
            isinstance(max_message_length, bool)
            or not 0 < max_message_length <= DISCORD_MESSAGE_LIMIT
        ):
            raise ValueError("max_message_length must be between 1 and 2000")
        self.webhook_url = webhook_url
        self.timeout_seconds = timeout_seconds
        self.max_message_length = max_message_length
        self.max_retries = max_retries
        self.retry_backoff_seconds = retry_backoff_seconds

    async def send_message(
        self, content: str | WebhookPayload
    ) -> None:
        if isinstance(content, WebhookPayload):
            await self._send_payload(
                {"content": content.content, "embeds": content.embeds}
            )
            return
        if not content.strip():
            raise WebhookError("Refusing to send an empty Discord message.")

        chunks = self._split_message(content, self.max_message_length)
        for chunk in chunks:
            await self._send_payload({"content": chunk})

    @staticmethod
    def _split_message(content: str, limit: int) -> list[str]:
        if not content:
            return []
        if len(content) <= limit:
            return [content]

        chunks: list[str] = []
        remaining = content
        while len(remaining) > limit:
            split_at = remaining.rfind("\n", 0, limit)
            if split_at <= 0:
                split_at = limit
            chunk = remaining[:split_at].rstrip()
            if chunk:
                chunks.append(chunk)
            remaining = remaining[split_at:].lstrip("\n")
        if remaining:
            chunks.append(remaining)
        return chunks

    async def _send_payload(self, payload: dict[str, Any]) -> None:
        payload["allowed_mentions"] = {"parse": []}
        if "embeds" in payload and not payload["embeds"]:
            payload.pop("embeds")
        timeout = httpx.Timeout(self.timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout) as client:
            await self._post_with_retries(client, payload)

    async def _post_with_retries(
        self, client: httpx.AsyncClient, payload: dict[str, Any]
    ) -> None:
        max_attempts = self.max_retries + 1
        for attempt in range(1, max_attempts + 1):
            try:
                response = await client.post(
                    self.webhook_url,
                    params={"wait": "true"},
                    json=payload,
                )
            except httpx.RequestError as error:
                # Webhook POSTs create messages. Only retry failures known to
                # precede sending the request; a lost response may hide success.
                if not isinstance(
                    error, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)
                ):
                    raise WebhookError(
                        "Discord webhook delivery is unconfirmed after "
                        f"{type(error).__name__}; not retrying to avoid a duplicate. "
                        "Check Discord before forcing a repost."
                    ) from error
                if attempt >= max_attempts:
                    raise WebhookError(
                        "Failed to send Discord webhook message after "
                        f"{max_attempts} attempts."
                    ) from error
                backoff = self._backoff_for_attempt(attempt)
                logger.warning(
                    "discord_request_failed error=%s attempt=%s/%s retry_in=%.2fs",
                    type(error).__name__,
                    attempt,
                    max_attempts,
                    backoff,
                )
                await asyncio.sleep(backoff)
                continue

            status = response.status_code
            if 200 <= status < 300:
                return

            if status == 429:
                retry_after = self._parse_retry_after(response)
                if retry_after > _MAX_IMMEDIATE_RETRY_AFTER_SECONDS:
                    raise WebhookError(
                        "Discord webhook rate limit requires waiting "
                        f"{retry_after:g}s; refusing to block the cron run."
                    )
                if attempt >= max_attempts:
                    raise WebhookError(
                        "Discord webhook rate-limited and exceeded retry attempts."
                    )
                logger.warning(
                    "discord_rate_limited attempt=%s/%s retry_after=%.2fs",
                    attempt,
                    max_attempts,
                    retry_after,
                )
                await asyncio.sleep(retry_after)
                continue

            if 500 <= status < 600:
                # A server/proxy error can arrive after the message was created.
                raise WebhookError(
                    "Discord webhook delivery is unconfirmed after "
                    f"HTTP status {status}; not retrying to avoid a duplicate. "
                    "Check Discord before forcing a repost."
                )

            raise WebhookError(
                f"Discord webhook rejected the message with status {status}."
            )

        raise AssertionError(
            "Discord webhook retry loop exited without returning or raising."
        )

    def _backoff_for_attempt(self, attempt: int) -> float:
        return min(
            self.retry_backoff_seconds * (2 ** (attempt - 1)),
            _MAX_BACKOFF_SECONDS,
        )

    @staticmethod
    def _parse_retry_after(response: httpx.Response) -> float:
        header = response.headers.get("Retry-After")
        if header:
            try:
                retry_after = DiscordWebhookSender._valid_retry_delay(float(header))
            except ValueError:
                retry_after = None
            if retry_after is not None:
                return retry_after
        try:
            data = response.json()
        except ValueError:
            return 1.0
        retry_after = data.get("retry_after") if isinstance(data, dict) else None
        if isinstance(retry_after, (int, float)) and not isinstance(
            retry_after, bool
        ):
            parsed_delay = DiscordWebhookSender._valid_retry_delay(
                float(retry_after)
            )
            if parsed_delay is not None:
                return parsed_delay
        return 1.0

    @staticmethod
    def _valid_retry_delay(value: float) -> float | None:
        if not math.isfinite(value) or value < 0:
            return None
        return value
