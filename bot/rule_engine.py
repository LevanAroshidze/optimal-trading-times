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
        if "any_of" in expected:
            sub_conditions = expected["any_of"]
            if not isinstance(sub_conditions, list):
                raise ValueError("'any_of' must be a list of operator maps")
            if not any(
                self._matches_operator(sub, actual) for sub in sub_conditions
            ):
                return False
        if "all_of" in expected:
            sub_conditions = expected["all_of"]
            if not isinstance(sub_conditions, list):
                raise ValueError("'all_of' must be a list of operator maps")
            if not all(
                self._matches_operator(sub, actual) for sub in sub_conditions
            ):
                return False

        if "not_equals" in expected:
            return actual != expected["not_equals"]

        if "not_contains" in expected:
            return not isinstance(actual, list) or expected["not_contains"] not in actual

        # Missing features may satisfy explicitly negative operators, but must
        # never make a positive constraint match and select a less-safe rule.
        if actual is None:
            return False

        if "equals" in expected:
            return actual == expected["equals"]

        if "contains" in expected:
            return isinstance(actual, list) and expected["contains"] in actual

        if "in" in expected:
            members = expected["in"]
            if not isinstance(members, list):
                raise ValueError("'in' must be a list")
            if actual not in members:
                return False

        if "between" in expected:
            bounds = expected["between"]
            if not isinstance(bounds, list) or len(bounds) != 2:
                raise ValueError("'between' must be a 2-element list")
            if not isinstance(actual, int):
                return False
            low, high = bounds
            if actual < low or actual > high:
                return False

        if "gt" in expected:
            if not isinstance(actual, int) or actual <= expected["gt"]:
                return False
        if "gte" in expected:
            if not isinstance(actual, int) or actual < expected["gte"]:
                return False
        if "lt" in expected:
            if not isinstance(actual, int) or actual >= expected["lt"]:
                return False
        if "lte" in expected:
            if not isinstance(actual, int) or actual > expected["lte"]:
                return False

        return True
