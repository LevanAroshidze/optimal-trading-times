from __future__ import annotations

import os
from dataclasses import replace
from datetime import date, datetime

import pytest

from bot.config import load_app_config, load_event_aliases, load_messages, load_rules
from bot.main import _build_components, _load_local_env, main, parse_args
from bot.models import CalendarEvent
from bot.webhook_sender import WebhookError
from tests.conftest import CONFIG_DIR

VALID_WEBHOOK_URL = "https://discord.com/api/webhooks/123456789012345678/abcDEFghi-_JKL"


def test_parse_args_dry_run_defaults():
    args = parse_args(["--dry-run"])
    assert args.dry_run is True
    assert args.target_date is None
    assert args.force_kind is None


def test_parse_args_target_date_iso_format():
    args = parse_args(["--target-date", "2026-04-16"])
    assert args.target_date == date(2026, 4, 16)


def test_parse_args_diagnose_week():
    args = parse_args(["--diagnose-week", "--target-date", "2026-04-16"])
    assert args.diagnose_week is True
    assert args.target_date == date(2026, 4, 16)


def test_parse_args_preview_message_styles():
    args = parse_args(["--preview-message-styles"])

    assert args.preview_message_styles is True
    assert args.preview_webhook_env == "DISCORD_PREVIEW_WEBHOOK_URL"
    assert args.allow_prod_preview is False


@pytest.mark.parametrize("safe_mode", ["--dry-run", "--diagnose-week"])
def test_parse_args_rejects_preview_that_would_bypass_safe_mode(safe_mode):
    with pytest.raises(SystemExit):
        parse_args([safe_mode, "--preview-message-styles"])


def test_parse_args_rejects_bad_date():
    with pytest.raises(SystemExit):
        parse_args(["--target-date", "not-a-date"])


def test_parse_args_rejects_unknown_force_kind():
    with pytest.raises(SystemExit):
        parse_args(["--force-kind", "yearly"])


def test_main_dry_run_with_target_date_does_not_send(monkeypatch, capsys):
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    monkeypatch.setattr(
        "bot.main.ForexFactoryCalendarSource.fetch_events",
        _async_raise,
    )
    rc = main(
        [
            "--dry-run",
            "--target-date",
            "2026-04-16",
            "--force-kind",
            "daily",
            "--log-level",
            "ERROR",
        ]
    )
    assert rc == 0
    captured = capsys.readouterr()
    assert "message" in captured.out


def test_main_dry_run_forced_weekly_explains_missing_calendar_data(monkeypatch, capsys):
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    monkeypatch.setattr(
        "bot.main.ForexFactoryCalendarSource.fetch_events",
        _async_raise,
    )

    rc = main(
        [
            "--dry-run",
            "--target-date",
            "2026-05-04",
            "--force-kind",
            "weekly",
            "--log-level",
            "ERROR",
        ]
    )

    assert rc == 0
    captured = capsys.readouterr()
    assert "no weekly outlook produced" in captured.out
    assert "target-week calendar data" in captured.out


def test_diagnose_week_fetches_target_week_when_default_feed_does_not_cover_it(
    monkeypatch, capsys
):
    async def current_week(self):
        return [
            CalendarEvent(
                title="Retail Sales m/m",
                country="USD",
                impact="High",
                starts_at=datetime.fromisoformat("2026-07-07T08:30:00-04:00"),
            )
        ]

    async def target_week(self, week_start):
        assert week_start == date(2026, 7, 13)
        return [
            CalendarEvent(
                title="CPI m/m",
                country="USD",
                impact="High",
                starts_at=datetime.fromisoformat("2026-07-14T08:30:00-04:00"),
            )
        ]

    monkeypatch.setattr(
        "bot.main.ForexFactoryCalendarSource.fetch_events", current_week
    )
    monkeypatch.setattr(
        "bot.main.ForexFactoryCalendarSource.fetch_events_for_week", target_week
    )

    rc = main(
        [
            "--diagnose-week",
            "--target-date",
            "2026-07-13",
            "--log-level",
            "ERROR",
        ]
    )

    assert rc == 0
    assert "2026-07-13 source_status=live rule=cpi_minus_1" in capsys.readouterr().out


