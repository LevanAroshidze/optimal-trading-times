from __future__ import annotations

from datetime import date
from typing import Any

from bot.models import DailyDecision, RuleDefinition


class RuleEngine:
    def __init__(self, rules: list[RuleDefinition]) -> None:
        self.rules = rules

    def match(self, target_date: date, features: dict[str, Any]) -> DailyDecision:
        for rule in self.rules:
            if self._rule_matches(rule.when, features):
                return DailyDecision(
                    rule_id=rule.id,
                    target_date=target_date,
                    day_color=rule.day_color,
                    tradable=rule.tradable,
                    session_colors=rule.session_colors,
                    reason=rule.reason,
                    matched_conditions=rule.when,
                )
        raise RuntimeError("No fallback rule configured.")

    def _rule_matches(self, conditions: dict[str, Any], features: dict[str, Any]) -> bool:
        for key, expected in conditions.items():
            actual = features.get(key)
            if isinstance(expected, dict):
                if not self._matches_operator(expected, actual):
                    return False
                continue
            if isinstance(expected, list):
                if isinstance(actual, list):
                    if not all(item in actual for item in expected):
                        return False
                elif actual not in expected:
                    return False
                continue
            if actual != expected:
                return False
        return True

    def _matches_operator(self, expected: dict[str, Any], actual: Any) -> bool:
        allowed = {"equals", "gte", "lte", "contains"}
        unknown = set(expected) - allowed
        if unknown:
            raise ValueError(f"Unsupported rule operator(s): {', '.join(sorted(unknown))}")

        if "equals" in expected:
            if len(expected) > 1:
                raise ValueError("'equals' cannot be combined with other operators.")
            return actual == expected["equals"]

        if "contains" in expected and len(expected) > 1:
            raise ValueError("'contains' cannot be combined with other operators.")

        if actual is None:
            return False
        if "gte" in expected and actual < expected["gte"]:
            return False
        if "lte" in expected and actual > expected["lte"]:
            return False
        if "contains" in expected:
            return isinstance(actual, list) and expected["contains"] in actual

        if not expected:
            raise ValueError("Operator block cannot be empty.")
        return True
