import json

import pytest

from bot.state_store import STATE_SCHEMA_VERSION, BotState, StateStore


def test_state_store_rejects_non_object_root(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps(["bad"]), encoding="utf-8")

    with pytest.raises(RuntimeError, match="State file root must be a JSON object"):
        StateStore(path).load()


def test_state_store_rejects_invalid_snapshot_shape(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps({"daily": {"snapshot": "bad"}}),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="state.daily.snapshot must be a list of strings"):
        StateStore(path).load()


def test_state_store_rejects_future_schema_version(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps({"schema_version": STATE_SCHEMA_VERSION + 1}),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="newer than supported"):
        StateStore(path).load()


def test_state_store_cleans_temp_file_when_save_fails(tmp_path):
    path = tmp_path / "state.json"
    path.mkdir()

    with pytest.raises(OSError):
        StateStore(path).save(BotState())

    assert list(tmp_path.glob("state.json.tmp-*")) == []
