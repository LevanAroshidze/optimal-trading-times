import asyncio
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest

from bot.calendar_source import CalendarSourceError
from bot.formatter import DiscordFormatter
from bot.models import (
    CalendarEvent,
    DailyDecision,
    DailyOutlook,
    WeeklyOutlook,
)
from bot.notifier import TradingCalendarNotifier
from bot.state_store import (
    BotState,
    CalendarCacheState,
    DailyPostState,
    OperationsState,
    StateStore,
)
from bot.type_defs import SourceStatus
from bot.webhook_sender import DiscordWebhookSender, WebhookError, WebhookPayload
from tests.conftest import make_app_config, make_outlook


class FakeSender:
    def __init__(self) -> None:
        self.messages: list[str | WebhookPayload] = []

    async def send_message(self, content: str | WebhookPayload) -> None:
        self.messages.append(content)


class FakeSummaryService:
    def __init__(
        self,
        outlooks: list[DailyOutlook],
        weekly: WeeklyOutlook | None = None,
        week_events: list[CalendarEvent] | None = None,
    ):
        self.outlooks = outlooks
        self.weekly = weekly
        self.week_events = week_events or []
        self.index = 0
        self.load_count = 0
        self.week_load_count = 0

    async def load_events(self) -> list[CalendarEvent]:
        self.load_count += 1
        return []

    async def load_events_for_week(self, week_start: date) -> list[CalendarEvent]:
        self.week_load_count += 1
        return list(self.week_events)

    def build_outlook_for_date(
        self,
        target_date: date,
        events: list[CalendarEvent],
        source_status: SourceStatus = "live",
    ) -> DailyOutlook:
        outlook = self.outlooks[min(self.index, len(self.outlooks) - 1)]
        self.index += 1
        return replace(outlook, source_status=source_status)

    def build_weekly_outlook(
        self,
        week_start: date,
        events: list[CalendarEvent],
        source_status: SourceStatus = "live",
    ) -> WeeklyOutlook | None:
        if self.weekly is None:
            return None
        return replace(
            self.weekly,
            source_status=source_status,
            days=[replace(day, source_status=source_status) for day in self.weekly.days],
        )

    def daily_fingerprint(self, outlook: DailyOutlook) -> str:
        return f"{outlook.decision.rule_id}:{outlook.summary_reason}:{','.join(outlook.calendar_snapshot)}"


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
    class CoveredSummaryService(FakeSummaryService):
        async def load_events(self) -> list[CalendarEvent]:
            return [
                CalendarEvent(
                    title="Retail Sales m/m",
                    country="USD",
                    impact="High",
                    starts_at=datetime.fromisoformat(
                        "2026-04-16T08:30:00-04:00"
                    ),
                )
            ]

    state_store = StateStore(tmp_path / "state.json")
    morning = make_outlook("no_major_usd_news_day", "No major USD news today.", ["quiet"])
    changed = make_outlook("cpi_day", "CPI day.", ["strong_today:CPI at 8:30 AM NY time"])
    summary = CoveredSummaryService([morning, changed])
    sender = FakeSender()
    notifier = TradingCalendarNotifier(
        app_config=make_app_config(
            str(tmp_path / "state.json"), post_on_weekend=False
        ),
        summary_service=summary,
        formatter=DiscordFormatter(),
        sender=sender,
        state_store=state_store,
    )

    morning_now = datetime(2026, 4, 16, 5, 0, tzinfo=UTC)
    recheck_now = datetime(2026, 4, 16, 12, 0, tzinfo=UTC)

    asyncio.run(notifier.run_once(morning_now))
    asyncio.run(notifier.run_once(recheck_now))

    assert len(sender.messages) == 2
    assert _embed_title(sender.messages[-1]) == "Calendar Update"


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
        app_config=make_app_config(
            str(tmp_path / "state.json"), post_on_weekend=False
        ),
        summary_service=summary,
        formatter=DiscordFormatter(),
        sender=sender,
        state_store=state_store,
    )

    sunday_now = datetime(2026, 4, 19, 17, 0, tzinfo=UTC)
    asyncio.run(notifier.run_once(sunday_now))
    asyncio.run(notifier.run_once(sunday_now))

    assert len(sender.messages) == 1
    assert _render_message(sender.messages[0]).startswith("**Week Ahead")
    state = state_store.load()
    assert state.weekly.posted_for_week_start == "2026-04-20"
    assert state.operations.pending_posts == {}


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

    now = datetime(2026, 4, 16, 5, 0, tzinfo=UTC)
    asyncio.run(notifier.run_once(now))

    assert len(sender.messages) == 1
    assert _embed_title(sender.messages[0]) == "Calendar Unavailable"
    assert "Calendar unavailable. Posting the fail-safe plan." in _render_message(
        sender.messages[0]
    )


def test_notifier_persists_source_failures_when_no_message_is_sent(tmp_path):
    class FailingSummaryService(FakeSummaryService):
        async def load_events(self) -> list[CalendarEvent]:
            raise CalendarSourceError("boom")

        def build_weekly_outlook(
            self,
            week_start: date,
            events: list[CalendarEvent],
            source_status: SourceStatus = "live",
        ) -> WeeklyOutlook | None:
            return None

    state_store = StateStore(tmp_path / "state.json")
    state_store.save(
        BotState(
            daily=DailyPostState(posted_for_date="2026-04-19"),
        )
    )
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

    sunday_weekly_time = datetime(2026, 4, 19, 17, 0, tzinfo=UTC)
    asyncio.run(notifier.run_once(sunday_weekly_time))
    asyncio.run(notifier.run_once(sunday_weekly_time))

    assert sender.messages == []
    state = state_store.load()
    assert state.operations.consecutive_source_failures == 1
    assert state.operations.last_source_status == "unavailable"
    assert "2026-04-20" in state.operations.weekly_retry_after


