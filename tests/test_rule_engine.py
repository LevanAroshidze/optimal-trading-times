from datetime import date

from bot.config import load_rules
from bot.models import DayColor, TradableStatus
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
    NY_AM: GREEN
    Lunch: GREEN
    NY_PM: GREEN
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
    NY_AM: GREEN
    Lunch: GREEN
    NY_PM: GREEN
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
    NY_AM: GREEN
    Lunch: GREEN
    NY_PM: GREEN
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
    NY_AM: GREEN
    Lunch: GREEN
    NY_PM: GREEN
""",
        encoding="utf-8",
    )

    engine = RuleEngine(load_rules(rules_path))
    try:
        engine.match(date(2026, 4, 1), {"days_before_cpi": 1})
        assert False, "Expected ValueError for unsupported operator."
    except ValueError as error:
        assert "Unsupported rule operator" in str(error)


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
    NY_AM: GREEN
    Lunch: GREEN
    NY_PM: GREEN
""",
        encoding="utf-8",
    )

    engine = RuleEngine(load_rules(rules_path))
    try:
        engine.match(date(2026, 4, 1), {"days_before_cpi": 1})
        assert False, "Expected ValueError for mixed equals operator."
    except ValueError as error:
        assert "cannot be combined" in str(error)
