from __future__ import annotations

from typing import Any

from bot.config import MessagesConfig
from bot.models import (
    SESSION_LABELS,
    SESSION_ORDER,
    DailyDecision,
    DailyOutlook,
    DayColor,
    SessionName,
    WeeklyOutlook,
)
from bot.webhook_sender import WebhookPayload

_DAY_COLOR_HEX = {
    DayColor.GREEN: 0x57F287,
    DayColor.WHITE: 0xE0E0E0,
    DayColor.RED: 0xED4245,
}
_SESSION_BAR_GLYPH = {
    DayColor.GREEN: "🟢",
    DayColor.WHITE: "⚪",
    DayColor.RED: "🔴",
}
_EMBED_FIELD_VALUE_LIMIT = 1024


class DiscordFormatter:
    _VISIBLE_SESSIONS = {
        SessionName.LONDON.value,
        SessionName.NY_AM_FIRST_HALF.value,
        SessionName.NY_AM_SECOND_HALF.value,
        SessionName.NY_PM_FIRST_HALF.value,
        SessionName.NY_PM_SECOND_HALF.value,
    }

    def __init__(self, messages: MessagesConfig | None = None) -> None:
        self.messages = messages or MessagesConfig()

    def format_daily(self, outlook: DailyOutlook) -> WebhookPayload:
        return self._format_outlook_embed(outlook)

    def format_update(
        self, outlook: DailyOutlook, changed_items: list[str]
    ) -> WebhookPayload:
        return self._format_outlook_embed(
            outlook,
            title="Calendar Update",
            leading_fields=[
                (
                    "Change",
                    "\n".join(changed_items) if changed_items else "Calendar changed.",
                )
            ],
        )

    def format_weekly(self, weekly: WeeklyOutlook) -> str:
        parts = [self._weekly_header(weekly)]
        macro_note_needed = False
        for outlook in weekly.days:
            summary = self._weekly_summary(outlook)
            macro_note_needed = macro_note_needed or "Macro-time entries only." in summary
            line = f"{self._day_icon(outlook.decision.day_color)} **{outlook.target_date:%a}** — {summary}"
            parts.append(line.strip())
        if macro_note_needed:
            parts.append("")
            parts.append(
                "_Macro-time = 10 minutes before through 10 minutes after the hour, "
                "e.g. 09:50-10:10._"
            )
        if weekly.source_status == "cached":
            parts.append("_Source: cached calendar data._")
        return "\n".join(parts)

    def format_daily_embed(self, outlook: DailyOutlook) -> WebhookPayload:
        return self.format_daily(outlook)

    def render_for_console(self, message: str | WebhookPayload) -> str:
        if isinstance(message, str):
            return message
        parts: list[str] = []
        if message.content:
            parts.append(message.content)
        for embed in message.embeds:
            title = embed.get("title")
            if isinstance(title, str) and title:
                parts.append(title)
            description = embed.get("description")
            if isinstance(description, str) and description:
                parts.append(description)
            fields = embed.get("fields", [])
            if isinstance(fields, list):
                for field in fields:
                    if not isinstance(field, dict):
                        continue
                    name = field.get("name")
                    value = field.get("value")
                    if isinstance(name, str) and isinstance(value, str):
                        parts.append(f"{name}\n{value}")
            footer = embed.get("footer")
            if isinstance(footer, dict):
                footer_text = footer.get("text")
                if isinstance(footer_text, str) and footer_text:
                    parts.append(footer_text)
        return "\n".join(parts)

    def _format_outlook_embed(
        self,
        outlook: DailyOutlook,
        *,
        title: str | None = None,
        leading_fields: list[tuple[str, str]] | None = None,
    ) -> WebhookPayload:
        embed: dict[str, Any] = {
            "title": title or self._embed_title(outlook),
            "color": _DAY_COLOR_HEX[outlook.decision.day_color],
            "fields": [],
            "footer": {"text": self._source_footer(outlook.source_status)},
        }

        for name, value in leading_fields or []:
            embed["fields"].append(
                {"name": name, "value": self._trim_field_value(value), "inline": False}
            )

        for label, sessions in (
            ("🟢 Focus", outlook.decision.allowed_sessions),
            ("⚪ Caution", outlook.decision.caution_sessions),
            ("🔴 Avoid", outlook.decision.blocked_sessions),
        ):
            visible = [
                session for session in sessions if session in self._VISIBLE_SESSIONS
            ]
            if visible:
                embed["fields"].append(
                    {
                        "name": label,
                        "value": self._trim_field_value(
                            self._join_sessions_compact(visible)
                        ),
                        "inline": False,
                    }
                )

        calendar_lines = self._calendar_lines(outlook)
        if calendar_lines:
            embed["fields"].append(
                {
                    "name": "Calendar",
                    "value": self._trim_field_value("\n".join(calendar_lines)),
                    "inline": False,
                }
            )
        quiet_note = self._quiet_note(outlook)
        if quiet_note:
            embed["fields"].append(
                {
                    "name": "Note",
                    "value": self._trim_field_value(quiet_note),
                    "inline": False,
                }
            )

        return WebhookPayload(content="", embeds=[embed])

    def session_bar(self, decision: DailyDecision) -> str:
        glyphs: list[str] = []
        for session in SESSION_ORDER:
            if session not in self._VISIBLE_SESSIONS:
                continue
            color = decision.session_colors.get(session, DayColor.GREEN)
            glyphs.append(f"{_SESSION_BAR_GLYPH[color]} {SESSION_LABELS[session]}")
        return "  ".join(glyphs)

    def _embed_title(self, outlook: DailyOutlook) -> str:
        title_by_rule = {
            "calendar_unavailable": "Calendar Unavailable",
            "weekend_no_trade": "Weekend",
            "bank_holiday_no_trade": "USD Bank Holiday",
            "partial_holiday_day": "Partial Holiday",
            "nfp_week_thursday": "NFP Week Thursday",
            "nfp_week_friday": "NFP Day",
            "nfp_week_wednesday": "NFP Week Wednesday",
            "nfp_week_tuesday": "NFP Week Tuesday",
            "nfp_week_monday": "NFP Week Monday",
            "cpi_day": "CPI Day",
            "ppi_day": "PPI Day",
            "cpi_minus_1": "1 Day Before CPI",
            "cpi_minus_2": "2 Days Before CPI",
            "powell_10am_previous_day": "Powell Tomorrow",
            "powell_10am_today": "Powell Today",
            "pm_red_news_today": "PM Red-Folder News",
            "red_830_news_today": "8:30 AM Red-Folder News",
            "post_bank_holiday_no_major_news_day": "Post-Holiday Caution",
            "no_major_usd_news_day": "No Major USD News",
            "default_green_day": "Clean Calendar Day",
        }
        if outlook.decision.rule_id in title_by_rule:
            return title_by_rule[outlook.decision.rule_id]
        return outlook.summary_reason.split(".", maxsplit=1)[0].strip() or "Trading Plan"

    def _weekly_header(self, weekly: WeeklyOutlook) -> str:
        week_end = weekly.week_start.fromordinal(weekly.week_start.toordinal() + 4)
        if weekly.week_start.month == week_end.month:
            return f"**{self.messages.week_ahead_header} | {weekly.week_start:%b} {weekly.week_start.day}-{week_end.day}**"
        return f"**{self.messages.week_ahead_header} | {weekly.week_start:%b} {weekly.week_start.day}-{week_end:%b} {week_end.day}**"

    def _weekly_summary(self, outlook: DailyOutlook) -> str:
        reason = outlook.summary_reason
        if outlook.decision.rule_id == "no_major_usd_news_day":
            return "No major USD news. Macro-time entries only."
        if (
            outlook.quiet_note
            and "macro-time" in outlook.quiet_note.casefold()
            and reason.casefold().startswith("no major usd news")
        ):
            return "No major USD news. Macro-time entries only."
        if outlook.quiet_note and "macro-time" in outlook.quiet_note.casefold():
            reason = f"{reason} Macro-time entries only."

        if outlook.strongest_news_today:
            return f"{reason} {self._join_items(self._short_news_items(outlook.strongest_news_today))}."
        if outlook.other_red_news_today:
            return (
                f"{reason} Other red news: "
                f"{self._join_items(self._short_news_items(outlook.other_red_news_today))}."
            )
        return reason

    def _day_icon(self, day_color: DayColor) -> str:
        return _SESSION_BAR_GLYPH[day_color]

    def _source_footer(self, source_status: str) -> str:
        if source_status == "live":
            return "Source: live calendar"
        if source_status == "cached":
            return "Source: cached calendar data"
        return "Source: calendar unavailable - fail-safe plan"

    def _quiet_note(self, outlook: DailyOutlook) -> str | None:
        if not outlook.quiet_note:
            return None
        prefix = "No major USD news."
        if (
            outlook.summary_reason.casefold().startswith("no major usd news")
            and outlook.quiet_note.casefold().startswith(prefix.casefold())
        ):
            return outlook.quiet_note[len(prefix) :].strip()
        return outlook.quiet_note

    def _calendar_lines(self, outlook: DailyOutlook) -> list[str]:
        lines: list[str] = []
        if outlook.source_status == "unavailable":
            lines.append("Calendar unavailable. Posting the fail-safe plan.")
        if outlook.strongest_news_today:
            line = self._join_items(outlook.strongest_news_today)
            line += f"\n{self.messages.strong_news_advice}"
            lines.append(line)
        if outlook.other_red_news_today:
            lines.append(
                "Other red news: "
                f"{self._join_items(outlook.other_red_news_today)}.\n"
                f"{self.messages.other_red_news_advice}"
            )
        if outlook.strongest_news_tomorrow:
            lines.append(
                "Tomorrow: "
                f"{self._join_items(outlook.strongest_news_tomorrow)}."
            )
        if not lines and outlook.summary_reason.casefold().startswith(
            "no major usd news"
        ):
            lines.append("No major USD news.")
        return lines

    def _join_sessions_compact(self, sessions: list[str]) -> str:
        labels = self._collapsed_session_labels_compact(sessions)
        return ", ".join(labels)

    def _collapsed_session_labels_compact(self, sessions: list[str]) -> list[str]:
        session_set = set(sessions)
        labels: list[str] = []

        for session in sessions:
            if session == SessionName.NY_AM_FIRST_HALF.value and (
                SessionName.NY_AM_SECOND_HALF.value in session_set
            ):
                labels.append("NY AM")
                continue
            if session == SessionName.NY_AM_SECOND_HALF.value and (
                SessionName.NY_AM_FIRST_HALF.value in session_set
            ):
                continue
            if session == SessionName.NY_PM_FIRST_HALF.value and (
                SessionName.NY_PM_SECOND_HALF.value in session_set
            ):
                labels.append("NY PM")
                continue
            if session == SessionName.NY_PM_SECOND_HALF.value and (
                SessionName.NY_PM_FIRST_HALF.value in session_set
            ):
                continue
            labels.append(
                {
                    SessionName.LONDON.value: "London",
                    SessionName.NY_AM_FIRST_HALF.value: "NY AM first half",
                    SessionName.NY_AM_SECOND_HALF.value: "NY AM second half",
                    SessionName.NY_PM_FIRST_HALF.value: "NY PM first half",
                    SessionName.NY_PM_SECOND_HALF.value: "NY PM second half",
                }.get(session, session)
            )
        return labels

    def _short_news_items(self, items: list[str]) -> list[str]:
        return [item.replace(" NY time", "") for item in items]

    def _join_items(self, items: list[str]) -> str:
        if len(items) == 1:
            return items[0]
        return ", ".join(items[:-1]) + f" and {items[-1]}"

    def _trim_field_value(self, value: str) -> str:
        if len(value) <= _EMBED_FIELD_VALUE_LIMIT:
            return value
        suffix = "\n..."
        return value[: _EMBED_FIELD_VALUE_LIMIT - len(suffix)].rstrip() + suffix
