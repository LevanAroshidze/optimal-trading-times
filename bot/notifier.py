from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from bot.calendar_source import CalendarSourceError
from bot.config import AppConfig, ScheduleConfig, WeeklyScheduleConfig
from bot.formatter import DiscordFormatter
from bot.state_store import BotState, DailyPostState, RecheckState, StateStore, WeeklyPostState
from bot.type_defs import SourceStatus, SummaryServiceProtocol
from bot.webhook_sender import DiscordWebhookSender


class TradingCalendarNotifier:
    def __init__(
        self,
        app_config: AppConfig,
        summary_service: SummaryServiceProtocol,
        formatter: DiscordFormatter,
        sender: DiscordWebhookSender,
        state_store: StateStore,
    ) -> None:
        self.app_config = app_config
        self.summary_service = summary_service
        self.formatter = formatter
        self.sender = sender
        self.state_store = state_store

    async def run_once(self, now: datetime | None = None) -> list[str]:
        now = now or datetime.now(tz=UTC)
        state = self.state_store.load()
        sent_messages: list[str] = []

        daily_target = self._schedule_date(now, self.app_config.morning_summary)
        daily_key = self._date_key(daily_target)
        daily_due = self._is_due(now, self.app_config.morning_summary) and (
            state.daily.posted_for_date != daily_key
        )

        weekly_target = self._weekly_target_week_start(now, self.app_config.weekly_outlook)
        weekly_due = weekly_target is not None and (
            state.weekly.posted_for_week_start != self._date_key(weekly_target)
        )

        recheck_target = self._schedule_date(now, self.app_config.morning_summary)
        recheck_key = self._date_key(recheck_target)
        recheck_due = self._is_due(now, self.app_config.ny_recheck) and (
            state.recheck.checked_for_date != recheck_key
        )

        if not (daily_due or weekly_due or recheck_due):
            return sent_messages

        source_status: SourceStatus = "live"
        try:
            events = await self.summary_service.load_events()
        except CalendarSourceError:
            events = []
            source_status = "unavailable"
        todays_outlook = None

        if daily_due:
            todays_outlook = self.summary_service.build_outlook_for_date(
                daily_target,
                events,
                source_status,
            )
            message = self.formatter.format_daily(todays_outlook)
            await self._send_and_record(
                message,
                sent_messages,
                state,
                "daily",
                DailyPostState(
                    posted_for_date=daily_key,
                    fingerprint=self.summary_service.daily_fingerprint(todays_outlook),
                    snapshot=todays_outlook.calendar_snapshot,
                ),
            )
        if weekly_due and weekly_target is not None:
            weekly_outlook = self.summary_service.build_weekly_outlook(
                weekly_target,
                events,
                source_status,
            )
            if weekly_outlook is not None:
                message = self.formatter.format_weekly(weekly_outlook)
                await self._send_and_record(
                    message,
                    sent_messages,
                    state,
                    "weekly",
                    WeeklyPostState(
                        posted_for_week_start=self._date_key(weekly_target),
                    ),
                )

        if recheck_due:
            if todays_outlook is None:
                todays_outlook = self.summary_service.build_outlook_for_date(
                    recheck_target, events, source_status
                )
            if source_status == "live" and state.daily.fingerprint:
                current_fingerprint = self.summary_service.daily_fingerprint(todays_outlook)
                next_recheck_state = RecheckState(checked_for_date=recheck_key)
                if current_fingerprint != state.daily.fingerprint:
                    changed_items = self._changed_items(
                        state.daily.snapshot, todays_outlook.calendar_snapshot
                    )
                    message = self.formatter.format_update(todays_outlook, changed_items)
                    next_recheck_state = RecheckState(checked_for_date=recheck_key)
                    await self._send_and_record(
                        message,
                        sent_messages,
                        state,
                        "recheck",
                        next_recheck_state,
                    )
                else:
                    state.recheck = RecheckState(checked_for_date=recheck_key)
                    self.state_store.save(state)

        return sent_messages

    async def _send_and_record(
        self,
        message: str,
        sent_messages: list[str],
        state: BotState,
        state_attr: str,
        next_state: DailyPostState | WeeklyPostState | RecheckState,
    ) -> None:
        await self.sender.send_message(message)
        sent_messages.append(message)
        setattr(state, state_attr, next_state)
        self.state_store.save(state)

    def _schedule_date(self, now: datetime, schedule: ScheduleConfig) -> date:
        return now.astimezone(ZoneInfo(schedule.timezone)).date()

    def _date_key(self, value: date) -> str:
        return value.isoformat()

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
        prefix, _, value = item.partition(":")
        if not _:
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
        if prefix == "quiet":
            return "the quiet-day note"
        return value
