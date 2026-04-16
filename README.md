# Optimal Trade Sessions Announcer

Discord webhook announcer that reads the Forex Factory calendar, applies the YAML trading matrix, and posts:

- one morning daily outlook
- one Sunday night weekly outlook
- one same-day recheck update only if the calendar meaningfully changed

The app stays calendar-first, USD-only, and session-based.

## Current behavior

- Pulls Forex Factory calendar events from JSON
- Separates `Strongest USD news` from `Other red-folder USD news`
- Applies your matrix rules from `config/rules.yaml`
- Treats `no major USD news` days as their own white/caution model
- Distinguishes full USD holidays from partial holiday / early-close days
- Applies non-core red-news pressure to affected sessions conservatively
- Suppresses duplicate posts across reruns by storing runtime state in `data/state.json`
- Posts trader-style Discord messages through a webhook

## What it does not do

- No broker integration
- No auto-trading
- No chart scanning
- No intraday buy/sell alerts
- No slash commands in this version

## Setup

1. Install Python 3.12+
2. Install dependencies:

```powershell
python -m pip install -e .[dev]
```

3. Create a Discord webhook in the channel where you want announcements:

- Open the channel
- Edit Channel
- Integrations
- Webhooks
- New Webhook
- Copy the webhook URL

4. Set environment variables:

```powershell
$env:DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."
```

5. Review and edit:

- `config/app.yaml`
- `config/event_aliases.yaml`
- `config/rules.yaml`

6. Run once locally:

```powershell
python -m bot.main
```

## Railway cron

Deploy this repo to Railway and set `DISCORD_WEBHOOK_URL` in Railway variables.

This version is meant to run every 15 minutes and decide internally whether it is time to post. Use:

```bash
python -m bot.main
```

Recommended cron:

```text
*/15 * * * *
```

The app will internally gate these schedules:

- Morning summary: `09:00 Asia/Tbilisi`
- NY recheck: `08:00 America/New_York`
- Weekly outlook: `21:00 Asia/Tbilisi` on Sunday

## Notes

- `config/app.yaml` defines the posting schedules and runtime state path
- Calendar logic uses `America/New_York`
- If calendar data cannot be loaded, the announcer falls back to a red no-trade outlook
- `data/state.json` is runtime state only and should not be committed
- The core rule engine stays separate from delivery, so you can add a full Discord bot later without rewriting the calendar logic
