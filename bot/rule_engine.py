from __future__ import annotations

from datetime import date

from bot.models import DailyDecision, RuleDefinition
from bot.type_defs import FeatureMap, FeatureValue, RuleConditions, RuleOperatorMap


class RuleEngine:
    def __init__(self, rules: list[RuleDefinition]) -> None:
        if not rules:
            raise ValueError("RuleEngine requires at least one rule.")
        if not any(not rule.when for rule in rules):
            raise ValueError("RuleEngine requires a fallback rule.")
        self.rules = rules

    def match(self, target_date: date, features: FeatureMap) -> DailyDecision:
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
        raise AssertionError("Validated rules should always include a fallback rule.")

    def _rule_matches(self, conditions: RuleConditions, features: FeatureMap) -> bool:
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

    def _matches_operator(
        self, expected: RuleOperatorMap, actual: FeatureValue | None
    ) -> bool:
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
        if "gte" in expected:
            if not isinstance(actual, int):
                return False
            if actual < expected["gte"]:
                return False
        if "lte" in expected:
            if not isinstance(actual, int):
                return False
            if actual > expected["lte"]:
                return False
        if "contains" in expected:
            return isinstance(actual, list) and expected["contains"] in actual

        if not expected:
            raise ValueError("Operator block cannot be empty.")
        return True
