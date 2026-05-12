from datetime import date
from pathlib import Path

import pytest

from bot.config import load_rules
from bot.models import SESSION_ORDER, DayColor
from bot.rule_engine import RuleEngine
from bot.type_defs import FeatureMap, FeatureValue

RULES = load_rules(Path("config/rules.yaml"))
ENGINE = RuleEngine(RULES)


def make_features(**overrides: FeatureValue) -> FeatureMap:
    features: FeatureMap = {
        "weekday": "MON",
        "week_type": None,
        "days_before_cpi": None,
        "days_before_ppi": None,
        "has_cpi_today": False,
        "has_ppi_today": False,
        "has_bank_holiday_today": False,
        "had_bank_holiday_yesterday": False,
        "has_partial_holiday_today": False,
        "has_830_red_news": False,
        "has_powell_10am_today": False,
        "days_until_powell_10am": None,
        "has_pm_red_news": False,
        "has_no_major_usd_news_today": False,
        "categories_today": [],
    }
    features.update(overrides)
    return features


@pytest.mark.parametrize(
    ("rule_id", "features", "expected_colors"),
    [
        (
            "weekend_no_trade",
            make_features(weekday="SUN"),
            {
                "London": DayColor.RED,
                "PreMarket": DayColor.RED,
                "NY_AM_FIRST_HALF": DayColor.RED,
                "NY_AM_SECOND_HALF": DayColor.RED,
                "Lunch": DayColor.RED,
                "NY_PM_FIRST_HALF": DayColor.RED,
                "NY_PM_SECOND_HALF": DayColor.RED,
            },
        ),
        (
            "bank_holiday_no_trade",
            make_features(weekday="MON", has_bank_holiday_today=True),
            {
                "London": DayColor.RED,
                "PreMarket": DayColor.RED,
                "NY_AM_FIRST_HALF": DayColor.RED,
                "NY_AM_SECOND_HALF": DayColor.RED,
                "Lunch": DayColor.RED,
                "NY_PM_FIRST_HALF": DayColor.RED,
                "NY_PM_SECOND_HALF": DayColor.RED,
            },
        ),
        (
            "partial_holiday_day",
            make_features(weekday="MON", has_partial_holiday_today=True),
            {
                "London": DayColor.RED,
                "PreMarket": DayColor.RED,
                "NY_AM_FIRST_HALF": DayColor.RED,
                "NY_AM_SECOND_HALF": DayColor.RED,
                "Lunch": DayColor.RED,
                "NY_PM_FIRST_HALF": DayColor.RED,
                "NY_PM_SECOND_HALF": DayColor.RED,
            },
        ),
        (
            "nfp_week_thursday",
            make_features(weekday="THU", week_type="NFP"),
            {
                "London": DayColor.RED,
                "PreMarket": DayColor.RED,
                "NY_AM_FIRST_HALF": DayColor.RED,
                "NY_AM_SECOND_HALF": DayColor.RED,
                "Lunch": DayColor.RED,
                "NY_PM_FIRST_HALF": DayColor.RED,
                "NY_PM_SECOND_HALF": DayColor.RED,
            },
        ),
        (
            "nfp_week_friday",
            make_features(weekday="FRI", week_type="NFP"),
            {
                "London": DayColor.RED,
                "PreMarket": DayColor.RED,
                "NY_AM_FIRST_HALF": DayColor.WHITE,
                "NY_AM_SECOND_HALF": DayColor.GREEN,
                "Lunch": DayColor.GREEN,
                "NY_PM_FIRST_HALF": DayColor.GREEN,
                "NY_PM_SECOND_HALF": DayColor.GREEN,
            },
        ),
        (
            "nfp_week_wednesday",
            make_features(weekday="WED", week_type="NFP"),
            {
                "London": DayColor.GREEN,
                "PreMarket": DayColor.GREEN,
                "NY_AM_FIRST_HALF": DayColor.GREEN,
                "NY_AM_SECOND_HALF": DayColor.WHITE,
                "Lunch": DayColor.RED,
                "NY_PM_FIRST_HALF": DayColor.RED,
                "NY_PM_SECOND_HALF": DayColor.RED,
            },
        ),
        (
            "nfp_week_tuesday",
            make_features(weekday="TUE", week_type="NFP"),
            {
                "London": DayColor.GREEN,
                "PreMarket": DayColor.GREEN,
                "NY_AM_FIRST_HALF": DayColor.GREEN,
                "NY_AM_SECOND_HALF": DayColor.GREEN,
                "Lunch": DayColor.GREEN,
                "NY_PM_FIRST_HALF": DayColor.GREEN,
                "NY_PM_SECOND_HALF": DayColor.GREEN,
            },
        ),
        (
            "nfp_week_monday",
            make_features(weekday="MON", week_type="NFP"),
            {
                "London": DayColor.GREEN,
                "PreMarket": DayColor.GREEN,
                "NY_AM_FIRST_HALF": DayColor.WHITE,
                "NY_AM_SECOND_HALF": DayColor.WHITE,
                "Lunch": DayColor.GREEN,
                "NY_PM_FIRST_HALF": DayColor.GREEN,
                "NY_PM_SECOND_HALF": DayColor.GREEN,
            },
        ),
        (
            "cpi_day",
            make_features(weekday="THU", has_cpi_today=True),
            {
                "London": DayColor.RED,
                "PreMarket": DayColor.RED,
                "NY_AM_FIRST_HALF": DayColor.WHITE,
                "NY_AM_SECOND_HALF": DayColor.GREEN,
                "Lunch": DayColor.GREEN,
                "NY_PM_FIRST_HALF": DayColor.GREEN,
                "NY_PM_SECOND_HALF": DayColor.GREEN,
            },
        ),
        (
            "ppi_day",
            make_features(weekday="THU", has_ppi_today=True),
            {
                "London": DayColor.RED,
                "PreMarket": DayColor.RED,
                "NY_AM_FIRST_HALF": DayColor.WHITE,
                "NY_AM_SECOND_HALF": DayColor.GREEN,
                "Lunch": DayColor.GREEN,
                "NY_PM_FIRST_HALF": DayColor.GREEN,
                "NY_PM_SECOND_HALF": DayColor.GREEN,
            },
        ),
        (
            "cpi_minus_1",
            make_features(weekday="WED", days_before_cpi=1),
            {
                "London": DayColor.GREEN,
                "PreMarket": DayColor.GREEN,
                "NY_AM_FIRST_HALF": DayColor.RED,
                "NY_AM_SECOND_HALF": DayColor.RED,
                "Lunch": DayColor.GREEN,
                "NY_PM_FIRST_HALF": DayColor.RED,
                "NY_PM_SECOND_HALF": DayColor.RED,
            },
        ),
        (
            "cpi_minus_2",
            make_features(weekday="TUE", days_before_cpi=2),
            {
                "London": DayColor.WHITE,
                "PreMarket": DayColor.WHITE,
                "NY_AM_FIRST_HALF": DayColor.WHITE,
                "NY_AM_SECOND_HALF": DayColor.WHITE,
                "Lunch": DayColor.WHITE,
                "NY_PM_FIRST_HALF": DayColor.WHITE,
                "NY_PM_SECOND_HALF": DayColor.WHITE,
            },
        ),
        (
            "ppi_minus_1",
            make_features(weekday="WED", days_before_ppi=1),
            {
                "London": DayColor.GREEN,
                "PreMarket": DayColor.GREEN,
                "NY_AM_FIRST_HALF": DayColor.RED,
                "NY_AM_SECOND_HALF": DayColor.RED,
                "Lunch": DayColor.GREEN,
                "NY_PM_FIRST_HALF": DayColor.RED,
                "NY_PM_SECOND_HALF": DayColor.RED,
            },
        ),
        (
            "ppi_minus_2",
            make_features(weekday="TUE", days_before_ppi=2),
            {
                "London": DayColor.WHITE,
                "PreMarket": DayColor.WHITE,
                "NY_AM_FIRST_HALF": DayColor.WHITE,
                "NY_AM_SECOND_HALF": DayColor.WHITE,
                "Lunch": DayColor.WHITE,
                "NY_PM_FIRST_HALF": DayColor.WHITE,
                "NY_PM_SECOND_HALF": DayColor.WHITE,
            },
        ),
        (
            "powell_10am_previous_day",
            make_features(weekday="MON", days_until_powell_10am=1),
            {
                "London": DayColor.WHITE,
                "PreMarket": DayColor.WHITE,
                "NY_AM_FIRST_HALF": DayColor.WHITE,
                "NY_AM_SECOND_HALF": DayColor.WHITE,
                "Lunch": DayColor.WHITE,
                "NY_PM_FIRST_HALF": DayColor.RED,
                "NY_PM_SECOND_HALF": DayColor.RED,
            },
        ),
        (
            "powell_10am_today",
            make_features(weekday="TUE", has_powell_10am_today=True),
            {
                "London": DayColor.RED,
                "PreMarket": DayColor.RED,
                "NY_AM_FIRST_HALF": DayColor.RED,
                "NY_AM_SECOND_HALF": DayColor.GREEN,
                "Lunch": DayColor.GREEN,
                "NY_PM_FIRST_HALF": DayColor.GREEN,
                "NY_PM_SECOND_HALF": DayColor.GREEN,
            },
        ),
        (
            "pm_red_news_today",
            make_features(weekday="WED", has_pm_red_news=True),
            {
                "London": DayColor.GREEN,
                "PreMarket": DayColor.GREEN,
                "NY_AM_FIRST_HALF": DayColor.RED,
                "NY_AM_SECOND_HALF": DayColor.RED,
                "Lunch": DayColor.RED,
                "NY_PM_FIRST_HALF": DayColor.RED,
                "NY_PM_SECOND_HALF": DayColor.GREEN,
            },
        ),
        (
            "red_830_news_today",
            make_features(weekday="THU", has_830_red_news=True),
            {
                "London": DayColor.WHITE,
                "PreMarket": DayColor.RED,
                "NY_AM_FIRST_HALF": DayColor.WHITE,
                "NY_AM_SECOND_HALF": DayColor.GREEN,
                "Lunch": DayColor.GREEN,
                "NY_PM_FIRST_HALF": DayColor.GREEN,
                "NY_PM_SECOND_HALF": DayColor.GREEN,
            },
        ),
        (
            "no_major_usd_news_day",
            make_features(weekday="MON", has_no_major_usd_news_today=True),
            {
                "London": DayColor.GREEN,
                "PreMarket": DayColor.GREEN,
                "NY_AM_FIRST_HALF": DayColor.WHITE,
                "NY_AM_SECOND_HALF": DayColor.WHITE,
                "Lunch": DayColor.GREEN,
                "NY_PM_FIRST_HALF": DayColor.WHITE,
                "NY_PM_SECOND_HALF": DayColor.WHITE,
            },
        ),
        (
            "post_bank_holiday_no_major_news_day",
            make_features(
                weekday="TUE",
                had_bank_holiday_yesterday=True,
                has_no_major_usd_news_today=True,
            ),
            {
                "London": DayColor.WHITE,
                "PreMarket": DayColor.WHITE,
                "NY_AM_FIRST_HALF": DayColor.WHITE,
                "NY_AM_SECOND_HALF": DayColor.WHITE,
                "Lunch": DayColor.WHITE,
                "NY_PM_FIRST_HALF": DayColor.WHITE,
                "NY_PM_SECOND_HALF": DayColor.WHITE,
            },
        ),
        (
            "default_green_day",
            make_features(weekday="MON"),
            {
                "London": DayColor.GREEN,
                "PreMarket": DayColor.GREEN,
                "NY_AM_FIRST_HALF": DayColor.GREEN,
                "NY_AM_SECOND_HALF": DayColor.GREEN,
                "Lunch": DayColor.GREEN,
                "NY_PM_FIRST_HALF": DayColor.GREEN,
                "NY_PM_SECOND_HALF": DayColor.GREEN,
            },
        ),
    ],
)
def test_production_rule_matrix(
    rule_id: str, features: FeatureMap, expected_colors: dict[str, DayColor]
) -> None:
    decision = ENGINE.match(date(2026, 4, 1), features)

    assert decision.rule_id == rule_id
    assert decision.session_colors == expected_colors
    assert list(decision.session_colors.keys()) == SESSION_ORDER


@pytest.mark.parametrize(
    ("features", "expected_rule"),
    [
        (
            make_features(
                weekday="MON",
                week_type="NFP",
                had_bank_holiday_yesterday=True,
                has_no_major_usd_news_today=True,
            ),
            "nfp_week_monday",
        ),
        (
            make_features(
                weekday="MON",
                days_before_cpi=1,
                had_bank_holiday_yesterday=True,
                has_no_major_usd_news_today=True,
            ),
            "cpi_minus_1",
        ),
        (
            make_features(
                weekday="MON",
                days_until_powell_10am=1,
                had_bank_holiday_yesterday=True,
                has_no_major_usd_news_today=True,
            ),
            "powell_10am_previous_day",
        ),
    ],
)
def test_post_bank_holiday_quiet_rule_stays_below_stronger_setups(
    features: FeatureMap, expected_rule: str
) -> None:
    decision = ENGINE.match(date(2026, 4, 13), features)

    assert decision.rule_id == expected_rule
