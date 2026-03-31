from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from bot.models import DayColor, RuleDefinition, SessionName, TradableStatus


@dataclass(frozen=True)
class DeliveryConfig:
    webhook_url_env: str


@dataclass(frozen=True)
class ScheduleConfig:
    timezone: str
    hour: int
    minute: int


@dataclass(frozen=True)
class CalendarConfig:
    urls: list[str]
    timeout_seconds: float
    market_timezone: str


@dataclass(frozen=True)
class AppConfig:
    delivery: DeliveryConfig
    schedule: ScheduleConfig
    calendar: CalendarConfig


@dataclass(frozen=True)
class EventAliasConfig:
    title_aliases: dict[str, list[str]]


def _load_yaml(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def load_app_config(path: Path) -> AppConfig:
    raw = _load_yaml(path)
    hour = int(raw["schedule"]["hour"])
    minute = int(raw["schedule"]["minute"])
    if not 0 <= hour <= 23:
        raise ValueError("schedule.hour must be between 0 and 23")
    if not 0 <= minute <= 59:
        raise ValueError("schedule.minute must be between 0 and 59")

    return AppConfig(
        delivery=DeliveryConfig(
            webhook_url_env=raw["delivery"]["webhook_url_env"],
        ),
        schedule=ScheduleConfig(
            timezone=raw["schedule"]["timezone"],
            hour=hour,
            minute=minute,
        ),
        calendar=CalendarConfig(
            urls=list(raw["calendar"]["urls"]),
            timeout_seconds=float(raw["calendar"]["timeout_seconds"]),
            market_timezone=raw["calendar"]["market_timezone"],
        ),
    )


def load_event_aliases(path: Path) -> EventAliasConfig:
    raw = _load_yaml(path)
    title_aliases = {
        category: [item.casefold() for item in values]
        for category, values in raw.get("title_aliases", {}).items()
    }
    return EventAliasConfig(title_aliases=title_aliases)


def load_rules(path: Path) -> list[RuleDefinition]:
    raw_rules = _load_yaml(path)
    if not isinstance(raw_rules, list) or not raw_rules:
        raise ValueError("rules.yaml must contain a non-empty list of rules")

    rules: list[RuleDefinition] = []
    for item in raw_rules:
        session_colors = {
            session: DayColor(color)
            for session, color in item["session_colors"].items()
        }
        for session_name in SessionName:
            session_colors.setdefault(session_name.value, DayColor.WHITE)
        rules.append(
            RuleDefinition(
                id=item["id"],
                priority=int(item["priority"]),
                when=item.get("when", {}),
                day_color=DayColor(item["day_color"]),
                tradable=TradableStatus(item["tradable"]),
                session_colors=session_colors,
                reason=item["reason"],
            )
        )
    ordered_rules = sorted(rules, key=lambda rule: rule.priority, reverse=True)
    if not any(not rule.when for rule in ordered_rules):
        raise ValueError("rules.yaml must include a fallback rule with an empty 'when' block")
    return ordered_rules