def test_notifier_caches_live_events_after_success(tmp_path):
    event = CalendarEvent(
        title="CPI m/m",
        country="USD",
        impact="High",
        starts_at=datetime.fromisoformat("2026-04-16T08:30:00-04:00"),
    )

    class EventSummaryService(FakeSummaryService):
        async def load_events(self) -> list[CalendarEvent]:
            return [event]

    state_store = StateStore(tmp_path / "state.json")
    summary = EventSummaryService(
        [make_outlook("cpi_day", "CPI day.", ["strong_today:CPI at 8:30 AM NY time"])]
    )
    notifier = TradingCalendarNotifier(
        app_config=make_app_config(str(tmp_path / "state.json")),
        summary_service=summary,
        formatter=DiscordFormatter(),
        sender=FakeSender(),
        state_store=state_store,
    )

    asyncio.run(notifier.run_once(datetime(2026, 4, 16, 5, 0, tzinfo=UTC)))

    state = state_store.load()
    assert state.calendar_cache.fetched_at is not None
    assert state.calendar_cache.events == [event]


def test_notifier_uses_fresh_cache_when_calendar_unavailable(tmp_path):
    class FailingSummaryService(FakeSummaryService):
        async def load_events(self) -> list[CalendarEvent]:
            raise CalendarSourceError("offline")

    event = CalendarEvent(
        title="Retail Sales m/m",
        country="USD",
        impact="High",
        starts_at=datetime.fromisoformat("2026-04-16T08:30:00-04:00"),
    )
    state_store = StateStore(tmp_path / "state.json")
    now = datetime(2026, 4, 16, 5, 0, tzinfo=UTC)
    state_store.save(
        BotState(
            calendar_cache=CalendarCacheState(
                fetched_at=(now - timedelta(hours=1)).isoformat(),
                events=[event],
            )
        )
    )
    sender = FakeSender()
    notifier = TradingCalendarNotifier(
        app_config=make_app_config(str(tmp_path / "state.json")),
        summary_service=FailingSummaryService(
            [make_outlook("red_830_news_today", "8:30am red-folder news today.", [])]
        ),
        formatter=DiscordFormatter(),
        sender=sender,
        state_store=state_store,
    )

    asyncio.run(notifier.run_once(now))

    assert "Source: cached calendar data" in _render_message(sender.messages[0])
    assert state_store.load().operations.last_source_status == "cached"


def test_notifier_ignores_stale_cache_when_calendar_unavailable(tmp_path):
    class FailingSummaryService(FakeSummaryService):
        async def load_events(self) -> list[CalendarEvent]:
            raise CalendarSourceError("offline")

    now = datetime(2026, 4, 16, 5, 0, tzinfo=UTC)
    state_store = StateStore(tmp_path / "state.json")
    state_store.save(
        BotState(
            calendar_cache=CalendarCacheState(
                fetched_at=(now - timedelta(hours=100)).isoformat(),
                events=[
                    CalendarEvent(
                        title="Retail Sales m/m",
                        country="USD",
                        impact="High",
                        starts_at=datetime.fromisoformat("2026-04-16T08:30:00-04:00"),
                    )
                ],
            )
        )
    )
    sender = FakeSender()
    notifier = TradingCalendarNotifier(
        app_config=make_app_config(str(tmp_path / "state.json")),
        summary_service=FailingSummaryService(
            [make_outlook("no_major_usd_news_day", "No major USD news today.", [])]
        ),
        formatter=DiscordFormatter(),
        sender=sender,
        state_store=state_store,
    )

    asyncio.run(notifier.run_once(now))

    assert "Source: calendar unavailable - fail-safe plan" in _render_message(
        sender.messages[0]
    )


def test_notifier_rejects_fresh_cache_that_does_not_cover_target_week(tmp_path):
    class FailingSummaryService(FakeSummaryService):
        async def load_events(self) -> list[CalendarEvent]:
            raise CalendarSourceError("offline")

    now = datetime(2026, 4, 16, 5, 0, tzinfo=UTC)
    state_store = StateStore(tmp_path / "state.json")
    state_store.save(
        BotState(
            calendar_cache=CalendarCacheState(
                fetched_at=(now - timedelta(hours=1)).isoformat(),
                events=[
                    CalendarEvent(
                        title="CPI m/m",
                        country="USD",
                        impact="High",
                        starts_at=datetime.fromisoformat(
                            "2026-04-01T08:30:00-04:00"
                        ),
                    )
                ],
            )
        )
    )
    sender = FakeSender()
    notifier = TradingCalendarNotifier(
        app_config=make_app_config(str(tmp_path / "state.json")),
        summary_service=FailingSummaryService(
            [make_outlook("no_major_usd_news_day", "No major USD news today.", [])]
        ),
        formatter=DiscordFormatter(),
        sender=sender,
        state_store=state_store,
    )

    asyncio.run(notifier.run_once(now))

    assert "Source: calendar unavailable - fail-safe plan" in _render_message(
        sender.messages[0]
    )


