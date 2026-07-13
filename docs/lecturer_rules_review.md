# Trading Session Bot Rules Review

This document describes the rules currently implemented by the bot in a human-readable format. It is intended for review by a lecturer or trading mentor.

## 1. What The Bot Does

The bot reads the Forex Factory USD calendar, classifies the trading day, applies a priority-based rule matrix, and posts Discord messages for:

- daily outlook: 09:00 Asia/Tbilisi;
- same-day New York recheck: 08:00 America/New_York, only if the morning post already exists for that exact date and the calendar meaningfully changed;
- weekly outlook: Sunday 21:00 Asia/Tbilisi for the coming Monday-Friday week.

The bot does not place trades, scan charts, or produce buy/sell signals. It only gives a calendar/session risk plan.

## 2. Color And Trade Status Meaning

| Color | Bot Status | Meaning |
| --- | --- | --- |
| GREEN | trade | Normal trading conditions according to the calendar rules. |
| WHITE | cautious | Caution or selective trading conditions. |
| RED | no_trade or blocked session | Avoid trading that day/session according to the calendar rules. |

## 3. Session Windows

All session windows are evaluated in `America/New_York` time.

| Session Key | Readable Name | Time |
| --- | --- | --- |
| London | London | 03:00-07:00 |
| PreMarket | Pre-market | 07:00-08:30 |
| NY_AM_FIRST_HALF | NY AM first half | 08:30-10:30 |
| NY_AM_SECOND_HALF | NY AM second half | 10:30-12:00 |
| Lunch | Lunch | 12:00-13:30 |
| NY_PM_FIRST_HALF | NY PM first half | 13:30-15:00 |
| NY_PM_SECOND_HALF | NY PM second half | 15:00-17:00 |

## 4. Rule Matching Method

The bot computes calendar features for the target date, then checks all rules by priority.

- Higher priority wins.
- The first matching rule is used.
- A fallback rule always exists at priority `0`.
- Explicit rules keep their configured session colors. The generic other-red-news pressure overlay only applies when the fallback `default_green_day` rule matched.

This means a higher-priority rule can override a lower-priority rule even if both are true.

## 5. Event Classification

The bot only lets high-impact USD events drive strongest-category rules.

### Strongest Categories

These categories can drive the strongest calendar rules:

| Category | Matched Titles |
| --- | --- |
| NFP | `non-farm employment change` |
| CPI | `cpi`, `consumer price index` |
| PPI | `ppi`, `producer price index` |
| FOMC | `federal funds rate`, `fomc statement` |
| POWELL | `fed chair warsh speaks`, `fed chairman warsh speaks`, `fed chair warsh testifies`, `fed chairman warsh testifies` |

Important NFP detail:

- ADP is excluded from NFP classification.
- `ADP Non-Farm Employment Change` does not make the week an NFP week.
- Official `Non-Farm Employment Change` counts as NFP only when the localized New York event date is Friday.
- If ADP or a non-Friday `Non-Farm Employment Change` is high-impact USD, it is treated as ordinary red-folder USD news, not official NFP.

Important FOMC detail:

- `Federal Funds Rate` and `FOMC Statement` are the real FOMC events.
- `FOMC Press Conference` and `FOMC Meeting Minutes` are not real FOMC for strongest-category or PM-red rules.
- If press conferences or meeting minutes are high-impact USD, they are treated as ordinary red-folder USD news.

### Partial Holiday Terms

An event is treated as a partial holiday or early close when the title contains:

- `early close`
- `early market close`
- `half day`
- `half-day`
- `early bond market close`
- `early stock market close`

Full USD holidays are limited to bank/market holiday titles such as `bank holiday`, `Thanksgiving Day`, `Christmas Day`, `Independence Day`, `Good Friday`, and similar named US closures. Time-shift markers such as `Daylight Saving Time Shift` are ignored.

### 8:30 AM Red-News Rule

The 8:30 AM red-news rule triggers for any high-impact USD event at the configured 08:30 New York time that is not one of the strongest-category events: CPI, PPI, official Friday NFP, Federal Funds Rate, FOMC Statement, or Warsh.

### PM Red-News Aliases

