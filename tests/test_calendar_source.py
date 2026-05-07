from __future__ import annotations

import asyncio

import httpx
import pytest

from bot.calendar_source import (
    CalendarSourceError,
    FetchResult,
    ForexFactoryCalendarSource,
    _WeeklyExportParser,
)

VALID_PAYLOAD = [
    {
        "title": "CPI m/m",
        "country": "USD",
        "date": "2026-04-02T08:30:00-04:00",
        "impact": "High",
    }
]

def test_partial_failure_uses_successful_url():
    """If one URL fails and another succeeds, surviving events are returned."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "thisweek" in str(request.url):
            return httpx.Response(200, json=VALID_PAYLOAD)
        return httpx.Response(503)

    transport = httpx.MockTransport(handler)
    source = ForexFactoryCalendarSource(
        urls=[
            "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
            "https://nfs.faireconomy.media/ff_calendar_nextweek.json",
        ],
        timeout_seconds=1,
        max_retries=2,
        retry_backoff_seconds=0,
    )

    async def run():
        async with httpx.AsyncClient(transport=transport) as client:
            return await asyncio.gather(
                *(source._fetch_with_retries(client, url) for url in source.urls)
            )

    results = asyncio.run(run())
    succeeded = [r for r in results if r.succeeded]
    failed = [r for r in results if not r.succeeded]
    assert len(succeeded) == 1
    assert len(failed) == 1


def test_all_failures_raise_calendar_source_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    transport = httpx.MockTransport(handler)

    class CustomSource(ForexFactoryCalendarSource):
        async def fetch_events(self):  # noqa: D401 - test override
            async with httpx.AsyncClient(transport=transport, timeout=1) as client:
                results = await asyncio.gather(
                    *(self._fetch_with_retries(client, url) for url in self.urls)
                )
            self._last_results = list(results)
            successful = [r for r in results if r.succeeded]
            if not successful:
                raise CalendarSourceError("All calendar URLs failed.")
            return []

    source = CustomSource(
        urls=["https://example.com/a", "https://example.com/b"],
        timeout_seconds=1,
        max_retries=1,
        retry_backoff_seconds=0,
    )

    with pytest.raises(CalendarSourceError):
        asyncio.run(source.fetch_events())


def test_retries_on_5xx_then_succeeds():
    counter = {"calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        counter["calls"] += 1
        if counter["calls"] < 2:
            return httpx.Response(500)
        return httpx.Response(200, json=VALID_PAYLOAD)

    transport = httpx.MockTransport(handler)
    source = ForexFactoryCalendarSource(
        urls=["https://example.com/a"],
        timeout_seconds=1,
        max_retries=3,
        retry_backoff_seconds=0,
    )

    async def run():
        async with httpx.AsyncClient(transport=transport) as client:
            return await source._fetch_with_retries(client, source.urls[0])

    result = asyncio.run(run())
    assert result.succeeded
    assert counter["calls"] == 2


def test_4xx_other_than_429_does_not_retry():
    counter = {"calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        counter["calls"] += 1
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    source = ForexFactoryCalendarSource(
        urls=["https://example.com/a"],
        timeout_seconds=1,
        max_retries=3,
        retry_backoff_seconds=0,
    )

    async def run():
        async with httpx.AsyncClient(transport=transport) as client:
            return await source._fetch_with_retries(client, source.urls[0])

    result = asyncio.run(run())
    assert not result.succeeded
    assert counter["calls"] == 1


def test_partial_fetch_failure_logs_info_when_success_exists(caplog):
    source = ForexFactoryCalendarSource(["https://example.com/ok", "https://example.com/missing"])
    successful = [
        FetchResult(
            url="https://example.com/ok",
            payload=VALID_PAYLOAD,
        )
    ]
    failed = [
        FetchResult(
            url="https://example.com/missing",
            payload=None,
            error="HTTP 404",
        )
    ]

    with caplog.at_level("INFO", logger="bot.calendar_source"):
        source._log_fetch_results(successful, failed)

    assert "calendar_partial_success ok=1 failed=1" in caplog.text
    assert all(record.levelname == "INFO" for record in caplog.records)


def test_429_with_long_retry_after_does_not_retry_immediately():
    counter = {"calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        counter["calls"] += 1
        return httpx.Response(429, headers={"Retry-After": "300"})

    transport = httpx.MockTransport(handler)
    source = ForexFactoryCalendarSource(
        urls=["https://example.com/a"],
        timeout_seconds=1,
        max_retries=3,
        retry_backoff_seconds=0,
    )

    async def run():
        async with httpx.AsyncClient(transport=transport) as client:
            return await source._fetch_with_retries(client, source.urls[0])

    result = asyncio.run(run())
    assert not result.succeeded
    assert result.error == "HTTP 429 retry_after=300s"
    assert result.retry_after_seconds == 300
    assert counter["calls"] == 1


def test_html_rate_limit_response_sets_retry_after():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="You've exceeded the limit for Calendar Export requests.",
        )

    transport = httpx.MockTransport(handler)
    source = ForexFactoryCalendarSource(
        urls=["https://example.com/a"],
        timeout_seconds=1,
        max_retries=3,
        retry_backoff_seconds=0,
    )

    async def run():
        async with httpx.AsyncClient(transport=transport) as client:
            return await source._fetch_with_retries(client, source.urls[0])

    result = asyncio.run(run())
    assert not result.succeeded
    assert result.error == "rate_limited retry_after=300s"
    assert result.retry_after_seconds == 300


def test_event_parser_skips_malformed_items_without_dropping_valid_events():
    source = ForexFactoryCalendarSource(["https://example.com/a"])
    events, skipped = source._events_from_results(
        [
            FetchResult(
                url="https://example.com/a",
                payload=[
                    *VALID_PAYLOAD,
                    {"title": "", "country": "USD", "date": "2026-04-02T08:30:00-04:00"},
                    {"title": "Bad date", "country": "USD", "date": "not-a-date"},
                    {"title": "Naive date", "country": "USD", "date": "2026-04-02T08:30:00"},
                ],
            )
        ]
    )

    assert len(events) == 1
    assert skipped == 3


def test_event_parser_deduplicates_events_across_successful_urls():
    source = ForexFactoryCalendarSource(["https://example.com/a", "https://example.com/b"])
    events, skipped = source._events_from_results(
        [
            FetchResult(url="https://example.com/a", payload=VALID_PAYLOAD),
            FetchResult(url="https://example.com/b", payload=VALID_PAYLOAD),
        ]
    )

    assert len(events) == 1
    assert skipped == 0


def test_user_agent_is_sent():
    seen_headers: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.update(dict(request.headers))
        return httpx.Response(200, json=VALID_PAYLOAD)

    transport = httpx.MockTransport(handler)
    source = ForexFactoryCalendarSource(
        urls=["https://example.com/a"],
        timeout_seconds=1,
        user_agent="custom-ua/1.0",
        max_retries=1,
        retry_backoff_seconds=0,
    )

    async def run():
        async with httpx.AsyncClient(
            transport=transport,
            headers={"User-Agent": source.user_agent, "Accept": "application/json"},
        ) as client:
            return await source._fetch_with_retries(client, source.urls[0])

    asyncio.run(run())
    assert seen_headers.get("user-agent") == "custom-ua/1.0"


def test_calendar_source_error_preserves_retry_after_from_failed_results():
    source = ForexFactoryCalendarSource(["https://example.com/a"])

    error = source._all_failed_error(
        [
            FetchResult(
                url="https://example.com/a",
                payload=None,
                error="HTTP 429 retry_after=300s",
                retry_after_seconds=300,
            )
        ]
    )

    assert isinstance(error, CalendarSourceError)
    assert error.retry_after_seconds == 300


def test_weekly_export_parser_finds_json_export_link():
    parser = _WeeklyExportParser("https://www.forexfactory.com/calendar?week=may4.2026")

    parser.feed(
        '<a href="https://nfs.faireconomy.media/ff_calendar_thisweek.json?version=abc">'
        "JSON</a>"
    )

    assert parser.json_export_url == (
        "https://nfs.faireconomy.media/ff_calendar_thisweek.json?version=abc"
    )