def test_notifier_rejects_live_feed_outside_forced_target_week(tmp_path):
    event = CalendarEvent(
        title="CPI m/m",
        country="USD",
        impact="High",
        starts_at=datetime.fromisoformat("2026-04-16T08:30:00-04:00"),
    )

    class EventSummaryService(FakeSummaryService):
        async def load_events(self) -> list[CalendarEvent]:
            return [event]

    sender = FakeSender()
    notifier = TradingCalendarNotifier(
        app_config=make_app_config(str(tmp_path / "state.json")),
        summary_service=EventSummaryService(
            [make_outlook("no_major_usd_news_day", "No major USD news today.", [])]
        ),
        formatter=DiscordFormatter(),
        sender=sender,
        state_store=StateStore(tmp_path / "state.json"),
        force_kind="daily",
    )

    asyncio.run(
        notifier.run_once(
            datetime(2026, 4, 16, 5, 0, tzinfo=UTC),
            target_date_override=date(2026, 5, 4),
        )
    )

    assert "Source: calendar unavailable - fail-safe plan" in _render_message(
        sender.messages[0]
    )


def test_notifier_rejects_cache_timestamp_far_in_the_future(tmp_path):
    now = datetime(2026, 4, 16, 5, 0, tzinfo=UTC)
    notifier = TradingCalendarNotifier(
        app_config=make_app_config(str(tmp_path / "state.json")),
        summary_service=FakeSummaryService([make_outlook()]),
        formatter=DiscordFormatter(),
        sender=FakeSender(),
        state_store=StateStore(tmp_path / "state.json"),
    )
    cache = CalendarCacheState(
        fetched_at=(now + timedelta(days=30)).isoformat(),
        events=[
            CalendarEvent(
                title="CPI m/m",
                country="USD",
                impact="High",
                starts_at=datetime.fromisoformat("2026-04-16T08:30:00-04:00"),
            )
        ],
    )

    assert notifier._fresh_cached_events(cache, now) is None


def test_notifier_weekly_uses_previous_cache_when_live_target_week_missing(tmp_path):
    class WeeklyFallbackSummaryService(FakeSummaryService):
        def build_weekly_outlook(
            self,
            week_start: date,
            events: list[CalendarEvent],
            source_status: SourceStatus = "live",
        ) -> WeeklyOutlook | None:
            if source_status == "live":
                return None
            return super().build_weekly_outlook(week_start, events, source_status)

    now = datetime(2026, 4, 19, 17, 0, tzinfo=UTC)
    cached_day = make_outlook(
        "no_major_usd_news_day",
        "No major USD news today.",
        ["quiet"],
        target_date=date(2026, 4, 20),
    )
    state_store = StateStore(tmp_path / "state.json")
    state_store.save(
        BotState(
            daily=DailyPostState(posted_for_date="2026-04-19"),
            calendar_cache=CalendarCacheState(
                fetched_at=(now - timedelta(hours=1)).isoformat(),
                events=[
                    CalendarEvent(
                        title="CPI m/m",
                        country="USD",
                        impact="High",
                        starts_at=datetime.fromisoformat("2026-04-20T08:30:00-04:00"),
                    )
                ],
            ),
        )
    )
    sender = FakeSender()
    notifier = TradingCalendarNotifier(
        app_config=make_app_config(
            str(tmp_path / "state.json"), post_on_weekend=False
        ),
        summary_service=WeeklyFallbackSummaryService(
            [cached_day],
            weekly=WeeklyOutlook(week_start=date(2026, 4, 20), days=[cached_day]),
        ),
        formatter=DiscordFormatter(),
        sender=sender,
        state_store=state_store,
    )

    asyncio.run(notifier.run_once(now))

    assert _render_message(sender.messages[0]).startswith("**Week Ahead")
    assert "_Source: cached calendar data._" in _render_message(sender.messages[0])


def test_notifier_weekly_fetches_target_week_when_live_week_missing(tmp_path):
    target_event = CalendarEvent(
        title="CPI m/m",
        country="USD",
        impact="High",
        starts_at=datetime.fromisoformat("2026-04-20T08:30:00-04:00"),
    )

    class WeeklyFallbackSummaryService(FakeSummaryService):
        def build_weekly_outlook(
            self,
            week_start: date,
            events: list[CalendarEvent],
            source_status: SourceStatus = "live",
        ) -> WeeklyOutlook | None:
            if source_status == "live" and not events:
                return None
            return super().build_weekly_outlook(week_start, events, source_status)

    now = datetime(2026, 4, 19, 17, 0, tzinfo=UTC)
    weekly_day = make_outlook(
        "cpi_day",
        "CPI day.",
        ["strong_today:CPI at 8:30 AM NY time"],
        target_date=date(2026, 4, 20),
    )
    state_store = StateStore(tmp_path / "state.json")
    state_store.save(BotState(daily=DailyPostState(posted_for_date="2026-04-19")))
    summary = WeeklyFallbackSummaryService(
        [weekly_day],
        weekly=WeeklyOutlook(week_start=date(2026, 4, 20), days=[weekly_day]),
        week_events=[target_event],
    )
    sender = FakeSender()
    notifier = TradingCalendarNotifier(
        app_config=make_app_config(
            str(tmp_path / "state.json"), post_on_weekend=False
        ),
        summary_service=summary,
        formatter=DiscordFormatter(),
        sender=sender,
        state_store=state_store,
    )

    asyncio.run(notifier.run_once(now))

    assert summary.week_load_count == 1
    assert _render_message(sender.messages[0]).startswith("**Week Ahead")
    assert "cached calendar data" not in _render_message(sender.messages[0])
    assert state_store.load().calendar_cache.events == [target_event]


