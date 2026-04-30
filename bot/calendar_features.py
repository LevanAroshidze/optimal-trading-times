from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from bot.config import EventAliasConfig, WindowsConfig
from bot.models import (
    CalendarDayAnalysis,
    CalendarEvent,
    DayColor,
    SessionColors,
)
from bot.type_defs import FeatureMap


@dataclass(frozen=True)
class EventCategoryMatch:
    event: CalendarEvent
    categories: set[str]


class CalendarFeatureBuilder:
    def __init__(
        self,
        aliases: EventAliasConfig,
        market_timezone: str,
        windows: WindowsConfig,
    ) -> None:
        self.aliases = aliases
        self.market_tz = ZoneInfo(market_timezone)
        self.strongest_categories = set(aliases.strongest_categories)
        self.windows = windows

    @property
    def session_windows(self) -> dict[str, tuple[time, time]]:
        return {
            name: (window.start, window.end)
            for name, window in self.windows.session_windows.items()
        }

    def build_for_date(
        self, target_date: date, events: list[CalendarEvent]
    ) -> FeatureMap:
        return self.analyse_date(target_date, events).features

    def analyse_date(
        self, target_date: date, events: list[CalendarEvent]
    ) -> CalendarDayAnalysis:
        localised = self._localise_events(events)
        today_events = self._events_on_date(localised, target_date)
        tomorrow_events = self._events_on_date(localised, target_date + timedelta(days=1))
        week_start = self._week_start(target_date)
        week_events = [
            match
            for match in localised
            if self._week_start(match.event.starts_at.date()) == week_start
        ]

        strongest_news_today = self._event_summaries(
            today_events, self.strongest_categories
        )
        strongest_news_tomorrow = self._event_summaries(
            tomorrow_events, self.strongest_categories
        )
        other_red_news_today = self._other_red_event_summaries(today_events)

        has_bank_holiday_today = self._has_full_holiday_today(today_events)
        has_partial_holiday_today = self._has_partial_holiday_today(today_events)
        has_major_usd_news_today = bool(strongest_news_today or other_red_news_today)
        quiet_note = None
        if (
            not has_major_usd_news_today
            and not has_bank_holiday_today
            and not has_partial_holiday_today
        ):
            quiet_note = (
                "No major USD news. NY AM may be slower and lower-volatility than usual."
            )

        categories_today = sorted(
            {category for match in today_events for category in match.categories}
        )

        powell_time = self.windows.powell_speech_at
        am_time = self.windows.am_red_news_at

        features = {
            "weekday": target_date.strftime("%a").upper()[:3],
            "week_type": self._week_type(week_events),
            "days_before_cpi": self._days_until_category(target_date, week_events, "CPI"),
            "days_before_ppi": self._days_until_category(target_date, week_events, "PPI"),
            "has_cpi_today": self._has_category_on_date(target_date, week_events, "CPI"),
            "has_ppi_today": self._has_category_on_date(target_date, week_events, "PPI"),
            "has_bank_holiday_today": has_bank_holiday_today,
            "has_partial_holiday_today": has_partial_holiday_today,
            "has_830_red_news": self._has_am_red_news(today_events, am_time),
            "has_powell_10am_today": self._has_time_sensitive_category(
                today_events, "POWELL", powell_time.hour, powell_time.minute
            ),
            "days_until_powell_10am": self._days_until_time_sensitive_category(
                target_date,
                week_events,
                "POWELL",
                powell_time.hour,
                powell_time.minute,
            ),
            "has_pm_red_news": self._has_pm_red_news(today_events),
            "has_no_major_usd_news_today": not has_major_usd_news_today,
            "categories_today": categories_today,
            "strong_usd_news_today": strongest_news_today,
            "strong_usd_news_tomorrow": strongest_news_tomorrow,
            "other_red_usd_news_today": other_red_news_today,
        }

        calendar_snapshot = self._calendar_snapshot(
            strongest_news_today=strongest_news_today,
            strongest_news_tomorrow=strongest_news_tomorrow,
            other_red_news_today=other_red_news_today,
            has_bank_holiday_today=has_bank_holiday_today,
            has_partial_holiday_today=has_partial_holiday_today,
            quiet_note=quiet_note,
        )

        return CalendarDayAnalysis(
            features=features,
            strongest_news_today=strongest_news_today,
            strongest_news_tomorrow=strongest_news_tomorrow,
            other_red_news_today=other_red_news_today,
            quiet_note=quiet_note,
            calendar_snapshot=calendar_snapshot,
            pressure_overlay=self._pressure_overlay(today_events),
        )

    def _to_market_timezone(self, event: CalendarEvent) -> CalendarEvent:
        return CalendarEvent(
            title=event.title,
            country=event.country,
            impact=event.impact,
            starts_at=event.starts_at.astimezone(self.market_tz),
        )

    def _localise_events(self, events: list[CalendarEvent]) -> list[EventCategoryMatch]:
        return [
            EventCategoryMatch(
                event=self._to_market_timezone(event),
                categories=self._categorise(event),
            )
            for event in events
        ]

    def _events_on_date(
        self, events: list[EventCategoryMatch], target_date: date
    ) -> list[EventCategoryMatch]:
        return [match for match in events if match.event.starts_at.date() == target_date]

    def _categorise(self, event: CalendarEvent) -> set[str]:
        haystack = event.title.casefold()
        categories: set[str] = set()
        for category, patterns in self.aliases.title_aliases.items():
            if any(pattern in haystack for pattern in patterns):
                categories.add(category)
        return categories

    def _week_start(self, value: date) -> date:
        return value - timedelta(days=value.weekday())

    def _is_usd_event(self, match: EventCategoryMatch) -> bool:
        return match.event.country.upper() == "USD"

    def _week_type(self, week_events: list[EventCategoryMatch]) -> str | None:
        if any(
            "NFP" in match.categories and self._is_usd_event(match)
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
                and self._is_usd_event(match)
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
            and self._is_usd_event(match)
            and match.event.starts_at.date() == target_date
            for match in events
        )

    def _has_am_red_news(
        self, today_events: list[EventCategoryMatch], at_time: time
    ) -> bool:
        return any(
            "RED_830AM" in match.categories
            and self._is_usd_event(match)
            and match.event.impact.casefold() == "high"
            and match.event.starts_at.hour == at_time.hour
            and match.event.starts_at.minute == at_time.minute
            for match in today_events
        )

    def _has_full_holiday_today(self, today_events: list[EventCategoryMatch]) -> bool:
        return any(
            self._is_usd_event(match)
            and match.event.impact.casefold() == "holiday"
            and not self._is_partial_holiday_title(match.event.title)
            for match in today_events
        )

    def _has_partial_holiday_today(
        self, today_events: list[EventCategoryMatch]
    ) -> bool:
        return any(
            self._is_usd_event(match)
            and match.event.impact.casefold() == "holiday"
            and self._is_partial_holiday_title(match.event.title)
            for match in today_events
        )

    def _is_partial_holiday_title(self, title: str) -> bool:
        haystack = title.casefold()
        return any(term in haystack for term in self.aliases.partial_holiday_terms)

    def _has_pm_red_news(self, today_events: list[EventCategoryMatch]) -> bool:
        threshold = self.windows.pm_news_after_hour
        return any(
            (
                "FOMC" in match.categories
                or "PM_RED" in match.categories
                or "POWELL_PM" in match.categories
            )
            and self._is_usd_event(match)
            and match.event.starts_at.hour >= threshold
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
            and self._is_usd_event(match)
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
                and self._is_usd_event(match)
                and match.event.starts_at.hour == hour
                and match.event.starts_at.minute == minute
                and match.event.starts_at.date() >= target_date
            }
        )
        if not future_dates:
            return None
        return (future_dates[0] - target_date).days

    def _event_summaries(
        self, events: list[EventCategoryMatch], categories: set[str]
    ) -> list[str]:
        summaries: list[str] = []
        seen: set[str] = set()

        for match in self._sorted_matches(events):
            if not self._is_usd_event(match):
                continue
            if not (match.categories & categories):
                continue

            summary = self._event_summary(self._display_name(match), match.event.starts_at)
            if summary in seen:
                continue
            seen.add(summary)
            summaries.append(summary)

        return summaries

    def _other_red_event_summaries(self, events: list[EventCategoryMatch]) -> list[str]:
        summaries: list[str] = []
        seen: set[str] = set()

        for match in self._sorted_matches(events):
            if not self._is_other_red_event(match):
                continue
            summary = self._event_summary(match.event.title.strip(), match.event.starts_at)
            if summary in seen:
                continue
            seen.add(summary)
            summaries.append(summary)

        return summaries

    def _is_other_red_event(self, match: EventCategoryMatch) -> bool:
        return (
            self._is_usd_event(match)
            and match.event.impact.casefold() == "high"
            and not self._is_partial_holiday_title(match.event.title)
            and not (match.categories & self.strongest_categories)
        )

    def _display_name(self, match: EventCategoryMatch) -> str:
        if "NFP" in match.categories:
            return "NFP"
        if "CPI" in match.categories:
            return "CPI"
        if "PPI" in match.categories:
            return "PPI"
        if "POWELL" in match.categories:
            return "Powell speech"
        if "FOMC" in match.categories:
            title = match.event.title.casefold()
            if "federal funds rate" in title:
                return "Federal Funds Rate"
            if "fomc statement" in title:
                return "FOMC Statement"
            if "fomc press conference" in title:
                return "FOMC Press Conference"
            return "FOMC"
        return match.event.title.strip()

    def _calendar_snapshot(
        self,
        *,
        strongest_news_today: list[str],
        strongest_news_tomorrow: list[str],
        other_red_news_today: list[str],
        has_bank_holiday_today: bool,
        has_partial_holiday_today: bool,
        quiet_note: str | None,
    ) -> list[str]:
        snapshot: list[str] = []
        snapshot.extend(f"strong_today:{item}" for item in strongest_news_today)
        snapshot.extend(f"other_red_today:{item}" for item in other_red_news_today)
        snapshot.extend(f"strong_tomorrow:{item}" for item in strongest_news_tomorrow)
        if has_bank_holiday_today:
            snapshot.append("holiday:full")
        if has_partial_holiday_today:
            snapshot.append("holiday:partial")
        if quiet_note:
            snapshot.append(f"quiet:{quiet_note}")
        return snapshot

    def _sorted_matches(self, events: list[EventCategoryMatch]) -> list[EventCategoryMatch]:
        return sorted(events, key=lambda item: (item.event.starts_at, item.event.title))

    def _event_summary(self, label: str, starts_at: datetime) -> str:
        return f"{label} at {starts_at.strftime('%I:%M %p').lstrip('0')} NY time"

    def _pressure_overlay(self, today_events: list[EventCategoryMatch]) -> SessionColors:
        overlay: SessionColors = {}
        for match in today_events:
            if not self._is_other_red_event(match):
                continue
            overlay = self._merge_pressure_overlay(
                overlay,
                self._pressure_overlay_for_event(match.event.starts_at),
            )
        return overlay

    def _pressure_overlay_for_event(self, starts_at: datetime) -> SessionColors:
        pressure_minutes = self.windows.pre_release_pressure_minutes
        pressure_start = starts_at - timedelta(minutes=pressure_minutes)
        overlay: SessionColors = {}

        for session_name, window in self.windows.session_windows.items():
            session_start = datetime.combine(
                starts_at.date(), window.start, tzinfo=starts_at.tzinfo
            )
            session_end = datetime.combine(
                starts_at.date(), window.end, tzinfo=starts_at.tzinfo
            )
            overlap_start = max(session_start, pressure_start)
            overlap_end = min(session_end, starts_at)
            if overlap_start >= overlap_end:
                continue

            if session_start >= pressure_start and session_end <= starts_at:
                overlay[session_name] = DayColor.RED
                continue

            if session_start <= starts_at < session_end:
                overlay[session_name] = DayColor.RED
                continue

            overlay[session_name] = DayColor.WHITE

        return overlay

    def _merge_pressure_overlay(
        self, base: SessionColors, incoming: SessionColors
    ) -> SessionColors:
        merged = dict(base)
        for session, color in incoming.items():
            existing = merged.get(session)
            if existing == DayColor.RED or color == existing:
                continue
            if color == DayColor.RED or existing is None:
                merged[session] = color
            else:
                merged[session] = DayColor.WHITE
        return merged
