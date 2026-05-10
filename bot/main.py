from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import get_args

from bot.calendar_features import CalendarFeatureBuilder
from bot.calendar_source import CalendarSourceError, ForexFactoryCalendarSource
from bot.config import (
    AppConfig,
    EventAliasConfig,
    MessagesConfig,
    load_app_config,
    load_event_aliases,
    load_messages,
    load_rules,
)
from bot.formatter import DiscordFormatter
from bot.notifier import TradingCalendarNotifier
from bot.preview_styles import build_preview_messages
from bot.rule_engine import RuleEngine
from bot.state_store import StateStore
from bot.summary_service import DailySummaryService
from bot.type_defs import PostKind, SourceStatus
from bot.webhook_sender import (
    DiscordWebhookSender,
    WebhookError,
    WebhookPayload,
    validate_webhook_url,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="bot",
        description="Discord webhook announcer for the trading session matrix.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build messages but do not send a webhook or write state.",
    )
    parser.add_argument(
        "--target-date",
        type=_parse_iso_date,
        help="Override the daily/recheck target date (ISO YYYY-MM-DD).",
    )
    parser.add_argument(
        "--force-kind",
        choices=list(get_args(PostKind)),
        help="Force a specific post kind to run (use with --dry-run).",
    )
    parser.add_argument(
        "--config-dir",
        type=Path,
        default=None,
        help="Override the directory holding app.yaml/rules.yaml/event_aliases.yaml.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    parser.add_argument(
        "--diagnose-week",
        action="store_true",
        help="Print matched rules and extracted calendar features for the target week.",
    )
    parser.add_argument(
        "--preview-message-styles",
        action="store_true",
        help="Send Discord visual style previews to a dedicated preview webhook.",
    )
    parser.add_argument(
        "--preview-webhook-env",
        default="DISCORD_PREVIEW_WEBHOOK_URL",
        help="Environment variable containing the preview webhook URL.",
    )
    parser.add_argument(
        "--allow-prod-preview",
        action="store_true",
        help="Allow preview posts when preview webhook matches DISCORD_WEBHOOK_URL.",
    )
    return parser.parse_args(argv)


def _parse_iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            f"--target-date must be ISO YYYY-MM-DD, got {value!r}"
        ) from error


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def _config_dir_from(args: argparse.Namespace) -> Path:
    if args.config_dir is not None:
        return args.config_dir
    cwd_config = Path.cwd() / "config"
    if cwd_config.exists():
        return cwd_config
    return Path(__file__).resolve().parent.parent / "config"