def test_notifier_weekly_no_data_alert_is_persisted_and_cooldown_skips_refetch(
    tmp_path,
):
    class EmptyWeeklySummaryService(FakeSummaryService):
        def build_weekly_outlook(
            self,
            week_start: date,
            events: list[CalendarEvent],
            source_status: SourceStatus = "live",
        ) -> WeeklyOutlook | None:
            return None

    state_store = StateStore(tmp_path / "state.json")
    state_store.save(BotState(daily=DailyPostState(posted_for_date="2026-04-19")))
    ops_sender = FakeSender()
    summary = EmptyWeeklySummaryService([])
    notifier = TradingCalendarNotifier(
        app_config=make_app_config(
            str(tmp_path / "state.json"), post_on_weekend=False
        ),
        summary_service=summary,
        formatter=DiscordFormatter(),
        sender=FakeSender(),
        ops_sender=ops_sender,
        state_store=state_store,
    )
    first_run = datetime(2026, 4, 19, 17, 0, tzinfo=UTC)

    asyncio.run(notifier.run_once(first_run))
    asyncio.run(notifier.run_once(first_run + timedelta(minutes=15)))

    assert len(ops_sender.messages) == 1
    assert summary.load_count == 1
    assert summary.week_load_count == 1
    state = state_store.load()
    assert "weekly_no_events:2026-04-20" in state.operations.alerted_keys
    assert "2026-04-20" in state.operations.weekly_retry_after


def test_notifier_persists_and_respects_source_retry_after(tmp_path):
    class RateLimitedSummaryService(FakeSummaryService):
        async def load_events(self) -> list[CalendarEvent]:
            self.load_count += 1
            raise CalendarSourceError("rate limited", retry_after_seconds=300)

    summary = RateLimitedSummaryService(
        [make_outlook("calendar_unavailable", "Calendar unavailable.", [])]
    )
    state_store = StateStore(tmp_path / "state.json")
    notifier = TradingCalendarNotifier(
        app_config=make_app_config(str(tmp_path / "state.json")),
        summary_service=summary,
        formatter=DiscordFormatter(),
        sender=FakeSender(),
        state_store=state_store,
    )
    now = datetime(2026, 4, 16, 5, 0, tzinfo=UTC)

    asyncio.run(notifier.run_once(now))
    state = state_store.load()
    second_status, _, _ = asyncio.run(
        notifier._load_events(state, now + timedelta(minutes=1))
    )

    assert second_status == "unavailable"
    assert summary.load_count == 1
    assert state.operations.source_retry_after is not None


@pytest.mark.parametrize("retry_after", [float("nan"), float("inf"), -1.0, 0.0])
def test_notifier_rejects_invalid_source_retry_after(tmp_path, retry_after):
    notifier = TradingCalendarNotifier(
        app_config=make_app_config(str(tmp_path / "state.json")),
        summary_service=FakeSummaryService([make_outlook()]),
        formatter=DiscordFormatter(),
        sender=FakeSender(),
        state_store=StateStore(tmp_path / "state.json"),
    )

    assert (
        notifier._source_retry_after(
            datetime(2026, 4, 16, 5, 0, tzinfo=UTC),
            CalendarSourceError("bad retry", retry_after_seconds=retry_after),
        )
        is None
    )


def test_pending_post_marker_blocks_automatic_duplicate_and_alerts_ops(tmp_path):
    state_store = StateStore(tmp_path / "state.json")
    state_store.save(
        BotState(
            operations=OperationsState(
                pending_posts={"daily:2026-04-16": "2026-04-16T05:00:00+00:00"}
            )
        )
    )
    ops_sender = FakeSender()
    sender = FakeSender()
    notifier = TradingCalendarNotifier(
        app_config=make_app_config(str(tmp_path / "state.json")),
        summary_service=FakeSummaryService(
            [make_outlook("no_major_usd_news_day", "No major USD news today.", ["quiet"])]
        ),
        formatter=DiscordFormatter(),
        sender=sender,
        ops_sender=ops_sender,
        state_store=state_store,
    )

    asyncio.run(notifier.run_once(datetime(2026, 4, 16, 5, 0, tzinfo=UTC)))

    assert sender.messages == []
    assert len(ops_sender.messages) == 1
    state = state_store.load()
    assert "pending_post:daily:2026-04-16" in state.operations.alerted_keys


def test_webhook_failure_retains_pending_marker_and_propagates(tmp_path):
    class FailingWebhookSender:
        async def send_message(self, content: str | WebhookPayload) -> None:
            raise WebhookError("delivery attempts exhausted")

    state_path = tmp_path / "state.json"
    state_store = StateStore(state_path)
    notifier = TradingCalendarNotifier(
        app_config=make_app_config(str(state_path)),
        summary_service=FakeSummaryService(
            [make_outlook("no_major_usd_news_day", "No major USD news today.", ["quiet"])]
        ),
        formatter=DiscordFormatter(),
        sender=FailingWebhookSender(),
        state_store=state_store,
    )

    with pytest.raises(WebhookError, match="delivery attempts exhausted"):
        asyncio.run(notifier.run_once(datetime(2026, 4, 16, 5, 0, tzinfo=UTC)))

    state = state_store.load()
    assert state.daily.posted_for_date is None
    assert "daily:2026-04-16" in state.operations.pending_posts


