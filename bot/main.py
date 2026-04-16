from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from bot.calendar_features import CalendarFeatureBuilder
from bot.calendar_source import ForexFactoryCalendarSource
from bot.config import load_app_config, load_event_aliases, load_rules
from bot.formatter import DiscordFormatter
from bot.notifier import TradingCalendarNotifier
from bot.rule_engine import RuleEngine
from bot.state_store import StateStore
from bot.summary_service import DailySummaryService
from bot.webhook_sender import DiscordWebhookSender


async def _run_once() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    root = Path(__file__).resolve().parent.parent
    app_config = load_app_config(root / "config" / "app.yaml")
    aliases = load_event_aliases(root / "config" / "event_aliases.yaml")
    rules = load_rules(root / "config" / "rules.yaml")

    webhook_url = os.getenv(app_config.delivery.webhook_url_env, "").strip()
    if not webhook_url:
        raise RuntimeError(
            f"Missing required environment variable: {app_config.delivery.webhook_url_env}"
        )

    source = ForexFactoryCalendarSource(
        urls=app_config.calendar.urls,
        timeout_seconds=app_config.calendar.timeout_seconds,
    )
    feature_builder = CalendarFeatureBuilder(
        aliases=aliases,
        market_timezone=app_config.calendar.market_timezone,
    )
    rule_engine = RuleEngine(rules)
    formatter = DiscordFormatter()
    summary_service = DailySummaryService(
        source=source,
        feature_builder=feature_builder,
        rule_engine=rule_engine,
        market_timezone=app_config.calendar.market_timezone,
    )
    sender = DiscordWebhookSender(
        webhook_url=webhook_url,
        timeout_seconds=app_config.calendar.timeout_seconds,
    )
    state_store = StateStore(root / app_config.runtime.state_path)
    notifier = TradingCalendarNotifier(
        app_config=app_config,
        summary_service=summary_service,
        formatter=formatter,
        sender=sender,
        state_store=state_store,
    )

    await notifier.run_once()


def main() -> None:
    asyncio.run(_run_once())


if __name__ == "__main__":
    main()
