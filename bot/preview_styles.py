from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from bot.formatter import DiscordFormatter
from bot.models import (
    SESSION_ORDER,
    DailyDecision,
    DailyOutlook,
    DayColor,
    TradableStatus,
    WeeklyOutlook,
)
from bot.webhook_sender import WebhookPayload

PREVIEW_PREFIX = "[STYLE PREVIEW - NOT A REAL TRADING OUTLOOK]"


@dataclass(frozen=True)
class PreviewMessage:
    style: str
    kind: str
    payload: str | WebhookPayload


_DAY_COLOR_HEX = {
    DayColor.GREEN: 0x57F287,
    DayColor.WHITE: 0xE0E0E0,
    DayColor.RED: 0xED4245,
}


def build_preview_messages() -> list[PreviewMessage]:
    samples = _sample_outlooks()
    return [
        *_text_color_previews(samples),
        *_small_icon_previews(samples),
        *_plain_label_previews(samples),
    ]


def _text_color_previews(samples: dict[str, DailyOutlook]) -> list[PreviewMessage]:
    style = "Style 1: Text + Color Only"
    return [
        PreviewMessage(
            style=style,
            kind="daily",
            payload=_embed_payload(
                style=style,
                kind="Daily",
                title="CPI Day",
                color=DayColor.WHITE,
                fields=[
                    (
                        "Plan",
                        "Focus: NY AM second half, NY PM\n"
                        "Caution: NY AM first half\n"
                        "Avoid: London",
                    ),
                    (
                        "Calendar",
                        "CPI at 8:30 AM NY time\n"
                        "Wait 15-30 minutes after the release before looking for trades.",
                    ),
                ],
                footer="Source: live calendar",
            ),
        ),
        PreviewMessage(
            style=style,
            kind="update",
            payload=_embed_payload(
                style=style,
                kind="Update",
                title="Calendar Update",
                color=DayColor.WHITE,
                fields=[
                    ("Change", "Added strongest USD news: CPI at 8:30 AM NY time"),
                    (
                        "Plan",
                        "Focus: NY AM second half, NY PM\n"
                        "Caution: NY AM first half\n"
                        "Avoid: London",
                    ),
                    ("Calendar", "CPI at 8:30 AM NY time"),
                ],
                footer="Source: live calendar",
            ),
        ),
        PreviewMessage(
            style=style,
            kind="weekly",
            payload=_weekly_markdown(style, samples, icon_mode=False),
        ),
    ]


def _small_icon_previews(samples: dict[str, DailyOutlook]) -> list[PreviewMessage]:
    style = "Style 2: Small Icons"
    formatter = DiscordFormatter()
    daily = _with_preview_content(
        formatter.format_daily(_sample_daily_outlook()),
        style,
        "Daily",
    )
    update = _with_preview_content(
        formatter.format_update(
            _sample_daily_outlook(),
            ["Added CPI at 8:30 AM NY time"],
        ),
        style,
        "Update",
    )
    weekly = _with_preview_text(
        formatter.format_weekly(_sample_weekly_outlook(samples)),
        style,
        "Weekly",
    )
    return [
        PreviewMessage(
            style=style,
            kind="daily",
            payload=daily,
        ),
        PreviewMessage(
            style=style,
            kind="update",
            payload=update,
        ),
        PreviewMessage(
            style=style,
            kind="weekly",
            payload=weekly,
        ),
    ]


def _plain_label_previews(samples: dict[str, DailyOutlook]) -> list[PreviewMessage]:
    style = "Style 3: Plain Labels"
    daily = "\n".join(
        [
            PREVIEW_PREFIX,
            f"**{style} | Daily**",
            "",
            "**CPI Day**",
            "Focus: NY AM second half, NY PM",
            "Caution: NY AM first half",
            "Avoid: London",
            "",
            "Calendar: CPI at 8:30 AM NY time",
            "Wait 15-30 minutes after the release before looking for trades.",
        ]
    )
    update = "\n".join(
        [
            PREVIEW_PREFIX,
            f"**{style} | Update**",
            "",
            "**Calendar Update**",
            "Change: Added CPI at 8:30 AM NY time",
            "Focus: NY AM second half, NY PM",
            "Caution: NY AM first half",
            "Avoid: London",
        ]
    )
    return [
        PreviewMessage(style=style, kind="daily", payload=daily),
        PreviewMessage(style=style, kind="update", payload=update),
        PreviewMessage(
            style=style,
            kind="weekly",
            payload=_weekly_markdown(style, samples, icon_mode=False),
        ),
    ]


def _embed_payload(
    *,
    style: str,
    kind: str,
    title: str,
    color: DayColor,
    fields: list[tuple[str, str]],
    footer: str,
) -> WebhookPayload:
    return WebhookPayload(
        content=f"{PREVIEW_PREFIX}\n**{style} | {kind}**",
        embeds=[
            {
                "title": title,
                "color": _DAY_COLOR_HEX[color],
                "fields": [
                    {"name": name, "value": value, "inline": False}
                    for name, value in fields
                ],
                "footer": {"text": footer},
            }
        ],
    )


def _with_preview_content(
    payload: WebhookPayload, style: str, kind: str
) -> WebhookPayload:
    return WebhookPayload(
        content=f"{PREVIEW_PREFIX}\n**{style} | {kind}**",
        embeds=list(payload.embeds),
    )