The PM red-news rule can trigger when a high-impact USD event after 14:00 New York time matches one of these titles:

- `federal funds rate`
- `fomc statement`
- `fed chair warsh speaks`
- `fed chairman warsh speaks`
- `fed chair warsh testifies`
- `fed chairman warsh testifies`

## 6. Current Rule Matrix

Legend for session columns:

- `G` = GREEN
- `W` = WHITE
- `R` = RED

| Priority | Rule ID | Trigger | Day | Status | London | PreMkt | AM1 | AM2 | Lunch | PM1 | PM2 | Reason |
| ---: | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 130 | weekend_no_trade | Saturday or Sunday | RED | no_trade | R | R | R | R | R | R | R | Weekend. Markets closed for this playbook. |
| 125 | bank_holiday_no_trade | Full USD bank/market holiday today | RED | no_trade | R | R | R | R | R | R | R | USD bank holiday. |
| 124 | partial_holiday_day | Partial holiday or early-close day | RED | no_trade | R | R | R | R | R | R | R | Partial holiday or early close day. |
| 123 | post_bank_holiday_no_major_news_day | Day after a full USD bank holiday with no red-folder USD news today | RED | no_trade | R | R | R | R | R | R | R | Day after a USD bank holiday with no red-folder USD news. |
| 120 | nfp_week_thursday | Current week is NFP week and today is Thursday | RED | no_trade | R | R | R | R | R | R | R | NFP week Thursday. |
| 115 | nfp_week_friday | Current week is NFP week and today is Friday | WHITE | cautious | R | R | W | G | G | G | G | NFP day. Wait 15-30 minutes after the 8:30 release before treating NY AM as green. |
| 110 | nfp_week_wednesday | Current week is NFP week and today is Wednesday | WHITE | cautious | G | G | G | W | R | R | R | NFP week Wednesday. |
| 105 | nfp_week_tuesday | Current week is NFP week and today is Tuesday | GREEN | trade | G | G | G | G | G | G | G | NFP week Tuesday. |
| 100 | nfp_week_monday | Current week is NFP week and today is Monday | GREEN | trade | G | G | W | W | G | G | G | NFP week Monday. |
| 95 | cpi_day | High-impact USD CPI today | WHITE | cautious | R | R | W | G | G | G | G | CPI day. |
| 94 | ppi_day | High-impact USD PPI today | WHITE | cautious | R | R | W | G | G | G | G | PPI day. |
| 93 | cpi_minus_1 | CPI is 1 day away in the same week | WHITE | cautious | G | G | R | R | G | R | R | 1 day before CPI. Avoid NY AM and NY PM. |
| 92 | cpi_minus_2 | CPI is 2 days away in the same week | WHITE | cautious | W | W | W | W | W | W | W | 2 days before CPI. Mid conditions. |
| 91 | ppi_minus_1 | PPI is 1 day away in the same week | WHITE | cautious | G | G | R | R | G | R | R | 1 day before PPI. Avoid NY AM and NY PM. |
| 90 | ppi_minus_2 | PPI is 2 days away in the same week | WHITE | cautious | W | W | W | W | W | W | W | 2 days before PPI. Mid conditions. |
| 85 | powell_10am_previous_day | Warsh speaks at the configured Fed-chair speech time tomorrow | WHITE | cautious | W | W | W | W | W | R | R | Warsh speaks tomorrow. |
| 84 | powell_10am_today | Warsh speaks at the configured Fed-chair speech time today | WHITE | cautious | R | R | R | G | G | G | G | Warsh speaks today. |
| 80 | pm_red_news_today | High-impact PM red-folder USD event after 14:00 today | WHITE | cautious | G | G | R | R | R | R | G | PM red-folder news today. |
| 70 | red_830_news_today | High-impact USD non-strong red-folder event at 08:30 today | WHITE | cautious | W | R | W | G | G | G | G | 8:30am red-folder news today. |
| 5 | no_major_usd_news_day | No strongest or other red-folder USD news today | WHITE | cautious | G | G | W | W | G | W | W | No major USD news today. |
| 0 | default_green_day | Fallback when no other rule matches | GREEN | trade | G | G | G | G | G | G | G | No blocking calendar condition. |

