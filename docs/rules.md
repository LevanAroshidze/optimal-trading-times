# Rule schema reference

Rules live in `config/rules.yaml`. The engine matches rules in priority order (highest first) and uses the first rule whose `when` block evaluates to true. There must always be a fallback rule whose `when` is empty.

```yaml
- id: cpi_day                 # required, unique within the file
  priority: 95                # required, integer (higher wins)
  when:                       # optional; absent or {} means always-match
    has_cpi_today: true
  day_color: WHITE            # one of GREEN | WHITE | RED
  tradable: cautious          # one of trade | cautious | no_trade
  reason: "CPI day."          # human-readable reason shown in Discord
  session_colors:             # required; must list every supported session
    London: RED
    PreMarket: RED
    NY_AM_FIRST_HALF: WHITE
    NY_AM_SECOND_HALF: GREEN
    Lunch: GREEN
    NY_PM_FIRST_HALF: GREEN
    NY_PM_SECOND_HALF: GREEN
```

## Feature keys

These are the values your `when:` block can reference. They are computed by `CalendarFeatureBuilder` and validated at config-load time. A typo will fail loading instead of silently falling through to the fallback rule.

| Key | Type | Meaning |
| --- | --- | --- |
| `weekday` | `MON..SUN` | Three-letter weekday code in the morning timezone. |
| `week_type` | `"NFP"` or `null` | Whether the current week contains an official Friday NFP release. |
| `days_before_cpi` | int or null | Days from the target date to the next USD CPI in the same week. |
| `days_before_ppi` | int or null | Same for PPI; used by the PPI lead-up rules. |
| `has_cpi_today` | bool | True when CPI lands on the target date. |
| `has_ppi_today` | bool | Same for PPI. |
| `has_bank_holiday_today` | bool | Full USD bank/market holiday today. Time-shift calendar markers are ignored. |
| `had_bank_holiday_yesterday` | bool | Full USD bank/market holiday on the previous calendar day. Time-shift calendar markers are ignored. |
| `has_partial_holiday_today` | bool | Early-close / half-day on the calendar today. |
| `has_830_red_news` | bool | A high-impact USD release at the AM red-news time configured in `windows.am_red_news_at`, excluding CPI/PPI/Friday NFP/Federal Funds Rate/FOMC Statement/Powell strongest-category events. |
| `has_powell_10am_today` | bool | A Fed Chair Powell speech at the time configured in `windows.powell_speech_at`. |
| `days_until_powell_10am` | int or null | Number of days until the next such speech. |
| `has_pm_red_news` | bool | A high-impact PM USD Federal Funds Rate, FOMC Statement, or Powell release after `windows.pm_news_after_hour`. |
| `has_no_major_usd_news_today` | bool | True when neither strong nor other-red USD news is on the calendar. |
| `categories_today` | list[str] | Categories detected on today's events (e.g. `["CPI", "RED_830AM"]`). |
| `strong_usd_news_today` | list[str] | Pretty-printed strongest news strings for the morning post. |
| `strong_usd_news_tomorrow` | list[str] | Same, but for tomorrow. |
| `other_red_usd_news_today` | list[str] | Other red-folder USD events today. |

Strongest-category features only use high-impact USD events. Alias excludes in `config/event_aliases.yaml` can keep lookalike events out of a category; for example, ADP Non-Farm Employment Change is excluded from official NFP classification. Official NFP also requires the localized New York event date to be Friday. For FOMC, only Federal Funds Rate and FOMC Statement are strongest-category events; FOMC Press Conference and FOMC Meeting Minutes remain ordinary red-folder USD news when high impact.

## Rule precedence and overlays

Rules match by priority, and explicit rules keep their configured session colors. The generic other-red-news pressure overlay only applies when the selected rule is `default_green_day`; explicit macro/session rules such as CPI, PPI, NFP, Powell, PM red news, and 8:30 red news are not downgraded after matching.

CPI and PPI both have two lead-up rules:

- 2 days before: every session is WHITE.
- 1 day before: London and PreMarket are GREEN, NY AM and NY PM are RED, and Lunch is GREEN.

## Operators

Within a `when` block you can use simple equality, list membership, or an operator block:

```yaml
when:
  weekday: MON                       # equality
  weekday: [TUE, WED]                # list membership (any-of)
  days_before_cpi:
    equals: 1                        # exact match (cannot combine)
    not_equals: 0
    gt: 1
    gte: 1
    lt: 5
    lte: 5
    between: [1, 3]
    in: [0, 1, 7]
  categories_today:
    contains: "CPI"
    not_contains: "POWELL"
  weekday:
    any_of:
      - equals: MON
      - equals: WED
    all_of:
      - not_equals: SUN
      - not_equals: SAT
```

Restrictions:

- `equals`, `not_equals`, `contains`, and `not_contains` cannot be combined with other operators.
- `between` requires a 2-element `[min, max]` list with `min <= max`.
- `any_of` and `all_of` recursively accept operator maps.
- Unknown operators fail loading.

## Priority guidance

- Reserve high priorities (>=120) for hard "no-trade" days (weekend, full holidays, partial holidays, NFP Thursday).
- Mid priorities (80-110) are for high-impact USD news or NFP-week patterns.
- Low priorities (1-15) are catch-all caution rules.
- Priority 0 must be reserved for the fallback rule.

If two rules share a priority and overlapping `when:` keys, `find_rule_priority_collisions` (used in tests) will flag them.

## Common pitfalls

- Forgetting `session_colors` for a session: load fails with a clear error.
- Misspelling a feature key: load fails with a clear error and lists the known keys.
- Missing fallback rule (empty `when`): load fails.
- Using `equals: false` and expecting it to match feature absence: it won't, because `actual is None` short-circuits in operator evaluation. Use `not_equals: true` if you need that behavior.
