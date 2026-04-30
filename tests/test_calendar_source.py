from __future__ import annotations

import asyncio

import httpx
import pytest

from bot.calendar_source import CalendarSourceError, ForexFactoryCalendarSource

VALID_PAYLOAD = [
    {
        "title": "CPI m/m",
        "country": "USD",
        "date": "2026-04-02T08:30:00-04:00",
        "impact": "High",
    }
]


def _patch_client(source: ForexFactoryCalendarSource, transport: httpx.MockTransport):
    source._build_client = lambda: httpx.AsyncClient(transport=transport)  # type: ignore[attr-defined]


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
