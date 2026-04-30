from __future__ import annotations

import contextlib
import json
import os
import secrets
from collections.abc import Mapping
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from bot.type_defs import (
    BotStatePayload,
    StateSection,
    StructuredMapping,
    StructuredValue,
)

STATE_SCHEMA_VERSION = 1


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


@dataclass
class BotState:
    daily: DailyPostState = field(default_factory=DailyPostState)
    recheck: RecheckState = field(default_factory=RecheckState)
    weekly: WeeklyPostState = field(default_factory=WeeklyPostState)
    operations: OperationsState = field(default_factory=OperationsState)


_StateField = Literal["daily", "weekly", "recheck", "operations"]


class StateStore:
    def __init__(self, path: Path, lock_path: Path | None = None) -> None:
        self.path = path
        self.lock_path = lock_path

    def lock(self) -> AbstractContextManager[None]:
        if self.lock_path is None:
            return nullcontext()
        return _FileLock(self.lock_path)

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
            },
        }
        suffix = f".tmp-{os.getpid()}-{secrets.token_hex(4)}"
        temp_path = self.path.with_name(self.path.name + suffix)
        temp_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temp_path.replace(self.path)

    def _coerce_payload(self, raw: StructuredValue) -> BotStatePayload:
        if not isinstance(raw, Mapping):
            raise RuntimeError(f"State file root must be a JSON object: {self.path}")
        root = {str(key): item for key, item in raw.items()}
        version_raw = root.get("schema_version") or 0
        version = version_raw if isinstance(version_raw, int) else 0
        return {
            "schema_version": version,
            "daily": self._coerce_section(root.get("daily"), "state.daily"),
            "recheck": self._coerce_section(root.get("recheck"), "state.recheck"),
            "weekly": self._coerce_section(root.get("weekly"), "state.weekly"),
            "operations": self._coerce_section(
                root.get("operations"), "state.operations"
            ),
        }

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
        return payload

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


class _FileLock:
    """Simple advisory lock around a sentinel file. Best effort, not network-safe."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fd: int | None = None

    def __enter__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(50):
            try:
                self._fd = os.open(
                    str(self.path),
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o600,
                )
                return
            except FileExistsError:
                self._steal_stale_lock()
        raise RuntimeError(f"Could not acquire state lock: {self.path}")

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        with contextlib.suppress(FileNotFoundError):
            self.path.unlink()

    def _steal_stale_lock(self) -> None:
        try:
            stat = self.path.stat()
        except FileNotFoundError:
            return
        import time

        if time.time() - stat.st_mtime > 600:
            with contextlib.suppress(FileNotFoundError):
                self.path.unlink()
        else:
            time.sleep(0.05)
