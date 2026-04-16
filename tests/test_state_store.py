import json

import pytest

from bot.state_store import StateStore


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
