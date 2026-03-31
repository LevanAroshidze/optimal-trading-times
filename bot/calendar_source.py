from __future__ import annotations

from datetime import datetime
from typing import Iterable

import httpx

from bot.models import CalendarEvent


class CalendarSourceError(RuntimeError):
    """Raised when Forex Factory calendar data cannot be loaded."""


class ForexFactoryCalendarSource:
    def __init__(self, urls: Iterable[str], timeout_seconds: float = 10.0) -> None:
        self.urls = list(urls)
        self.timeout_seconds = timeout_seconds

    async def fetch_events(self) -> list[CalendarEvent]:
        if not self.urls:
            raise CalendarSourceError("No calendar URLs configured.")

        timeout = httpx.Timeout(self.timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout) as client:
            responses = await self._fetch_all(client)

        events: dict[tuple[str, str, str], CalendarEvent] = {}
        for payload in responses:
            for item in payload:
                try:
                    starts_at = datetime.fromisoformat(item["date"])
                except (KeyError, TypeError, ValueError) as error:
                    raise CalendarSourceError(
                        f"Invalid event date in calendar payload: {item!r}"
                    ) from error

                event = CalendarEvent(
                    title=str(item.get("title", "")).strip(),
                    country=str(item.get("country", "")).strip(),
                    impact=str(item.get("impact", "")).strip(),
                    starts_at=starts_at,
                    forecast=str(item.get("forecast", "")).strip(),
                    previous=str(item.get("previous", "")).strip(),
                )
                unique_key = (
                    event.title,
                    event.country,
                    event.starts_at.isoformat(),
                )
                events[unique_key] = event

        if not events:
            raise CalendarSourceError("Calendar response was empty.")

        return sorted(events.values(), key=lambda event: event.starts_at)

    async def _fetch_all(self, client: httpx.AsyncClient) -> list[list[dict]]:
        responses: list[list[dict]] = []
        for url in self.urls:
            try:
                response = await client.get(url)
                response.raise_for_status()
            except httpx.HTTPError as error:
                raise CalendarSourceError(
                    f"Failed to fetch calendar URL: {url}"
                ) from error

            payload = response.json()
            if not isinstance(payload, list):
                raise CalendarSourceError(f"Unexpected payload for calendar URL: {url}")
            responses.append(payload)
        return responses

