from datetime import date

from bot.formatter import DiscordFormatter
from bot.models import DailyDecision, DailyOutlook, DayColor, TradableStatus, WeeklyOutlook
from bot.preview_styles import PREVIEW_PREFIX, build_preview_messages
from bot.webhook_sender import WebhookPayload


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
    embed = _only_embed(message)

    assert embed["title"] == "CPI Day"
    assert embed["color"] == 0xE0E0E0
    assert _field(embed, "🟢 Focus") == "NY AM second half, NY PM"
    assert _field(embed, "⚪ Caution") == "NY AM first half"
    assert _field(embed, "🔴 Avoid") == "London"
    rendered = DiscordFormatter().render_for_console(message)
    assert "PreMarket" not in rendered
    assert "Lunch" not in rendered
    assert (
        "CPI at 8:30 AM NY time\n"
        "Wait 15-30 minutes after the release before looking for trades."
    ) in _field(embed, "Calendar")
    assert embed["footer"] == {"text": "Source: live calendar"}


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
    embed = _only_embed(message)

    assert _field(embed, "🟢 Focus") == "London, NY AM"
    rendered = DiscordFormatter().render_for_console(message)
    assert "NY AM (First Half)" not in rendered
    assert "NY AM (Second Half)" not in rendered


def test_formatter_builds_update_embed_with_change_field():
    message = DiscordFormatter().format_update(
        make_outlook(),
        [
            "Added strongest USD news: CPI at 8:30 AM NY time",
            "Removed quiet-day marker",
        ],
    )
    embed = _only_embed(message)

    assert embed["title"] == "Calendar Update"
    assert _field(embed, "Change") == (
        "Added strongest USD news: CPI at 8:30 AM NY time\n"
        "Removed quiet-day marker"
    )
    assert _field(embed, "🟢 Focus") == "NY AM second half, NY PM"
    assert "CPI at 8:30 AM NY time" in _field(embed, "Calendar")


def test_formatter_trims_long_update_change_field_for_discord_limits():
    message = DiscordFormatter().format_update(
        make_outlook(),
        [f"Added calendar item {index}" for index in range(100)],
    )
    change = _field(_only_embed(message), "Change")

    assert len(change) <= 1024
    assert change.endswith("...")


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
    embed = _only_embed(message)
    rendered = DiscordFormatter().render_for_console(message)

    assert "PreMarket" not in rendered
    assert "Lunch" not in rendered
    assert _field(embed, "🟢 Focus") == "NY PM"
    assert _field(embed, "⚪ Caution") == "London"
    assert _field(embed, "🔴 Avoid") == "NY AM"


def test_formatter_builds_weekly_message():
    weekly = WeeklyOutlook(week_start=date(2026, 4, 20), days=[make_outlook()])

    message = DiscordFormatter().format_weekly(weekly)

    assert message.startswith("**Week Ahead | Apr 20-24**")
    assert "⚪ **Thu** — CPI day. CPI at 8:30 AM." in message
    assert "White day." not in message


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

    daily = formatter.format_daily(cached_daily)

    assert _only_embed(daily)["footer"] == {"text": "Source: cached calendar data"}
    assert "_Source: cached calendar data._" in formatter.format_weekly(weekly)


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
    rendered = DiscordFormatter().render_for_console(message)

    assert rendered.count("Wait 15-30 minutes") == 1
    assert "NFP at 8:30 AM NY time" in rendered


def test_formatter_lists_other_red_news_without_generic_advice_on_explicit_rule():
    outlook = make_outlook()
    explicit = DailyOutlook(
        decision=outlook.decision,
        summary_reason=outlook.summary_reason,
        strongest_news_today=outlook.strongest_news_today,
        other_red_news_today=["Fed Chair Nomination Vote at 12:00 PM NY time"],
    )

    message = DiscordFormatter().format_daily(explicit)
    calendar = _field(_only_embed(message), "Calendar")

    assert "Other red news: Fed Chair Nomination Vote at 12:00 PM NY time." in calendar
    assert "Avoid the 2-3 hours before the release." not in calendar


