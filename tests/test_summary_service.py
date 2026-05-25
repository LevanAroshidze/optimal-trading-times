from datetime import date, datetime

import pytest

from bot.config import load_rules
from bot.models import CalendarEvent
from bot.rule_engine import RuleEngine
from bot.summary_service import DailySummaryService
from tests.conftest import CONFIG_DIR, make_aliases, make_feature_builder, make_outlook


class FakeSource:
    async def fetch_events(self) -> list[CalendarEvent]:
        return []

    async def fetch_events_for_week(self, week_start: date) -> list[CalendarEvent]:
        return []


def make_service() -> DailySummaryService:
    aliases = make_aliases(
        title_aliases={
            "NFP": ["non-farm employment change"],
            "CPI": ["cpi"],
            "PPI": ["ppi"],
            "POWELL": ["fed chair powell speaks"],
            "FOMC": ["federal funds rate", "fomc statement"],
            "RED_830AM": ["retail sales", "unemployment claims"],
        },
    )
    feature_builder = make_feature_builder(aliases)
    rules = load_rules(CONFIG_DIR / "rules.yaml")
    return DailySummaryService(
        source=FakeSource(),
        feature_builder=feature_builder,
        rule_engine=RuleEngine(rules),
        market_timezone="America/New_York",
    )


def test_summary_service_applies_no_news_rule():
    service = make_service()

    outlook = service.build_outlook_for_date(date(2026, 4, 13), [])

    assert outlook.decision.rule_id == "no_major_usd_news_day"
    assert outlook.quiet_note is not None
    assert "09:50-10:10" in outlook.quiet_note
    assert outlook.decision.session_colors["NY_PM_FIRST_HALF"].value == "WHITE"
    assert outlook.decision.session_colors["NY_PM_SECOND_HALF"].value == "WHITE"


def test_summary_service_applies_pressure_overlay_to_default_green():
    service = make_service()

    outlook = service.build_outlook_for_date(
        date(2026, 4, 14),
        [
            CalendarEvent(
                title="Retail Sales m/m",
                country="USD",
                impact="High",
                starts_at=datetime.fromisoformat("2026-04-14T10:00:00-04:00"),
            )
        ],
    )

    assert outlook.decision.rule_id == "default_green_day"
    assert outlook.decision.day_color.value == "WHITE"
    assert outlook.summary_reason == "Other red-folder USD news today."
    assert outlook.decision.session_colors["PreMarket"].value == "RED"
    assert outlook.decision.session_colors["NY_AM_FIRST_HALF"].value == "RED"


def test_summary_service_uses_named_830_rule_for_non_core_red_news():
    service = make_service()

    outlook = service.build_outlook_for_date(
        date(2026, 4, 14),
        [
            CalendarEvent(
                title="Retail Sales m/m",
                country="USD",
                impact="High",
                starts_at=datetime.fromisoformat("2026-04-14T08:30:00-04:00"),
            )
        ],
    )

    assert outlook.decision.rule_id == "red_830_news_today"
    assert outlook.decision.session_colors["London"].value == "WHITE"
    assert outlook.decision.session_colors["PreMarket"].value == "RED"
    assert outlook.decision.session_colors["NY_AM_FIRST_HALF"].value == "WHITE"
    assert outlook.decision.session_colors["NY_AM_SECOND_HALF"].value == "GREEN"


def test_summary_service_matches_unaliased_high_impact_830_news():
    service = make_service()

    outlook = service.build_outlook_for_date(
        date(2026, 4, 14),
        [
            CalendarEvent(
                title="Advance GDP q/q",
                country="USD",
                impact="High",
                starts_at=datetime.fromisoformat("2026-04-14T08:30:00-04:00"),
            )
        ],
    )

    assert outlook.decision.rule_id == "red_830_news_today"
    assert outlook.decision.session_colors["London"].value == "WHITE"


