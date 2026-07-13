from __future__ import annotations

from pathlib import Path

import pytest

from bot.config import (
    ConfigError,
    find_rule_priority_collisions,
    load_app_config,
    load_event_aliases,
    load_messages,
    load_rules,
)

_VALID_BASE = """
delivery:
  webhook_url_env: DISCORD_WEBHOOK_URL

runtime:
  state_path: data/state.json

schedules:
  morning_summary:
    timezone: Asia/Tbilisi
    hour: 9
    minute: 0
  ny_recheck:
    timezone: America/New_York
    hour: 8
    minute: 0
  weekly_outlook:
    timezone: Asia/Tbilisi
    weekday: SUN
    hour: 21
    minute: 0

calendar:
  urls:
    - https://nfs.faireconomy.media/ff_calendar_thisweek.json
  timeout_seconds: 10
  market_timezone: America/New_York
"""


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "app.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_load_app_config_accepts_minimal_valid_file(tmp_path):
    config = load_app_config(_write(tmp_path, _VALID_BASE))
    assert config.delivery.timeout_seconds == 10
    assert config.delivery.max_retries == 3
    assert config.delivery.retry_backoff_seconds == 1
    assert config.calendar.market_timezone == "America/New_York"
    assert config.policy.post_on_weekend is True
    assert config.policy.calendar_cache_max_age_hours == 72
    assert "London" in config.windows.session_windows


def test_load_app_config_accepts_explicit_delivery_retry_settings(tmp_path):
    body = _VALID_BASE.replace(
        "  webhook_url_env: DISCORD_WEBHOOK_URL",
        "  webhook_url_env: DISCORD_WEBHOOK_URL\n"
        "  timeout_seconds: 12.5\n"
        "  max_retries: 5\n"
        "  retry_backoff_seconds: 0.25",
    )

    config = load_app_config(_write(tmp_path, body))

    assert config.delivery.timeout_seconds == 12.5
    assert config.delivery.max_retries == 5
    assert config.delivery.retry_backoff_seconds == 0.25


@pytest.mark.parametrize(
    ("setting", "message"),
    [
        ("timeout_seconds: 0", "delivery.timeout_seconds must be between"),
        ("timeout_seconds: 61", "delivery.timeout_seconds must be between"),
        ("max_retries: -1", "delivery.max_retries must be between"),
        ("max_retries: 6", "delivery.max_retries must be between"),
        (
            "retry_backoff_seconds: -0.1",
            "delivery.retry_backoff_seconds must be between",
        ),
        (
            "retry_backoff_seconds: 11",
            "delivery.retry_backoff_seconds must be between",
        ),
    ],
)
def test_load_app_config_rejects_invalid_delivery_retry_settings(tmp_path, setting, message):
    body = _VALID_BASE.replace(
        "  webhook_url_env: DISCORD_WEBHOOK_URL",
        f"  webhook_url_env: DISCORD_WEBHOOK_URL\n  {setting}",
    )

    with pytest.raises(ConfigError, match=message):
        load_app_config(_write(tmp_path, body))


@pytest.mark.parametrize("value", [".nan", ".inf", "-.inf"])
@pytest.mark.parametrize(
    "setting",
    ["delivery.timeout_seconds", "delivery.retry_backoff_seconds"],
)
def test_load_app_config_rejects_nonfinite_delivery_values(tmp_path, value, setting):
    key = setting.rsplit(".", maxsplit=1)[1]
    body = _VALID_BASE.replace(
        "  webhook_url_env: DISCORD_WEBHOOK_URL",
        f"  webhook_url_env: DISCORD_WEBHOOK_URL\n  {key}: {value}",
    )

    with pytest.raises(ConfigError, match=f"{setting} must be finite"):
        load_app_config(_write(tmp_path, body))


@pytest.mark.parametrize("value", [".nan", ".inf", "-.inf"])
def test_load_app_config_rejects_nonfinite_calendar_values(tmp_path, value):
    body = _VALID_BASE.replace("timeout_seconds: 10", f"timeout_seconds: {value}")

    with pytest.raises(ConfigError, match="calendar.timeout_seconds must be finite"):
        load_app_config(_write(tmp_path, body))


def test_load_app_config_rejects_invalid_timezone(tmp_path):
    body = _VALID_BASE.replace("timezone: Asia/Tbilisi", "timezone: Mars/Olympus_Mons", 1)
    with pytest.raises(ConfigError, match="not a valid timezone"):
        load_app_config(_write(tmp_path, body))


def test_load_app_config_rejects_unsupported_weekly_weekday(tmp_path):
    body = _VALID_BASE.replace("weekday: SUN", "weekday: SAT")

    with pytest.raises(ConfigError, match="weekday must be SUN"):
        load_app_config(_write(tmp_path, body))


def test_load_app_config_requires_weekly_and_morning_timezone_to_match(tmp_path):
    body = _VALID_BASE.replace(
        "weekly_outlook:\n    timezone: Asia/Tbilisi",
        "weekly_outlook:\n    timezone: UTC",
    )

    with pytest.raises(ConfigError, match="must match morning_summary.timezone"):
        load_app_config(_write(tmp_path, body))


def test_load_app_config_rejects_empty_calendar_urls(tmp_path):
    body = _VALID_BASE.replace(
        "  urls:\n    - https://nfs.faireconomy.media/ff_calendar_thisweek.json",
        "  urls: []",
    )
    with pytest.raises(ConfigError, match="urls must contain at least one"):
        load_app_config(_write(tmp_path, body))