def _load_local_env(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        value = value.strip().strip("'\"")
        os.environ.setdefault(key, value)


def _load_all_config(
    config_dir: Path,
) -> tuple[AppConfig, EventAliasConfig, list, MessagesConfig]:
    app_config = load_app_config(config_dir / "app.yaml")
    aliases = load_event_aliases(config_dir / "event_aliases.yaml")
    rules = load_rules(config_dir / "rules.yaml")
    messages_path = config_dir / "messages.yaml"
    messages = load_messages(messages_path if messages_path.exists() else None)
    return app_config, aliases, rules, messages


def _build_components(
    app_config: AppConfig,
    aliases: EventAliasConfig,
    rules: list,
    messages: MessagesConfig,
    *,
    dry_run: bool,
    config_dir: Path,
) -> tuple[
    DailySummaryService,
    DiscordFormatter,
    DiscordWebhookSender | None,
    DiscordWebhookSender | None,
    StateStore,
]:
    source = ForexFactoryCalendarSource(
        urls=app_config.calendar.urls,
        timeout_seconds=app_config.calendar.timeout_seconds,
        user_agent=app_config.calendar.user_agent,
        max_retries=app_config.calendar.max_retries,
        retry_backoff_seconds=app_config.calendar.retry_backoff_seconds,
    )
    feature_builder = CalendarFeatureBuilder(
        aliases=aliases,
        market_timezone=app_config.calendar.market_timezone,
        windows=app_config.windows,
        quiet_day_note=messages.quiet_day_note,
    )
    rule_engine = RuleEngine(rules)
    formatter = DiscordFormatter(messages=messages)
    summary_service = DailySummaryService(
        source=source,
        feature_builder=feature_builder,
        rule_engine=rule_engine,
        market_timezone=app_config.calendar.market_timezone,
    )

    state_path = config_dir.parent / app_config.runtime.state_path
    lock_path = (
        config_dir.parent / app_config.runtime.lock_path
        if app_config.runtime.lock_path
        else None
    )
    state_store = StateStore(state_path, lock_path=lock_path)

    sender: DiscordWebhookSender | None = None
    ops_sender: DiscordWebhookSender | None = None
    if not dry_run:
        webhook_url = validate_webhook_url(
            os.getenv(app_config.delivery.webhook_url_env, ""),
            app_config.delivery.webhook_url_env,
        )
        sender = DiscordWebhookSender(
            webhook_url=webhook_url,
            timeout_seconds=app_config.calendar.timeout_seconds,
        )
        if app_config.delivery.ops_webhook_url_env:
            ops_webhook_raw = os.getenv(app_config.delivery.ops_webhook_url_env, "")
            if ops_webhook_raw.strip():
                ops_sender = DiscordWebhookSender(
                    webhook_url=validate_webhook_url(
                        ops_webhook_raw, app_config.delivery.ops_webhook_url_env
                    ),
                    timeout_seconds=app_config.calendar.timeout_seconds,
                )

    return summary_service, formatter, sender, ops_sender, state_store


async def _run_async(args: argparse.Namespace) -> int:
    _load_local_env()
    if args.preview_message_styles:
        await _send_message_style_previews(args)
        return 0

    config_dir = _config_dir_from(args)
    app_config, aliases, rules, messages = _load_all_config(config_dir)
    summary_service, formatter, sender, ops_sender, state_store = _build_components(
        app_config,
        aliases,
        rules,
        messages,
        dry_run=args.dry_run or args.diagnose_week,
        config_dir=config_dir,
    )

    if args.diagnose_week:
        await _print_week_diagnostics(summary_service, args.target_date)
        return 0

    notifier = TradingCalendarNotifier(
        app_config=app_config,
        summary_service=summary_service,
        formatter=formatter,
        sender=sender or _NoopSender(),
        ops_sender=ops_sender,
        state_store=state_store,
        dry_run=args.dry_run,
        force_kind=args.force_kind,
    )

    sent = await notifier.run_once(target_date_override=args.target_date)

    if args.dry_run:
        for index, message in enumerate(sent, start=1):
            sys.stdout.write(
                f"--- message {index} ---\n"
                f"{formatter.render_for_console(message)}\n"
            )
        if not sent:
            if args.force_kind == "weekly":
                sys.stdout.write(
                    "(no weekly outlook produced; no live or fresh cached "
                    "target-week calendar data was available)\n"
                )
            else:
                sys.stdout.write("(no messages produced; nothing was due)\n")

    return 0


async def _send_message_style_previews(args: argparse.Namespace) -> None:
    preview_env = args.preview_webhook_env
    preview_url = validate_webhook_url(os.getenv(preview_env, ""), preview_env)
    production_url = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    if (
        production_url
        and preview_url == production_url
        and not args.allow_prod_preview
    ):
        raise WebhookError(
            "Refusing to send previews to DISCORD_WEBHOOK_URL. "
            "Set a separate preview webhook or pass --allow-prod-preview."
        )

    sender = DiscordWebhookSender(webhook_url=preview_url)
    previews = build_preview_messages()
    for preview in previews:
        await sender.send_message(preview.payload)
    sys.stdout.write(
        f"Sent {len(previews)} Discord style preview messages via {preview_env}.\n"
    )


async def _print_week_diagnostics(
    summary_service: DailySummaryService, target_date: date | None
) -> None:
    target = target_date or date.today()
    week_start = target - timedelta(days=target.weekday())
    try:
        events = await summary_service.load_events()
        source_status: SourceStatus = "live"
    except CalendarSourceError as error:
        events = []
        source_status = "unavailable"
        sys.stdout.write(f"source_status={source_status} error={error}\n")

    for offset in range(5):
        day = week_start + timedelta(days=offset)
        analysis = summary_service.feature_builder.analyse_date(day, events)
        outlook = summary_service.build_outlook_for_date(day, events, source_status)
        sessions = {
            session: color.value
            for session, color in outlook.decision.session_colors.items()
        }
        sys.stdout.write(
            f"{day.isoformat()} source_status={source_status} "
            f"rule={outlook.decision.rule_id} "
            f"categories={analysis.features['categories_today']} "
            f"strong={outlook.strongest_news_today} "
            f"other={outlook.other_red_news_today} "
            f"sessions={sessions}\n"
        )


class _NoopSender:
    """Fallback used in dry-run mode so the notifier sees a sender protocol object."""

    async def send_message(self, content: str | WebhookPayload) -> None:
        return None


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    _configure_logging(args.log_level)
    return asyncio.run(_run_async(args))


if __name__ == "__main__":
    sys.exit(main())
