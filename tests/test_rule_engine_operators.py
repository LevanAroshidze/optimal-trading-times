from __future__ import annotations

from datetime import date

import pytest

from bot.config import ConfigError, load_rules
from bot.rule_engine import RuleEngine


def _write_rules(tmp_path, body: str):
    path = tmp_path / "rules.yaml"
    path.write_text(body, encoding="utf-8")
    return path


_FALLBACK = """
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
"""


def _full_rule(rule_id: str, condition_yaml: str, priority: int = 10) -> str:
    return f"""
- id: {rule_id}
  priority: {priority}
  when:
    {condition_yaml}
  day_color: RED
  tradable: no_trade
  reason: {rule_id}
  session_colors:
    London: RED
    PreMarket: RED
    NY_AM_FIRST_HALF: RED
    NY_AM_SECOND_HALF: RED
    Lunch: RED
    NY_PM_FIRST_HALF: RED
    NY_PM_SECOND_HALF: RED
"""


def test_gt_lt_operators_match_correctly(tmp_path):
    body = _full_rule("gt_rule", "days_before_cpi:\n      gt: 1") + _FALLBACK
    rules = load_rules(_write_rules(tmp_path, body))
    engine = RuleEngine(rules)
    assert engine.match(date(2026, 1, 1), {"days_before_cpi": 2}).rule_id == "gt_rule"
    assert engine.match(date(2026, 1, 1), {"days_before_cpi": 1}).rule_id == "fallback"


def test_between_operator(tmp_path):
    body = _full_rule("between_rule", "days_before_cpi:\n      between: [1, 3]") + _FALLBACK
    engine = RuleEngine(load_rules(_write_rules(tmp_path, body)))
    assert engine.match(date(2026, 1, 1), {"days_before_cpi": 2}).rule_id == "between_rule"
    assert engine.match(date(2026, 1, 1), {"days_before_cpi": 4}).rule_id == "fallback"


def test_in_operator_matches_membership(tmp_path):
    body = _full_rule("in_rule", "weekday:\n      in: [TUE, WED]") + _FALLBACK
    engine = RuleEngine(load_rules(_write_rules(tmp_path, body)))
    assert engine.match(date(2026, 1, 1), {"weekday": "TUE"}).rule_id == "in_rule"
    assert engine.match(date(2026, 1, 1), {"weekday": "FRI"}).rule_id == "fallback"


def test_not_equals_and_not_contains(tmp_path):
    body = (
        _full_rule("not_eq_rule", "weekday:\n      not_equals: MON")
        + _FALLBACK
    )
    engine = RuleEngine(load_rules(_write_rules(tmp_path, body)))
    assert engine.match(date(2026, 1, 1), {"weekday": "TUE"}).rule_id == "not_eq_rule"
    assert engine.match(date(2026, 1, 1), {"weekday": "MON"}).rule_id == "fallback"


def test_any_of_operator(tmp_path):
    body = (
        _full_rule(
            "any_of_rule",
            "days_before_cpi:\n      any_of:\n        - equals: 0\n        - equals: 7",
        )
        + _FALLBACK
    )
    engine = RuleEngine(load_rules(_write_rules(tmp_path, body)))
    assert engine.match(date(2026, 1, 1), {"days_before_cpi": 7}).rule_id == "any_of_rule"
    assert engine.match(date(2026, 1, 1), {"days_before_cpi": 5}).rule_id == "fallback"


def test_all_of_operator(tmp_path):
    body = (
        _full_rule(
            "all_of_rule",
            "days_before_cpi:\n      all_of:\n        - gte: 1\n        - lte: 3",
        )
        + _FALLBACK
    )
    engine = RuleEngine(load_rules(_write_rules(tmp_path, body)))
    assert engine.match(date(2026, 1, 1), {"days_before_cpi": 2}).rule_id == "all_of_rule"
    assert engine.match(date(2026, 1, 1), {"days_before_cpi": 5}).rule_id == "fallback"


def test_grouped_operator_does_not_ignore_sibling_constraint(tmp_path):
    body = (
        _full_rule(
            "conjunctive_rule",
            (
                "days_before_cpi:\n"
                "      any_of:\n"
                "        - equals: 0\n"
                "        - equals: 7\n"
                "      gt: 1"
            ),
        )
        + _FALLBACK
    )
    engine = RuleEngine(load_rules(_write_rules(tmp_path, body)))

    assert engine.match(date(2026, 1, 1), {"days_before_cpi": 0}).rule_id == "fallback"
    assert (
        engine.match(date(2026, 1, 1), {"days_before_cpi": 7}).rule_id
        == "conjunctive_rule"
    )


@pytest.mark.parametrize(
    "condition_yaml",
    [
        "days_before_cpi:\n      equals: null",
        "days_before_cpi:\n      in: [null, 1]",
        (
            "days_before_cpi:\n"
            "      any_of:\n"
            "        - equals: null\n"
            "        - equals: 1"
        ),
        (
            "days_before_cpi:\n"
            "      all_of:\n"
            "        - not_equals: 1\n"
            "        - not_equals: 2"
        ),
    ],
)
def test_positive_operator_constraints_do_not_match_missing_feature(
    tmp_path, condition_yaml
):
    body = _full_rule("missing_feature_rule", condition_yaml) + _FALLBACK
    engine = RuleEngine(load_rules(_write_rules(tmp_path, body)))

    assert engine.match(date(2026, 1, 1), {}).rule_id == "fallback"


@pytest.mark.parametrize(
    ("condition_yaml", "feature_key"),
    [
        ("days_before_cpi:\n      not_equals: 1", "days_before_cpi"),
        ("categories_today:\n      not_contains: CPI", "categories_today"),
    ],
)
def test_standalone_negative_operator_can_match_missing_feature(
    tmp_path, condition_yaml, feature_key
):
    body = _full_rule("negative_rule", condition_yaml) + _FALLBACK
    engine = RuleEngine(load_rules(_write_rules(tmp_path, body)))

    assert engine.match(date(2026, 1, 1), {feature_key: None}).rule_id == "negative_rule"


@pytest.mark.parametrize("operator", ["any_of", "all_of"])
def test_grouped_operator_rejects_empty_members(tmp_path, operator):
    body = (
        _full_rule("empty_group", f"days_before_cpi:\n      {operator}: []")
        + _FALLBACK
    )

    with pytest.raises(ConfigError, match="must not be empty"):
        load_rules(_write_rules(tmp_path, body))
