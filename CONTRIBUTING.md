# Contributing

Thanks for considering a contribution. The project's core promise is "rules go in YAML, not Python." Please respect that boundary.

## Setup

```bash
python -m pip install -e '.[dev]'
ruff check bot tests
mypy bot
pytest -q
```

## Where work goes

- **Trading logic / new conditions** → extend the feature registry in `bot/calendar_features.py` and `bot/type_defs.py::KNOWN_FEATURE_KEYS`, then write the rule in `config/rules.yaml`. Tests in `tests/test_production_rules.py` should grow alongside.
- **New rule operators** → `bot/rule_engine.py` plus `_ALLOWED_RULE_OPERATORS` and `_coerce_rule_operator_map` in `bot/config.py`. Cover the new operator with a test in `tests/test_rule_engine_operators.py`.
- **Output / wording** → adjust `config/messages.yaml`, not `bot/formatter.py`. The formatter is purely structural.
- **Schedules / windows / policy** → edit `config/app.yaml`. Keep validation strict in `bot/config.py`.
- **Calendar source changes** → keep `bot/calendar_source.py` async-safe and tolerant of partial URL failure. Add `httpx.MockTransport` tests.
- **Webhook formatting / Discord limits** → `bot/webhook_sender.py`. Always retry 429s, split long messages.

## Pull-request checklist

- [ ] `ruff check bot tests` passes.
- [ ] `pytest -q` is green and tests cover the new behavior.
- [ ] No new tracked binaries (the `.gitignore` rejects `*.exe` and egg-info).
- [ ] Plan-mode changes: if you change a behavior described in `docs/rules.md` or `docs/runbook.md`, update the docs in the same PR.
- [ ] CHANGELOG entry appended under `[Unreleased]`.

## Commit and PR style

- Imperative, present-tense subject (`Fix recheck timezone bug`).
- One logical change per commit; small, easy-to-review PRs preferred over giant ones.
- Mention any rule-id changes in the body so future archaeology is easy.
