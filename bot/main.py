from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import date
from pathlib import Path
from typing import get_args

from bot.calendar_features import CalendarFeatureBuilder
from bot.calendar_source import ForexFactoryCalendarSource
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
from bot.rule_engine import RuleEngine
from bot.state_store import StateStore
from bot.summary_service import DailySummaryService
from bot.type_defs import PostKind
from bot.webhook_sender import DiscordWebhookSender, validate_webhook_url


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
    return Path(__file__).resolve().parent.parent / "config"


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
    if not dry_run:
        webhook_url = validate_webhook_url(
            os.getenv(app_config.delivery.webhook_url_env, ""),
            app_config.delivery.webhook_url_env,
        )
        sender = DiscordWebhookSender(
            webhook_url=webhook_url,
            timeout_seconds=app_config.calendar.timeout_seconds,
        )

    return summary_service, formatter, sender, state_store


async def _run_async(args: argparse.Namespace) -> int:
    config_dir = _config_dir_from(args)
    app_config, aliases, rules, messages = _load_all_config(config_dir)
    summary_service, formatter, sender, state_store = _build_components(
        app_config,
        aliases,
        rules,
        messages,
        dry_run=args.dry_run,
        config_dir=config_dir,
    )

    notifier = TradingCalendarNotifier(
        app_config=app_config,
        summary_service=summary_service,
        formatter=formatter,
        sender=sender or _NoopSender(),
        state_store=state_store,
        dry_run=args.dry_run,
        force_kind=args.force_kind,
    )

    sent = await notifier.run_once(target_date_override=args.target_date)

    if args.dry_run:
        for index, message in enumerate(sent, start=1):
            sys.stdout.write(f"--- message {index} ---\n{message}\n")
        if not sent:
            sys.stdout.write("(no messages produced; nothing was due)\n")

    return 0


class _NoopSender:
    """Fallback used in dry-run mode so the notifier sees a sender protocol object."""

    async def send_message(self, content: str) -> None:
        return None


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    _configure_logging(args.log_level)
    return asyncio.run(_run_async(args))


if __name__ == "__main__":
    sys.exit(main())
