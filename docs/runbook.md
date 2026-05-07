# Runbook

Common production questions and how to answer them.

## "Why didn't it post this morning?"

Check Railway (or wherever the cron runs) for the run that should have fired. The notifier emits one of these structured log lines:

| Log | Meaning |
| --- | --- |
| `notifier_idle daily_due=False ...` | Schedule not reached or already posted. |
| `daily_skipped reason=already_posted` | `state.daily.posted_for_date` already matches today. |
| `daily_skipped reason=not_due_yet` | Local time has not yet passed `09:00 Asia/Tbilisi`. |
| `daily_skipped reason=post_window_expired` | Local time is more than `policy.max_post_age_minutes` past the schedule. The bot deliberately skips stale "good morning" posts. |
| `daily_skipped reason=weekend_skip` | `policy.post_on_weekend: false` and today is Sat/Sun. |
| `calendar_unavailable consecutive_failures=N` | Calendar fetch failed; the next run will try again. |
| `calendar_fetch_skipped reason=source_retry_after` | Forex Factory asked the bot to wait; cached data is used if available. |
| `weekly_skipped reason=retry_after` | The weekly target week was missing recently; the bot waits for the hourly FF export refresh before retrying. |
| `dry_run_message kind=daily ...` | The process was invoked with `--dry-run`; nothing was sent. |

If `state.json` is gone (Railway redeploy, manual delete), and `policy.max_post_age_minutes` had already elapsed, the bot will not re-post. Trigger a manual post with `python -m bot --force-kind daily` from a session that has the right environment. If `python` is not on your local PATH, use `python3 -m bot` instead.

With `policy.post_on_weekend: false`, Saturday/Sunday daily summaries and NY rechecks are intentionally silent. The Sunday `21:00 Asia/Tbilisi` weekly outlook is separate and should still post the coming-week overview.

## "Why did it post twice?"

Discord can occasionally accept a webhook but the network blip means the process crashes before saving state. The next run sees `state.daily.posted_for_date` still empty and reposts. Mitigations:

- Mount a persistent volume so `state.json` always survives restarts.
- Set `runtime.lock_path` so two overlapping crons cannot both believe the post is due.
- Watch logs for `posted kind=daily` followed by another within minutes.

## "How do I reset state?"

Stop the cron, delete `data/state.json`, and let the next scheduled run recreate it. The bot tolerates missing state but enforces the late-post window described above.

## "Forex Factory is down. What now?"

The bot will:

1. Retry each URL up to `calendar.max_retries` times with backoff.
2. If at least one URL succeeds, partial results are used.
3. If every URL fails, fresh cached events are used and the post is marked as cached.
4. If every URL fails and no fresh cache exists, it posts the "Red day. Calendar unavailable." plan and increments `state.operations.consecutive_source_failures`.

Forex Factory's calendar export is updated hourly and can rate-limit repeated export requests. When the response includes a long retry window, the bot stores it in state and skips source fetches until that time. Monitor `consecutive_source_failures` via logs. If `DISCORD_OPS_WEBHOOK_URL` is configured, the bot sends a deduped ops alert when failures cross `policy.max_consecutive_source_failures`.

## "Why did the weekly outlook skip?"

The Sunday weekly outlook first tries the configured JSON feed. If that feed does not contain the coming week, it fetches the Forex Factory target-week calendar page and uses that page's JSON export. If neither live nor cached target-week data is available, it sends one ops alert and records a 60-minute retry cooldown so the cron does not hammer Forex Factory every 15 minutes.

## "How do I dry-run for tomorrow before changing rules?"

```bash
python -m bot --dry-run --target-date 2026-04-17 --force-kind daily
```

This builds the message in memory, prints it to stdout, and does not touch the webhook or `state.json`. Use `python3 -m bot ...` if your shell does not have a `python` command.

For a full rule/feature diagnostic view of a week:

```bash
python -m bot --diagnose-week --target-date 2026-05-04
```

## "How do I migrate state to a new schema?"

`state.json` carries `schema_version`. If you change `bot.state_store`, increment `STATE_SCHEMA_VERSION` and add a small migration in `_coerce_payload`. Old files without the field are treated as version 0 and auto-upgrade on next save. Schema v2 adds cached calendar events and deduped ops-alert keys. Schema v3 adds source and weekly retry cooldown timestamps.
