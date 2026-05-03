from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from bot.calendar_source import CalendarSourceError
from bot.config import AppConfig, ScheduleConfig, WeeklyScheduleConfig
from bot.formatter import DiscordFormatter
from bot.state_store import (
    BotState,
    CalendarCacheState,
    DailyPostState,
    OperationsState,
    RecheckState,
    StateStore,
    WeeklyPostState,
)
from bot.type_defs import (
    PostKind,
    SourceStatus,
    SummaryServiceProtocol,
    WebhookSenderProtocol,
)
from bot.webhook_sender import WebhookError

logger = logging.getLogger(__name__)


_WEEKEND_DAYS = {"SAT", "SUN"}


class TradingCalendarNotifier:
    def __init__(
        self,
        app_config: AppConfig,
        summary_service: SummaryServiceProtocol,
        formatter: DiscordFormatter,
        sender: WebhookSenderProtocol,
        state_store: StateStore,
        *,
        ops_sender: WebhookSenderProtocol | None = None,
        dry_run: bool = False,
        force_kind: PostKind | None = None,
    ) -> None:
        self.app_config = app_config
        self.summary_service = summary_service
        self.formatter = formatter
        self.sender = sender
        self.ops_sender = ops_sender
        self.state_store = state_store
        self.dry_run = dry_run
        self.force_kind = force_kind

    async def run_once(
        self,
        now: datetime | None = None,
        target_date_override: date | None = None,
    ) -> list[str]:
        now = now or datetime.now(tz=UTC)
        sent_messages: list[str] = []

        lock = self.state_store.lock()
        try:
            lock.__enter__()
        except RuntimeError as error:
            await self._send_ops_alert_without_state(
                "State lock failed",
                f"Could not acquire state lock: {error}",
            )
            raise

        try:
            state = self.state_store.load()
            state_was_saved = False
            previous_daily = DailyPostState(
                posted_for_date=state.daily.posted_for_date,
                fingerprint=state.daily.fingerprint,
                snapshot=list(state.daily.snapshot),
            )
            previous_cache = CalendarCacheState(
                fetched_at=state.calendar_cache.fetched_at,
                events=list(state.calendar_cache.events),
            )

            if self.state_store.consume_stale_lock_recovered():
                alert_sent = await self._send_ops_alert_once(
                    state,
                    "state_lock_stale_recovered",
                    "Recovered stale state lock",
                    "A stale state lock was removed before this run. Check for prior interrupted cron runs.",
                )
                state_was_saved = alert_sent and not self.dry_run

            daily_target = target_date_override or self._schedule_date(
                now, self.app_config.morning_summary
            )
            daily_key = self._date_key(daily_target)
            daily_due = self._is_daily_due(now, state, daily_key, daily_target)

            weekly_target = self._weekly_target_week_start(
                now, self.app_config.weekly_outlook
            )
            if self.force_kind == "weekly" and weekly_target is None:
                weekly_target = (target_date_override or daily_target) - timedelta(
                    days=(target_date_override or daily_target).weekday()
                )
            weekly_due = self._is_weekly_due(state, weekly_target)

            recheck_target = target_date_override or self._schedule_date(
                now, self.app_config.ny_recheck
            )
            recheck_key = self._date_key(recheck_target)
            recheck_due = self._is_recheck_due(
                now, state, recheck_key, recheck_target
            )

            if not (daily_due or weekly_due or recheck_due):
                if state_was_saved and not self.dry_run:
                    self.state_store.save(state)
                logger.info(
                    "notifier_idle daily_due=%s weekly_due=%s recheck_due=%s",
                    daily_due,
                    weekly_due,
                    recheck_due,
                )
                return sent_messages

            source_status, events, operations_changed = await self._load_events(
                state, now
            )

            todays_outlook = None
            if daily_due:
                todays_outlook = self.summary_service.build_outlook_for_date(
                    daily_target, events, source_status
                )
                message = self.formatter.format_daily(todays_outlook)
                fingerprint = self.summary_service.daily_fingerprint(todays_outlook)
                next_state = DailyPostState(
                    posted_for_date=daily_key,
                    fingerprint=fingerprint,
                    snapshot=todays_outlook.calendar_snapshot,
                )
                await self._send_and_record(
                    message, sent_messages, state, "daily", next_state
                )
                state_was_saved = not self.dry_run
            else:
                logger.info(
                    "daily_skipped reason=%s",
                    self._daily_skip_reason(now, state, daily_key, daily_target),
                )

            if weekly_due and weekly_target is not None:
                weekly_outlook = self.summary_service.build_weekly_outlook(
                    weekly_target, events, source_status
                )
                if weekly_outlook is None and source_status == "live":
                    cached_events = self._fresh_cached_events(previous_cache, now)
                    if cached_events:
                        weekly_outlook = self.summary_service.build_weekly_outlook(
                            weekly_target, cached_events, "cached"
                        )
                if weekly_outlook is not None:
                    message = self.formatter.format_weekly(weekly_outlook)
                    await self._send_and_record(
                        message,
                        sent_messages,
                        state,
                        "weekly",
                        WeeklyPostState(
                            posted_for_week_start=self._date_key(weekly_target)
                        ),
                    )
                    state_was_saved = not self.dry_run
                else:
                    alert_sent = await self._send_ops_alert_once(
                        state,
                        f"weekly_no_events:{self._date_key(weekly_target)}",
                        "Weekly outlook skipped",
                        f"No live or cached events were available for week_start={weekly_target}.",
                    )
                    state_was_saved = state_was_saved or (alert_sent and not self.dry_run)
                    logger.info(
                        "weekly_skipped reason=no_events week_start=%s",
                        weekly_target,
                    )

            if recheck_due:
                if todays_outlook is None or todays_outlook.target_date != recheck_target:
                    todays_outlook = self.summary_service.build_outlook_for_date(
                        recheck_target, events, source_status
                    )
                baseline = state.daily
                if daily_due and daily_key != recheck_key:
                    baseline = previous_daily
                if source_status == "unavailable":
                    logger.info("recheck_skipped reason=source_unavailable")
                elif not baseline.fingerprint:
                    logger.info("recheck_skipped reason=no_baseline_fingerprint")
                else:
                    current_fp = self.summary_service.daily_fingerprint(todays_outlook)
                    if current_fp != baseline.fingerprint:
                        changed_items = self._changed_items(
                            baseline.snapshot, todays_outlook.calendar_snapshot
                        )
                        update = self.formatter.format_update(
                            todays_outlook, changed_items
                        )
                        await self._send_and_record(
                            update,
                            sent_messages,
                            state,
                            "recheck",
                            RecheckState(checked_for_date=recheck_key),
                        )
                        state_was_saved = not self.dry_run
                    else:
                        logger.info(
                            "recheck_unchanged date=%s", recheck_key
                        )
                        state.recheck = RecheckState(checked_for_date=recheck_key)
                        if not self.dry_run:
                            self.state_store.save(state)
                            state_was_saved = True

            if operations_changed and not state_was_saved and not self.dry_run:
                self.state_store.save(state)
                logger.info(
                    "operations_state_saved source_status=%s consecutive_failures=%s",
                    state.operations.last_source_status,
                    state.operations.consecutive_source_failures,
                )

            return sent_messages
        finally:
            lock.__exit__(None, None, None)

    async def _load_events(
        self, state: BotState, now: datetime
    ) -> tuple[SourceStatus, list, bool]:
        previous = state.operations
        try:
            events = await self.summary_service.load_events()
        except CalendarSourceError as error:
            state.operations = OperationsState(
                consecutive_source_failures=previous.consecutive_source_failures + 1,
                last_source_status="unavailable",
                alerted_keys=list(previous.alerted_keys),
            )
            logger.warning(
                "calendar_unavailable consecutive_failures=%s error=%s",
                state.operations.consecutive_source_failures,
                error,
            )
            if (
                previous.consecutive_source_failures
                < self.app_config.policy.max_consecutive_source_failures
                <= state.operations.consecutive_source_failures
            ):
                await self._send_ops_alert_once(
                    state,
                    f"source_failures_threshold:{now.astimezone(UTC).date().isoformat()}",
                    "Calendar source failure threshold reached",
                    (
                        "Calendar source failed "
                        f"{state.operations.consecutive_source_failures} consecutive times: {error}"
                    ),
                )
            cached_events = self._fresh_cached_events(state.calendar_cache, now)
            if cached_events:
                state.operations = OperationsState(
                    consecutive_source_failures=state.operations.consecutive_source_failures,
                    last_source_status="cached",
                    alerted_keys=list(state.operations.alerted_keys),
                )
                return "cached", cached_events, True
            return "unavailable", [], True
        else:
            state.operations = OperationsState(
                consecutive_source_failures=0,
                last_source_status="live",
                alerted_keys=list(previous.alerted_keys),
            )
            state.calendar_cache = CalendarCacheState(
                fetched_at=now.astimezone(UTC).isoformat(),
                events=list(events),
            )
            operations_changed = state.operations != previous
            return "live", events, True if events else operations_changed

    def _fresh_cached_events(
        self, cache: CalendarCacheState, now: datetime
    ) -> list | None:
        if not cache.fetched_at or not cache.events:
            return None
        try:
            fetched_at = datetime.fromisoformat(cache.fetched_at)
        except ValueError:
            return None
        if fetched_at.tzinfo is None:
            return None
        max_age = timedelta(hours=self.app_config.policy.calendar_cache_max_age_hours)
        if now.astimezone(UTC) - fetched_at.astimezone(UTC) > max_age:
            return None
        return list(cache.events)

    async def _send_ops_alert_once(
        self, state: BotState, key: str, title: str, body: str
    ) -> bool:
        if key in state.operations.alerted_keys:
            return False
        if self.dry_run or self.ops_sender is None:
            logger.info("ops_alert_skipped key=%s title=%s", key, title)
            return False

        message = f"{title}\n{body}"
        try:
            await self.ops_sender.send_message(message)
        except WebhookError as error:
            logger.error("ops_alert_failed key=%s error=%s", key, error)
            return False

        state.operations.alerted_keys.append(key)
        logger.info("ops_alert_sent key=%s", key)
        return True

    async def _send_ops_alert_without_state(self, title: str, body: str) -> None:
        if self.dry_run or self.ops_sender is None:
            logger.info("ops_alert_skipped title=%s", title)
            return
        try:
            await self.ops_sender.send_message(f"{title}\n{body}")
        except WebhookError as error:
            logger.error("ops_alert_failed title=%s error=%s", title, error)

    async def _send_and_record(
        self,
        message: str,
        sent_messages: list[str],
        state: BotState,
        kind: PostKind,
        next_state: DailyPostState | WeeklyPostState | RecheckState,
    ) -> None:
        if self.dry_run:
            logger.info("dry_run_message kind=%s length=%s", kind, len(message))
            sent_messages.append(message)
            self._apply_state_update(state, kind, next_state)
            return
        try:
            await self.sender.send_message(message)
        except WebhookError as error:
            logger.error("webhook_send_failed kind=%s error=%s", kind, error)
            raise

        sent_messages.append(message)
        self._apply_state_update(state, kind, next_state)
        self.state_store.save(state)
        logger.info("posted kind=%s length=%s", kind, len(message))

    @staticmethod
    def _apply_state_update(
        state: BotState,
        kind: PostKind,
        next_state: DailyPostState | WeeklyPostState | RecheckState,
    ) -> None:
        match kind:
            case "daily":
                assert isinstance(next_state, DailyPostState)
                state.daily = next_state
            case "weekly":
                assert isinstance(next_state, WeeklyPostState)
                state.weekly = next_state
            case "recheck":
                assert isinstance(next_state, RecheckState)
                state.recheck = next_state

    def _is_daily_due(
        self, now: datetime, state: BotState, key: str, target_date: date
    ) -> bool:
        if self.force_kind == "daily":
            return True
        if self.force_kind is not None:
            return False
        if state.daily.posted_for_date == key:
            return False
        if not self._is_due(now, self.app_config.morning_summary):
            return False
        if self._is_post_too_late(now, self.app_config.morning_summary):
            return False
        return not self._is_weekend_suppressed(target_date)

    def _is_weekly_due(self, state: BotState, week_start: date | None) -> bool:
        if self.force_kind == "weekly":
            return week_start is not None
        if self.force_kind is not None or week_start is None:
            return False
        return state.weekly.posted_for_week_start != self._date_key(week_start)

    def _is_recheck_due(
        self, now: datetime, state: BotState, key: str, target_date: date
    ) -> bool:
        if self.force_kind is not None and self.force_kind != "recheck":
            return False
        if not self._has_same_date_daily_baseline(state, key):
            return False
        if self.force_kind == "recheck":
            return True
        if state.recheck.checked_for_date == key:
            return False
        if not self._is_due(now, self.app_config.ny_recheck):
            return False
        if self._is_post_too_late(now, self.app_config.ny_recheck):
            return False
        return not self._is_weekend_suppressed(target_date)

    def _has_same_date_daily_baseline(self, state: BotState, key: str) -> bool:
        return state.daily.posted_for_date == key

    def _is_weekend_suppressed(self, target_date: date) -> bool:
        return (
            not self.app_config.policy.post_on_weekend
            and self._weekday_code(target_date) in _WEEKEND_DAYS
        )

    def _daily_skip_reason(
        self, now: datetime, state: BotState, key: str, target_date: date
    ) -> str:
        if state.daily.posted_for_date == key:
            return "already_posted"
        if not self._is_due(now, self.app_config.morning_summary):
            return "not_due_yet"
        if self._is_post_too_late(now, self.app_config.morning_summary):
            return "post_window_expired"
        if self._is_weekend_suppressed(target_date):
            return "weekend_skip"
        return "unknown"

    def _is_post_too_late(self, now: datetime, schedule: ScheduleConfig) -> bool:
        local_now = now.astimezone(ZoneInfo(schedule.timezone))
        threshold = local_now.replace(
            hour=schedule.hour, minute=schedule.minute, second=0, microsecond=0
        )
        if local_now < threshold:
            return False
        delta = local_now - threshold
        return delta > timedelta(minutes=self.app_config.policy.max_post_age_minutes)

    def _schedule_date(self, now: datetime, schedule: ScheduleConfig) -> date:
        return now.astimezone(ZoneInfo(schedule.timezone)).date()

    def _date_key(self, value: date) -> str:
        return value.isoformat()

    def _weekday_code(self, value: date) -> str:
        return value.strftime("%a").upper()[:3]

    def _schedule_clock(self, local_now: datetime) -> tuple[int, int]:
        return local_now.hour, local_now.minute

    def _is_due(self, now: datetime, schedule: ScheduleConfig) -> bool:
        local_now = now.astimezone(ZoneInfo(schedule.timezone))
        return self._schedule_clock(local_now) >= (schedule.hour, schedule.minute)

    def _weekly_target_week_start(
        self, now: datetime, schedule: WeeklyScheduleConfig
    ) -> date | None:
        local_now = now.astimezone(ZoneInfo(schedule.timezone))
        current_day = local_now.strftime("%a").upper()[:3]
        current_clock = self._schedule_clock(local_now)
        weekly_clock = (schedule.hour, schedule.minute)

        if current_day == schedule.weekday and current_clock >= weekly_clock:
            return local_now.date() + timedelta(days=1)

        morning = self.app_config.morning_summary
        if current_day == "MON" and current_clock < (morning.hour, morning.minute):
            return local_now.date()

        return None

    def _changed_items(
        self, previous_snapshot: list[str], current_snapshot: list[str]
    ) -> list[str]:
        previous = set(previous_snapshot)
        current = set(current_snapshot)
        added = sorted(current - previous)
        removed = sorted(previous - current)

        changed: list[str] = []
        changed.extend(f"Added {self._describe_snapshot_item(item)}" for item in added)
        changed.extend(f"Removed {self._describe_snapshot_item(item)}" for item in removed)
        return changed

    def _describe_snapshot_item(self, item: str) -> str:
        prefix, sep, value = item.partition(":")
        if not sep:
            return item
        if prefix == "strong_today":
            return f"strongest USD news: {value}"
        if prefix == "strong_tomorrow":
            return f"tomorrow's strongest USD news: {value}"
        if prefix == "other_red_today":
            return f"other red-folder USD news: {value}"
        if prefix == "holiday":
            if value == "full":
                return "a full USD holiday on the calendar"
            if value == "partial":
                return "a partial holiday or early close on the calendar"
            return f"an unknown USD holiday marker: {value}"
        if prefix == "quiet":
            return "the quiet-day note"
        return value


__all__ = ["TradingCalendarNotifier"]
