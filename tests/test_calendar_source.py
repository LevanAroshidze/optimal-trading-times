from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

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


@pytest.mark.parametrize(
    "kwargs",
    [
        {"timeout_seconds": float("nan")},
        {"timeout_seconds": float("inf")},
        {"max_retries": 0},
        {"retry_backoff_seconds": float("nan")},
        {"retry_backoff_seconds": float("inf")},
    ],
)
def test_calendar_source_rejects_invalid_retry_configuration(kwargs):
    with pytest.raises(ValueError):
        ForexFactoryCalendarSource(["https://example.com/a"], **kwargs)


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


def test_calendar_retries_use_exponential_backoff(monkeypatch):
    counter = {"calls": 0}
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        counter["calls"] += 1
        if counter["calls"] <= 3:
            return httpx.Response(503)
        return httpx.Response(200, json=VALID_PAYLOAD)

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr("bot.calendar_source.asyncio.sleep", record_sleep)
    source = ForexFactoryCalendarSource(
        ["https://example.com/a"], max_retries=4, retry_backoff_seconds=1
    )

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            return await source._fetch_with_retries(client, source.urls[0])

    result = asyncio.run(run())

    assert result.succeeded
    assert delays == [1, 2, 4]


@pytest.mark.parametrize(
    ("first_response", "first_error"),
    [
        (httpx.Response(200, text="not-json"), "invalid_json"),
        (httpx.Response(200, json=[]), "empty_payload"),
    ],
)
def test_retries_malformed_or_empty_success_response(first_response, first_error):
    counter = {"calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        counter["calls"] += 1
        if counter["calls"] == 1:
            return first_response
        return httpx.Response(200, json=VALID_PAYLOAD)

    source = ForexFactoryCalendarSource(
        ["https://example.com/a"], max_retries=2, retry_backoff_seconds=0
    )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await source._fetch_with_retries(client, source.urls[0])

    result = asyncio.run(run())

    assert result.succeeded, first_error
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


def test_429_with_http_date_retry_after_does_not_retry_immediately():
    counter = {"calls": 0}
    retry_at = datetime.now(tz=UTC) + timedelta(minutes=5)

    def handler(request: httpx.Request) -> httpx.Response:
        counter["calls"] += 1
        return httpx.Response(
            429,
            headers={"Retry-After": format_datetime(retry_at, usegmt=True)},
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
    assert result.retry_after_seconds is not None
    assert result.retry_after_seconds > 250
    assert counter["calls"] == 1


def test_retry_after_rejects_nonfinite_and_caps_extreme_value():
    source = ForexFactoryCalendarSource(["https://example.com/a"])

    assert source._parse_retry_after(httpx.Response(429, headers={"Retry-After": "inf"})) is None
    assert (
        source._parse_retry_after(httpx.Response(429, headers={"Retry-After": "999999999"}))
        == 86_400
    )
    assert source._retry_after_from_payload({"retry_after": float("nan")}) is None


def test_retry_after_falls_back_to_json_when_header_is_invalid():
    source = ForexFactoryCalendarSource(["https://example.com/a"])

    assert (
        source._parse_retry_after(
            httpx.Response(
                429,
                headers={"Retry-After": "invalid"},
                json={"retry_after": 12.5},
            )
        )
        == 12.5
    )


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


def test_rate_limit_body_is_detected_despite_misleading_content_type():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            text="YOU'VE EXCEEDED THE LIMIT FOR CALENDAR EXPORT REQUESTS.",
        )

    source = ForexFactoryCalendarSource(
        ["https://example.com/a"], max_retries=1
    )

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            return await source._fetch_with_retries(client, source.urls[0])

    result = asyncio.run(run())

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


def test_event_deduplication_keeps_more_conservative_impact():
    source = ForexFactoryCalendarSource(
        ["https://example.com/a", "https://example.com/b"]
    )
    low_impact_duplicate = dict(VALID_PAYLOAD[0], impact="Low", title="cpi M/M")

    events, skipped = source._events_from_results(
        [
            FetchResult(url="https://example.com/a", payload=VALID_PAYLOAD),
            FetchResult(
                url="https://example.com/b", payload=[low_impact_duplicate]
            ),
        ]
    )

    assert len(events) == 1
    assert next(iter(events.values())).impact == "High"
    assert skipped == 0


def test_event_deduplication_treats_holiday_as_more_conservative_than_high():
    source = ForexFactoryCalendarSource(
        ["https://example.com/a", "https://example.com/b"]
    )
    holiday_duplicate = dict(
        VALID_PAYLOAD[0], impact="Holiday", title="cpi M/M"
    )

    events, skipped = source._events_from_results(
        [
            FetchResult(url="https://example.com/a", payload=VALID_PAYLOAD),
            FetchResult(
                url="https://example.com/b", payload=[holiday_duplicate]
            ),
        ]
    )

    assert len(events) == 1
    assert next(iter(events.values())).impact == "Holiday"
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
        '<a href="https://nfs.faireconomy.media/ff_calendar_thisweek.json?version=abc">JSON</a>'
    )

    assert parser.json_export_url == (
        "https://nfs.faireconomy.media/ff_calendar_thisweek.json?version=abc"
    )


def test_weekly_calendar_page_retries_transient_failure():
    counter = {"calls": 0}
    page_url = "https://www.forexfactory.com/calendar?week=may4.2026"

    def handler(request: httpx.Request) -> httpx.Response:
        counter["calls"] += 1
        if counter["calls"] == 1:
            return httpx.Response(503)
        return httpx.Response(
            200,
            text=(
                '<a href="https://nfs.faireconomy.media/'
                'ff_calendar_thisweek.json?version=abc">JSON</a>'
            ),
        )

    source = ForexFactoryCalendarSource(
        ["https://example.com/a"], max_retries=2, retry_backoff_seconds=0
    )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await source._fetch_week_export_url(client, page_url)

    export_url = asyncio.run(run())

    assert export_url.endswith("ff_calendar_thisweek.json?version=abc")
    assert counter["calls"] == 2


def test_weekly_calendar_page_does_not_retry_permanent_4xx():
    counter = {"calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        counter["calls"] += 1
        return httpx.Response(404)

    source = ForexFactoryCalendarSource(
        ["https://example.com/a"], max_retries=3, retry_backoff_seconds=0
    )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await source._fetch_week_export_url(
                client, "https://www.forexfactory.com/calendar?week=may4.2026"
            )

    with pytest.raises(CalendarSourceError, match="HTTP 404"):
        asyncio.run(run())
    assert counter["calls"] == 1
