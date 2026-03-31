from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from bot.calendar_features import CalendarFeatureBuilder
from bot.calendar_source import CalendarSourceError, ForexFactoryCalendarSource
from bot.models import CalendarEvent, DailyDecision
from bot.rule_engine import RuleEngine


class DailySummaryService:
    def __init__(
        self,
        source: ForexFactoryCalendarSource,
        feature_builder: CalendarFeatureBuilder,
        rule_engine: RuleEngine,
        market_timezone: str,
    ) -> None:
        self.source = source
        self.feature_builder = feature_builder
        self.rule_engine = rule_engine
        self.market_tz = ZoneInfo(market_timezone)
        self.cached_events: list[CalendarEvent] = []
        self.last_refresh_at: datetime | None = None

    async def refresh_events(self) -> list[CalendarEvent]:
        events = await self.source.fetch_events()
        self.cached_events = events
        self.last_refresh_at = datetime.now(tz=self.market_tz)
        return events

    async def build_decision_for_today(self) -> DailyDecision:
        target_date = datetime.now(tz=self.market_tz).date()

        try:
            events = await self.refresh_events()
            source_status = "live"
        except CalendarSourceError:
            if not self.cached_events:
                return DailyDecision.calendar_unavailable(target_date)
            events = self.cached_events
            source_status = "stale"

        features = self.feature_builder.build_for_date(target_date, events)
        decision = self.rule_engine.match(target_date, features)
        if source_status == "live":
            return decision
        return DailyDecision(
            rule_id=decision.rule_id,
            target_date=decision.target_date,
            day_color=decision.day_color,
            tradable=decision.tradable,
            session_colors=decision.session_colors,
            reason=decision.reason,
            matched_conditions=decision.matched_conditions,
            source_status=source_status,
        )

