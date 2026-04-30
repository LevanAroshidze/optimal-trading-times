from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from bot.models import (
    DayColor,
    RuleConditions,
    RuleDefinition,
    SessionName,
    TradableStatus,
)
from bot.type_defs import (
    KNOWN_FEATURE_KEYS,
    RuleConditionValue,
    RuleOperatorMap,
    StructuredMapping,
    StructuredValue,
)


class ConfigError(ValueError):
    """Raised when a YAML config file fails validation."""


@dataclass(frozen=True)
class DeliveryConfig:
    webhook_url_env: str
    ops_webhook_url_env: str | None = None


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
    lock_path: str | None = None


@dataclass(frozen=True)
class CalendarConfig:
    urls: list[str]
    timeout_seconds: float
    market_timezone: str
    user_agent: str = "optimal-trade-sessions-bot/0.1 (+https://github.com)"
    max_retries: int = 3
    retry_backoff_seconds: float = 1.0


@dataclass(frozen=True)
class SessionWindowConfig:
    start: time
    end: time


@dataclass(frozen=True)
class WindowsConfig:
    session_windows: dict[str, SessionWindowConfig]
    pre_release_pressure_minutes: int
    pm_news_after_hour: int
    am_red_news_at: time
    powell_speech_at: time


@dataclass(frozen=True)
class PolicyConfig:
    max_post_age_minutes: int = 240
    post_on_weekend: bool = True
    max_consecutive_source_failures: int = 3
    duplicate_post_warning: bool = True


@dataclass(frozen=True)
class AppConfig:
    delivery: DeliveryConfig
    runtime: RuntimeConfig
    morning_summary: ScheduleConfig
    ny_recheck: ScheduleConfig
    weekly_outlook: WeeklyScheduleConfig
    calendar: CalendarConfig
    windows: WindowsConfig
    policy: PolicyConfig


@dataclass(frozen=True)
class EventAliasConfig:
    title_aliases: dict[str, tuple[str, ...]]
    strongest_categories: tuple[str, ...] = ()
    partial_holiday_terms: tuple[str, ...] = ()


@dataclass(frozen=True)
class MessagesConfig:
    """Trader-editable copy used by the formatter."""

    strong_news_advice: str = (
        "Wait 15-30 minutes after the release before looking for trades."
    )
    other_red_news_advice: str = "Avoid the 2-3 hours before the release."
    calendar_unavailable_note: str = (
        "Calendar source unavailable. Posting the fail-safe plan."
    )
    update_header: str = "Update: the calendar changed after the morning post."
    week_ahead_header: str = "Week Ahead"
    quiet_day_note: str = (
        "No major USD news. NY AM may be slower and lower-volatility than usual."
    )
    day_color_labels: dict[str, str] = field(
        default_factory=lambda: {"GREEN": "Green", "WHITE": "White", "RED": "Red"}
    )


_VALID_WEEKDAYS = {"MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"}
_DEFAULT_SESSION_WINDOWS: dict[str, tuple[time, time]] = {
    SessionName.LONDON.value: (time(3, 0), time(7, 0)),
    SessionName.PREMARKET.value: (time(7, 0), time(8, 30)),
    SessionName.NY_AM_FIRST_HALF.value: (time(8, 30), time(10, 30)),
    SessionName.NY_AM_SECOND_HALF.value: (time(10, 30), time(12, 0)),
    SessionName.LUNCH.value: (time(12, 0), time(13, 30)),
    SessionName.NY_PM_FIRST_HALF.value: (time(13, 30), time(15, 0)),
    SessionName.NY_PM_SECOND_HALF.value: (time(15, 0), time(17, 0)),
}
_ALLOWED_APP_KEYS = frozenset(
    {"delivery", "runtime", "schedules", "calendar", "windows", "policy"}
)
_ALLOWED_DELIVERY_KEYS = frozenset({"webhook_url_env", "ops_webhook_url_env"})
_ALLOWED_RUNTIME_KEYS = frozenset({"state_path", "lock_path"})
_ALLOWED_SCHEDULE_KEYS = frozenset({"timezone", "hour", "minute"})
_ALLOWED_WEEKLY_KEYS = _ALLOWED_SCHEDULE_KEYS | {"weekday"}
_ALLOWED_SCHEDULES_KEYS = frozenset(
    {"morning_summary", "ny_recheck", "weekly_outlook"}
)
_ALLOWED_CALENDAR_KEYS = frozenset(
    {
        "urls",
        "timeout_seconds",
        "market_timezone",
        "user_agent",
        "max_retries",
        "retry_backoff_seconds",
    }
)
_ALLOWED_WINDOWS_KEYS = frozenset(
    {
        "session_windows",
        "pre_release_pressure_minutes",
        "pm_news_after_hour",
        "am_red_news_at",
        "powell_speech_at",
    }
)
_ALLOWED_POLICY_KEYS = frozenset(
    {
        "max_post_age_minutes",
        "post_on_weekend",
        "max_consecutive_source_failures",
        "duplicate_post_warning",
    }
)
_ALLOWED_RULE_OPERATORS = frozenset(
    {
        "equals",
        "not_equals",
        "gt",
        "gte",
        "lt",
        "lte",
        "between",
        "in",
        "contains",
        "not_contains",
        "any_of",
        "all_of",
    }
)