def test_summary_service_keeps_strong_830_news_london_red():
    service = make_service()

    outlook = service.build_outlook_for_date(
        date(2026, 4, 14),
        [
            CalendarEvent(
                title="CPI m/m",
                country="USD",
                impact="High",
                starts_at=datetime.fromisoformat("2026-04-14T08:30:00-04:00"),
            )
        ],
    )

    assert outlook.decision.rule_id == "cpi_day"
    assert outlook.decision.session_colors["London"].value == "RED"
    assert outlook.decision.session_colors["PreMarket"].value == "RED"


def test_summary_service_keeps_explicit_cpi_colors_with_later_red_news():
    service = make_service()

    outlook = service.build_outlook_for_date(
        date(2026, 4, 14),
        [
            CalendarEvent(
                title="CPI m/m",
                country="USD",
                impact="High",
                starts_at=datetime.fromisoformat("2026-04-14T08:30:00-04:00"),
            ),
            CalendarEvent(
                title="Fed Chair Nomination Vote",
                country="USD",
                impact="High",
                starts_at=datetime.fromisoformat("2026-04-14T12:00:00-04:00"),
            ),
        ],
    )

    assert outlook.decision.rule_id == "cpi_day"
    assert outlook.other_red_news_today == [
        "Fed Chair Nomination Vote at 12:00 PM NY time"
    ]
    assert outlook.decision.session_colors["NY_AM_FIRST_HALF"].value == "WHITE"
    assert outlook.decision.session_colors["NY_AM_SECOND_HALF"].value == "GREEN"
    assert outlook.decision.session_colors["NY_PM_FIRST_HALF"].value == "GREEN"
    assert outlook.decision.session_colors["NY_PM_SECOND_HALF"].value == "GREEN"


def test_summary_service_applies_post_bank_holiday_quiet_rule():
    service = make_service()

    outlook = service.build_outlook_for_date(
        date(2026, 4, 14),
        [
            CalendarEvent(
                title="Bank Holiday",
                country="USD",
                impact="Holiday",
                starts_at=datetime.fromisoformat("2026-04-13T08:30:00-04:00"),
            )
        ],
    )

    assert outlook.decision.rule_id == "post_bank_holiday_no_major_news_day"
    assert outlook.decision.day_color.value == "RED"
    assert outlook.decision.tradable.value == "no_trade"
    assert {color.value for color in outlook.decision.session_colors.values()} == {
        "RED"
    }


@pytest.mark.parametrize(
    ("target_date", "holiday_date", "news_title", "news_time", "expected_rule"),
    [
        (
            date(2026, 4, 14),
            "2026-04-13T08:30:00-04:00",
            "CPI m/m",
            "2026-04-14T08:30:00-04:00",
            "cpi_day",
        ),
        (
            date(2026, 4, 14),
            "2026-04-13T08:30:00-04:00",
            "PPI m/m",
            "2026-04-14T08:30:00-04:00",
            "ppi_day",
        ),
        (
            date(2026, 4, 14),
            "2026-04-13T08:30:00-04:00",
            "Fed Chair Powell Speaks",
            "2026-04-14T10:00:00-04:00",
            "powell_10am_today",
        ),
        (
            date(2026, 4, 17),
            "2026-04-16T08:30:00-04:00",
            "Non-Farm Employment Change",
            "2026-04-17T08:30:00-04:00",
            "nfp_week_friday",
        ),
    ],
)
def test_summary_service_post_bank_holiday_with_strong_news_today_uses_normal_rule(
    target_date: date,
    holiday_date: str,
    news_title: str,
    news_time: str,
    expected_rule: str,
) -> None:
    service = make_service()

    outlook = service.build_outlook_for_date(
        target_date,
        [
            CalendarEvent(
                title="Bank Holiday",
                country="USD",
                impact="Holiday",
                starts_at=datetime.fromisoformat(holiday_date),
            ),
            CalendarEvent(
                title=news_title,
                country="USD",
                impact="High",
                starts_at=datetime.fromisoformat(news_time),
            ),
        ],
    )

    assert outlook.decision.rule_id == expected_rule


