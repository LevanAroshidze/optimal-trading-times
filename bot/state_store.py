from __future__ import annotations

import contextlib
import json
import os
import secrets
import threading
import time
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

from bot.models import CalendarEvent
from bot.type_defs import (
    BotStatePayload,
    StateSection,
    StructuredMapping,
    StructuredValue,
)

STATE_SCHEMA_VERSION = 4
_LOCK_ACQUIRE_ATTEMPTS = 50
_LOCK_RETRY_SLEEP_SECONDS = 0.05
_LOCK_STALE_AFTER_SECONDS = 600
_LOCK_HEARTBEAT_SECONDS = 60
_LOCK_TOKEN_BYTES = 16


@dataclass
class DailyPostState:
    posted_for_date: str | None = None
    fingerprint: str | None = None
    snapshot: list[str] = field(default_factory=list)


@dataclass
class RecheckState:
    checked_for_date: str | None = None


@dataclass
class WeeklyPostState:
    posted_for_week_start: str | None = None


@dataclass
class OperationsState:
    consecutive_source_failures: int = 0
    last_source_status: str | None = None
    alerted_keys: list[str] = field(default_factory=list)
    source_retry_after: str | None = None
    weekly_retry_after: dict[str, str] = field(default_factory=dict)
    pending_posts: dict[str, str] = field(default_factory=dict)


@dataclass
class CalendarCacheState:
    fetched_at: str | None = None
    events: list[CalendarEvent] = field(default_factory=list)


@dataclass
class BotState:
    daily: DailyPostState = field(default_factory=DailyPostState)
    recheck: RecheckState = field(default_factory=RecheckState)
    weekly: WeeklyPostState = field(default_factory=WeeklyPostState)
    operations: OperationsState = field(default_factory=OperationsState)
    calendar_cache: CalendarCacheState = field(default_factory=CalendarCacheState)


_StateField = Literal["daily", "weekly", "recheck", "operations"]


