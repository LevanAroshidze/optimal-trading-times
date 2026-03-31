from datetime import date, datetime

from bot.calendar_features import CalendarFeatureBuilder
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
