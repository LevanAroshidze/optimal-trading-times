from __future__ import annotations

import json
import os
import time

from bot.state_store import (
    _LOCK_STALE_AFTER_SECONDS,
    STATE_SCHEMA_VERSION,
    BotState,
    DailyPostState,
    OperationsState,
    StateStore,
)


def test_save_writes_schema_version_and_operations_section(tmp_path):
    path = tmp_path / "state.json"
    store = StateStore(path)
    state = BotState(
        daily=DailyPostState(posted_for_date="2026-04-16"),
        operations=OperationsState(consecutive_source_failures=2, last_source_status="unavailable"),
    )
    store.save(state)
    payload = json.loads(path.read_text())
    assert payload["schema_version"] == STATE_SCHEMA_VERSION
    assert payload["operations"]["consecutive_source_failures"] == 2


def test_save_uses_unique_temp_filename(tmp_path):
    path = tmp_path / "state.json"
    store = StateStore(path)
    store.save(BotState())
    leftovers = list(path.parent.glob("state.json.tmp-*"))
    assert leftovers == []


def test_lock_when_no_lock_path_is_noop(tmp_path):
    store = StateStore(tmp_path / "state.json")
    with store.lock():
        store.save(BotState())
    assert (tmp_path / "state.json").exists()


def test_lock_creates_and_releases_sentinel(tmp_path):
    state_path = tmp_path / "state.json"
    lock_path = tmp_path / "state.lock"
    store = StateStore(state_path, lock_path=lock_path)
    with store.lock():
        assert lock_path.exists()
    assert not lock_path.exists()


def test_stale_lock_exit_does_not_delete_new_lock_owner(tmp_path):
    state_path = tmp_path / "state.json"
    lock_path = tmp_path / "state.lock"
    first = StateStore(state_path, lock_path=lock_path).lock()
    second = StateStore(state_path, lock_path=lock_path).lock()

    first.__enter__()
    stale_time = time.time() - _LOCK_STALE_AFTER_SECONDS - 1
    os.utime(lock_path, (stale_time, stale_time))
    second.__enter__()

    try:
        first.__exit__(None, None, None)
        assert lock_path.exists()
    finally:
        second.__exit__(None, None, None)

    assert not lock_path.exists()


def test_load_handles_missing_operations_section(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "daily": {"posted_for_date": "2026-04-16"},
                "recheck": {},
                "weekly": {},
            }
        ),
        encoding="utf-8",
    )
    state = StateStore(path).load()
    assert state.daily.posted_for_date == "2026-04-16"
    assert state.operations.consecutive_source_failures == 0