class StateStore:
    def __init__(self, path: Path, lock_path: Path | None = None) -> None:
        self.path = path
        self.lock_path = lock_path
        self._stale_lock_recovered = False

    def lock(self) -> AbstractContextManager[None]:
        if self.lock_path is None:
            return nullcontext()
        return _FileLock(self.lock_path, self._mark_stale_lock_recovered)

    def consume_stale_lock_recovered(self) -> bool:
        recovered = self._stale_lock_recovered
        self._stale_lock_recovered = False
        return recovered

    def load(self) -> BotState:
        if not self.path.exists():
            return BotState()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise RuntimeError(f"Invalid state file: {self.path}") from error
        except OSError as error:
            raise RuntimeError(f"Failed to read state file: {self.path}") from error
        payload = self._coerce_payload(raw)
        daily = payload.get("daily", {})
        recheck = payload.get("recheck", {})
        weekly = payload.get("weekly", {})
        operations = payload.get("operations", {})
        calendar_cache = payload.get("calendar_cache", {})

        return BotState(
            daily=DailyPostState(
                posted_for_date=daily.get("posted_for_date"),
                fingerprint=daily.get("fingerprint"),
                snapshot=list(daily.get("snapshot", [])),
            ),
            recheck=RecheckState(
                checked_for_date=recheck.get("checked_for_date"),
            ),
            weekly=WeeklyPostState(
                posted_for_week_start=weekly.get("posted_for_week_start"),
            ),
            operations=OperationsState(
                consecutive_source_failures=int(
                    operations.get("consecutive_source_failures") or 0
                ),
                last_source_status=operations.get("last_source_status"),
                alerted_keys=list(operations.get("alerted_keys", [])),
                source_retry_after=operations.get("source_retry_after"),
                weekly_retry_after=dict(operations.get("weekly_retry_after", {})),
                pending_posts=dict(operations.get("pending_posts", {})),
            ),
            calendar_cache=CalendarCacheState(
                fetched_at=calendar_cache.get("fetched_at"),
                events=self._events_from_payload(calendar_cache.get("events", [])),
            ),
        )

    def save(self, state: BotState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": STATE_SCHEMA_VERSION,
            "daily": {
                "posted_for_date": state.daily.posted_for_date,
                "fingerprint": state.daily.fingerprint,
                "snapshot": state.daily.snapshot,
            },
            "recheck": {
                "checked_for_date": state.recheck.checked_for_date,
            },
            "weekly": {
                "posted_for_week_start": state.weekly.posted_for_week_start,
            },
            "operations": {
                "consecutive_source_failures": state.operations.consecutive_source_failures,
                "last_source_status": state.operations.last_source_status,
                "alerted_keys": state.operations.alerted_keys,
                "source_retry_after": state.operations.source_retry_after,
                "weekly_retry_after": state.operations.weekly_retry_after,
                "pending_posts": state.operations.pending_posts,
            },
            "calendar_cache": {
                "fetched_at": state.calendar_cache.fetched_at,
                "events": [
                    {
                        "title": event.title,
                        "country": event.country,
                        "impact": event.impact,
                        "starts_at": event.starts_at.isoformat(),
                    }
                    for event in state.calendar_cache.events
                ],
            },
        }
        suffix = f".tmp-{os.getpid()}-{secrets.token_hex(4)}"
        temp_path = self.path.with_name(self.path.name + suffix)
        try:
            temp_path.write_text(
                json.dumps(payload, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            temp_path.replace(self.path)
        except OSError:
            with contextlib.suppress(FileNotFoundError):
                temp_path.unlink()
            raise

    def _coerce_payload(self, raw: StructuredValue) -> BotStatePayload:
        if not isinstance(raw, Mapping):
            raise RuntimeError(f"State file root must be a JSON object: {self.path}")
        root = {str(key): item for key, item in raw.items()}
        version = self._coerce_schema_version(root.get("schema_version"))
        return {
            "schema_version": version,
            "daily": self._coerce_section(root.get("daily"), "state.daily"),
            "recheck": self._coerce_section(root.get("recheck"), "state.recheck"),
            "weekly": self._coerce_section(root.get("weekly"), "state.weekly"),
            "operations": self._coerce_section(
                root.get("operations"), "state.operations"
            ),
            "calendar_cache": self._coerce_section(
                root.get("calendar_cache"), "state.calendar_cache"
            ),
        }

    def _coerce_schema_version(self, value: StructuredValue | None) -> int:
        if value is None:
            return 0
        if not isinstance(value, int) or isinstance(value, bool):
            return 0
        if value > STATE_SCHEMA_VERSION:
            raise RuntimeError(
                f"State file schema_version {value} is newer than supported "
                f"{STATE_SCHEMA_VERSION}: {self.path}"
            )
        return value

    def _coerce_section(self, value: StructuredValue | None, name: str) -> StateSection:
        if value is None:
            return {}

        section = self._expect_mapping(value, name)
        payload: StateSection = {}
        for key, item in section.items():
            if key == "posted_for_date":
                payload["posted_for_date"] = self._optional_string(
                    item, f"{name}.posted_for_date"
                )
            elif key == "fingerprint":
                payload["fingerprint"] = self._optional_string(
                    item, f"{name}.fingerprint"
                )
            elif key == "checked_for_date":
                payload["checked_for_date"] = self._optional_string(
                    item, f"{name}.checked_for_date"
                )
            elif key == "posted_for_week_start":
                payload["posted_for_week_start"] = self._optional_string(
                    item, f"{name}.posted_for_week_start"
                )
            elif key == "snapshot" and name == "state.daily":
                payload["snapshot"] = self._string_list(item, f"{name}.snapshot")
            elif key == "consecutive_source_failures" and name == "state.operations":
                if not isinstance(item, int) or isinstance(item, bool):
                    raise RuntimeError(
                        f"{name}.consecutive_source_failures must be an int"
                    )
                payload["consecutive_source_failures"] = item
            elif key == "last_source_status" and name == "state.operations":
                payload["last_source_status"] = self._optional_string(
                    item, f"{name}.last_source_status"
                )
            elif key == "alerted_keys" and name == "state.operations":
                payload["alerted_keys"] = self._string_list(
                    item, f"{name}.alerted_keys"
                )
            elif key == "source_retry_after" and name == "state.operations":
                payload["source_retry_after"] = self._optional_string(
                    item, f"{name}.source_retry_after"
                )
            elif key == "weekly_retry_after" and name == "state.operations":
                payload["weekly_retry_after"] = self._string_dict(
                    item, f"{name}.weekly_retry_after"
                )
            elif key == "pending_posts" and name == "state.operations":
                payload["pending_posts"] = self._string_dict(
                    item, f"{name}.pending_posts"
                )
            elif key == "fetched_at" and name == "state.calendar_cache":
                payload["fetched_at"] = self._optional_string(
                    item, f"{name}.fetched_at"
                )
            elif key == "events" and name == "state.calendar_cache":
                payload["events"] = self._event_list(item, f"{name}.events")
        return payload

    def _mark_stale_lock_recovered(self) -> None:
        self._stale_lock_recovered = True

    def _expect_mapping(self, value: StructuredValue, name: str) -> StructuredMapping:
        if not isinstance(value, Mapping):
            raise RuntimeError(f"{name} must be a mapping")
        return {str(key): item for key, item in value.items()}

    def _optional_string(self, value: StructuredValue, name: str) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise RuntimeError(f"{name} must be a string or null")
        return value

    def _string_list(self, value: StructuredValue, name: str) -> list[str]:
        if not isinstance(value, list):
            raise RuntimeError(f"{name} must be a list of strings")
        strings: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise RuntimeError(f"{name} must be a list of strings")
            strings.append(item)
        return strings

    def _string_dict(self, value: StructuredValue, name: str) -> dict[str, str]:
        if not isinstance(value, Mapping):
            raise RuntimeError(f"{name} must be a mapping of strings")
        strings: dict[str, str] = {}
        for key, item in value.items():
            if not isinstance(item, str):
                raise RuntimeError(f"{name} must be a mapping of strings")
            strings[str(key)] = item
        return strings

    def _event_list(
        self, value: StructuredValue, name: str
    ) -> list[StructuredMapping]:
        if not isinstance(value, list):
            raise RuntimeError(f"{name} must be a list of event objects")
        events: list[StructuredMapping] = []
        for item in value:
            event = self._expect_mapping(item, name)
            events.append(event)
        return events

    def _events_from_payload(
        self, items: object
    ) -> list[CalendarEvent]:
        if not isinstance(items, list):
            return []
        events: list[CalendarEvent] = []
        for index, item in enumerate(items):
            event = self._expect_mapping(item, f"state.calendar_cache.events.{index}")
            title = self._required_string(event.get("title"), f"cache event {index}.title")
            country = self._required_string(
                event.get("country"), f"cache event {index}.country"
            )
            impact = self._required_string(
                event.get("impact"), f"cache event {index}.impact"
            )
            starts_raw = self._required_string(
                event.get("starts_at"), f"cache event {index}.starts_at"
            )
            try:
                starts_at = datetime.fromisoformat(starts_raw)
            except ValueError as error:
                raise RuntimeError(
                    f"cache event {index}.starts_at must be ISO datetime"
                ) from error
            if starts_at.tzinfo is None:
                raise RuntimeError(f"cache event {index}.starts_at must be timezone-aware")
            events.append(
                CalendarEvent(
                    title=title,
                    country=country,
                    impact=impact,
                    starts_at=starts_at,
                )
            )
        return events

    def _required_string(self, value: StructuredValue, name: str) -> str:
        if not isinstance(value, str) or not value:
            raise RuntimeError(f"{name} must be a non-empty string")
        return value


class _FileLock:
    """Simple advisory lock around a sentinel file. Best effort, not network-safe."""

    def __init__(self, path: Path, on_stale_recovered: Callable[[], None]) -> None:
        self.path = path
        self._fd: int | None = None
        self._on_stale_recovered = on_stale_recovered
        self._token = secrets.token_hex(_LOCK_TOKEN_BYTES)
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None

    def __enter__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(_LOCK_ACQUIRE_ATTEMPTS):
            try:
                self._fd = os.open(
                    str(self.path),
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o600,
                )
                os.write(self._fd, self._token.encode("ascii"))
                self._start_heartbeat()
                return
            except FileExistsError:
                self._steal_stale_lock()
        raise RuntimeError(f"Could not acquire state lock: {self.path}")

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        self._stop_heartbeat()
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        with contextlib.suppress(FileNotFoundError, OSError):
            if self.path.read_text(encoding="ascii") == self._token:
                self.path.unlink()

    def _steal_stale_lock(self) -> None:
        try:
            stat = self.path.stat()
        except FileNotFoundError:
            return

        if time.time() - stat.st_mtime > _LOCK_STALE_AFTER_SECONDS:
            with contextlib.suppress(FileNotFoundError):
                self.path.unlink()
                self._on_stale_recovered()
        else:
            time.sleep(_LOCK_RETRY_SLEEP_SECONDS)

    def _start_heartbeat(self) -> None:
        self._heartbeat_stop.clear()
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat,
            name="state-lock-heartbeat",
            daemon=True,
        )
        self._heartbeat_thread.start()

    def _stop_heartbeat(self) -> None:
        self._heartbeat_stop.set()
        if self._heartbeat_thread is not None:
            self._heartbeat_thread.join(timeout=1.0)
            self._heartbeat_thread = None

    def _heartbeat(self) -> None:
        while not self._heartbeat_stop.wait(_LOCK_HEARTBEAT_SECONDS):
            with contextlib.suppress(FileNotFoundError, OSError):
                if self.path.read_text(encoding="ascii") == self._token:
                    os.utime(self.path, None)