def test_summary_service_post_bank_holiday_with_830_red_news_today_uses_normal_rule():
    service = make_service()

    outlook = service.build_outlook_for_date(
        date(2026, 4, 14),
        [
            CalendarEvent(
                title="Bank Holiday",
                country="USD",
                impact="Holiday",
                starts_at=datetime.fromisoformat("2026-04-13T08:30:00-04:00"),
            ),
            CalendarEvent(
                title="Retail Sales m/m",
                country="USD",
                impact="High",
                starts_at=datetime.fromisoformat("2026-04-14T08:30:00-04:00"),
            ),
        ],
    )

    assert outlook.decision.rule_id == "red_830_news_today"


def test_summary_service_post_bank_holiday_with_other_red_news_today_uses_overlay():
    service = make_service()

    outlook = service.build_outlook_for_date(
        date(2026, 4, 14),
        [
            CalendarEvent(
                title="Bank Holiday",
                country="USD",
                impact="Holiday",
                starts_at=datetime.fromisoformat("2026-04-13T08:30:00-04:00"),
            ),
            CalendarEvent(
                title="Retail Sales m/m",
                country="USD",
                impact="High",
                starts_at=datetime.fromisoformat("2026-04-14T10:00:00-04:00"),
            ),
        ],
    )

    assert outlook.decision.rule_id == "default_green_day"
    assert outlook.decision.day_color.value == "WHITE"
    assert outlook.decision.tradable.value == "cautious"
    assert outlook.decision.session_colors["PreMarket"].value == "RED"
    assert outlook.decision.session_colors["NY_AM_FIRST_HALF"].value == "RED"


def test_summary_service_post_bank_holiday_without_red_news_overrides_future_setups():
    service = make_service()

    outlook = service.build_outlook_for_date(
        date(2026, 4, 14),
        [
            CalendarEvent(
                title="Bank Holiday",
                country="USD",
                impact="Holiday",
                starts_at=datetime.fromisoformat("2026-04-13T08:30:00-04:00"),
            ),
            CalendarEvent(
                title="CPI m/m",
                country="USD",
                impact="High",
                starts_at=datetime.fromisoformat("2026-04-15T08:30:00-04:00"),
            ),
        ],
    )

    assert outlook.decision.rule_id == "post_bank_holiday_no_major_news_day"
    assert outlook.decision.day_color.value == "RED"
    assert outlook.decision.tradable.value == "no_trade"


def test_summary_service_partial_holiday_is_all_red():
    service = make_service()

    outlook = service.build_outlook_for_date(
        date(2026, 4, 14),
        [
            CalendarEvent(
                title="Early Close",
                country="USD",
                impact="Holiday",
                starts_at=datetime.fromisoformat("2026-04-14T13:00:00-04:00"),
            )
        ],
    )

    assert outlook.decision.rule_id == "partial_holiday_day"
    assert outlook.decision.day_color.value == "RED"
    assert {color.value for color in outlook.decision.session_colors.values()} == {"RED"}


def test_summary_service_builds_weekly_outlook_for_target_week():
    service = make_service()

    weekly = service.build_weekly_outlook(
        date(2026, 4, 20),
        [
            CalendarEvent(
                title="CPI m/m",
                country="USD",
                impact="High",
                starts_at=datetime.fromisoformat("2026-04-22T08:30:00-04:00"),
            )
        ],
    )

    assert weekly is not None
    assert len(weekly.days) == 5


def test_daily_fingerprint_includes_calendar_snapshot():
    service = make_service()
    first = make_outlook(
        "no_major_usd_news_day",
        "No major USD news today.",
        ["quiet:baseline"],
    )
    second = make_outlook(
        "no_major_usd_news_day",
        "No major USD news today.",
        ["holiday:partial"],
    )

    assert service.daily_fingerprint(first) != service.daily_fingerprint(second)
