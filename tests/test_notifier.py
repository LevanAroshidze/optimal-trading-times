from datetime import UTC, date, datetime

import asyncio

from bot.calendar_source import CalendarSourceError
from bot.config import (
    AppConfig,
    CalendarConfig,
    DeliveryConfig,
    RuntimeConfig,
    ScheduleConfig,
    WeeklyScheduleConfig,
)
from bot.formatter import DiscordFormatter
from bot.models import CalendarEvent, DailyDecision, DailyOutlook, DayColor, TradableStatus, WeeklyOutlook
from bot.notifier import TradingCalendarNotifier
from bot.state_store import BotState, DailyPostState, StateStore
from bot.type_defs import SourceStatus


class FakeSender:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send_message(self, content: str) -> None:
        self.messages.append(content)


class FakeSummaryService:
    def __init__(self, outlooks: list[DailyOutlook], weekly: WeeklyOutlook | None = None):
        self.outlooks = outlooks
        self.weekly = weekly
        self.index = 0

    async def load_events(self) -> list[CalendarEvent]:
        return []

    def build_outlook_for_date(
        self,
        target_date: date,
        events: list[CalendarEvent],
        source_status: SourceStatus = "live",
    ) -> DailyOutlook:
        outlook = self.outlooks[min(self.index, len(self.outlooks) - 1)]
        self.index += 1
        return outlook

    def build_weekly_outlook(
        self,
        week_start: date,
        events: list[CalendarEvent],
        source_status: SourceStatus = "live",
    ) -> WeeklyOutlook | None:
        return self.weekly

    def daily_fingerprint(self, outlook: DailyOutlook) -> str:
        return f"{outlook.decision.rule_id}:{outlook.summary_reason}:{','.join(outlook.calendar_snapshot)}"


def make_app_config(state_path: str) -> AppConfig:
    return AppConfig(
        delivery=DeliveryConfig(webhook_url_env="DISCORD_WEBHOOK_URL"),
        runtime=RuntimeConfig(state_path=state_path),
        morning_summary=ScheduleConfig(timezone="Asia/Tbilisi", hour=9, minute=0),
        ny_recheck=ScheduleConfig(timezone="America/New_York", hour=8, minute=0),
        weekly_outlook=WeeklyScheduleConfig(
            timezone="Asia/Tbilisi", weekday="SUN", hour=21, minute=0
        ),
        calendar=CalendarConfig(
            urls=["https://example.com"], timeout_seconds=10.0, market_timezone="America/New_York"
        ),
    )


def make_outlook(rule_id: str, reason: str, snapshot: list[str]) -> DailyOutlook:
    decision = DailyDecision(
        rule_id=rule_id,
        target_date=date(2026, 4, 16),
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
        calendar_snapshot=snapshot,
    )


def test_notifier_posts_daily_summary_only_once(tmp_path):
    state_store = StateStore(tmp_path / "state.json")
    summary = FakeSummaryService(
        [make_outlook("no_major_usd_news_day", "No major USD news today.", ["quiet"])]
    )
    sender = FakeSender()
    notifier = TradingCalendarNotifier(
        app_config=make_app_config(str(tmp_path / "state.json")),
        summary_service=summary,
        formatter=DiscordFormatter(),
        sender=sender,
        state_store=state_store,
    )

    now = datetime(2026, 4, 16, 6, 0, tzinfo=UTC)
    asyncio.run(notifier.run_once(now))
    asyncio.run(notifier.run_once(now))

    assert len(sender.messages) == 1


def test_notifier_posts_recheck_update_when_outlook_changes(tmp_path):
    state_store = StateStore(tmp_path / "state.json")
    morning = make_outlook("no_major_usd_news_day", "No major USD news today.", ["quiet"])
    changed = make_outlook("cpi_day", "CPI day.", ["strong_today:CPI at 8:30 AM NY time"])
    summary = FakeSummaryService([morning, changed])
    sender = FakeSender()
    notifier = TradingCalendarNotifier(
        app_config=make_app_config(str(tmp_path / "state.json")),
        summary_service=summary,
        formatter=DiscordFormatter(),
        sender=sender,
        state_store=state_store,
    )

    morning_now = datetime(2026, 4, 16, 6, 0, tzinfo=UTC)
    recheck_now = datetime(2026, 4, 16, 12, 0, tzinfo=UTC)

    asyncio.run(notifier.run_once(morning_now))
    asyncio.run(notifier.run_once(recheck_now))

    assert len(sender.messages) == 2
    assert sender.messages[-1].startswith("Update:")


def test_notifier_posts_weekly_outlook_once(tmp_path):
    state_store = StateStore(tmp_path / "state.json")
    state_store.save(
        BotState(
            daily=DailyPostState(posted_for_date="2026-04-19"),
        )
    )
    weekly = WeeklyOutlook(
        week_start=date(2026, 4, 20),
        days=[make_outlook("no_major_usd_news_day", "No major USD news today.", ["quiet"])],
    )
    summary = FakeSummaryService(
        [make_outlook("no_major_usd_news_day", "No major USD news today.", ["quiet"])],
        weekly=weekly,
    )
    sender = FakeSender()
    notifier = TradingCalendarNotifier(
        app_config=make_app_config(str(tmp_path / "state.json")),
        summary_service=summary,
        formatter=DiscordFormatter(),
        sender=sender,
        state_store=state_store,
    )

    sunday_now = datetime(2026, 4, 19, 17, 0, tzinfo=UTC)
    asyncio.run(notifier.run_once(sunday_now))
    asyncio.run(notifier.run_once(sunday_now))

    assert len(sender.messages) == 1
    assert sender.messages[0].startswith("Week Ahead")


def test_notifier_posts_fail_safe_daily_message_when_calendar_is_unavailable(tmp_path):
    class FailingSummaryService(FakeSummaryService):
        async def load_events(self) -> list[CalendarEvent]:
            raise CalendarSourceError("boom")

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
            return super().build_outlook_for_date(target_date, events, source_status)

    state_store = StateStore(tmp_path / "state.json")
    summary = FailingSummaryService(
        [make_outlook("no_major_usd_news_day", "No major USD news today.", ["quiet"])]
    )
    sender = FakeSender()
    notifier = TradingCalendarNotifier(
        app_config=make_app_config(str(tmp_path / "state.json")),
        summary_service=summary,
        formatter=DiscordFormatter(),
        sender=sender,
        state_store=state_store,
    )

    now = datetime(2026, 4, 16, 6, 0, tzinfo=UTC)
    asyncio.run(notifier.run_once(now))

    assert len(sender.messages) == 1
    assert "Red day. Calendar unavailable." in sender.messages[0]