def test_partial_multi_chunk_delivery_retains_pending_marker(
    tmp_path, monkeypatch
):
    class PlainTextFormatter(DiscordFormatter):
        def format_daily(self, outlook):
            return "first\nsecond"

    attempted_chunks: list[str] = []

    async def fail_second_chunk(self, client, payload):
        attempted_chunks.append(payload["content"])
        if len(attempted_chunks) == 2:
            raise WebhookError("second chunk failed")

    monkeypatch.setattr(
        DiscordWebhookSender, "_post_with_retries", fail_second_chunk
    )
    state_path = tmp_path / "state.json"
    state_store = StateStore(state_path)
    sender = DiscordWebhookSender(
        "https://discord.com/api/webhooks/123456789012345678/abcDEFghi-_JKL",
        max_message_length=6,
    )
    notifier = TradingCalendarNotifier(
        app_config=make_app_config(str(state_path)),
        summary_service=FakeSummaryService(
            [make_outlook("no_major_usd_news_day", "No major USD news today.", ["quiet"])]
        ),
        formatter=PlainTextFormatter(),
        sender=sender,
        state_store=state_store,
    )

    with pytest.raises(WebhookError, match="second chunk failed"):
        asyncio.run(notifier.run_once(datetime(2026, 4, 16, 5, 0, tzinfo=UTC)))

    assert attempted_chunks == ["first", "second"]
    state = state_store.load()
    assert state.daily.posted_for_date is None
    assert "daily:2026-04-16" in state.operations.pending_posts


def test_pending_post_marker_survives_final_save_failure_to_prevent_repost(tmp_path):
    class FailingFinalSaveStore(StateStore):
        def __init__(self, path):
            super().__init__(path)
            self.save_count = 0

        def save(self, state: BotState) -> None:
            self.save_count += 1
            if self.save_count == 2:
                raise OSError("final save failed")
            super().save(state)

    state_path = tmp_path / "state.json"
    state_store = FailingFinalSaveStore(state_path)
    sender = FakeSender()
    notifier = TradingCalendarNotifier(
        app_config=make_app_config(str(state_path)),
        summary_service=FakeSummaryService(
            [make_outlook("no_major_usd_news_day", "No major USD news today.", ["quiet"])]
        ),
        formatter=DiscordFormatter(),
        sender=sender,
        state_store=state_store,
    )
    now = datetime(2026, 4, 16, 5, 0, tzinfo=UTC)

    with pytest.raises(OSError, match="final save failed"):
        asyncio.run(notifier.run_once(now))

    assert len(sender.messages) == 1
    assert "daily:2026-04-16" in StateStore(state_path).load().operations.pending_posts

    retry_sender = FakeSender()
    ops_sender = FakeSender()
    retry_notifier = TradingCalendarNotifier(
        app_config=make_app_config(str(state_path)),
        summary_service=FakeSummaryService(
            [make_outlook("no_major_usd_news_day", "No major USD news today.", ["quiet"])]
        ),
        formatter=DiscordFormatter(),
        sender=retry_sender,
        ops_sender=ops_sender,
        state_store=StateStore(state_path),
    )
    asyncio.run(retry_notifier.run_once(now))

    assert retry_sender.messages == []
    assert len(ops_sender.messages) == 1


def test_notifier_sends_ops_alert_once_at_source_failure_threshold(tmp_path):
    class FailingSummaryService(FakeSummaryService):
        async def load_events(self) -> list[CalendarEvent]:
            raise CalendarSourceError("offline")

    state = BotState(
        operations=OperationsState(consecutive_source_failures=2),
    )
    ops_sender = FakeSender()
    notifier = TradingCalendarNotifier(
        app_config=make_app_config(str(tmp_path / "state.json")),
        summary_service=FailingSummaryService([]),
        formatter=DiscordFormatter(),
        sender=FakeSender(),
        ops_sender=ops_sender,
        state_store=StateStore(tmp_path / "state.json"),
    )
    now = datetime(2026, 4, 16, 5, 0, tzinfo=UTC)

    asyncio.run(notifier._load_events(state, now))
    asyncio.run(notifier._load_events(state, now))

    assert len(ops_sender.messages) == 1
    assert "Calendar source failure threshold reached" in ops_sender.messages[0]


def test_notifier_describes_unknown_holiday_snapshot_marker(tmp_path):
    summary = FakeSummaryService(
        [make_outlook("no_major_usd_news_day", "No major USD news today.", ["quiet"])]
    )
    notifier = TradingCalendarNotifier(
        app_config=make_app_config(str(tmp_path / "state.json")),
        summary_service=summary,
        formatter=DiscordFormatter(),
        sender=FakeSender(),
        state_store=StateStore(tmp_path / "state.json"),
    )

    changed = notifier._changed_items([], ["holiday:unexpected"])

    assert changed == ["Added an unknown USD holiday marker: unexpected"]