def _with_preview_text(text: str, style: str, kind: str) -> str:
    return "\n".join([PREVIEW_PREFIX, f"**{style} | {kind}**", "", text])


def _weekly_markdown(
    style: str, samples: dict[str, DailyOutlook], *, icon_mode: bool
) -> str:
    weekly = WeeklyOutlook(
        week_start=date(2026, 5, 11),
        days=[
            samples["monday"],
            samples["tuesday"],
            samples["wednesday"],
            samples["thursday"],
            samples["friday"],
        ],
    )
    lines = [
        PREVIEW_PREFIX,
        f"**{style} | Weekly**",
        "",
        "**Week Ahead | May 11-15**",
        "",
    ]
    for outlook in weekly.days:
        prefix = _weekly_prefix(outlook) if icon_mode else ""
        lines.append(f"{prefix}**{outlook.target_date:%a}** — {_weekly_summary(outlook)}")
    lines.extend(
        [
            "",
            "_Macro-time = 10 minutes before through 10 minutes after the hour, "
            "e.g. 09:50-10:10._",
        ]
    )
    return "\n".join(lines)


def _sample_weekly_outlook(samples: dict[str, DailyOutlook]) -> WeeklyOutlook:
    return WeeklyOutlook(
        week_start=date(2026, 5, 11),
        days=[
            samples["monday"],
            samples["tuesday"],
            samples["wednesday"],
            samples["thursday"],
            samples["friday"],
        ],
    )


def _weekly_prefix(outlook: DailyOutlook) -> str:
    return {
        DayColor.GREEN: "🟢 ",
        DayColor.WHITE: "⚪ ",
        DayColor.RED: "🔴 ",
    }[outlook.decision.day_color]


def _weekly_summary(outlook: DailyOutlook) -> str:
    if outlook.decision.rule_id in {"no_major_usd_news_day", "cpi_minus_1"}:
        if outlook.decision.rule_id == "cpi_minus_1":
            return "1 day before CPI. Avoid NY AM and NY PM. Macro-time entries only."
        return "No major USD news. Macro-time entries only."
    if outlook.strongest_news_today:
        return (
            f"{outlook.summary_reason} "
            f"{_trim_news_label(outlook.strongest_news_today[0])}."
        )
    if outlook.other_red_news_today:
        return (
            f"{outlook.summary_reason} "
            f"{_join_items([_trim_news_label(item) for item in outlook.other_red_news_today])}."
        )
    return outlook.summary_reason


def _trim_news_label(value: str) -> str:
    return value.replace(" NY time", "")


def _sample_outlooks() -> dict[str, DailyOutlook]:
    return {
        "monday": DailyOutlook(
            decision=_decision(
                "cpi_minus_1",
                date(2026, 5, 11),
                DayColor.WHITE,
                "1 day before CPI. Avoid NY AM and NY PM.",
            ),
            summary_reason="1 day before CPI. Avoid NY AM and NY PM.",
            quiet_note=(
                "No major USD news. On NY AM and NY PM, only consider macro-time "
                "entries 10 minutes before through 10 minutes after the hour, "
                "for example 09:50-10:10."
            ),
        ),
        "tuesday": DailyOutlook(
            decision=_decision("cpi_day", date(2026, 5, 12), DayColor.WHITE, "CPI day."),
            summary_reason="CPI day.",
            strongest_news_today=["CPI at 8:30 AM NY time"],
        ),
        "wednesday": DailyOutlook(
            decision=_decision("ppi_day", date(2026, 5, 13), DayColor.WHITE, "PPI day."),
            summary_reason="PPI day.",
            strongest_news_today=["PPI at 8:30 AM NY time"],
        ),
        "thursday": DailyOutlook(
            decision=_decision(
                "red_830_news_today",
                date(2026, 5, 14),
                DayColor.WHITE,
                "8:30am red-folder news today.",
            ),
            summary_reason="8:30am red-folder news today.",
            other_red_news_today=[
                "Core Retail Sales m/m at 8:30 AM NY time",
                "Retail Sales m/m at 8:30 AM NY time",
            ],
        ),
        "friday": DailyOutlook(
            decision=_decision(
                "no_major_usd_news_day",
                date(2026, 5, 15),
                DayColor.WHITE,
                "No major USD news today.",
            ),
            summary_reason="No major USD news today.",
            quiet_note=(
                "No major USD news. On NY AM and NY PM, only consider macro-time "
                "entries 10 minutes before through 10 minutes after the hour, "
                "for example 09:50-10:10."
            ),
        ),
    }


def _sample_daily_outlook() -> DailyOutlook:
    return DailyOutlook(
        decision=DailyDecision(
            rule_id="cpi_day",
            target_date=date(2026, 5, 12),
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
        ),
        summary_reason="CPI day.",
        strongest_news_today=["CPI at 8:30 AM NY time"],
    )


def _decision(
    rule_id: str, target_date: date, day_color: DayColor, reason: str
) -> DailyDecision:
    return DailyDecision(
        rule_id=rule_id,
        target_date=target_date,
        day_color=day_color,
        tradable=TradableStatus.CAUTION,
        session_colors={session: DayColor.WHITE for session in SESSION_ORDER},
        reason=reason,
    )


def _join_items(items: list[str]) -> str:
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + f" and {items[-1]}"
