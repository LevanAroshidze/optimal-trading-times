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
            response = await client.post(self.webhook_url, json=payload)
            response.raise_for_status()
