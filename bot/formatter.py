from __future__ import annotations

from bot.models import (
    DailyDecision,
    DailyOutlook,
    DayColor,
    SESSION_LABELS,
    SessionName,
    WeeklyOutlook,
)


class DiscordFormatter:
    _VISIBLE_SESSIONS = {
        SessionName.LONDON.value,
        SessionName.NY_AM_FIRST_HALF.value,
        SessionName.NY_AM_SECOND_HALF.value,
        SessionName.NY_PM_FIRST_HALF.value,
        SessionName.NY_PM_SECOND_HALF.value,
    }

    def format_daily(self, outlook: DailyOutlook) -> str:
        parts = [self._headline(outlook.decision, outlook.summary_reason)]
        parts.extend(self._session_lines(outlook.decision))

        parts.extend(self._news_lines(outlook))

        if outlook.quiet_note:
            parts.append(outlook.quiet_note)

        if outlook.source_status != "live":
            parts.append("Calendar source unavailable. Posting the fail-safe plan.")

        return "\n".join(parts)

    def format_update(self, outlook: DailyOutlook, changed_items: list[str]) -> str:
        parts = ["Update: the calendar changed after the morning post."]
        parts.append(self._headline(outlook.decision, outlook.summary_reason))
        parts.extend(self._session_lines(outlook.decision))

        if changed_items:
            parts.append("Calendar changes:")
            parts.extend(f"- {item}" for item in changed_items)

        parts.extend(self._news_lines(outlook))
        if outlook.quiet_note:
            parts.append(outlook.quiet_note)
        return "\n".join(parts)

    def format_weekly(self, weekly: WeeklyOutlook) -> str:
        parts = ["Week Ahead"]
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
        return "\n".join(parts)

    def _headline(self, decision: DailyDecision, reason: str) -> str:
        return f"{self._day_color_label(decision.day_color)} day. {reason}".strip()

    def _day_color_label(self, day_color: DayColor) -> str:
        if day_color == DayColor.RED:
            return "Red"
        if day_color == DayColor.WHITE:
            return "White"
        return "Green"

    def _news_lines(self, outlook: DailyOutlook) -> list[str]:
        lines: list[str] = []
        if outlook.strongest_news_today:
            lines.append(
                "Strongest USD news: "
                f"{self._join_items(outlook.strongest_news_today)}. "
                "Wait 15-30 minutes after the release before looking for trades."
            )
        if outlook.other_red_news_today:
            lines.append(
                "Other red-folder USD news: "
                f"{self._join_items(outlook.other_red_news_today)}. "
                "Avoid the 2-3 hours before the release."
            )
        if outlook.strongest_news_tomorrow:
            lines.append(
                "Strongest USD news tomorrow: "
                f"{self._join_items(outlook.strongest_news_tomorrow)}."
            )
        return lines

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
