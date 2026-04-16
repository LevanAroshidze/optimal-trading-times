from datetime import date, datetime
import asyncio
from collections.abc import Mapping

import httpx

from bot.calendar_features import CalendarFeatureBuilder
from bot.calendar_source import ForexFactoryCalendarSource
from bot.config import EventAliasConfig
from bot.models import CalendarEvent, DayColor
from bot.type_defs import StructuredValue


def make_aliases(**overrides) -> EventAliasConfig:
    title_aliases = {
        category: tuple(values)
        for category, values in overrides.pop("title_aliases", {}).items()
    }
    strongest_categories = overrides.pop(
        "strongest_categories", ("NFP", "CPI", "PPI", "FOMC", "POWELL")
    )
    partial_holiday_terms = overrides.pop(
        "partial_holiday_terms", ("early close", "half day")
    )
    return EventAliasConfig(
        title_aliases=title_aliases,
        strongest_categories=tuple(strongest_categories),
        partial_holiday_terms=tuple(partial_holiday_terms),
    )


def test_calendar_feature_builder_detects_nfp_and_powell_offset():
    aliases = make_aliases(
        title_aliases={
            "NFP": ["non-farm employment change"],
            "CPI": ["cpi"],
            "POWELL": ["fed chair powell speaks"],
            "RED_830AM": ["unemployment claims"],
        }
    )
    builder = CalendarFeatureBuilder(aliases, "America/New_York")

    events = [
        CalendarEvent(
            title="Non-Farm Employment Change",
            country="USD",
            impact="High",
            starts_at=datetime.fromisoformat("2026-04-03T08:30:00-04:00"),
        ),
        CalendarEvent(
            title="Fed Chair Powell Speaks",
            country="USD",
            impact="High",
            starts_at=datetime.fromisoformat("2026-04-02T10:00:00-04:00"),
        ),
    ]

    nfp_features = builder.build_for_date(date(2026, 3, 30), events)
    powell_previous_day_features = builder.build_for_date(date(2026, 4, 1), events)

    assert nfp_features["week_type"] == "NFP"
    assert powell_previous_day_features["days_until_powell_10am"] == 1


def test_calendar_feature_builder_detects_830_red_news():
    aliases = make_aliases(title_aliases={"RED_830AM": ["unemployment claims"]})
    builder = CalendarFeatureBuilder(aliases, "America/New_York")

    events = [
        CalendarEvent(
            title="Unemployment Claims",
            country="USD",
            impact="High",
            starts_at=datetime.fromisoformat("2026-04-02T08:30:00-04:00"),
        )
    ]

    features = builder.build_for_date(date(2026, 4, 2), events)
    assert features["has_830_red_news"] is True


def test_calendar_feature_builder_ignores_non_usd_cpi_for_us_rules():
    aliases = make_aliases(title_aliases={"CPI": ["cpi"]})
    builder = CalendarFeatureBuilder(aliases, "America/New_York")

    events = [
        CalendarEvent(
            title="German Prelim CPI m/m",
            country="EUR",
            impact="High",
            starts_at=datetime.fromisoformat("2026-04-02T08:30:00-04:00"),
        )
    ]

    features = builder.build_for_date(date(2026, 4, 1), events)
    assert features["days_before_cpi"] is None


def test_calendar_source_skips_malformed_items():
    class FakeSource(ForexFactoryCalendarSource):
        async def _fetch_all(
            self, client: httpx.AsyncClient
        ) -> list[list[Mapping[str, StructuredValue]]]:
            return [
                [
                    {
                        "title": "CPI m/m",
                        "country": "USD",
                        "date": "2026-04-02T08:30:00-04:00",
                        "impact": "High",
                    },
                    {
                        "title": "",
                        "country": "USD",
                        "date": "2026-04-02T08:30:00-04:00",
                        "impact": "High",
                    },
                    {
                        "title": "Bad Date",
                        "country": "USD",
                        "date": "not-a-date",
                        "impact": "High",
                    },
                    {
                        "title": "Naive Date",
                        "country": "USD",
                        "date": "2026-04-02T08:30:00",
                        "impact": "High",
                    },
                ]
            ]

    source = FakeSource(urls=["https://example.com/calendar.json"])
    events = asyncio.run(source.fetch_events())

    assert len(events) == 1
    assert events[0].title == "CPI m/m"


