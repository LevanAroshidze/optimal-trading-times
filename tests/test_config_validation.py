from __future__ import annotations

from pathlib import Path

import pytest

from bot.config import (
    ConfigError,
    find_rule_priority_collisions,
    load_app_config,
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
    assert config.calendar.market_timezone == "America/New_York"
    assert config.policy.post_on_weekend is True
    assert "London" in config.windows.session_windows


def test_load_app_config_rejects_invalid_timezone(tmp_path):
    body = _VALID_BASE.replace(
        "timezone: Asia/Tbilisi", "timezone: Mars/Olympus_Mons", 1
    )
    with pytest.raises(ConfigError, match="not a valid timezone"):
        load_app_config(_write(tmp_path, body))


def test_load_app_config_rejects_empty_calendar_urls(tmp_path):
    body = _VALID_BASE.replace(
        "  urls:\n    - https://nfs.faireconomy.media/ff_calendar_thisweek.json",
        "  urls: []",
    )
    with pytest.raises(ConfigError, match="urls must contain at least one"):
        load_app_config(_write(tmp_path, body))


def test_load_app_config_rejects_zero_timeout(tmp_path):
    body = _VALID_BASE.replace("timeout_seconds: 10", "timeout_seconds: 0")
    with pytest.raises(ConfigError, match="timeout_seconds must be positive"):
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


def test_find_rule_priority_collisions_flags_overlapping_rules():
    repo_root = Path(__file__).resolve().parent.parent
    rules = load_rules(repo_root / "config" / "rules.yaml")
    collisions = find_rule_priority_collisions(rules)
    assert collisions == []


def test_load_messages_returns_defaults_when_missing(tmp_path):
    messages = load_messages(tmp_path / "missing.yaml")
    assert "Wait 15-30 minutes" in messages.strong_news_advice