def _load_yaml(path: Path) -> StructuredValue:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _reject_unknown_keys(
    mapping: StructuredMapping, allowed: frozenset[str], name: str
) -> None:
    unknown = sorted(set(mapping) - allowed)
    if unknown:
        raise ConfigError(
            f"{name} contains unsupported keys: {', '.join(unknown)}"
        )


def _validate_timezone(tz_name: str, name: str) -> str:
    try:
        ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ConfigError(f"{name} is not a valid timezone: {tz_name!r}") from error
    return tz_name


def load_app_config(path: Path) -> AppConfig:
    raw = _expect_mapping(_load_yaml(path), path.name)
    _reject_unknown_keys(raw, _ALLOWED_APP_KEYS, path.name)

    delivery_map = _expect_mapping(raw["delivery"], "delivery")
    _reject_unknown_keys(delivery_map, _ALLOWED_DELIVERY_KEYS, "delivery")
    webhook_env = _expect_string(
        delivery_map["webhook_url_env"], "delivery.webhook_url_env"
    )
    if not webhook_env.strip():
        raise ConfigError("delivery.webhook_url_env must be non-empty")
    ops_env_raw = delivery_map.get("ops_webhook_url_env")
    ops_env = (
        _expect_string(ops_env_raw, "delivery.ops_webhook_url_env")
        if ops_env_raw is not None
        else None
    )

    runtime_map = _expect_mapping(raw["runtime"], "runtime")
    _reject_unknown_keys(runtime_map, _ALLOWED_RUNTIME_KEYS, "runtime")
    state_path = _expect_string(runtime_map["state_path"], "runtime.state_path")
    if not state_path.strip():
        raise ConfigError("runtime.state_path must be non-empty")
    lock_path_raw = runtime_map.get("lock_path")
    lock_path = (
        _expect_string(lock_path_raw, "runtime.lock_path")
        if lock_path_raw is not None
        else None
    )

    schedules_map = _expect_mapping(raw["schedules"], "schedules")
    _reject_unknown_keys(schedules_map, _ALLOWED_SCHEDULES_KEYS, "schedules")
    morning_summary = _load_schedule(
        schedules_map["morning_summary"], "morning_summary"
    )
    ny_recheck = _load_schedule(schedules_map["ny_recheck"], "ny_recheck")
    weekly_outlook = _load_weekly_schedule(
        schedules_map["weekly_outlook"], "weekly_outlook"
    )

    calendar_map = _expect_mapping(raw["calendar"], "calendar")
    _reject_unknown_keys(calendar_map, _ALLOWED_CALENDAR_KEYS, "calendar")
    urls = _expect_string_list(calendar_map["urls"], "calendar.urls")
    if not urls:
        raise ConfigError("calendar.urls must contain at least one URL")
    timeout_seconds = _coerce_float(
        calendar_map["timeout_seconds"], "calendar.timeout_seconds"
    )
    if timeout_seconds <= 0:
        raise ConfigError("calendar.timeout_seconds must be positive")
    market_tz = _validate_timezone(
        _expect_string(calendar_map["market_timezone"], "calendar.market_timezone"),
        "calendar.market_timezone",
    )
    user_agent = _expect_string(
        calendar_map.get("user_agent", CalendarConfig.user_agent),
        "calendar.user_agent",
    )
    max_retries_raw = calendar_map.get("max_retries", CalendarConfig.max_retries)
    max_retries = _coerce_int(max_retries_raw, "calendar.max_retries")
    if max_retries < 1:
        raise ConfigError("calendar.max_retries must be at least 1")
    retry_backoff = _coerce_float(
        calendar_map.get("retry_backoff_seconds", CalendarConfig.retry_backoff_seconds),
        "calendar.retry_backoff_seconds",
    )
    if retry_backoff < 0:
        raise ConfigError("calendar.retry_backoff_seconds must be non-negative")

    windows = _load_windows_config(raw.get("windows"))
    policy = _load_policy_config(raw.get("policy"))

    return AppConfig(
        delivery=DeliveryConfig(
            webhook_url_env=webhook_env,
            ops_webhook_url_env=ops_env,
        ),
        runtime=RuntimeConfig(state_path=state_path, lock_path=lock_path),
        morning_summary=morning_summary,
        ny_recheck=ny_recheck,
        weekly_outlook=weekly_outlook,
        calendar=CalendarConfig(
            urls=urls,
            timeout_seconds=timeout_seconds,
            market_timezone=market_tz,
            user_agent=user_agent,
            max_retries=max_retries,
            retry_backoff_seconds=retry_backoff,
        ),
        windows=windows,
        policy=policy,
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
            _expect_string_list(
                raw.get("strongest_categories", []), "strongest_categories"
            )
        ),
        partial_holiday_terms=tuple(
            item.casefold()
            for item in _expect_string_list(
                raw.get("partial_holiday_terms", []), "partial_holiday_terms"
            )
        ),
    )


