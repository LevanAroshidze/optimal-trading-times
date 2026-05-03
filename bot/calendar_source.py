from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime

import httpx

from bot.models import CalendarEvent
from bot.type_defs import StructuredValue

logger = logging.getLogger(__name__)

_HTTP_TOO_MANY_REQUESTS = 429
_HTTP_SERVER_ERROR_MIN = 500
_MAX_RETRY_AFTER_SECONDS = 30.0


class CalendarSourceError(RuntimeError):
    """Raised when Forex Factory calendar data cannot be loaded."""


@dataclass(frozen=True)
class FetchResult:
    url: str
    payload: list[Mapping[str, StructuredValue]] | None
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.error is None and self.payload is not None


class ForexFactoryCalendarSource:
    def __init__(
        self,
        urls: Iterable[str],
        timeout_seconds: float = 10.0,
        user_agent: str = "optimal-trade-sessions-bot/0.1",
        max_retries: int = 3,
        retry_backoff_seconds: float = 1.0,
    ) -> None:
        self.urls = list(urls)
        self.timeout_seconds = timeout_seconds
        self.user_agent = user_agent
        self.max_retries = max(1, max_retries)
        self.retry_backoff_seconds = max(0.0, retry_backoff_seconds)
        self._last_results: list[FetchResult] = []

    @property
    def last_results(self) -> list[FetchResult]:
        return list(self._last_results)

    async def fetch_events(self) -> list[CalendarEvent]:
        if not self.urls:
            raise CalendarSourceError("No calendar URLs configured.")

        timeout = httpx.Timeout(self.timeout_seconds)
        headers = {"User-Agent": self.user_agent, "Accept": "application/json"}
        async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
            results = await self._fetch_results(client)
        self._last_results = list(results)

        successful, failed = self._partition_results(results)
        self._log_fetch_results(successful, failed)

        if not successful:
            raise self._all_failed_error(failed)

        events, skipped_items = self._events_from_results(successful)
        if not events:
            raise CalendarSourceError("Calendar response was empty.")

        if skipped_items:
            logger.info("calendar_skipped_items count=%s", skipped_items)

        return sorted(events.values(), key=lambda event: event.starts_at)

    async def _fetch_results(self, client: httpx.AsyncClient) -> list[FetchResult]:
        return list(
            await asyncio.gather(
                *(self._fetch_with_retries(client, url) for url in self.urls)
            )
        )

    def _partition_results(
        self, results: list[FetchResult]
    ) -> tuple[list[FetchResult], list[FetchResult]]:
        successful = [result for result in results if result.succeeded]
        failed = [result for result in results if not result.succeeded]
        return successful, failed

    def _log_fetch_results(
        self, successful: list[FetchResult], failed: list[FetchResult]
    ) -> None:
        if not failed:
            return

        log = logger.info if successful else logger.warning
        for result in failed:
            log(
                "calendar_fetch_failed url=%s error=%s",
                result.url,
                result.error,
            )

        if successful:
            logger.info(
                "calendar_partial_success ok=%s failed=%s",
                len(successful),
                len(failed),
            )

    def _events_from_results(
        self, successful: list[FetchResult]
    ) -> tuple[dict[tuple[str, str, str], CalendarEvent], int]:
        events: dict[tuple[str, str, str], CalendarEvent] = {}
        skipped_items = 0
        for result in successful:
            assert result.payload is not None
            for item in result.payload:
                event = self._event_from_item(item)
                if event is None:
                    skipped_items += 1
                    continue

                unique_key = (
                    event.title,
                    event.country,
                    event.starts_at.isoformat(),
                )
                events[unique_key] = event

        return events, skipped_items

    def _event_from_item(
        self, item: Mapping[str, StructuredValue]
    ) -> CalendarEvent | None:
        title = str(item.get("title", "")).strip()
        country = str(item.get("country", "")).strip()
        date_value = item.get("date")
        if not title or not country or not date_value:
            logger.warning(
                "calendar_item_missing_fields title=%r country=%r date=%r",
                title,
                country,
                date_value,
            )
            return None

        try:
            starts_at = datetime.fromisoformat(str(date_value))
        except ValueError:
            logger.warning("calendar_item_bad_date date=%r", date_value)
            return None
        if starts_at.tzinfo is None:
            logger.warning("calendar_item_naive_date date=%r", date_value)
            return None

        return CalendarEvent(
            title=title,
            country=country,
            impact=str(item.get("impact", "")).strip(),
            starts_at=starts_at,
        )

    def _all_failed_error(self, failed: list[FetchResult]) -> CalendarSourceError:
        joined = "; ".join(f"{result.url}: {result.error}" for result in failed)
        return CalendarSourceError(
            f"All calendar URLs failed. {joined}" if joined else "All calendar URLs failed."
        )

    async def _fetch_all(
        self, client: httpx.AsyncClient
    ) -> list[list[Mapping[str, StructuredValue]]]:
        """Compatibility shim retained for tests that subclass this method."""

        results = await asyncio.gather(
            *(self._fetch_with_retries(client, url) for url in self.urls)
        )
        self._last_results = list(results)
        successful = [result.payload for result in results if result.succeeded]
        if not successful:
            joined = "; ".join(
                f"{result.url}: {result.error}" for result in results if result.error
            )
            raise CalendarSourceError(
                f"All calendar URLs failed. {joined}" if joined else "All calendar URLs failed."
            )
        return [payload for payload in successful if payload is not None]

    async def _fetch_with_retries(
        self, client: httpx.AsyncClient, url: str
    ) -> FetchResult:
        attempt = 0
        last_error: str | None = None
        while attempt < self.max_retries:
            attempt += 1
            try:
                response = await client.get(url)
                response.raise_for_status()
            except httpx.HTTPStatusError as error:
                status_code = error.response.status_code
                last_error = f"HTTP {status_code}"
                if status_code == _HTTP_TOO_MANY_REQUESTS:
                    retry_after = self._parse_retry_after(error.response)
                    if retry_after is not None:
                        last_error = f"HTTP 429 retry_after={retry_after:g}s"
                    if retry_after is not None and retry_after > _MAX_RETRY_AFTER_SECONDS:
                        return FetchResult(url=url, payload=None, error=last_error)
                    if attempt < self.max_retries:
                        await self._sleep_before_retry(attempt, retry_after)
                    continue
                if status_code < _HTTP_SERVER_ERROR_MIN:
                    return FetchResult(url=url, payload=None, error=last_error)
            except httpx.HTTPError as error:
                last_error = error.__class__.__name__
            else:
                try:
                    payload = response.json()
                except ValueError:
                    return FetchResult(
                        url=url, payload=None, error="invalid_json"
                    )
                try:
                    coerced = self._coerce_payload(payload, url)
                except CalendarSourceError as error:
                    return FetchResult(url=url, payload=None, error=str(error))
                return FetchResult(url=url, payload=coerced, error=None)

            if attempt < self.max_retries and self.retry_backoff_seconds:
                await self._sleep_before_retry(attempt)

        return FetchResult(url=url, payload=None, error=last_error or "unknown")

    async def _sleep_before_retry(
        self, attempt: int, retry_after: float | None = None
    ) -> None:
        delay = retry_after
        if delay is None:
            delay = self.retry_backoff_seconds * attempt
        if delay > 0:
            await asyncio.sleep(delay)

    def _parse_retry_after(self, response: httpx.Response) -> float | None:
        header = response.headers.get("Retry-After")
        if header:
            try:
                return max(0.0, float(header))
            except ValueError:
                return None
        try:
            data = response.json()
        except ValueError:
            return None
        retry_after = data.get("retry_after") if isinstance(data, dict) else None
        if isinstance(retry_after, (int, float)):
            return max(0.0, float(retry_after))
        return None

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
            items.append({str(key): value for key, value in item.items()})
        return items
