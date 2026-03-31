from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from bot.config import EventAliasConfig
from bot.models import CalendarEvent


@dataclass(frozen=True)
class EventCategoryMatch:
    event: CalendarEvent
    categories: set[str]


class CalendarFeatureBuilder:
    def __init__(self, aliases: EventAliasConfig, market_timezone: str) -> None:
        self.aliases = aliases
        self.market_tz = ZoneInfo(market_timezone)

    def build_for_date(
        self, target_date: date, events: list[CalendarEvent]
    ) -> dict[str, object]:
        localised = [
            EventCategoryMatch(
                event=self._to_market_timezone(event),
                categories=self._categorise(event),
            )
            for event in events
        ]
        today_events = [
            match for match in localised if match.event.starts_at.date() == target_date
        ]
        week_events = [
            match
            for match in localised
            if self._week_start(match.event.starts_at.date()) == self._week_start(target_date)
        ]

        categories_today = sorted(
            {category for match in today_events for category in match.categories}
        )

        return {
            "weekday": target_date.strftime("%a").upper()[:3],
            "week_type": self._week_type(week_events),
            "days_before_cpi": self._days_until_category(target_date, week_events, "CPI"),
            "days_before_ppi": self._days_until_category(target_date, week_events, "PPI"),
            "has_cpi_today": self._has_category_on_date(target_date, week_events, "CPI"),
            "has_ppi_today": self._has_category_on_date(target_date, week_events, "PPI"),
            "has_bank_holiday_today": self._has_bank_holiday_today(today_events),
            "has_830_red_news": self._has_830_red_news(today_events),
            "has_powell_10am_today": self._has_time_sensitive_category(
                today_events, "POWELL", 10, 0
            ),
            "days_until_powell_10am": self._days_until_time_sensitive_category(
                target_date, week_events, "POWELL", 10, 0
            ),
            "has_pm_red_news": self._has_pm_red_news(today_events),
            "categories_today": categories_today,
        }

    def _to_market_timezone(self, event: CalendarEvent) -> CalendarEvent:
        return CalendarEvent(
            title=event.title,
            country=event.country,
            impact=event.impact,
            starts_at=event.starts_at.astimezone(self.market_tz),
            forecast=event.forecast,
            previous=event.previous,
        )

    def _categorise(self, event: CalendarEvent) -> set[str]:
        haystack = event.title.casefold()
        categories: set[str] = set()
        for category, patterns in self.aliases.title_aliases.items():
            if any(pattern in haystack for pattern in patterns):
                categories.add(category)
        return categories

    def _week_start(self, value: date) -> date:
        return value - timedelta(days=value.weekday())

    def _week_type(self, week_events: list[EventCategoryMatch]) -> str | None:
        if any(
            "NFP" in match.categories and match.event.country == "USD"
            for match in week_events
        ):
            return "NFP"
        return None

    def _days_until_category(
        self, target_date: date, events: list[EventCategoryMatch], category: str
    ) -> int | None:
        future_dates = sorted(
            {
                match.event.starts_at.date()
                for match in events
                if category in match.categories
                and match.event.country == "USD"
                and match.event.starts_at.date() >= target_date
            }
        )
        if not future_dates:
            return None
        return (future_dates[0] - target_date).days

    def _has_category_on_date(
        self, target_date: date, events: list[EventCategoryMatch], category: str
    ) -> bool:
        return any(
            category in match.categories
            and match.event.country == "USD"
            and match.event.starts_at.date() == target_date
            for match in events
        )

    def _has_830_red_news(self, today_events: list[EventCategoryMatch]) -> bool:
        return any(
            "RED_830AM" in match.categories
            and match.event.country == "USD"
            and match.event.impact.casefold() == "high"
            and match.event.starts_at.hour == 8
            and match.event.starts_at.minute == 30
            for match in today_events
        )

    def _has_bank_holiday_today(self, today_events: list[EventCategoryMatch]) -> bool:
        return any(
            match.event.country == "USD"
            and match.event.impact.casefold() == "holiday"
            and "bank holiday" in match.event.title.casefold()
            for match in today_events
        )

    def _has_pm_red_news(self, today_events: list[EventCategoryMatch]) -> bool:
        return any(
            (
                "FOMC" in match.categories
                or "PM_RED" in match.categories
                or "POWELL_PM" in match.categories
            )
            and match.event.country == "USD"
            and match.event.starts_at.hour >= 14
            for match in today_events
        )

    def _has_time_sensitive_category(
        self,
        today_events: list[EventCategoryMatch],
        category: str,
        hour: int,
        minute: int,
    ) -> bool:
        return any(
            category in match.categories
            and match.event.country == "USD"
            and match.event.starts_at.hour == hour
            and match.event.starts_at.minute == minute
            for match in today_events
        )

    def _days_until_time_sensitive_category(
        self,
        target_date: date,
        events: list[EventCategoryMatch],
        category: str,
        hour: int,
        minute: int,
    ) -> int | None:
        future_dates = sorted(
            {
                match.event.starts_at.date()
                for match in events
                if category in match.categories
                and match.event.country == "USD"
                and match.event.starts_at.hour == hour
                and match.event.starts_at.minute == minute
                and match.event.starts_at.date() >= target_date
            }
        )
        if not future_dates:
            return None
        return (future_dates[0] - target_date).days