def test_build_components_does_not_require_missing_ops_webhook(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", VALID_WEBHOOK_URL)
    monkeypatch.delenv("DISCORD_OPS_WEBHOOK_URL", raising=False)
    app_config = load_app_config(CONFIG_DIR / "app.yaml")
    aliases = load_event_aliases(CONFIG_DIR / "event_aliases.yaml")
    rules = load_rules(CONFIG_DIR / "rules.yaml")
    messages = load_messages(CONFIG_DIR / "messages.yaml")

    *_components, ops_sender, _state_store = _build_components(
        app_config,
        aliases,
        rules,
        messages,
        dry_run=False,
        config_dir=CONFIG_DIR,
    )

    assert ops_sender is None


def test_build_components_uses_delivery_retry_settings(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", VALID_WEBHOOK_URL)
    monkeypatch.setenv("DISCORD_OPS_WEBHOOK_URL", VALID_WEBHOOK_URL)
    app_config = load_app_config(CONFIG_DIR / "app.yaml")
    app_config = replace(
        app_config,
        delivery=replace(
            app_config.delivery,
            timeout_seconds=12.5,
            max_retries=5,
            retry_backoff_seconds=0.25,
        ),
    )
    aliases = load_event_aliases(CONFIG_DIR / "event_aliases.yaml")
    rules = load_rules(CONFIG_DIR / "rules.yaml")
    messages = load_messages(CONFIG_DIR / "messages.yaml")

    _, _, sender, ops_sender, _ = _build_components(
        app_config,
        aliases,
        rules,
        messages,
        dry_run=False,
        config_dir=CONFIG_DIR,
    )

    assert sender is not None
    assert ops_sender is not None
    for configured_sender in (sender, ops_sender):
        assert configured_sender.timeout_seconds == 12.5
        assert configured_sender.max_retries == 5
        assert configured_sender.retry_backoff_seconds == 0.25


def test_preview_message_styles_sends_to_preview_webhook(monkeypatch, capsys):
    sent = []

    async def fake_send_message(self, content):
        sent.append(content)

    monkeypatch.setattr(
        "bot.main.DiscordWebhookSender.send_message",
        fake_send_message,
    )
    monkeypatch.setenv("DISCORD_PREVIEW_WEBHOOK_URL", VALID_WEBHOOK_URL)
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)

    rc = main(["--preview-message-styles", "--log-level", "ERROR"])

    assert rc == 0
    assert len(sent) == 9
    assert "Sent 9 Discord style preview messages" in capsys.readouterr().out


def test_preview_message_styles_requires_preview_webhook(monkeypatch):
    monkeypatch.delenv("DISCORD_PREVIEW_WEBHOOK_URL", raising=False)

    with pytest.raises(WebhookError, match="Missing required environment variable"):
        main(["--preview-message-styles", "--log-level", "ERROR"])


def test_preview_message_styles_refuses_production_webhook(monkeypatch):
    monkeypatch.setenv("DISCORD_PREVIEW_WEBHOOK_URL", VALID_WEBHOOK_URL)
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", VALID_WEBHOOK_URL)

    with pytest.raises(WebhookError, match="Refusing to send previews"):
        main(["--preview-message-styles", "--log-level", "ERROR"])


def test_preview_message_styles_refuses_production_webhook_with_slash_difference(
    monkeypatch,
):
    monkeypatch.setenv("DISCORD_PREVIEW_WEBHOOK_URL", VALID_WEBHOOK_URL)
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", VALID_WEBHOOK_URL + "/")

    with pytest.raises(WebhookError, match="Refusing to send previews"):
        main(["--preview-message-styles", "--log-level", "ERROR"])


def test_load_local_env_sets_missing_values_without_overriding(monkeypatch, tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/123456789012345678/fromfile\n"
        "EXISTING=fromfile\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    monkeypatch.setenv("EXISTING", "fromshell")

    _load_local_env(env_path)

    assert (
        os.environ["DISCORD_WEBHOOK_URL"]
        == "https://discord.com/api/webhooks/123456789012345678/fromfile"
    )
    assert os.environ["EXISTING"] == "fromshell"


async def _async_raise(self):
    from bot.calendar_source import CalendarSourceError

    raise CalendarSourceError("offline test")
