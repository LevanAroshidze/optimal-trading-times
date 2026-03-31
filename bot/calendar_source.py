from __future__ import annotations

import logging
from datetime import datetime
from typing import Iterable

import httpx

from bot.models import CalendarEvent


logger = logging.getLogger(__name__)


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
        skipped_items = 0
        for payload in responses:
            for item in payload:
                title = str(item.get("title", "")).strip()
                country = str(item.get("country", "")).strip()
                date_value = item.get("date")
                if not title or not country or not date_value:
                    skipped_items += 1
                    logger.warning("Skipping malformed calendar item without title/country/date.")
                    continue

                try:
                    starts_at = datetime.fromisoformat(str(date_value))
                except ValueError:
                    skipped_items += 1
                    logger.warning("Skipping calendar item with invalid date: %r", date_value)
                    continue
                if starts_at.tzinfo is None:
                    skipped_items += 1
                    logger.warning("Skipping calendar item with timezone-naive date: %r", date_value)
                    continue

                event = CalendarEvent(
                    title=title,
                    country=country,
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

        if skipped_items:
            logger.info("Skipped %s malformed calendar item(s).", skipped_items)

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

            try:
                payload = response.json()
            except ValueError as error:
                raise CalendarSourceError(
                    f"Calendar URL returned invalid JSON: {url}"
                ) from error
            if not isinstance(payload, list):
                raise CalendarSourceError(f"Unexpected payload for calendar URL: {url}")
            responses.append(payload)
        return responses
