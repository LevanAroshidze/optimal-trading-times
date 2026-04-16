from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime
from typing import Iterable

import httpx

from bot.models import CalendarEvent
from bot.type_defs import StructuredValue


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

    async def _fetch_all(
        self, client: httpx.AsyncClient
    ) -> list[list[Mapping[str, StructuredValue]]]:
        responses: list[list[Mapping[str, StructuredValue]]] = []
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
            responses.append(self._coerce_payload(payload, url))
        return responses

    def _coerce_payload(
        self, payload: StructuredValue, url: str
    ) -> list[Mapping[str, StructuredValue]]:
        if not isinstance(payload, list):
            raise CalendarSourceError(f"Unexpected payload for calendar URL: {url}")

        items: list[Mapping[str, StructuredValue]] = []
        for item in payload:
            if not isinstance(item, Mapping):
                raise CalendarSourceError(
                    f"Calendar payload item must be a mapping for URL: {url}"
                )
            items.append(
                {
                    str(key): value
                    for key, value in item.items()
                }
            )
        return items