def test_recheck_uses_recheck_target_when_daily_target_differs(tmp_path):
    class DateAwareSummaryService(FakeSummaryService):
        def __init__(self) -> None:
            super().__init__([])
            self.requested_dates: list[date] = []

        async def load_events(self) -> list[CalendarEvent]:
            return [
                CalendarEvent(
                    title="Retail Sales m/m",
                    country="USD",
                    impact="High",
                    starts_at=datetime.fromisoformat(
                        "2026-04-16T08:30:00-04:00"
                    ),
                )
            ]

        def build_outlook_for_date(
            self,
            target_date: date,
            events: list[CalendarEvent],
            source_status: SourceStatus = "live",
        ) -> DailyOutlook:
            self.requested_dates.append(target_date)
            if target_date == date(2026, 4, 17):
                return make_outlook(
                    "tomorrow_daily",
                    "Tomorrow daily.",
                    ["quiet:tomorrow"],
                    target_date=target_date,
                )
            return make_outlook(
                "today_recheck",
                "Today recheck.",
                ["strong_today:CPI at 8:30 AM NY time"],
                target_date=target_date,
            )

    state_store = StateStore(tmp_path / "state.json")
    summary = DateAwareSummaryService()
    baseline = make_outlook(
        "today_recheck",
        "Today recheck.",
        ["quiet:baseline"],
        target_date=date(2026, 4, 16),
    )
    state_store.save(
        BotState(
            daily=DailyPostState(
                posted_for_date="2026-04-16",
                fingerprint=summary.daily_fingerprint(baseline),
                snapshot=baseline.calendar_snapshot,
            )
        )
    )
    app_config = make_app_config(
        str(tmp_path / "state.json"), max_post_age_minutes=1000
    )
    app_config = replace(
        app_config,
        morning_summary=replace(app_config.morning_summary, hour=0, minute=0),
    )
    sender = FakeSender()
    notifier = TradingCalendarNotifier(
        app_config=app_config,
        summary_service=summary,
        formatter=DiscordFormatter(),
        sender=sender,
        state_store=state_store,
    )

    # 20:30 UTC is Apr 17 in Tbilisi but still Apr 16 in New York.
    asyncio.run(notifier.run_once(datetime(2026, 4, 16, 20, 30, tzinfo=UTC)))

    assert summary.requested_dates == [date(2026, 4, 17), date(2026, 4, 16)]
    assert len(sender.messages) == 2
    assert _embed_title(sender.messages[1]) == "Calendar Update"
    assert "CPI" in _render_message(sender.messages[1])


def test_dry_run_send_updates_state_in_memory_without_persisting(tmp_path):
    state_store = StateStore(tmp_path / "state.json")
    state = BotState()
    sent_messages: list[str] = []
    summary = FakeSummaryService(
        [make_outlook("no_major_usd_news_day", "No major USD news today.", ["quiet"])]
    )
    notifier = TradingCalendarNotifier(
        app_config=make_app_config(str(tmp_path / "state.json")),
        summary_service=summary,
        formatter=DiscordFormatter(),
        sender=FakeSender(),
        state_store=state_store,
        dry_run=True,
    )

    asyncio.run(
        notifier._send_and_record(
            "preview",
            sent_messages,
            state,
            "daily",
            DailyPostState(posted_for_date="2026-04-16", fingerprint="abc"),
        )
    )

    assert sent_messages == ["preview"]
    assert state.daily.posted_for_date == "2026-04-16"
    assert state_store.load().daily.posted_for_date is None


def test_notifier_skips_when_post_window_expired(tmp_path):
    """Late evening on Tbilisi time should not retroactively post a morning summary."""

    state_store = StateStore(tmp_path / "state.json")
    summary = FakeSummaryService(
        [make_outlook("no_major_usd_news_day", "No major USD news today.", ["quiet"])]
    )
    sender = FakeSender()
    notifier = TradingCalendarNotifier(
        app_config=make_app_config(
            str(tmp_path / "state.json"), max_post_age_minutes=120
        ),
        summary_service=summary,
        formatter=DiscordFormatter(),
        sender=sender,
        state_store=state_store,
    )

    # 19:00 UTC on Apr 16 = 23:00 Tbilisi same day, 14 hours after 09:00 Tbilisi.
    very_late = datetime(2026, 4, 16, 19, 0, tzinfo=UTC)
    asyncio.run(notifier.run_once(very_late))
    assert sender.messages == []


def test_notifier_skips_weekend_when_disabled(tmp_path):
    state_store = StateStore(tmp_path / "state.json")
    summary = FakeSummaryService(
        [make_outlook("weekend_no_trade", "Weekend.", [])]
    )
    sender = FakeSender()
    notifier = TradingCalendarNotifier(
        app_config=make_app_config(
            str(tmp_path / "state.json"), post_on_weekend=False
        ),
        summary_service=summary,
        formatter=DiscordFormatter(),
        sender=sender,
        state_store=state_store,
    )

    saturday_morning_tbilisi = datetime(2026, 4, 18, 5, 0, tzinfo=UTC)
    asyncio.run(notifier.run_once(saturday_morning_tbilisi))
    assert sender.messages == []


@pytest.mark.parametrize(
    "now",
    [
        # 2026-05-02 12:00 UTC = Saturday 16:00 Tbilisi / 08:00 New York.
        datetime(2026, 5, 2, 12, 0, tzinfo=UTC),
        # 2026-05-03 12:00 UTC = Sunday 16:00 Tbilisi / 08:00 New York.
        datetime(2026, 5, 3, 12, 0, tzinfo=UTC),
    ],
)
def test_notifier_skips_weekend_recheck_when_disabled_with_stale_baseline(
    tmp_path, now
):
    state_store = StateStore(tmp_path / "state.json")
    baseline = make_outlook(
        "no_major_usd_news_day",
        "No major USD news today.",
        ["quiet"],
        target_date=date(2026, 5, 1),
    )
    summary = FakeSummaryService(
        [make_outlook("cpi_day", "CPI day.", ["strong_today:CPI at 8:30 AM NY time"])]
    )
    state_store.save(
        BotState(
            daily=DailyPostState(
                posted_for_date="2026-05-01",
                fingerprint=summary.daily_fingerprint(baseline),
                snapshot=baseline.calendar_snapshot,
            )
        )
    )
    sender = FakeSender()
    notifier = TradingCalendarNotifier(
        app_config=make_app_config(
            str(tmp_path / "state.json"), post_on_weekend=False
        ),
        summary_service=summary,
        formatter=DiscordFormatter(),
        sender=sender,
        state_store=state_store,
    )

    asyncio.run(notifier.run_once(now))

    assert sender.messages == []