On no-major-USD-news days, the Discord note says NY AM and NY PM should only be considered around macro times: 10 minutes before through 10 minutes after the hour, for example `09:50-10:10`.

## 7. Other Red-Folder USD News Overlay

After the base rule is selected, the bot separately checks for high-impact USD events that are not strongest-category events and are not partial holidays. This overlay only changes the session map when the selected rule is `default_green_day`; for explicit rules, those events are listed for awareness but do not downgrade the configured sessions.

For those events, it applies a pressure overlay:

- pressure window starts 180 minutes before the event;
- any session fully inside that window becomes RED;
- any session containing the event time becomes RED;
- any partly affected session becomes WHITE;
- if multiple overlays apply, the stricter color wins.

This overlay can make a clean fallback day stricter, but it does not make sessions more permissive and does not override explicit macro/session rules.

Example:

- A 10:00 high-impact USD ISM Services PMI is not CPI/PPI/NFP/FOMC/Warsh.
- It is treated as other red-folder USD news.
- The 180-minute pressure window starts at 07:00.
- PreMarket and the first NY AM segment can become stricter for 10:00 releases even if the base rule was otherwise green or white.
- For any 08:30 high-impact USD release that is not CPI/PPI/NFP/FOMC/Warsh, the named 8:30 rule applies: London and NY AM first half are WHITE, PreMarket is RED, and later sessions are GREEN.

## 8. Calendar Source And Cache Rules

The bot fetches Forex Factory JSON calendar data from:

- `https://nfs.faireconomy.media/ff_calendar_thisweek.json`

Calendar failure behavior:

- If live calendar fetch succeeds, the bot stores the successful events as last-known-good cache.
- If live calendar fetch fails, the bot can use cached events for up to `72` hours.
- Cached Discord posts are visibly marked as cached.
- If the Sunday weekly outlook cannot find the coming week in the default JSON feed, the bot fetches the date-specific Forex Factory calendar page for the target Monday and uses that page's JSON export.
- Forex Factory export rate limits and missing weekly data create state-backed retry cooldowns so the 15-minute cron does not keep requesting the same unavailable export.
- If live calendar fetch fails and no fresh cache exists:
  - daily post becomes a RED fail-safe day;
  - weekly outlook is skipped instead of inventing a week plan;
  - ops alert can be sent if `DISCORD_OPS_WEBHOOK_URL` is configured.

## 9. Weekend Posting Policy

The configured policy is:

```yaml
post_on_weekend: false
```

This means:

- automatic Saturday/Sunday 09:00 daily posts are suppressed;
- automatic Saturday/Sunday New York rechecks are suppressed;
- a Sunday 21:00 Asia/Tbilisi weekly outlook is still allowed because it is the future-week overview.

Rechecks also require a same-date daily baseline:

- the bot only sends an automatic recheck if `state.daily.posted_for_date` exactly matches the recheck target date;
- stale weekday fingerprints cannot trigger weekend rechecks.

## 10. Day After Full USD Bank Holiday

If yesterday was a full USD bank holiday and today has no red-folder USD news, every session is RED/no_trade. This rule overrides future setup rules such as CPI/PPI/Warsh tomorrow or NFP-week setup pressure. If today has strongest USD news or other high-impact red-folder USD news, the bot ignores the post-holiday quiet rule and applies the normal news rule.

## 11. Reviewer Questions

These are the main questions worth asking a lecturer:

- Should NFP Thursday always be full RED, or should any session remain tradable?
- Should NFP Friday become green immediately after the 08:30 release, or should there be a longer wait period?
- Should CPI/PPI days block only London and pre-market, or should the first NY AM segment be stricter?
- Should the day before CPI always block both NY AM and NY PM?
- Should all no-major-news days be WHITE/cautious, or should some be GREEN by default?
- Should 10:00 red-folder releases such as ISM/JOLTS have a named rule instead of relying only on the pressure overlay?
- Should Warsh/FOMC PM events block more or less of NY PM?
- Should partial holidays remain hard no-trade days even if other news is also on the calendar?