def load_messages(path: Path | None) -> MessagesConfig:
    if path is None or not path.exists():
        return MessagesConfig()
    raw = _expect_mapping(_load_yaml(path), path.name)
    defaults = MessagesConfig()
    return MessagesConfig(
        strong_news_advice=_expect_string(
            raw.get("strong_news_advice", defaults.strong_news_advice),
            "messages.strong_news_advice",
        ),
        other_red_news_advice=_expect_string(
            raw.get("other_red_news_advice", defaults.other_red_news_advice),
            "messages.other_red_news_advice",
        ),
        calendar_unavailable_note=_expect_string(
            raw.get(
                "calendar_unavailable_note", defaults.calendar_unavailable_note
            ),
            "messages.calendar_unavailable_note",
        ),
        update_header=_expect_string(
            raw.get("update_header", defaults.update_header), "messages.update_header"
        ),
        week_ahead_header=_expect_string(
            raw.get("week_ahead_header", defaults.week_ahead_header),
            "messages.week_ahead_header",
        ),
        quiet_day_note=_expect_string(
            raw.get("quiet_day_note", defaults.quiet_day_note),
            "messages.quiet_day_note",
        ),
        day_color_labels=_load_day_color_labels(
            raw.get("day_color_labels"), defaults.day_color_labels
        ),
    )