def test_load_app_config_rejects_invalid_calendar_url(tmp_path):
    body = _VALID_BASE.replace(
        "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
        "not-a-url",
    )

    with pytest.raises(ConfigError, match=r"valid HTTP\(S\) URLs"):
        load_app_config(_write(tmp_path, body))


def test_load_app_config_reports_missing_required_key(tmp_path):
    body = _VALID_BASE.replace("  market_timezone: America/New_York\n", "")

    with pytest.raises(ConfigError, match="missing required keys: market_timezone"):
        load_app_config(_write(tmp_path, body))


def test_load_app_config_rejects_zero_timeout(tmp_path):
    body = _VALID_BASE.replace("timeout_seconds: 10", "timeout_seconds: 0")
    with pytest.raises(ConfigError, match="timeout_seconds must be between"):
        load_app_config(_write(tmp_path, body))


def test_load_app_config_rejects_unknown_top_key(tmp_path):
    body = _VALID_BASE + "\nunknown_top_key: 1\n"
    with pytest.raises(ConfigError, match="unsupported keys"):
        load_app_config(_write(tmp_path, body))


def test_load_app_config_rejects_unknown_window_key(tmp_path):
    body = _VALID_BASE + "\nwindows:\n  bogus: 1\n"
    with pytest.raises(ConfigError, match="windows contains unsupported keys"):
        load_app_config(_write(tmp_path, body))


def test_load_app_config_rejects_invalid_session_window(tmp_path):
    body = (
        _VALID_BASE
        + """
windows:
  session_windows:
    London:
      start: "07:00"
      end: "03:00"
    PreMarket:
      start: "07:00"
      end: "08:30"
    NY_AM_FIRST_HALF:
      start: "08:30"
      end: "10:30"
    NY_AM_SECOND_HALF:
      start: "10:30"
      end: "12:00"
    Lunch:
      start: "12:00"
      end: "13:30"
    NY_PM_FIRST_HALF:
      start: "13:30"
      end: "15:00"
    NY_PM_SECOND_HALF:
      start: "15:00"
      end: "17:00"
"""
    )
    with pytest.raises(ConfigError, match="start must be before end"):
        load_app_config(_write(tmp_path, body))


def test_load_rules_rejects_unknown_feature_key(tmp_path):
    rules_path = tmp_path / "rules.yaml"
    rules_path.write_text(
        """
- id: bogus_feature
  priority: 10
  when:
    not_a_real_feature: true
  day_color: RED
  tradable: no_trade
  reason: bogus
  session_colors:
    London: RED
    PreMarket: RED
    NY_AM_FIRST_HALF: RED
    NY_AM_SECOND_HALF: RED
    Lunch: RED
    NY_PM_FIRST_HALF: RED
    NY_PM_SECOND_HALF: RED
- id: fallback
  priority: 0
  when: {}
  day_color: GREEN
  tradable: trade
  reason: fallback
  session_colors:
    London: GREEN
    PreMarket: GREEN
    NY_AM_FIRST_HALF: GREEN
    NY_AM_SECOND_HALF: GREEN
    Lunch: GREEN
    NY_PM_FIRST_HALF: GREEN
    NY_PM_SECOND_HALF: GREEN
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="unknown feature"):
        load_rules(rules_path)


def test_load_rules_rejects_unknown_rule_key(tmp_path):
    rules_path = tmp_path / "rules.yaml"
    rules_path.write_text(
        """
- id: fallback
  priority: 0
  when: {}
  day_color: GREEN
  tradable: trade
  reason: fallback
  typo: ignored-before
  session_colors:
    London: GREEN
    PreMarket: GREEN
    NY_AM_FIRST_HALF: GREEN
    NY_AM_SECOND_HALF: GREEN
    Lunch: GREEN
    NY_PM_FIRST_HALF: GREEN
    NY_PM_SECOND_HALF: GREEN
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="unsupported keys: typo"):
        load_rules(rules_path)


def test_find_rule_priority_collisions_flags_overlapping_rules():
    repo_root = Path(__file__).resolve().parent.parent
    rules = load_rules(repo_root / "config" / "rules.yaml")
    collisions = find_rule_priority_collisions(rules)
    assert collisions == []


def test_load_messages_returns_defaults_when_missing(tmp_path):
    messages = load_messages(tmp_path / "missing.yaml")
    assert "Wait 15-30 minutes" in messages.strong_news_advice
    assert "cached" in messages.calendar_cached_note.casefold()


def test_load_messages_rejects_unknown_keys(tmp_path):
    path = tmp_path / "messages.yaml"
    path.write_text("quiet_day_typo: ignored-before\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="unsupported keys"):
        load_messages(path)


def test_load_event_aliases_accepts_title_excludes(tmp_path):
    path = tmp_path / "event_aliases.yaml"
    path.write_text(
        """
strongest_categories:
  - NFP
title_aliases:
  NFP:
    - non-farm employment change
title_excludes:
  NFP:
    - adp
""",
        encoding="utf-8",
    )

    aliases = load_event_aliases(path)

    assert aliases.title_aliases["NFP"] == ("non-farm employment change",)
    assert aliases.title_excludes["NFP"] == ("adp",)
