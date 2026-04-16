from __future__ import annotations

import httpx


class DiscordWebhookSender:
    def __init__(self, webhook_url: str, timeout_seconds: float = 10.0) -> None:
        self.webhook_url = webhook_url
        self.timeout_seconds = timeout_seconds

    async def send_message(self, content: str) -> None:
        payload = {"content": content}
        timeout = httpx.Timeout(self.timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                response = await client.post(self.webhook_url, json=payload)
                response.raise_for_status()
            except httpx.HTTPStatusError as error:
                status_code = error.response.status_code
                raise RuntimeError(
                    f"Discord webhook rejected the message with status {status_code}."
                ) from error
            except httpx.RequestError as error:
                raise RuntimeError("Failed to send Discord webhook message.") from error
