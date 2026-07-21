from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_mem_bridge import state_io


@pytest.mark.parametrize(
    "payload",
    ["{", "[]", '"state"', "null", "\ufeff{"],
)
def test_load_json_state_tolerates_malformed_or_non_object_payloads(tmp_path: Path, payload: str) -> None:
    state_path = tmp_path / "lane-state.json"
    state_path.write_text(payload, encoding="utf-8")

    assert state_io.load_json_state(state_path) == {}


def test_write_json_state_atomic_replaces_valid_state_without_temp_files(tmp_path: Path) -> None:
    state_path = tmp_path / "lane-state.json"
    state_path.write_text(json.dumps({"old": True}), encoding="utf-8")

    state_io.write_json_state_atomic(state_path, {"cycle": 2, "ok": True})

    assert json.loads(state_path.read_text(encoding="utf-8")) == {"cycle": 2, "ok": True}
    assert list(tmp_path.glob(".lane-state.json.*.tmp")) == []


def test_failed_atomic_replace_preserves_previous_state(monkeypatch, tmp_path: Path) -> None:
    state_path = tmp_path / "lane-state.json"
    state_path.write_text(json.dumps({"stable": True}), encoding="utf-8")

    def fail_replace(source: Path, target: Path) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(state_io.os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        state_io.write_json_state_atomic(state_path, {"stable": False})

    assert json.loads(state_path.read_text(encoding="utf-8")) == {"stable": True}
    assert list(tmp_path.glob(".lane-state.json.*.tmp")) == []
