from __future__ import annotations

from datetime import date

import pytest

from bot.main import main, parse_args


def test_parse_args_dry_run_defaults():
    args = parse_args(["--dry-run"])
    assert args.dry_run is True
    assert args.target_date is None
    assert args.force_kind is None


def test_parse_args_target_date_iso_format():
    args = parse_args(["--target-date", "2026-04-16"])
    assert args.target_date == date(2026, 4, 16)


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


async def _async_raise(self):
    from bot.calendar_source import CalendarSourceError

    raise CalendarSourceError("offline test")
