from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import TYPE_CHECKING, Literal, Protocol, TypeAlias, TypedDict

if TYPE_CHECKING:
    from bot.models import CalendarEvent, DailyOutlook, WeeklyOutlook


StructuredScalar: TypeAlias = str | int | float | bool | None
StructuredValue: TypeAlias = (
    StructuredScalar | list["StructuredValue"] | dict[str, "StructuredValue"]
)
StructuredMapping: TypeAlias = dict[str, StructuredValue]

ScalarValue: TypeAlias = str | int | bool | None
FeatureList: TypeAlias = list[str]
FeatureValue: TypeAlias = ScalarValue | FeatureList
StringItems: TypeAlias = Sequence[str]


RuleOperatorMap = TypedDict(
    "RuleOperatorMap",
    {
        "equals": ScalarValue,
        "gte": int,
        "lte": int,
        "contains": str,
    },
    total=False,
)


RuleConditionValue: TypeAlias = FeatureValue | RuleOperatorMap
FeatureMap: TypeAlias = dict[str, FeatureValue]
RuleConditions: TypeAlias = dict[str, RuleConditionValue]
SourceStatus: TypeAlias = Literal["live", "unavailable"]


class CalendarEventSource(Protocol):
    async def fetch_events(self) -> list[CalendarEvent]: ...


class SummaryServiceProtocol(Protocol):
    async def load_events(self) -> list[CalendarEvent]: ...

    def build_outlook_for_date(
        self,
        target_date: date,
        events: list[CalendarEvent],
        source_status: SourceStatus = "live",
    ) -> DailyOutlook: ...

    def build_weekly_outlook(
        self,
        week_start: date,
        events: list[CalendarEvent],
        source_status: SourceStatus = "live",
    ) -> WeeklyOutlook | None: ...

    def daily_fingerprint(self, outlook: DailyOutlook) -> str: ...


class StateSection(TypedDict, total=False):
    posted_for_date: str | None
    fingerprint: str | None
    snapshot: list[str]
    checked_for_date: str | None
    posted_for_week_start: str | None


class BotStatePayload(TypedDict, total=False):
    daily: StateSection
    recheck: StateSection
    weekly: StateSection
