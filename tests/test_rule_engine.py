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
""",
        encoding="utf-8",
    )

    engine = RuleEngine(load_rules(rules_path))
    decision = engine.match(date(2026, 3, 30), {"weekday": "MON"})

    assert decision.rule_id == "high"
    assert decision.day_color == DayColor.RED
    assert decision.tradable == TradableStatus.NO_TRADE

