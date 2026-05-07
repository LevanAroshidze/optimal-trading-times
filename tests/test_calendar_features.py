import asyncio
from datetime import date, datetime

from bot.calendar_features import CalendarFeatureBuilder
from bot.calendar_source import ForexFactoryCalendarSource
from bot.models import CalendarEvent, DayColor
from tests.conftest import make_aliases, make_windows


def _builder(aliases=None) -> CalendarFeatureBuilder:
    return CalendarFeatureBuilder(
        aliases=aliases or make_aliases(),
        market_timezone="America/New_York",
        windows=make_windows(),
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
    builder = _builder(aliases)

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


def test_calendar_feature_builder_does_not_treat_adp_as_nfp():
    aliases = make_aliases(
        title_aliases={"NFP": ["non-farm employment change"]},
        title_excludes={"NFP": ["adp"]},
    )
    builder = _builder(aliases)

    events = [
        CalendarEvent(
            title="ADP Non-Farm Employment Change",
            country="USD",
            impact="High",
            starts_at=datetime.fromisoformat("2026-05-06T08:15:00-04:00"),
        )
    ]

    analysis = builder.analyse_date(date(2026, 5, 6), events)

    assert analysis.features["week_type"] is None
    assert analysis.strongest_news_today == []
    assert analysis.other_red_news_today == [
        "ADP Non-Farm Employment Change at 8:15 AM NY time"
    ]


def test_calendar_feature_builder_still_treats_official_release_as_nfp():
    aliases = make_aliases(
        title_aliases={"NFP": ["non-farm employment change"]},
        title_excludes={"NFP": ["adp"]},
    )
    builder = _builder(aliases)

    events = [
        CalendarEvent(
            title="Non-Farm Employment Change",
            country="USD",
            impact="High",
            starts_at=datetime.fromisoformat("2026-05-08T08:30:00-04:00"),
        )
    ]

    analysis = builder.analyse_date(date(2026, 5, 8), events)

    assert analysis.features["week_type"] == "NFP"
    assert analysis.strongest_news_today == ["NFP at 8:30 AM NY time"]


def test_calendar_feature_builder_ignores_medium_impact_strong_alias():
    aliases = make_aliases(title_aliases={"CPI": ["cpi"]})
    builder = _builder(aliases)

    events = [
        CalendarEvent(
            title="CPI m/m",
            country="USD",
            impact="Medium",
            starts_at=datetime.fromisoformat("2026-04-02T08:30:00-04:00"),
        )
    ]

    analysis = builder.analyse_date(date(2026, 4, 2), events)

    assert analysis.features["has_cpi_today"] is False
    assert analysis.strongest_news_today == []


def test_calendar_feature_builder_detects_830_red_news():
    aliases = make_aliases(title_aliases={"RED_830AM": ["unemployment claims"]})
    builder = _builder(aliases)

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
    builder = _builder(aliases)

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
        async def _fetch_with_retries(self, client, url):  # type: ignore[override]
            from bot.calendar_source import FetchResult

            payload = [
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
            return FetchResult(url=url, payload=payload, error=None)

    source = FakeSource(urls=["https://example.com/calendar.json"])
    events = asyncio.run(source.fetch_events())

    assert len(events) == 1
    assert events[0].title == "CPI m/m"


def test_calendar_feature_builder_detects_usd_bank_holiday_only():
    aliases = make_aliases()
    builder = _builder(aliases)

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


def test_calendar_feature_builder_ignores_usd_time_shift_holiday_marker():
    aliases = make_aliases()
    builder = _builder(aliases)

    events = [
        CalendarEvent(
            title="Daylight Saving Time Shift",
            country="USD",
            impact="Holiday",
            starts_at=datetime.fromisoformat("2026-03-08T02:00:00-05:00"),
        )
    ]

    features = builder.build_for_date(date(2026, 3, 8), events)

    assert features["has_bank_holiday_today"] is False
    assert features["has_partial_holiday_today"] is False


def test_calendar_feature_builder_detects_partial_holiday():
    aliases = make_aliases()
    builder = _builder(aliases)

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


def test_calendar_feature_builder_detects_full_bank_holiday_yesterday():
    aliases = make_aliases()
    builder = _builder(aliases)

    events = [
        CalendarEvent(
            title="Bank Holiday",
            country="USD",
            impact="Holiday",
            starts_at=datetime.fromisoformat("2026-04-01T08:30:00-04:00"),
        )
    ]

    features = builder.build_for_date(date(2026, 4, 2), events)

    assert features["had_bank_holiday_yesterday"] is True


def test_calendar_feature_builder_ignores_partial_and_non_usd_holiday_yesterday():
    aliases = make_aliases()
    builder = _builder(aliases)

    events = [
        CalendarEvent(
            title="Early Close",
            country="USD",
            impact="Holiday",
            starts_at=datetime.fromisoformat("2026-04-01T13:00:00-04:00"),
        ),
        CalendarEvent(
            title="Bank Holiday",
            country="GBP",
            impact="Holiday",
            starts_at=datetime.fromisoformat("2026-04-01T08:30:00-04:00"),
        ),
    ]

    features = builder.build_for_date(date(2026, 4, 2), events)

    assert features["had_bank_holiday_yesterday"] is False


def test_calendar_feature_builder_ignores_non_usd_holiday_days():
    aliases = make_aliases()
    builder = _builder(aliases)

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
    builder = _builder(aliases)

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
    builder = _builder(aliases)

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
    builder = _builder(aliases)

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


def test_calendar_feature_builder_keeps_london_clear_for_non_core_830_overlay():
    aliases = make_aliases(title_aliases={})
    builder = _builder(aliases)

    events = [
        CalendarEvent(
            title="Retail Sales m/m",
            country="USD",
            impact="High",
            starts_at=datetime.fromisoformat("2026-04-02T08:30:00-04:00"),
        )
    ]

    analysis = builder.analyse_date(date(2026, 4, 2), events)

    assert analysis.features["has_830_red_news"] is True
    assert "London" not in analysis.pressure_overlay
    assert analysis.pressure_overlay["PreMarket"] == DayColor.RED
