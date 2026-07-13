from __future__ import annotations

from datetime import date, time
from pathlib import Path

import pytest

from bot.calendar_features import CalendarFeatureBuilder
from bot.config import (
    AppConfig,
    CalendarConfig,
    DeliveryConfig,
    EventAliasConfig,
    PolicyConfig,
    RuntimeConfig,
    ScheduleConfig,
    SessionWindowConfig,
    WeeklyScheduleConfig,
    WindowsConfig,
)
from bot.models import (
    DailyDecision,
    DailyOutlook,
    DayColor,
    SessionName,
    TradableStatus,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = REPO_ROOT / "config"


def make_windows() -> WindowsConfig:
    sessions = {
        SessionName.LONDON.value: SessionWindowConfig(time(3, 0), time(7, 0)),
        SessionName.PREMARKET.value: SessionWindowConfig(time(7, 0), time(8, 30)),
        SessionName.NY_AM_FIRST_HALF.value: SessionWindowConfig(
            time(8, 30), time(10, 30)
        ),
        SessionName.NY_AM_SECOND_HALF.value: SessionWindowConfig(
            time(10, 30), time(12, 0)
        ),
        SessionName.LUNCH.value: SessionWindowConfig(time(12, 0), time(13, 30)),
        SessionName.NY_PM_FIRST_HALF.value: SessionWindowConfig(
            time(13, 30), time(15, 0)
        ),
        SessionName.NY_PM_SECOND_HALF.value: SessionWindowConfig(
            time(15, 0), time(17, 0)
        ),
    }
    return WindowsConfig(
        session_windows=sessions,
        pre_release_pressure_minutes=180,
        pm_news_after_hour=14,
        am_red_news_at=time(8, 30),
        powell_speech_at=time(10, 0),
    )


def make_aliases(**overrides) -> EventAliasConfig:
    title_aliases = {
        category: tuple(values)
        for category, values in overrides.pop("title_aliases", {}).items()
    }
    title_excludes = {
        category: tuple(values)
        for category, values in overrides.pop("title_excludes", {}).items()
    }
    strongest_categories = overrides.pop(
        "strongest_categories", ("NFP", "CPI", "PPI", "FOMC", "POWELL")
    )
    partial_holiday_terms = overrides.pop(
        "partial_holiday_terms", ("early close", "half day")
    )
    return EventAliasConfig(
        title_aliases=title_aliases,
        title_excludes=title_excludes,
        strongest_categories=tuple(strongest_categories),
        partial_holiday_terms=tuple(partial_holiday_terms),
    )


def make_feature_builder(aliases: EventAliasConfig | None = None) -> CalendarFeatureBuilder:
    return CalendarFeatureBuilder(
        aliases=aliases or make_aliases(),
        market_timezone="America/New_York",
        windows=make_windows(),
    )


def make_app_config(
    state_path: str,
    *,
    post_on_weekend: bool = True,
    max_post_age_minutes: int = 240,
) -> AppConfig:
    return AppConfig(
        delivery=DeliveryConfig(webhook_url_env="DISCORD_WEBHOOK_URL"),
        runtime=RuntimeConfig(state_path=state_path),
        morning_summary=ScheduleConfig(timezone="Asia/Tbilisi", hour=9, minute=0),
        ny_recheck=ScheduleConfig(timezone="America/New_York", hour=8, minute=0),
        weekly_outlook=WeeklyScheduleConfig(
            timezone="Asia/Tbilisi", weekday="SUN", hour=21, minute=0
        ),
        calendar=CalendarConfig(
            urls=["https://example.com"],
            timeout_seconds=10.0,
            market_timezone="America/New_York",
        ),
        windows=make_windows(),
        policy=PolicyConfig(
            post_on_weekend=post_on_weekend,
            max_post_age_minutes=max_post_age_minutes,
        ),
    )


def make_outlook(
    rule_id: str = "no_major_usd_news_day",
    reason: str = "No major USD news today.",
    snapshot: list[str] | None = None,
    target_date: date | None = None,
) -> DailyOutlook:
    decision = DailyDecision(
        rule_id=rule_id,
        target_date=target_date or date(2026, 4, 16),
        day_color=DayColor.WHITE,
        tradable=TradableStatus.CAUTION,
        session_colors={
            "London": DayColor.GREEN,
            "PreMarket": DayColor.GREEN,
            "NY_AM_FIRST_HALF": DayColor.WHITE,
            "NY_AM_SECOND_HALF": DayColor.WHITE,
            "Lunch": DayColor.GREEN,
            "NY_PM_FIRST_HALF": DayColor.GREEN,
            "NY_PM_SECOND_HALF": DayColor.GREEN,
        },
        reason=reason,
    )
    return DailyOutlook(
        decision=decision,
        summary_reason=reason,
        calendar_snapshot=snapshot or [],
    )


@pytest.fixture()
def windows_config() -> WindowsConfig:
    return make_windows()


@pytest.fixture()
def feature_builder() -> CalendarFeatureBuilder:
    return make_feature_builder()


@pytest.fixture()
def repo_root() -> Path:
    return REPO_ROOT
