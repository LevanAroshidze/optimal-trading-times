from __future__ import annotations

from datetime import date, timedelta
from hashlib import sha256
from zoneinfo import ZoneInfo

from bot.calendar_features import CalendarFeatureBuilder
from bot.models import (
    CalendarDayAnalysis,
    CalendarEvent,
    DailyDecision,
    DailyOutlook,
    DayColor,
    SessionColors,
    TradableStatus,
    WeeklyOutlook,
)
from bot.rule_engine import RuleEngine
from bot.type_defs import CalendarEventSource, SourceStatus, StringItems


class DailySummaryService:
    def __init__(
        self,
        source: CalendarEventSource,
        feature_builder: CalendarFeatureBuilder,
        rule_engine: RuleEngine,
        market_timezone: str,
    ) -> None:
        self.source = source
        self.feature_builder = feature_builder
        self.rule_engine = rule_engine
        self.market_tz = ZoneInfo(market_timezone)

    async def load_events(self) -> list[CalendarEvent]:
        return await self.source.fetch_events()

    def build_outlook_for_date(
        self,
        target_date: date,
        events: list[CalendarEvent],
        source_status: SourceStatus = "live",
    ) -> DailyOutlook:
        if source_status != "live":
            decision = DailyDecision.calendar_unavailable(target_date)
            return DailyOutlook(
                decision=decision,
                summary_reason=decision.reason,
                source_status=source_status,
            )
        analysis = self.feature_builder.analyse_date(target_date, events)
        decision = self.rule_engine.match(target_date, analysis.features)
        decision = self._apply_pressure_overlay(decision, analysis)

        return DailyOutlook(
            decision=decision,
            summary_reason=self._summary_reason(decision, analysis),
            strongest_news_today=analysis.strongest_news_today,
            strongest_news_tomorrow=analysis.strongest_news_tomorrow,
            other_red_news_today=analysis.other_red_news_today,
            quiet_note=analysis.quiet_note,
            calendar_snapshot=analysis.calendar_snapshot,
            source_status=source_status,
        )

    def build_weekly_outlook(
        self,
        week_start: date,
        events: list[CalendarEvent],
        source_status: SourceStatus = "live",
    ) -> WeeklyOutlook | None:
        if source_status != "live":
            return None
        if not self._has_any_events_for_week(week_start, events):
            return None

        days: list[DailyOutlook] = []
        for offset in range(5):
            target_date = week_start + timedelta(days=offset)
            days.append(
                self.build_outlook_for_date(target_date, events, source_status)
            )
        return WeeklyOutlook(week_start=week_start, days=days)

    def daily_fingerprint(self, outlook: DailyOutlook) -> str:
        payload = [
            outlook.decision.rule_id,
            outlook.decision.day_color.value,
            outlook.decision.reason,
            outlook.summary_reason,
            outlook.source_status,
            *[
                f"{session}:{color.value}"
                for session, color in outlook.decision.session_colors.items()
            ],
            *outlook.strongest_news_today,
            *outlook.other_red_news_today,
            *outlook.strongest_news_tomorrow,
            *outlook.calendar_snapshot,
            outlook.quiet_note or "",
        ]
        return self._fingerprint(payload)

    def _summary_reason(
        self, decision: DailyDecision, analysis: CalendarDayAnalysis
    ) -> str:
        if analysis.other_red_news_today and decision.rule_id == "default_green_day":
            return "Other red-folder USD news today."
        return decision.reason

    def _apply_pressure_overlay(
        self, decision: DailyDecision, analysis: CalendarDayAnalysis
    ) -> DailyDecision:
        if not analysis.pressure_overlay:
            return decision

        session_colors = dict(decision.session_colors)
        changed = False
        for session, overlay_color in analysis.pressure_overlay.items():
            current_color = session_colors.get(session, DayColor.GREEN)
            merged_color = self._stricter_color(current_color, overlay_color)
            if merged_color != current_color:
                session_colors[session] = merged_color
                changed = True

        if not changed:
            return decision

        day_color, tradable = self._overall_status(session_colors)
        return DailyDecision(
            rule_id=decision.rule_id,
            target_date=decision.target_date,
            day_color=day_color,
            tradable=tradable,
            session_colors=session_colors,
            reason=decision.reason,
            matched_conditions=decision.matched_conditions,
        )

    def _stricter_color(self, left: DayColor, right: DayColor) -> DayColor:
        priority = {
            DayColor.GREEN: 0,
            DayColor.WHITE: 1,
            DayColor.RED: 2,
        }
        return left if priority[left] >= priority[right] else right

    def _overall_status(
        self, session_colors: SessionColors
    ) -> tuple[DayColor, TradableStatus]:
        colors = list(session_colors.values())
        if colors and all(color == DayColor.RED for color in colors):
            return DayColor.RED, TradableStatus.NO_TRADE
        if any(color != DayColor.GREEN for color in colors):
            return DayColor.WHITE, TradableStatus.CAUTION
        return DayColor.GREEN, TradableStatus.TRADE

    def _has_any_events_for_week(
        self, week_start: date, events: list[CalendarEvent]
    ) -> bool:
        week_end = week_start + timedelta(days=7)
        for event in events:
            local_event = event.starts_at.astimezone(self.market_tz)
            if week_start <= local_event.date() < week_end:
                return True
        return False

    def _fingerprint(self, payload: StringItems) -> str:
        return sha256("\n".join(payload).encode("utf-8")).hexdigest()
