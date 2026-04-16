from datetime import date, datetime
from pathlib import Path

from bot.calendar_features import CalendarFeatureBuilder
from bot.config import EventAliasConfig, load_rules
from bot.models import CalendarEvent
from bot.rule_engine import RuleEngine
from bot.summary_service import DailySummaryService


class FakeSource:
    async def fetch_events(self) -> list[CalendarEvent]:
        return []


def make_service() -> DailySummaryService:
    aliases = EventAliasConfig(
        title_aliases={
            "CPI": ("cpi",),
            "POWELL": ("fed chair powell speaks",),
            "FOMC": ("federal funds rate", "fomc statement", "fomc press conference"),
        },
        strongest_categories=("NFP", "CPI", "PPI", "FOMC", "POWELL"),
        partial_holiday_terms=("early close", "half day"),
    )
    feature_builder = CalendarFeatureBuilder(aliases, "America/New_York")
    rules = load_rules(Path("config/rules.yaml"))
    return DailySummaryService(
        source=FakeSource(),
        feature_builder=feature_builder,
        rule_engine=RuleEngine(rules),
        market_timezone="America/New_York",
    )


def test_summary_service_applies_no_news_rule():
    service = make_service()

    outlook = service.build_outlook_for_date(
        date(2026, 4, 13),
        [],
    )

    assert outlook.decision.rule_id == "no_major_usd_news_day"
    assert outlook.quiet_note is not None


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

    assert outlook.decision.day_color.value == "WHITE"
    assert outlook.summary_reason == "Other red-folder USD news today."
    assert outlook.decision.session_colors["PreMarket"].value == "RED"


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
