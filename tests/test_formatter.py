from datetime import date

from bot.formatter import DiscordFormatter
from bot.models import DailyDecision, DailyOutlook, DayColor, TradableStatus, WeeklyOutlook


def make_outlook() -> DailyOutlook:
    decision = DailyDecision(
        rule_id="cpi_day",
        target_date=date(2026, 4, 16),
        day_color=DayColor.WHITE,
        tradable=TradableStatus.CAUTION,
        session_colors={
            "London": DayColor.RED,
            "PreMarket": DayColor.RED,
            "NY_AM_FIRST_HALF": DayColor.WHITE,
            "NY_AM_SECOND_HALF": DayColor.GREEN,
            "Lunch": DayColor.GREEN,
            "NY_PM_FIRST_HALF": DayColor.GREEN,
            "NY_PM_SECOND_HALF": DayColor.GREEN,
        },
        reason="CPI day.",
    )
    return DailyOutlook(
        decision=decision,
        summary_reason="CPI day.",
        strongest_news_today=["CPI at 8:30 AM NY time"],
        strongest_news_tomorrow=["NFP at 8:30 AM NY time"],
    )


def test_formatter_builds_trader_style_daily_message():
    message = DiscordFormatter().format_daily(make_outlook())

    assert "White day. CPI day." in message
    assert "Focus: NY AM (Second Half), Lunch and NY PM." in message
    assert "Caution: NY AM (First Half)." in message
    assert "Avoid: London and PreMarket." in message
    assert (
        "Strongest USD news: CPI at 8:30 AM NY time. "
        "Wait 15-30 minutes after the release before looking for trades."
    ) in message


def test_formatter_collapses_full_ny_am_and_ny_pm_sessions():
    decision = DailyDecision(
        rule_id="green_day",
        target_date=date(2026, 4, 17),
        day_color=DayColor.GREEN,
        tradable=TradableStatus.TRADE,
        session_colors={
            "London": DayColor.GREEN,
            "PreMarket": DayColor.GREEN,
            "NY_AM_FIRST_HALF": DayColor.GREEN,
            "NY_AM_SECOND_HALF": DayColor.GREEN,
            "Lunch": DayColor.WHITE,
            "NY_PM_FIRST_HALF": DayColor.RED,
            "NY_PM_SECOND_HALF": DayColor.RED,
        },
        reason="Clean day.",
    )
    outlook = DailyOutlook(decision=decision, summary_reason="Clean day.")

    message = DiscordFormatter().format_daily(outlook)

    assert "Focus: London, PreMarket and NY AM." in message
    assert "NY AM (First Half)" not in message
    assert "NY AM (Second Half)" not in message


def test_formatter_builds_weekly_message():
    weekly = WeeklyOutlook(week_start=date(2026, 4, 20), days=[make_outlook()])

    message = DiscordFormatter().format_weekly(weekly)

    assert message.startswith("Week Ahead")
    assert "Thu: White day. CPI day." in message
