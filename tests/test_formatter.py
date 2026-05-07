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
    assert "Focus: NY AM (Second Half) and NY PM." in message
    assert "Caution: NY AM (First Half)." in message
    assert "Avoid: London." in message
    assert "PreMarket" not in message
    assert "Lunch" not in message
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

    assert "Focus: London and NY AM." in message
    assert "NY AM (First Half)" not in message
    assert "NY AM (Second Half)" not in message


def test_formatter_hides_non_trading_sessions_entirely():
    decision = DailyDecision(
        rule_id="mixed_day",
        target_date=date(2026, 4, 18),
        day_color=DayColor.WHITE,
        tradable=TradableStatus.CAUTION,
        session_colors={
            "London": DayColor.WHITE,
            "PreMarket": DayColor.GREEN,
            "NY_AM_FIRST_HALF": DayColor.RED,
            "NY_AM_SECOND_HALF": DayColor.RED,
            "Lunch": DayColor.RED,
            "NY_PM_FIRST_HALF": DayColor.GREEN,
            "NY_PM_SECOND_HALF": DayColor.GREEN,
        },
        reason="Mixed day.",
    )
    outlook = DailyOutlook(decision=decision, summary_reason="Mixed day.")

    message = DiscordFormatter().format_daily(outlook)

    assert "PreMarket" not in message
    assert "Lunch" not in message
    assert "Focus: NY PM." in message
    assert "Caution: London." in message
    assert "Avoid: NY AM." in message


def test_formatter_builds_weekly_message():
    weekly = WeeklyOutlook(week_start=date(2026, 4, 20), days=[make_outlook()])

    message = DiscordFormatter().format_weekly(weekly)

    assert message.startswith("Week Ahead")
    assert "Thu: White day. CPI day." in message


def test_formatter_marks_cached_daily_and_weekly_messages():
    outlook = make_outlook()
    cached_daily = DailyOutlook(
        decision=outlook.decision,
        summary_reason=outlook.summary_reason,
        source_status="cached",
    )
    weekly = WeeklyOutlook(
        week_start=date(2026, 4, 20),
        days=[cached_daily],
        source_status="cached",
    )

    formatter = DiscordFormatter()

    assert "Using cached calendar data" in formatter.format_daily(cached_daily)
    assert "Using cached calendar data" in formatter.format_weekly(weekly)


def test_formatter_does_not_duplicate_nfp_wait_copy():
    outlook = make_outlook()
    decision = DailyDecision(
        rule_id="nfp_week_friday",
        target_date=date(2026, 4, 17),
        day_color=DayColor.WHITE,
        tradable=TradableStatus.CAUTION,
        session_colors=outlook.decision.session_colors,
        reason="NFP day. Wait 15-30 minutes after the 8:30 release before treating NY AM as green.",
    )
    nfp = DailyOutlook(
        decision=decision,
        summary_reason=decision.reason,
        strongest_news_today=["NFP at 8:30 AM NY time"],
    )

    message = DiscordFormatter().format_daily(nfp)

    assert message.count("Wait 15-30 minutes") == 1
    assert "Strongest USD news: NFP at 8:30 AM NY time." in message


def test_formatter_includes_weekly_quiet_note():
    quiet_note = (
        "No major USD news. On NY AM and NY PM, only consider macro-time entries "
        "10 minutes before through 10 minutes after the hour, for example 09:50-10:10."
    )
    outlook = DailyOutlook(
        decision=make_outlook().decision,
        summary_reason="No major USD news today.",
        quiet_note=quiet_note,
    )
    weekly = WeeklyOutlook(week_start=date(2026, 4, 20), days=[outlook])

    message = DiscordFormatter().format_weekly(weekly)

    assert "09:50-10:10" in message