def test_notifier_posts_weekday_recheck_with_same_date_baseline(tmp_path):
    class CoveredSummaryService(FakeSummaryService):
        async def load_events(self) -> list[CalendarEvent]:
            return [
                CalendarEvent(
                    title="Retail Sales m/m",
                    country="USD",
                    impact="High",
                    starts_at=datetime.fromisoformat(
                        "2026-05-04T08:30:00-04:00"
                    ),
                )
            ]

    state_store = StateStore(tmp_path / "state.json")
    baseline = make_outlook(
        "no_major_usd_news_day",
        "No major USD news today.",
        ["quiet"],
        target_date=date(2026, 5, 4),
    )
    changed = make_outlook(
        "cpi_day",
        "CPI day.",
        ["strong_today:CPI at 8:30 AM NY time"],
        target_date=date(2026, 5, 4),
    )
    summary = CoveredSummaryService([changed])
    state_store.save(
        BotState(
            daily=DailyPostState(
                posted_for_date="2026-05-04",
                fingerprint=summary.daily_fingerprint(baseline),
                snapshot=baseline.calendar_snapshot,
            )
        )
    )
    sender = FakeSender()
    notifier = TradingCalendarNotifier(
        app_config=make_app_config(
            str(tmp_path / "state.json"), post_on_weekend=False
        ),
        summary_service=summary,
        formatter=DiscordFormatter(),
        sender=sender,
        state_store=state_store,
    )

    # 2026-05-04 12:00 UTC = Monday 16:00 Tbilisi / 08:00 New York.
    asyncio.run(notifier.run_once(datetime(2026, 5, 4, 12, 0, tzinfo=UTC)))

    assert len(sender.messages) == 1
    assert _embed_title(sender.messages[0]) == "Calendar Update"


def test_notifier_posts_cached_recheck_update_with_same_date_baseline(tmp_path):
    class FailingSummaryService(FakeSummaryService):
        async def load_events(self) -> list[CalendarEvent]:
            raise CalendarSourceError("offline")

    now = datetime(2026, 5, 4, 12, 0, tzinfo=UTC)
    state_store = StateStore(tmp_path / "state.json")
    baseline = make_outlook(
        "no_major_usd_news_day",
        "No major USD news today.",
        ["quiet"],
        target_date=date(2026, 5, 4),
    )
    changed = make_outlook(
        "cpi_day",
        "CPI day.",
        ["strong_today:CPI at 8:30 AM NY time"],
        target_date=date(2026, 5, 4),
    )
    summary = FailingSummaryService([changed])
    state_store.save(
        BotState(
            daily=DailyPostState(
                posted_for_date="2026-05-04",
                fingerprint=summary.daily_fingerprint(baseline),
                snapshot=baseline.calendar_snapshot,
            ),
            calendar_cache=CalendarCacheState(
                fetched_at=(now - timedelta(hours=1)).isoformat(),
                events=[
                    CalendarEvent(
                        title="CPI m/m",
                        country="USD",
                        impact="High",
                        starts_at=datetime.fromisoformat("2026-05-04T08:30:00-04:00"),
                    )
                ],
            ),
        )
    )
    sender = FakeSender()
    notifier = TradingCalendarNotifier(
        app_config=make_app_config(
            str(tmp_path / "state.json"), post_on_weekend=False
        ),
        summary_service=summary,
        formatter=DiscordFormatter(),
        sender=sender,
        state_store=state_store,
    )

    asyncio.run(notifier.run_once(now))

    assert len(sender.messages) == 1
    assert _embed_title(sender.messages[0]) == "Calendar Update"
    assert "Source: cached calendar data" in _render_message(sender.messages[0])


def test_notifier_skips_cached_recheck_when_cache_is_from_another_week(tmp_path):
    class FailingSummaryService(FakeSummaryService):
        async def load_events(self) -> list[CalendarEvent]:
            raise CalendarSourceError("offline")

    now = datetime(2026, 5, 4, 12, 0, tzinfo=UTC)
    baseline = make_outlook(
        "no_major_usd_news_day",
        "No major USD news today.",
        ["quiet"],
        target_date=date(2026, 5, 4),
    )
    changed = make_outlook(
        "cpi_day",
        "CPI day.",
        ["strong_today:CPI at 8:30 AM NY time"],
        target_date=date(2026, 5, 4),
    )
    summary = FailingSummaryService([changed])
    state_store = StateStore(tmp_path / "state.json")
    state_store.save(
        BotState(
            daily=DailyPostState(
                posted_for_date="2026-05-04",
                fingerprint=summary.daily_fingerprint(baseline),
                snapshot=baseline.calendar_snapshot,
            ),
            calendar_cache=CalendarCacheState(
                fetched_at=(now - timedelta(hours=1)).isoformat(),
                events=[
                    CalendarEvent(
                        title="CPI m/m",
                        country="USD",
                        impact="High",
                        starts_at=datetime.fromisoformat(
                            "2026-04-27T08:30:00-04:00"
                        ),
                    )
                ],
            ),
        )
    )
    sender = FakeSender()
    notifier = TradingCalendarNotifier(
        app_config=make_app_config(
            str(tmp_path / "state.json"), post_on_weekend=False
        ),
        summary_service=summary,
        formatter=DiscordFormatter(),
        sender=sender,
        state_store=state_store,
    )

    asyncio.run(notifier.run_once(now))

    assert sender.messages == []
    assert state_store.load().recheck.checked_for_date is None


