from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import urljoin

import httpx

from bot.models import CalendarEvent
from bot.type_defs import StructuredValue

logger = logging.getLogger(__name__)

_HTTP_TOO_MANY_REQUESTS = 429
_HTTP_SERVER_ERROR_MIN = 500
_MAX_RETRY_AFTER_SECONDS = 30.0
_MAX_PERSISTED_RETRY_AFTER_SECONDS = 86_400.0
_MAX_TIMEOUT_SECONDS = 60.0
_MAX_RETRIES = 5
_MAX_BACKOFF_SECONDS = 10.0
_EXPORT_RATE_LIMIT_TEXT = "exceeded the limit for calendar export requests"


class CalendarSourceError(RuntimeError):
    """Raised when Forex Factory calendar data cannot be loaded."""

    def __init__(self, message: str, retry_after_seconds: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True)
class FetchResult:
    url: str
    payload: list[Mapping[str, StructuredValue]] | None
    error: str | None = None
    retry_after_seconds: float | None = None

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
        if (
            not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= _MAX_TIMEOUT_SECONDS
        ):
            raise ValueError("timeout_seconds must be finite and between 0 and 60")
        if (
            isinstance(max_retries, bool)
            or not 1 <= max_retries <= _MAX_RETRIES
        ):
            raise ValueError("max_retries must be between 1 and 5")
        if (
            not math.isfinite(retry_backoff_seconds)
            or not 0 <= retry_backoff_seconds <= _MAX_BACKOFF_SECONDS
        ):
            raise ValueError(
                "retry_backoff_seconds must be finite and between 0 and 10"
            )
        self.urls = list(urls)
        self.timeout_seconds = timeout_seconds
        self.user_agent = user_agent
        self.max_retries = max_retries
        self.retry_backoff_seconds = retry_backoff_seconds
        self._last_results: list[FetchResult] = []

    @property
    def last_results(self) -> list[FetchResult]:
        return list(self._last_results)

    async def fetch_events(self) -> list[CalendarEvent]:
        if not self.urls:
            raise CalendarSourceError("No calendar URLs configured.")

        timeout = httpx.Timeout(self.timeout_seconds)
        headers = {"User-Agent": self.user_agent, "Accept": "application/json"}
        async with httpx.AsyncClient(
            timeout=timeout, headers=headers, follow_redirects=True
        ) as client:
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

    async def fetch_events_for_week(self, week_start: date) -> list[CalendarEvent]:
        week_token = self._week_token(week_start)
        page_url = f"https://www.forexfactory.com/calendar?week={week_token}"
        timeout = httpx.Timeout(self.timeout_seconds)
        page_headers = {"User-Agent": self.user_agent, "Accept": "text/html"}
        json_headers = {"User-Agent": self.user_agent, "Accept": "application/json"}
        async with httpx.AsyncClient(
            timeout=timeout, headers=page_headers, follow_redirects=True
        ) as client:
            export_url = await self._fetch_week_export_url(client, page_url)
        async with httpx.AsyncClient(
            timeout=timeout, headers=json_headers, follow_redirects=True
        ) as client:
            result = await self._fetch_with_retries(client, export_url)
        self._last_results = [result]

        if not result.succeeded:
            raise self._all_failed_error([result])

        events, skipped_items = self._events_from_results([result])
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
                    event.title.casefold(),
                    event.country.upper(),
                    event.starts_at.astimezone(UTC).isoformat(),
                )
                existing = events.get(unique_key)
                if (
                    existing is None
                    or self._impact_rank(event.impact)
                    > self._impact_rank(existing.impact)
                ):
                    events[unique_key] = event

        return events, skipped_items

    @staticmethod
    def _impact_rank(impact: str) -> int:
        return {"holiday": 4, "high": 3, "medium": 2, "low": 1}.get(
            impact.casefold(), 0
        )

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
        retry_after_values = [
            result.retry_after_seconds
            for result in failed
            if result.retry_after_seconds is not None
        ]
        return CalendarSourceError(
            f"All calendar URLs failed. {joined}" if joined else "All calendar URLs failed.",
            retry_after_seconds=max(retry_after_values) if retry_after_values else None,
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
                        return FetchResult(
                            url=url,
                            payload=None,
                            error=last_error,
                            retry_after_seconds=retry_after,
                        )
                    if attempt < self.max_retries:
                        await self._sleep_before_retry(attempt, retry_after)
                    continue
                if status_code < _HTTP_SERVER_ERROR_MIN:
                    return FetchResult(url=url, payload=None, error=last_error)
            except httpx.HTTPError as error:
                last_error = error.__class__.__name__
            else:
                if self._looks_rate_limited(response):
                    return FetchResult(
                        url=url,
                        payload=None,
                        error="rate_limited retry_after=300s",
                        retry_after_seconds=300.0,
                    )
                try:
                    payload = response.json()
                except ValueError:
                    last_error = "invalid_json"
                else:
                    retry_after = self._retry_after_from_payload(payload)
                    if retry_after is not None:
                        last_error = f"rate_limited retry_after={retry_after:g}s"
                        if retry_after > _MAX_RETRY_AFTER_SECONDS:
                            return FetchResult(
                                url=url,
                                payload=None,
                                error=last_error,
                                retry_after_seconds=retry_after,
                            )
                        if attempt < self.max_retries:
                            await self._sleep_before_retry(attempt, retry_after)
                        continue
                    try:
                        coerced = self._coerce_payload(payload, url)
                    except CalendarSourceError as error:
                        last_error = str(error)
                    else:
                        if coerced:
                            return FetchResult(url=url, payload=coerced, error=None)
                        last_error = "empty_payload"

            if attempt < self.max_retries and self.retry_backoff_seconds:
                await self._sleep_before_retry(attempt)

        return FetchResult(url=url, payload=None, error=last_error or "unknown")

    async def _sleep_before_retry(
        self, attempt: int, retry_after: float | None = None
    ) -> None:
        delay = retry_after
        if delay is None:
            delay = min(
                self.retry_backoff_seconds * (2 ** (attempt - 1)),
                _MAX_BACKOFF_SECONDS,
            )
        if delay > 0:
            await asyncio.sleep(delay)

    def _parse_retry_after(self, response: httpx.Response) -> float | None:
        header = response.headers.get("Retry-After")
        if header:
            try:
                retry_after = self._valid_retry_after(float(header))
            except ValueError:
                try:
                    retry_at = parsedate_to_datetime(header)
                except (TypeError, ValueError):
                    retry_after = None
                else:
                    if retry_at.tzinfo is None:
                        retry_at = retry_at.replace(tzinfo=UTC)
                    retry_after = self._valid_retry_after(
                        (
                            retry_at.astimezone(UTC) - datetime.now(tz=UTC)
                        ).total_seconds()
                    )
            if retry_after is not None:
                return retry_after
        try:
            data = response.json()
        except ValueError:
            return None
        retry_after = data.get("retry_after") if isinstance(data, dict) else None
        if isinstance(retry_after, (int, float)) and not isinstance(
            retry_after, bool
        ):
            return self._valid_retry_after(float(retry_after))
        return None

    def _retry_after_from_payload(self, payload: StructuredValue) -> float | None:
        retry_after = payload.get("retry_after") if isinstance(payload, dict) else None
        if isinstance(retry_after, (int, float)) and not isinstance(
            retry_after, bool
        ):
            return self._valid_retry_after(float(retry_after))
        return None

    @staticmethod
    def _valid_retry_after(value: float) -> float | None:
        if not math.isfinite(value):
            return None
        return min(max(0.0, value), _MAX_PERSISTED_RETRY_AFTER_SECONDS)

    def _looks_rate_limited(self, response: httpx.Response) -> bool:
        return _EXPORT_RATE_LIMIT_TEXT in response.text.casefold()

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

    async def _fetch_week_export_url(
        self, client: httpx.AsyncClient, page_url: str
    ) -> str:
        last_error: CalendarSourceError | None = None
        for attempt in range(1, self.max_retries + 1):
            retry_after: float | None = None
            try:
                response = await client.get(page_url)
                response.raise_for_status()
            except httpx.HTTPStatusError as error:
                status = error.response.status_code
                if status == _HTTP_TOO_MANY_REQUESTS:
                    retry_after = self._parse_retry_after(error.response)
                last_error = CalendarSourceError(
                    f"Weekly calendar page failed with HTTP {status}: {page_url}",
                    retry_after_seconds=retry_after,
                )
                retryable = status == _HTTP_TOO_MANY_REQUESTS or status >= 500
                if not retryable:
                    raise last_error from error
                if retry_after is not None and retry_after > _MAX_RETRY_AFTER_SECONDS:
                    raise last_error from error
            except httpx.HTTPError as error:
                last_error = CalendarSourceError(
                    "Weekly calendar page failed with "
                    f"{error.__class__.__name__}: {page_url}"
                )
            else:
                parser = _WeeklyExportParser(page_url)
                parser.feed(response.text)
                if parser.json_export_url is not None:
                    return parser.json_export_url
                if _EXPORT_RATE_LIMIT_TEXT in response.text.casefold():
                    raise CalendarSourceError(
                        "Weekly calendar export was rate limited.",
                        retry_after_seconds=300.0,
                    )
                last_error = CalendarSourceError(
                    "Weekly calendar page did not include a JSON export link: "
                    f"{page_url}"
                )

            if attempt < self.max_retries:
                await self._sleep_before_retry(attempt, retry_after)

        assert last_error is not None
        raise last_error

    def _week_token(self, week_start: date) -> str:
        return f"{week_start.strftime('%b').casefold()}{week_start.day}.{week_start.year}"


class _WeeklyExportParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.json_export_url: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a" or self.json_export_url is not None:
            return
        values = dict(attrs)
        href = values.get("href")
        if href and "ff_calendar_thisweek.json" in href:
            self.json_export_url = urljoin(self.base_url, href)