def test_calendar_feature_builder_detects_usd_bank_holiday_only():
    aliases = make_aliases()
    builder = CalendarFeatureBuilder(aliases, "America/New_York")

    events = [
        CalendarEvent(
            title="Bank Holiday",
            country="USD",
            impact="Holiday",
            starts_at=datetime.fromisoformat("2026-04-02T08:30:00-04:00"),
        ),
        CalendarEvent(
            title="Bank Holiday",
            country="GBP",
            impact="Holiday",
            starts_at=datetime.fromisoformat("2026-04-02T08:30:00-04:00"),
        ),
    ]

    features = builder.build_for_date(date(2026, 4, 2), events)
    assert features["has_bank_holiday_today"] is True


def test_calendar_feature_builder_detects_partial_holiday():
    aliases = make_aliases()
    builder = CalendarFeatureBuilder(aliases, "America/New_York")

    events = [
        CalendarEvent(
            title="Early Close",
            country="USD",
            impact="Holiday",
            starts_at=datetime.fromisoformat("2026-04-02T13:00:00-04:00"),
        )
    ]

    features = builder.build_for_date(date(2026, 4, 2), events)
    assert features["has_partial_holiday_today"] is True
    assert features["has_bank_holiday_today"] is False


def test_calendar_feature_builder_ignores_non_usd_holiday_days():
    aliases = make_aliases()
    builder = CalendarFeatureBuilder(aliases, "America/New_York")

    events = [
        CalendarEvent(
            title="Bank Holiday",
            country="GBP",
            impact="Holiday",
            starts_at=datetime.fromisoformat("2026-04-02T08:30:00-04:00"),
        )
    ]

    features = builder.build_for_date(date(2026, 4, 2), events)
    assert features["has_bank_holiday_today"] is False


def test_calendar_feature_builder_summarises_strong_and_other_red_news():
    aliases = make_aliases(
        title_aliases={
            "CPI": ["cpi"],
            "FOMC": ["federal funds rate"],
        }
    )
    builder = CalendarFeatureBuilder(aliases, "America/New_York")

    events = [
        CalendarEvent(
            title="CPI m/m",
            country="USD",
            impact="High",
            starts_at=datetime.fromisoformat("2026-04-02T08:30:00-04:00"),
        ),
        CalendarEvent(
            title="Retail Sales m/m",
            country="USD",
            impact="High",
            starts_at=datetime.fromisoformat("2026-04-02T08:30:00-04:00"),
        ),
        CalendarEvent(
            title="Federal Funds Rate",
            country="USD",
            impact="High",
            starts_at=datetime.fromisoformat("2026-04-03T14:00:00-04:00"),
        ),
        CalendarEvent(
            title="German CPI m/m",
            country="EUR",
            impact="High",
            starts_at=datetime.fromisoformat("2026-04-02T08:30:00-04:00"),
        ),
    ]

    analysis = builder.analyse_date(date(2026, 4, 2), events)

    assert analysis.strongest_news_today == ["CPI at 8:30 AM NY time"]
    assert analysis.other_red_news_today == ["Retail Sales m/m at 8:30 AM NY time"]
    assert analysis.strongest_news_tomorrow == [
        "Federal Funds Rate at 2:00 PM NY time"
    ]


def test_calendar_feature_builder_marks_no_major_usd_news_days():
    aliases = make_aliases(title_aliases={"CPI": ["cpi"]})
    builder = CalendarFeatureBuilder(aliases, "America/New_York")

    events = [
        CalendarEvent(
            title="Existing Home Sales",
            country="USD",
            impact="Low",
            starts_at=datetime.fromisoformat("2026-04-02T10:00:00-04:00"),
        )
    ]

    analysis = builder.analyse_date(date(2026, 4, 2), events)

    assert analysis.features["has_no_major_usd_news_today"] is True
    assert analysis.quiet_note is not None


def test_calendar_feature_builder_applies_non_core_pressure_overlay():
    aliases = make_aliases(title_aliases={})
    builder = CalendarFeatureBuilder(aliases, "America/New_York")

    events = [
        CalendarEvent(
            title="Retail Sales m/m",
            country="USD",
            impact="High",
            starts_at=datetime.fromisoformat("2026-04-02T10:00:00-04:00"),
        )
    ]

    analysis = builder.analyse_date(date(2026, 4, 2), events)

    assert analysis.pressure_overlay["PreMarket"] == DayColor.RED
    assert analysis.pressure_overlay["NY_AM_FIRST_HALF"] == DayColor.RED
