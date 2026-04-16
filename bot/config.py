from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml

from bot.models import (
    DayColor,
    RuleConditions,
    RuleDefinition,
    SessionName,
    TradableStatus,
)
from bot.type_defs import (
    RuleConditionValue,
    RuleOperatorMap,
    StructuredMapping,
    StructuredValue,
)


@dataclass(frozen=True)
class DeliveryConfig:
    webhook_url_env: str


@dataclass(frozen=True)
class ScheduleConfig:
    timezone: str
    hour: int
    minute: int


@dataclass(frozen=True)
class WeeklyScheduleConfig(ScheduleConfig):
    weekday: str


@dataclass(frozen=True)
class RuntimeConfig:
    state_path: str


@dataclass(frozen=True)
class CalendarConfig:
    urls: list[str]
    timeout_seconds: float
    market_timezone: str


@dataclass(frozen=True)
class AppConfig:
    delivery: DeliveryConfig
    runtime: RuntimeConfig
    morning_summary: ScheduleConfig
    ny_recheck: ScheduleConfig
    weekly_outlook: WeeklyScheduleConfig
    calendar: CalendarConfig


@dataclass(frozen=True)
class EventAliasConfig:
    title_aliases: dict[str, tuple[str, ...]]
    strongest_categories: tuple[str, ...] = ()
    partial_holiday_terms: tuple[str, ...] = ()


def _load_yaml(path: Path) -> StructuredValue:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def load_app_config(path: Path) -> AppConfig:
    raw = _expect_mapping(_load_yaml(path), path.name)
    schedules = _expect_mapping(raw["schedules"], "schedules")
    runtime = _expect_mapping(raw["runtime"], "runtime")
    calendar = _expect_mapping(raw["calendar"], "calendar")
    morning_summary = _load_schedule(schedules["morning_summary"], "morning_summary")
    ny_recheck = _load_schedule(schedules["ny_recheck"], "ny_recheck")
    weekly_outlook = _load_weekly_schedule(
        schedules["weekly_outlook"], "weekly_outlook"
    )

    return AppConfig(
        delivery=DeliveryConfig(
            webhook_url_env=_expect_string(
                _expect_mapping(raw["delivery"], "delivery")["webhook_url_env"],
                "delivery.webhook_url_env",
            ),
        ),
        runtime=RuntimeConfig(
            state_path=_expect_string(runtime["state_path"], "runtime.state_path"),
        ),
        morning_summary=morning_summary,
        ny_recheck=ny_recheck,
        weekly_outlook=weekly_outlook,
        calendar=CalendarConfig(
            urls=_expect_string_list(calendar["urls"], "calendar.urls"),
            timeout_seconds=_coerce_float(calendar["timeout_seconds"], "calendar.timeout_seconds"),
            market_timezone=_expect_string(
                calendar["market_timezone"], "calendar.market_timezone"
            ),
        ),
    )


def load_event_aliases(path: Path) -> EventAliasConfig:
    raw = _expect_mapping(_load_yaml(path), path.name)
    title_aliases = {
        _expect_string(category, "title_aliases key"): tuple(
            item.casefold()
            for item in _expect_string_list(values, f"title_aliases.{category}")
        )
        for category, values in _expect_mapping(
            raw.get("title_aliases", {}), "title_aliases"
        ).items()
    }
    return EventAliasConfig(
        title_aliases=title_aliases,
        strongest_categories=tuple(
            _expect_string_list(raw.get("strongest_categories", []), "strongest_categories")
        ),
        partial_holiday_terms=tuple(
            item.casefold()
            for item in _expect_string_list(
                raw.get("partial_holiday_terms", []), "partial_holiday_terms"
            )
        ),
    )


def load_rules(path: Path) -> list[RuleDefinition]:
    raw_rules = _load_yaml(path)
    if not isinstance(raw_rules, list) or not raw_rules:
        raise ValueError("rules.yaml must contain a non-empty list of rules")

    rules: list[RuleDefinition] = []
    for raw_item in raw_rules:
        item = _expect_mapping(raw_item, "rule")
        rules.append(
            RuleDefinition(
                id=_expect_string(item["id"], "rule.id"),
                priority=_coerce_int(item["priority"], "rule.priority"),
                when=_coerce_rule_conditions(item.get("when", {})),
                day_color=DayColor(_expect_string(item["day_color"], "rule.day_color")),
                tradable=TradableStatus(
                    _expect_string(item["tradable"], "rule.tradable")
                ),
                session_colors=_coerce_session_colors(item["session_colors"]),
                reason=_expect_string(item["reason"], "rule.reason"),
            )
        )
    ordered_rules = sorted(rules, key=lambda rule: rule.priority, reverse=True)
    if not any(not rule.when for rule in ordered_rules):
        raise ValueError("rules.yaml must include a fallback rule with an empty 'when' block")
    return ordered_rules


