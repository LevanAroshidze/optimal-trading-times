from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Any


class DayColor(StrEnum):
    GREEN = "GREEN"
    WHITE = "WHITE"
    RED = "RED"


class TradableStatus(StrEnum):
    TRADE = "trade"
    CAUTION = "cautious"
    NO_TRADE = "no_trade"


class SessionName(StrEnum):
    LONDON = "London"
    PREMARKET = "PreMarket"
    NY_AM_FIRST_HALF = "NY_AM_FIRST_HALF"
    NY_AM_SECOND_HALF = "NY_AM_SECOND_HALF"
    LUNCH = "Lunch"
    NY_PM_FIRST_HALF = "NY_PM_FIRST_HALF"
    NY_PM_SECOND_HALF = "NY_PM_SECOND_HALF"


SESSION_ORDER = [
    SessionName.LONDON.value,
    SessionName.PREMARKET.value,
    SessionName.NY_AM_FIRST_HALF.value,
    SessionName.NY_AM_SECOND_HALF.value,
    SessionName.LUNCH.value,
    SessionName.NY_PM_FIRST_HALF.value,
    SessionName.NY_PM_SECOND_HALF.value,
]


SESSION_LABELS = {
    SessionName.LONDON.value: "London",
    SessionName.PREMARKET.value: "PreMarket",
    SessionName.NY_AM_FIRST_HALF.value: "NY AM (First Half)",
    SessionName.NY_AM_SECOND_HALF.value: "NY AM (Second Half)",
    SessionName.LUNCH.value: "Lunch",
    SessionName.NY_PM_FIRST_HALF.value: "NY PM (First Half)",
    SessionName.NY_PM_SECOND_HALF.value: "NY PM (Second Half)",
}


@dataclass(frozen=True)
class CalendarEvent:
    title: str
    country: str
    impact: str
    starts_at: datetime
    forecast: str = ""
    previous: str = ""


@dataclass(frozen=True)
class RuleDefinition:
    id: str
    priority: int
    when: dict[str, Any]
    day_color: DayColor
    tradable: TradableStatus
    session_colors: dict[str, DayColor]
    reason: str


@dataclass(frozen=True)
class DailyDecision:
    rule_id: str
    target_date: date
    day_color: DayColor
    tradable: TradableStatus
    session_colors: dict[str, DayColor]
    reason: str
    matched_conditions: dict[str, Any] = field(default_factory=dict)
    source_status: str = "live"

    @property
    def allowed_sessions(self) -> list[str]:
        return [
            session
            for session in SESSION_ORDER
            if self.session_colors.get(session) == DayColor.GREEN
        ]

    @property
    def caution_sessions(self) -> list[str]:
        return [
            session
            for session in SESSION_ORDER
            if self.session_colors.get(session) == DayColor.WHITE
        ]

    @property
    def blocked_sessions(self) -> list[str]:
        return [
            session
            for session in SESSION_ORDER
            if self.session_colors.get(session) == DayColor.RED
        ]

    @classmethod
    def calendar_unavailable(cls, target_date: date) -> "DailyDecision":
        return cls(
            rule_id="calendar_unavailable",
            target_date=target_date,
            day_color=DayColor.RED,
            tradable=TradableStatus.NO_TRADE,
            session_colors={session: DayColor.RED for session in SESSION_ORDER},
            reason="Calendar unavailable.",
            source_status="unavailable",
        )
