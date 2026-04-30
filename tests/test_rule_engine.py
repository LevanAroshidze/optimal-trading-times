from datetime import date

import pytest

from bot.config import load_rules
from bot.models import DayColor, RuleDefinition, TradableStatus
from bot.rule_engine import RuleEngine


def test_rule_engine_prefers_highest_priority(tmp_path):
    rules_path = tmp_path / "rules.yaml"
    rules_path.write_text(
        """
- id: low
  priority: 10
  when:
    weekday: MON
  day_color: GREEN
  tradable: trade
  reason: low
  session_colors:
    London: GREEN
    PreMarket: GREEN
    NY_AM_FIRST_HALF: GREEN
    NY_AM_SECOND_HALF: GREEN
    Lunch: GREEN
    NY_PM_FIRST_HALF: GREEN
    NY_PM_SECOND_HALF: GREEN
- id: high
  priority: 20
  when:
    weekday: MON
  day_color: RED
  tradable: no_trade
  reason: high
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

    engine = RuleEngine(load_rules(rules_path))
    decision = engine.match(date(2026, 3, 30), {"weekday": "MON"})

    assert decision.rule_id == "high"
    assert decision.day_color == DayColor.RED
    assert decision.tradable == TradableStatus.NO_TRADE
    assert decision.blocked_sessions == [
        "London",
        "PreMarket",
        "NY_AM_FIRST_HALF",
        "NY_AM_SECOND_HALF",
        "Lunch",
        "NY_PM_FIRST_HALF",
        "NY_PM_SECOND_HALF",
    ]


def test_rule_engine_matches_weekend_list_condition(tmp_path):
    rules_path = tmp_path / "rules.yaml"
    rules_path.write_text(
        """
- id: weekend
  priority: 100
  when:
    weekday:
      - SAT
      - SUN
  day_color: RED
  tradable: no_trade
  reason: weekend
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

    engine = RuleEngine(load_rules(rules_path))
    decision = engine.match(date(2026, 4, 5), {"weekday": "SUN"})

    assert decision.rule_id == "weekend"
    assert decision.tradable == TradableStatus.NO_TRADE


def test_rule_engine_rejects_unknown_operator(tmp_path):
    rules_path = tmp_path / "rules.yaml"
    rules_path.write_text(
        """
- id: bad_operator
  priority: 10
  when:
    days_before_cpi:
      eq: 1
  day_color: RED
  tradable: no_trade
  reason: bad
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

    with pytest.raises(ValueError, match="Unsupported rule operator"):
        load_rules(rules_path)


def test_rule_engine_rejects_mixed_equals_operator(tmp_path):
    rules_path = tmp_path / "rules.yaml"
    rules_path.write_text(
        """
- id: bad_equals_mix
  priority: 10
  when:
    days_before_cpi:
      equals: 1
      gte: 1
  day_color: RED
  tradable: no_trade
  reason: bad
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

    with pytest.raises(ValueError, match="cannot be combined"):
        load_rules(rules_path)


def test_rule_engine_requires_fallback_rule():
    only_rule = RuleDefinition(
        id="only_rule",
        priority=10,
        when={"weekday": "MON"},
        day_color=DayColor.GREEN,
        tradable=TradableStatus.TRADE,
        session_colors={
            "London": DayColor.GREEN,
            "PreMarket": DayColor.GREEN,
            "NY_AM_FIRST_HALF": DayColor.GREEN,
            "NY_AM_SECOND_HALF": DayColor.GREEN,
            "Lunch": DayColor.GREEN,
            "NY_PM_FIRST_HALF": DayColor.GREEN,
            "NY_PM_SECOND_HALF": DayColor.GREEN,
        },
        reason="only",
    )
    with pytest.raises(ValueError, match="fallback rule"):
        RuleEngine([only_rule])


def test_load_rules_rejects_non_string_contains_operator(tmp_path):
    rules_path = tmp_path / "rules.yaml"
    rules_path.write_text(
        """
- id: bad_contains
  priority: 10
  when:
    categories_today:
      contains: 5
  day_color: RED
  tradable: no_trade
  reason: bad
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

    with pytest.raises(ValueError, match="contains must be a string"):
        load_rules(rules_path)


def test_load_rules_rejects_non_int_gte_operator(tmp_path):
    rules_path = tmp_path / "rules.yaml"
    rules_path.write_text(
        """
- id: bad_gte
  priority: 10
  when:
    days_before_cpi:
      gte: soon
  day_color: RED
  tradable: no_trade
  reason: bad
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

    with pytest.raises(ValueError, match="gte must be an int"):
        load_rules(rules_path)


def test_load_rules_rejects_legacy_session_names(tmp_path):
    rules_path = tmp_path / "rules.yaml"
    rules_path.write_text(
        """
- id: bad_sessions
  priority: 10
  when:
    weekday: MON
  day_color: RED
  tradable: no_trade
  reason: bad
  session_colors:
    London: RED
    PreMarket: RED
    NY_AM: RED
    Lunch: RED
    NY_PM: RED
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

    with pytest.raises(ValueError, match="unsupported session names"):
        load_rules(rules_path)