def test_notifier_dry_run_does_not_send_or_persist(tmp_path):
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
        dry_run=True,
    )

    now = datetime(2026, 4, 16, 5, 0, tzinfo=UTC)
    sent = asyncio.run(notifier.run_once(now))

    assert sent and sender.messages == []
    state = state_store.load()
    assert state.daily.posted_for_date is None


def test_notifier_rejects_naive_now(tmp_path):
    notifier = TradingCalendarNotifier(
        app_config=make_app_config(str(tmp_path / "state.json")),
        summary_service=FakeSummaryService([make_outlook()]),
        formatter=DiscordFormatter(),
        sender=FakeSender(),
        state_store=StateStore(tmp_path / "state.json"),
    )

    with pytest.raises(ValueError, match="timezone-aware"):
        asyncio.run(notifier.run_once(datetime(2026, 4, 16, 5, 0)))


@pytest.mark.parametrize(
    ("now", "expected_due"),
    [
        (datetime(2026, 1, 15, 12, 59, tzinfo=UTC), False),
        (datetime(2026, 1, 15, 13, 0, tzinfo=UTC), True),
        (datetime(2026, 7, 15, 11, 59, tzinfo=UTC), False),
        (datetime(2026, 7, 15, 12, 0, tzinfo=UTC), True),
    ],
)
def test_ny_recheck_schedule_tracks_dst(tmp_path, now, expected_due):
    notifier = TradingCalendarNotifier(
        app_config=make_app_config(str(tmp_path / "state.json")),
        summary_service=FakeSummaryService([make_outlook()]),
        formatter=DiscordFormatter(),
        sender=FakeSender(),
        state_store=StateStore(tmp_path / "state.json"),
    )

    assert notifier._is_due(now, notifier.app_config.ny_recheck) is expected_due


def test_post_window_includes_exact_expiration_boundary(tmp_path):
    app_config = make_app_config(
        str(tmp_path / "state.json"), max_post_age_minutes=120
    )
    notifier = TradingCalendarNotifier(
        app_config=app_config,
        summary_service=FakeSummaryService([make_outlook()]),
        formatter=DiscordFormatter(),
        sender=FakeSender(),
        state_store=StateStore(tmp_path / "state.json"),
    )

    assert not notifier._is_post_too_late(
        datetime(2026, 7, 15, 7, 0, tzinfo=UTC),
        app_config.morning_summary,
    )
    assert notifier._is_post_too_late(
        datetime(2026, 7, 15, 7, 0, 1, tzinfo=UTC),
        app_config.morning_summary,
    )


def test_weekly_schedule_has_sunday_slot_and_monday_fallback(tmp_path):
    notifier = TradingCalendarNotifier(
        app_config=make_app_config(str(tmp_path / "state.json")),
        summary_service=FakeSummaryService([make_outlook()]),
        formatter=DiscordFormatter(),
        sender=FakeSender(),
        state_store=StateStore(tmp_path / "state.json"),
    )
    schedule = notifier.app_config.weekly_outlook

    assert notifier._weekly_target_week_start(
        datetime(2026, 7, 12, 17, 0, tzinfo=UTC), schedule
    ) == date(2026, 7, 13)
    assert notifier._weekly_target_week_start(
        datetime(2026, 7, 13, 4, 59, tzinfo=UTC), schedule
    ) == date(2026, 7, 13)
    assert (
        notifier._weekly_target_week_start(
            datetime(2026, 7, 13, 5, 0, tzinfo=UTC), schedule
        )
        is None
    )


@pytest.mark.parametrize("force", ["daily", "weekly", "recheck"])
def test_notifier_force_kind_emits_only_that_kind(tmp_path, force):
    state_store = StateStore(tmp_path / "state.json")
    weekly = WeeklyOutlook(
        week_start=date(2026, 4, 20),
        days=[make_outlook("no_major_usd_news_day", "No major USD news today.", ["quiet"])],
    )
    daily = make_outlook("no_major_usd_news_day", "No major USD news today.", ["quiet"])
    summary = FakeSummaryService([daily, daily, daily], weekly=weekly)

    notifier = TradingCalendarNotifier(
        app_config=make_app_config(str(tmp_path / "state.json")),
        summary_service=summary,
        formatter=DiscordFormatter(),
        sender=FakeSender(),
        state_store=state_store,
        dry_run=True,
        force_kind=force,
    )
    sent = asyncio.run(
        notifier.run_once(
            now=datetime(2026, 4, 16, 5, 0, tzinfo=UTC),
            target_date_override=date(2026, 4, 16),
        )
    )
    if force == "weekly":
        assert sent and _render_message(sent[0]).startswith("**Week Ahead")
    elif force == "daily":
        assert sent and _embed_title(sent[0])
    else:
        # recheck without a same-date daily baseline emits nothing.
        assert sent == []


def _render_message(message: str | WebhookPayload) -> str:
    return DiscordFormatter().render_for_console(message)


def _embed_title(message: str | WebhookPayload) -> str:
    assert isinstance(message, WebhookPayload)
    assert len(message.embeds) == 1
    title = message.embeds[0]["title"]
    assert isinstance(title, str)
    return title
