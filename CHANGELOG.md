# Changelog

All notable changes are recorded here. Versions follow [SemVer](https://semver.org/).

## [Unreleased]

### Added
- Operational CLI (`--dry-run`, `--target-date`, `--force-kind`, `--config-dir`, `--log-level`).
- Discord embed support and externalized copy in `config/messages.yaml`.
- State `schema_version`, optional file lock (`runtime.lock_path`), unique temp filenames, and operations section tracking consecutive source failures.
- Rule operators: `gt`, `lt`, `ne`, `not_contains`, `between`, `in`, `any_of`, `all_of`.
- Strict YAML validation: invalid timezones, empty URL list, non-positive timeouts, unknown keys, unknown rule feature keys, duplicate rule ids.
- Forex Factory next-week URL, parallel fetch, retries with backoff, partial-success tolerance, configurable User-Agent.
- Discord webhook 429/5xx retry handling and 2000-character message splitting.
- Late-post guard (`policy.max_post_age_minutes`) and optional `policy.post_on_weekend` skip.
- GitHub Actions CI running ruff, mypy, vulture, and pytest.
- Dockerfile, Procfile, and `railway.json` manifests.
- `docs/rules.md`, `docs/runbook.md`, expanded README architecture diagram.
- LICENSE (MIT) and `.env.example`.

### Changed
- `_setattr` dispatch in the notifier replaced by an explicit `match` statement on `PostKind`.
- Recheck date now uses `app_config.ny_recheck` timezone (previously used the morning timezone).
- Holiday detection: partial holidays now require an `impact == "holiday"` USD entry, aligned with full-holiday detection.
- Day-after-bank-holiday days with no red-folder USD news are now hard no-trade days, while days with red-folder USD news still use the normal news rules.
- `requirements.txt` removed; `pyproject.toml` is the source of truth for runtime and dev dependencies.
- Dockerfile no longer declares `VOLUME ["/app/data"]` — Railway rejects `VOLUME` instructions; mount a Railway Volume at `/app/data` from the dashboard instead. The directory is still created at build time so non-Railway runs work without an explicit mount.

### Removed
- Tracked Windows installer binaries (`Git-2.53.0.2-64-bit.exe`, `python-3.12.0-amd64.exe`) and the build-time `optimal_trade_sessions_bot.egg-info/` directory.