def _load_schedule(raw: StructuredValue, name: str) -> ScheduleConfig:
    schedule = _expect_mapping(raw, name)
    hour = _coerce_int(schedule["hour"], f"{name}.hour")
    minute = _coerce_int(schedule["minute"], f"{name}.minute")
    if not 0 <= hour <= 23:
        raise ValueError(f"{name}.hour must be between 0 and 23")
    if not 0 <= minute <= 59:
        raise ValueError(f"{name}.minute must be between 0 and 59")
    return ScheduleConfig(
        timezone=_expect_string(schedule["timezone"], f"{name}.timezone"),
        hour=hour,
        minute=minute,
    )


def _load_weekly_schedule(raw: StructuredValue, name: str) -> WeeklyScheduleConfig:
    weekly = _expect_mapping(raw, name)
    schedule = _load_schedule(weekly, name)
    weekday = _expect_string(weekly["weekday"], f"{name}.weekday").upper()
    if weekday not in {"MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"}:
        raise ValueError(f"{name}.weekday must be a three-letter weekday code")
    return WeeklyScheduleConfig(
        timezone=schedule.timezone,
        hour=schedule.hour,
        minute=schedule.minute,
        weekday=weekday,
    )


def _coerce_session_colors(value: StructuredValue) -> dict[str, DayColor]:
    session_mapping = _expect_mapping(value, "rule.session_colors")
    expected_sessions = {session.value for session in SessionName}
    provided_sessions = set(session_mapping)

    unknown_sessions = sorted(provided_sessions - expected_sessions)
    if unknown_sessions:
        raise ValueError(
            "rule.session_colors contains unsupported session names: "
            f"{', '.join(unknown_sessions)}"
        )

    missing_sessions = sorted(expected_sessions - provided_sessions)
    if missing_sessions:
        raise ValueError(
            "rule.session_colors is missing required session names: "
            f"{', '.join(missing_sessions)}"
        )

    return {
        session.value: DayColor(
            _expect_string(
                session_mapping[session.value], f"rule.session_colors.{session.value}"
            )
        )
        for session in SessionName
    }


def _expect_mapping(value: StructuredValue, name: str) -> StructuredMapping:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a mapping")
    return {
        _expect_string(key, f"{name} key"): item
        for key, item in value.items()
    }


def _coerce_rule_conditions(value: StructuredValue) -> RuleConditions:
    mapping = _expect_mapping(value, "rule.when")
    return {
        key: _coerce_rule_condition_value(item, key)
        for key, item in mapping.items()
    }


def _coerce_rule_condition_value(value: StructuredValue, name: str) -> RuleConditionValue:
    if isinstance(value, Mapping):
        return _coerce_rule_operator_map(_expect_mapping(value, name), name)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return _expect_string_list(value, name)
    return _coerce_rule_scalar(value, name)


def _coerce_rule_operator_map(
    value: Mapping[str, StructuredValue], name: str
) -> RuleOperatorMap:
    operator_map: RuleOperatorMap = {}
    for operator, item in value.items():
        if operator == "equals":
            operator_map["equals"] = _coerce_rule_scalar(item, f"{name}.equals")
            continue
        if operator == "gte":
            operator_map["gte"] = _coerce_int(item, f"{name}.gte")
            continue
        if operator == "lte":
            operator_map["lte"] = _coerce_int(item, f"{name}.lte")
            continue
        if operator == "contains":
            operator_map["contains"] = _expect_string(item, f"{name}.contains")
            continue
        raise ValueError(f"Unsupported rule operator: {operator}")
    return operator_map


def _coerce_rule_scalar(value: StructuredValue, name: str) -> str | int | bool | None:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise ValueError(f"{name} must be a string, int, bool, or null")


def _expect_string(value: StructuredValue, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    return value


def _expect_string_list(value: StructuredValue, name: str) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"{name} must be a list of strings")
    return [_expect_string(item, name) for item in value]


def _coerce_int(value: StructuredValue, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError(f"{name} must be an int")
    try:
        return int(value)
    except ValueError as error:
        raise ValueError(f"{name} must be an int") from error


def _coerce_float(value: StructuredValue, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f"{name} must be a float")
    try:
        return float(value)
    except ValueError as error:
        raise ValueError(f"{name} must be a float") from error
