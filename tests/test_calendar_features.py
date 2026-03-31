from datetime import date, datetime
import asyncio

from bot.calendar_features import CalendarFeatureBuilder
from bot.calendar_source import ForexFactoryCalendarSource
from bot.config import EventAliasConfig
from bot.models import CalendarEvent


def test_calendar_feature_builder_detects_nfp_and_powell_offset():
    aliases = EventAliasConfig(
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
    aliases = EventAliasConfig(title_aliases={"RED_830AM": ["unemployment claims"]})
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
    aliases = EventAliasConfig(title_aliases={"CPI": ["cpi"]})
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
    class StubSource(ForexFactoryCalendarSource):
        async def _fetch_all(self, client):
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

    source = StubSource(urls=["https://example.com/calendar.json"])
    events = asyncio.run(source.fetch_events())

    assert len(events) == 1
    assert events[0].title == "CPI m/m"


def test_calendar_feature_builder_detects_usd_bank_holiday_only():
    aliases = EventAliasConfig(title_aliases={})
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


def test_calendar_feature_builder_ignores_non_usd_holiday_days():
    aliases = EventAliasConfig(title_aliases={})
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
