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

    def format_daily(self, outlook: DailyOutlook) -> str:
        parts = [self._headline(outlook.decision, outlook.summary_reason)]
        parts.extend(self._session_lines(outlook.decision))
        parts.extend(self._news_lines(outlook))

        if outlook.quiet_note:
            parts.append(outlook.quiet_note)

        if outlook.source_status != "live":
            parts.append(self._source_status_note(outlook.source_status))

        return "\n".join(parts)

    def format_update(self, outlook: DailyOutlook, changed_items: list[str]) -> str:
        parts = [self.messages.update_header]
        parts.append(self._headline(outlook.decision, outlook.summary_reason))
        parts.extend(self._session_lines(outlook.decision))

        if changed_items:
            parts.append("Calendar changes:")
            parts.extend(f"- {item}" for item in changed_items)

        parts.extend(self._news_lines(outlook))
        if outlook.quiet_note:
            parts.append(outlook.quiet_note)
        if outlook.source_status == "cached":
            parts.append(self.messages.calendar_cached_note)
        return "\n".join(parts)

    def format_weekly(self, weekly: WeeklyOutlook) -> str:
        parts = [self.messages.week_ahead_header]
        for outlook in weekly.days:
            line = (
                f"{outlook.target_date.strftime('%a')}: "
                f"{self._day_color_label(outlook.decision.day_color)} day. "
                f"{outlook.summary_reason}"
            )
            if outlook.strongest_news_today:
                line += f" Strongest news: {self._join_items(outlook.strongest_news_today)}."
            elif outlook.other_red_news_today:
                line += (
                    f" Other red-folder news: "
                    f"{self._join_items(outlook.other_red_news_today)}."
                )
            parts.append(line.strip())
        if weekly.source_status == "cached":
            parts.append(self.messages.calendar_cached_note)
        return "\n".join(parts)

    def format_daily_embed(self, outlook: DailyOutlook) -> WebhookPayload:
        embed: dict[str, Any] = {
            "title": self._headline(outlook.decision, outlook.summary_reason),
            "color": _DAY_COLOR_HEX[outlook.decision.day_color],
            "fields": [],
        }
        bar = self.session_bar(outlook.decision)
        if bar:
            embed["description"] = bar

        for label, sessions in (
            ("Focus", outlook.decision.allowed_sessions),
            ("Caution", outlook.decision.caution_sessions),
            ("Avoid", outlook.decision.blocked_sessions),
        ):
            visible = [
                session for session in sessions if session in self._VISIBLE_SESSIONS
            ]
            if visible:
                embed["fields"].append(
                    {
                        "name": label,
                        "value": self._join_sessions(visible),
                        "inline": False,
                    }
                )

        news_lines = self._news_lines(outlook)
        if news_lines:
            embed["fields"].append(
                {"name": "News", "value": "\n".join(news_lines), "inline": False}
            )
        if outlook.quiet_note:
            embed["fields"].append(
                {"name": "Note", "value": outlook.quiet_note, "inline": False}
            )

        if outlook.source_status != "live":
            embed["footer"] = {"text": self._source_status_note(outlook.source_status)}

        return WebhookPayload(content="", embeds=[embed])

    def session_bar(self, decision: DailyDecision) -> str:
        glyphs: list[str] = []
        for session in SESSION_ORDER:
            if session not in self._VISIBLE_SESSIONS:
                continue
            color = decision.session_colors.get(session, DayColor.GREEN)
            glyphs.append(f"{_SESSION_BAR_GLYPH[color]} {SESSION_LABELS[session]}")
        return "  ".join(glyphs)

    def _headline(self, decision: DailyDecision, reason: str) -> str:
        return f"{self._day_color_label(decision.day_color)} day. {reason}".strip()

    def _day_color_label(self, day_color: DayColor) -> str:
        return self.messages.day_color_labels.get(day_color.value, day_color.value)

    def _news_lines(self, outlook: DailyOutlook) -> list[str]:
        lines: list[str] = []
        if outlook.strongest_news_today:
            lines.append(
                "Strongest USD news: "
                f"{self._join_items(outlook.strongest_news_today)}. "
                f"{self.messages.strong_news_advice}"
            )
        if outlook.other_red_news_today:
            lines.append(
                "Other red-folder USD news: "
                f"{self._join_items(outlook.other_red_news_today)}. "
                f"{self.messages.other_red_news_advice}"
            )
        if outlook.strongest_news_tomorrow:
            lines.append(
                "Strongest USD news tomorrow: "
                f"{self._join_items(outlook.strongest_news_tomorrow)}."
            )
        return lines

    def _source_status_note(self, source_status: str) -> str:
        if source_status == "cached":
            return self.messages.calendar_cached_note
        return self.messages.calendar_unavailable_note

    def _session_lines(self, decision: DailyDecision) -> list[str]:
        lines: list[str] = []
        session_groups = (
            ("Focus", decision.allowed_sessions),
            ("Caution", decision.caution_sessions),
            ("Avoid", decision.blocked_sessions),
        )
        for label, sessions in session_groups:
            visible_sessions = [
                session for session in sessions if session in self._VISIBLE_SESSIONS
            ]
            if visible_sessions:
                lines.append(f"{label}: {self._join_sessions(visible_sessions)}.")
        return lines

    def _join_sessions(self, sessions: list[str]) -> str:
        labels = self._collapsed_session_labels(sessions)
        return self._join_items(labels)

    def _collapsed_session_labels(self, sessions: list[str]) -> list[str]:
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
            labels.append(SESSION_LABELS.get(session, session))
        return labels

    def _join_items(self, items: list[str]) -> str:
        if len(items) == 1:
            return items[0]
        return ", ".join(items[:-1]) + f" and {items[-1]}"
