from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from bot.type_defs import BotStatePayload, StateSection, StructuredMapping, StructuredValue


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
class BotState:
    daily: DailyPostState = field(default_factory=DailyPostState)
    recheck: RecheckState = field(default_factory=RecheckState)
    weekly: WeeklyPostState = field(default_factory=WeeklyPostState)


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path

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
        daily = payload["daily"]
        recheck = payload["recheck"]
        weekly = payload["weekly"]

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
        )

    def save(self, state: BotState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
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
        }
        temp_path = self.path.with_suffix(".tmp")
        temp_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temp_path.replace(self.path)

    def _coerce_payload(self, raw: StructuredValue) -> BotStatePayload:
        if not isinstance(raw, Mapping):
            raise RuntimeError(f"State file root must be a JSON object: {self.path}")
        root = {str(key): item for key, item in raw.items()}
        return {
            "daily": self._coerce_section(root.get("daily"), "state.daily"),
            "recheck": self._coerce_section(root.get("recheck"), "state.recheck"),
            "weekly": self._coerce_section(root.get("weekly"), "state.weekly"),
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
                continue
            if key == "fingerprint":
                payload["fingerprint"] = self._optional_string(
                    item, f"{name}.fingerprint"
                )
                continue
            if key == "checked_for_date":
                payload["checked_for_date"] = self._optional_string(
                    item, f"{name}.checked_for_date"
                )
                continue
            if key == "posted_for_week_start":
                payload["posted_for_week_start"] = self._optional_string(
                    item, f"{name}.posted_for_week_start"
                )
                continue
            if key == "snapshot" and name == "state.daily":
                payload["snapshot"] = self._string_list(item, f"{name}.snapshot")
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
