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
        "not_equals": ScalarValue,
        "gt": int,
        "gte": int,
        "lt": int,
        "lte": int,
        "between": "list[int]",
        "in": "list[ScalarValue]",
        "contains": str,
        "not_contains": str,
        "any_of": "list[RuleOperatorMap]",
        "all_of": "list[RuleOperatorMap]",
    },
    total=False,
)


RuleConditionValue: TypeAlias = FeatureValue | RuleOperatorMap
FeatureMap: TypeAlias = dict[str, FeatureValue]
RuleConditions: TypeAlias = dict[str, RuleConditionValue]
SourceStatus: TypeAlias = Literal["live", "unavailable"]
PostKind: TypeAlias = Literal["daily", "weekly", "recheck"]


class CalendarEventSource(Protocol):
    async def fetch_events(self) -> list[CalendarEvent]: ...


class WebhookSenderProtocol(Protocol):
    async def send_message(self, content: str) -> None: ...


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
    consecutive_source_failures: int
    last_source_status: str | None


class BotStatePayload(TypedDict, total=False):
    schema_version: int
    daily: StateSection
    recheck: StateSection
    weekly: StateSection
    operations: StateSection


KNOWN_FEATURE_KEYS: frozenset[str] = frozenset(
    {
        "weekday",
        "week_type",
        "days_before_cpi",
        "days_before_ppi",
        "has_cpi_today",
        "has_ppi_today",
        "has_bank_holiday_today",
        "has_partial_holiday_today",
        "has_830_red_news",
        "has_powell_10am_today",
        "days_until_powell_10am",
        "has_pm_red_news",
        "has_no_major_usd_news_today",
        "categories_today",
        "strong_usd_news_today",
        "strong_usd_news_tomorrow",
        "other_red_usd_news_today",
    }
)