def load_rules(path: Path) -> list[RuleDefinition]:
    raw_rules = _load_yaml(path)
    if not isinstance(raw_rules, list) or not raw_rules:
        raise ConfigError("rules.yaml must contain a non-empty list of rules")

    rules: list[RuleDefinition] = []
    seen_ids: set[str] = set()
    for raw_item in raw_rules:
        item = _expect_mapping(raw_item, "rule")
        rule_id = _expect_string(item["id"], "rule.id")
        if rule_id in seen_ids:
            raise ConfigError(f"Duplicate rule id: {rule_id}")
        seen_ids.add(rule_id)
        rules.append(
            RuleDefinition(
                id=rule_id,
                priority=_coerce_int(item["priority"], "rule.priority"),
                when=_coerce_rule_conditions(item.get("when", {}), rule_id),
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
        raise ConfigError(
            "rules.yaml must include a fallback rule with an empty 'when' block"
        )
    return ordered_rules


def find_rule_priority_collisions(
    rules: list[RuleDefinition],
) -> list[tuple[str, str]]:
    """Return pairs of rule ids sharing priority where the smaller rule
    is a subset of the larger rule's conditions."""

    collisions: list[tuple[str, str]] = []
    for index, left in enumerate(rules):
        for right in rules[index + 1 :]:
            if left.priority != right.priority:
                continue
            if not left.when or not right.when:
                continue
            left_keys = set(left.when)
            right_keys = set(right.when)
            if left_keys.isdisjoint(right_keys):
                continue
            collisions.append((left.id, right.id))
    return collisions


def _load_schedule(raw: StructuredValue, name: str) -> ScheduleConfig:
    schedule = _expect_mapping(raw, name)
    _reject_unknown_keys(schedule, _ALLOWED_SCHEDULE_KEYS, name)
    hour = _coerce_int(schedule["hour"], f"{name}.hour")
    minute = _coerce_int(schedule["minute"], f"{name}.minute")
    if not 0 <= hour <= 23:
        raise ConfigError(f"{name}.hour must be between 0 and 23")
    if not 0 <= minute <= 59:
        raise ConfigError(f"{name}.minute must be between 0 and 59")
    timezone = _validate_timezone(
        _expect_string(schedule["timezone"], f"{name}.timezone"),
        f"{name}.timezone",
    )
    return ScheduleConfig(timezone=timezone, hour=hour, minute=minute)


def _load_weekly_schedule(raw: StructuredValue, name: str) -> WeeklyScheduleConfig:
    weekly = _expect_mapping(raw, name)
    _reject_unknown_keys(weekly, _ALLOWED_WEEKLY_KEYS, name)
    schedule = _load_schedule({k: v for k, v in weekly.items() if k != "weekday"}, name)
    weekday = _expect_string(weekly["weekday"], f"{name}.weekday").upper()
    if weekday not in _VALID_WEEKDAYS:
        raise ConfigError(f"{name}.weekday must be a three-letter weekday code")
    return WeeklyScheduleConfig(
        timezone=schedule.timezone,
        hour=schedule.hour,
        minute=schedule.minute,
        weekday=weekday,
    )


def _load_windows_config(raw: StructuredValue | None) -> WindowsConfig:
    if raw is None:
        session_windows = {
            session: SessionWindowConfig(start=start, end=end)
            for session, (start, end) in _DEFAULT_SESSION_WINDOWS.items()
        }
        return WindowsConfig(
            session_windows=session_windows,
            pre_release_pressure_minutes=180,
            pm_news_after_hour=14,
            am_red_news_at=time(8, 30),
            powell_speech_at=time(10, 0),
        )

    mapping = _expect_mapping(raw, "windows")
    _reject_unknown_keys(mapping, _ALLOWED_WINDOWS_KEYS, "windows")

    session_windows_raw = mapping.get("session_windows")
    if session_windows_raw is None:
        session_windows = {
            session: SessionWindowConfig(start=start, end=end)
            for session, (start, end) in _DEFAULT_SESSION_WINDOWS.items()
        }
    else:
        session_windows = _coerce_session_windows(session_windows_raw)

    pre_release = _coerce_int(
        mapping.get("pre_release_pressure_minutes", 180),
        "windows.pre_release_pressure_minutes",
    )
    if pre_release <= 0:
        raise ConfigError("windows.pre_release_pressure_minutes must be positive")

    pm_after = _coerce_int(
        mapping.get("pm_news_after_hour", 14), "windows.pm_news_after_hour"
    )
    if not 0 <= pm_after <= 23:
        raise ConfigError("windows.pm_news_after_hour must be between 0 and 23")

    am_red = _coerce_time(
        mapping.get("am_red_news_at", "08:30"), "windows.am_red_news_at"
    )
    powell = _coerce_time(
        mapping.get("powell_speech_at", "10:00"), "windows.powell_speech_at"
    )

    return WindowsConfig(
        session_windows=session_windows,
        pre_release_pressure_minutes=pre_release,
        pm_news_after_hour=pm_after,
        am_red_news_at=am_red,
        powell_speech_at=powell,
    )


def _coerce_session_windows(value: StructuredValue) -> dict[str, SessionWindowConfig]:
    mapping = _expect_mapping(value, "windows.session_windows")
    expected = {session.value for session in SessionName}
    unknown = sorted(set(mapping) - expected)
    if unknown:
        raise ConfigError(
            "windows.session_windows contains unsupported sessions: "
            f"{', '.join(unknown)}"
        )
    missing = sorted(expected - set(mapping))
    if missing:
        raise ConfigError(
            "windows.session_windows is missing sessions: " f"{', '.join(missing)}"
        )

    result: dict[str, SessionWindowConfig] = {}
    for session, raw_window in mapping.items():
        window = _expect_mapping(raw_window, f"windows.session_windows.{session}")
        _reject_unknown_keys(
            window, frozenset({"start", "end"}), f"windows.session_windows.{session}"
        )
        start = _coerce_time(
            window["start"], f"windows.session_windows.{session}.start"
        )
        end = _coerce_time(window["end"], f"windows.session_windows.{session}.end")
        if start >= end:
            raise ConfigError(
                f"windows.session_windows.{session}: start must be before end"
            )
        result[session] = SessionWindowConfig(start=start, end=end)
    return result


def _load_policy_config(raw: StructuredValue | None) -> PolicyConfig:
    if raw is None:
        return PolicyConfig()
    mapping = _expect_mapping(raw, "policy")
    _reject_unknown_keys(mapping, _ALLOWED_POLICY_KEYS, "policy")

    defaults = PolicyConfig()
    max_age = _coerce_int(
        mapping.get("max_post_age_minutes", defaults.max_post_age_minutes),
        "policy.max_post_age_minutes",
    )
    if max_age <= 0:
        raise ConfigError("policy.max_post_age_minutes must be positive")
    post_weekend_raw = mapping.get("post_on_weekend", defaults.post_on_weekend)
    if not isinstance(post_weekend_raw, bool):
        raise ConfigError("policy.post_on_weekend must be a boolean")
    failures = _coerce_int(
        mapping.get(
            "max_consecutive_source_failures",
            defaults.max_consecutive_source_failures,
        ),
        "policy.max_consecutive_source_failures",
    )
    if failures <= 0:
        raise ConfigError("policy.max_consecutive_source_failures must be positive")
    duplicate_warning_raw = mapping.get(
        "duplicate_post_warning", defaults.duplicate_post_warning
    )
    if not isinstance(duplicate_warning_raw, bool):
        raise ConfigError("policy.duplicate_post_warning must be a boolean")
    return PolicyConfig(
        max_post_age_minutes=max_age,
        post_on_weekend=post_weekend_raw,
        max_consecutive_source_failures=failures,
        duplicate_post_warning=duplicate_warning_raw,
    )


def _load_day_color_labels(
    value: StructuredValue | None, fallback: dict[str, str]
) -> dict[str, str]:
    if value is None:
        return dict(fallback)
    mapping = _expect_mapping(value, "messages.day_color_labels")
    expected = {color.value for color in DayColor}
    unknown = sorted(set(mapping) - expected)
    if unknown:
        raise ConfigError(
            "messages.day_color_labels contains unsupported colors: "
            f"{', '.join(unknown)}"
        )
    labels = dict(fallback)
    for color, raw_label in mapping.items():
        labels[color] = _expect_string(
            raw_label, f"messages.day_color_labels.{color}"
        )
    return labels


def _coerce_session_colors(value: StructuredValue) -> dict[str, DayColor]:
    session_mapping = _expect_mapping(value, "rule.session_colors")
    expected_sessions = {session.value for session in SessionName}
    provided_sessions = set(session_mapping)

    unknown_sessions = sorted(provided_sessions - expected_sessions)
    if unknown_sessions:
        raise ConfigError(
            "rule.session_colors contains unsupported session names: "
            f"{', '.join(unknown_sessions)}"
        )

    missing_sessions = sorted(expected_sessions - provided_sessions)
    if missing_sessions:
        raise ConfigError(
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
        raise ConfigError(f"{name} must be a mapping")
    return {
        _expect_string(key, f"{name} key"): item
        for key, item in value.items()
    }


def _coerce_rule_conditions(
    value: StructuredValue, rule_id: str
) -> RuleConditions:
    mapping = _expect_mapping(value, f"rule[{rule_id}].when")
    conditions: RuleConditions = {}
    for key, item in mapping.items():
        if key not in KNOWN_FEATURE_KEYS:
            raise ConfigError(
                f"rule[{rule_id}].when references unknown feature: {key!r}. "
                f"Known features: {sorted(KNOWN_FEATURE_KEYS)}"
            )
        conditions[key] = _coerce_rule_condition_value(item, f"rule[{rule_id}].when.{key}")
    return conditions


def _coerce_rule_condition_value(value: StructuredValue, name: str) -> RuleConditionValue:
    if isinstance(value, Mapping):
        return _coerce_rule_operator_map(_expect_mapping(value, name), name)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return _expect_string_list(value, name)
    return _coerce_rule_scalar(value, name)


def _coerce_rule_operator_map(
    value: Mapping[str, StructuredValue], name: str
) -> RuleOperatorMap:
    unknown = sorted(set(value) - _ALLOWED_RULE_OPERATORS)
    if unknown:
        raise ConfigError(
            f"Unsupported rule operator(s) in {name}: {', '.join(unknown)}"
        )

    operator_map: RuleOperatorMap = {}
    for operator, item in value.items():
        if operator == "equals":
            operator_map["equals"] = _coerce_rule_scalar(item, f"{name}.equals")
        elif operator == "not_equals":
            operator_map["not_equals"] = _coerce_rule_scalar(
                item, f"{name}.not_equals"
            )
        elif operator == "gt":
            operator_map["gt"] = _coerce_int(item, f"{name}.gt")
        elif operator == "gte":
            operator_map["gte"] = _coerce_int(item, f"{name}.gte")
        elif operator == "lt":
            operator_map["lt"] = _coerce_int(item, f"{name}.lt")
        elif operator == "lte":
            operator_map["lte"] = _coerce_int(item, f"{name}.lte")
        elif operator == "between":
            bounds = _expect_list(item, f"{name}.between")
            if len(bounds) != 2:
                raise ConfigError(
                    f"{name}.between must be a 2-element list [min, max]"
                )
            low = _coerce_int(bounds[0], f"{name}.between[0]")
            high = _coerce_int(bounds[1], f"{name}.between[1]")
            if low > high:
                raise ConfigError(f"{name}.between[0] must be <= between[1]")
            operator_map["between"] = [low, high]
        elif operator == "in":
            members = _expect_list(item, f"{name}.in")
            operator_map["in"] = [
                _coerce_rule_scalar(member, f"{name}.in[{index}]")
                for index, member in enumerate(members)
            ]
        elif operator == "contains":
            operator_map["contains"] = _expect_string(item, f"{name}.contains")
        elif operator == "not_contains":
            operator_map["not_contains"] = _expect_string(
                item, f"{name}.not_contains"
            )
        elif operator in ("any_of", "all_of"):
            members = _expect_list(item, f"{name}.{operator}")
            operator_map[operator] = [  # type: ignore[literal-required]
                _coerce_rule_operator_map(
                    _expect_mapping(member, f"{name}.{operator}[{index}]"),
                    f"{name}.{operator}[{index}]",
                )
                for index, member in enumerate(members)
            ]

    if "equals" in operator_map and len(operator_map) > 1:
        raise ConfigError(f"{name}: 'equals' cannot be combined with other operators.")
    if "not_equals" in operator_map and len(operator_map) > 1:
        raise ConfigError(
            f"{name}: 'not_equals' cannot be combined with other operators."
        )
    if "contains" in operator_map and len(operator_map) > 1:
        raise ConfigError(
            f"{name}: 'contains' cannot be combined with other operators."
        )
    if "not_contains" in operator_map and len(operator_map) > 1:
        raise ConfigError(
            f"{name}: 'not_contains' cannot be combined with other operators."
        )
    if not operator_map:
        raise ConfigError(f"{name}: operator block cannot be empty")
    return operator_map


def _coerce_rule_scalar(value: StructuredValue, name: str) -> str | int | bool | None:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise ConfigError(f"{name} must be a string, int, bool, or null")


def _expect_string(value: StructuredValue, name: str) -> str:
    if not isinstance(value, str):
        raise ConfigError(f"{name} must be a string")
    return value


def _expect_string_list(value: StructuredValue, name: str) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ConfigError(f"{name} must be a list of strings")
    return [_expect_string(item, name) for item in value]


def _expect_list(value: StructuredValue, name: str) -> list[StructuredValue]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ConfigError(f"{name} must be a list")
    return list(value)


def _coerce_int(value: StructuredValue, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ConfigError(f"{name} must be an int")
    try:
        return int(value)
    except ValueError as error:
        raise ConfigError(f"{name} must be an int") from error


def _coerce_float(value: StructuredValue, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ConfigError(f"{name} must be a float")
    try:
        return float(value)
    except ValueError as error:
        raise ConfigError(f"{name} must be a float") from error


def _coerce_time(value: StructuredValue, name: str) -> time:
    if isinstance(value, time):
        return value
    if not isinstance(value, str):
        raise ConfigError(f"{name} must be an HH:MM string")
    parts = value.split(":")
    if len(parts) != 2:
        raise ConfigError(f"{name} must look like 'HH:MM', got {value!r}")
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError as error:
        raise ConfigError(f"{name} must look like 'HH:MM', got {value!r}") from error
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ConfigError(f"{name} must be in 00:00 - 23:59, got {value!r}")
    return time(hour, minute)
