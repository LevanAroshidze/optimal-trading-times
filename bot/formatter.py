from __future__ import annotations

from bot.models import DailyDecision, DayColor, TradableStatus


class DiscordFormatter:
    def format(self, decision: DailyDecision) -> str:
        headline = self._headline(decision)
        parts = [f"{headline} {decision.reason}".strip()]

        if decision.allowed_sessions:
            parts.append(f"Allowed: {self._join_sessions(decision.allowed_sessions)}.")
        if decision.caution_sessions:
            parts.append(f"Caution: {self._join_sessions(decision.caution_sessions)}.")
        if decision.blocked_sessions:
            parts.append(f"Avoid: {self._join_sessions(decision.blocked_sessions)}.")

        if decision.source_status != "live":
            parts.append("Source: calendar unavailable, fail-safe mode.")

        return "\n".join(parts)

    def _headline(self, decision: DailyDecision) -> str:
        if decision.tradable == TradableStatus.NO_TRADE or decision.day_color == DayColor.RED:
            return "No trade today."
        if decision.tradable == TradableStatus.CAUTION or decision.day_color == DayColor.WHITE:
            return "Caution today."
        return "Trade allowed today."

    def _join_sessions(self, sessions: list[str]) -> str:
        if len(sessions) == 1:
            return sessions[0]
        return ", ".join(sessions[:-1]) + f" and {sessions[-1]}"