def test_formatter_keeps_generic_advice_when_default_overlay_controls_plan():
    decision = DailyDecision(
        rule_id="default_green_day",
        target_date=date(2026, 4, 17),
        day_color=DayColor.WHITE,
        tradable=TradableStatus.CAUTION,
        session_colors={
            "London": DayColor.GREEN,
            "PreMarket": DayColor.RED,
            "NY_AM_FIRST_HALF": DayColor.RED,
            "NY_AM_SECOND_HALF": DayColor.GREEN,
            "Lunch": DayColor.GREEN,
            "NY_PM_FIRST_HALF": DayColor.GREEN,
            "NY_PM_SECOND_HALF": DayColor.GREEN,
        },
        reason="No blocking calendar condition.",
    )
    outlook = DailyOutlook(
        decision=decision,
        summary_reason="Other red-folder USD news today.",
        other_red_news_today=["Retail Sales m/m at 10:00 AM NY time"],
    )

    message = DiscordFormatter().format_daily(outlook)
    calendar = _field(_only_embed(message), "Calendar")

    assert "Other red news: Retail Sales m/m at 10:00 AM NY time." in calendar
    assert "Avoid the 2-3 hours before the release." in calendar


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
    assert "No major USD news today. No major USD news." not in message
    assert "No major USD news. Macro-time entries only." in message
    assert "_Macro-time = 10 minutes before through 10 minutes after the hour" in message
    assert "White day." not in message


def test_formatter_trims_duplicate_quiet_note_prefix_in_daily_message():
    quiet_note = (
        "No major USD news. On NY AM and NY PM, only consider macro-time entries "
        "10 minutes before through 10 minutes after the hour, for example 09:50-10:10."
    )
    outlook = DailyOutlook(
        decision=make_outlook().decision,
        summary_reason="No major USD news today.",
        quiet_note=quiet_note,
    )

    message = DiscordFormatter().format_daily(outlook)
    rendered = DiscordFormatter().render_for_console(message)

    assert _only_embed(message)["title"] == "CPI Day"
    assert "No major USD news. On NY AM" not in rendered
    assert "On NY AM and NY PM" in _field(_only_embed(message), "Note")


def test_formatter_hides_post_holiday_no_trade_quiet_note():
    quiet_note = (
        "No major USD news. On NY AM and NY PM, only consider macro-time entries "
        "10 minutes before through 10 minutes after the hour."
    )
    decision = DailyDecision(
        rule_id="post_bank_holiday_no_major_news_day",
        target_date=date(2026, 4, 14),
        day_color=DayColor.RED,
        tradable=TradableStatus.NO_TRADE,
        session_colors={
            "London": DayColor.RED,
            "PreMarket": DayColor.RED,
            "NY_AM_FIRST_HALF": DayColor.RED,
            "NY_AM_SECOND_HALF": DayColor.RED,
            "Lunch": DayColor.RED,
            "NY_PM_FIRST_HALF": DayColor.RED,
            "NY_PM_SECOND_HALF": DayColor.RED,
        },
        reason="Day after a USD bank holiday with no red-folder USD news.",
    )
    outlook = DailyOutlook(
        decision=decision,
        summary_reason=decision.reason,
        quiet_note=quiet_note,
    )

    message = DiscordFormatter().format_daily(outlook)
    embed = _only_embed(message)

    assert embed["title"] == "Post-Holiday No Trade"
    assert _field(embed, "🔴 Avoid") == "London, NY AM, NY PM"
    assert all(field["name"] != "Note" for field in embed["fields"])


def test_formatter_keeps_calendar_field_on_no_major_news_daily_embed():
    outlook = DailyOutlook(
        decision=make_outlook().decision,
        summary_reason="No major USD news today.",
    )

    message = DiscordFormatter().format_daily(outlook)

    assert _field(_only_embed(message), "Calendar") == "No major USD news."


def test_preview_styles_cover_three_formats_and_message_kinds():
    previews = build_preview_messages()

    assert len(previews) == 9
    assert {preview.style for preview in previews} == {
        "Style 1: Text + Color Only",
        "Style 2: Small Icons",
        "Style 3: Plain Labels",
    }
    assert {preview.kind for preview in previews} == {"daily", "update", "weekly"}


def test_preview_styles_are_prefixed_and_include_embed_examples():
    previews = build_preview_messages()
    payload_text = "\n".join(_preview_payload_text(preview.payload) for preview in previews)

    assert all(
        _preview_payload_text(preview.payload).startswith(PREVIEW_PREFIX)
        for preview in previews
    )
    assert any(isinstance(preview.payload, WebhookPayload) for preview in previews)
    assert "White day." not in payload_text
    assert "No major USD news today. No major USD news." not in payload_text
    assert "Macro-time" in payload_text


def _preview_payload_text(payload: str | WebhookPayload) -> str:
    if isinstance(payload, str):
        return payload
    field_text = "\n".join(
        f"{field.get('name', '')}\n{field.get('value', '')}"
        for embed in payload.embeds
        for field in embed.get("fields", [])
    )
    return "\n".join([payload.content, field_text])


def _only_embed(payload: WebhookPayload) -> dict:
    assert isinstance(payload, WebhookPayload)
    assert len(payload.embeds) == 1
    return payload.embeds[0]


def _field(embed: dict, name: str) -> str:
    for field in embed["fields"]:
        if field["name"] == name:
            return field["value"]
    raise AssertionError(f"field not found: {name}")
