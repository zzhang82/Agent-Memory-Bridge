import json
from pathlib import Path

from agent_mem_bridge.session_closeout import closeout_session_from_json
from agent_mem_bridge.storage import MemoryStore


def test_closeout_session_writes_note_and_syncs(tmp_path: Path) -> None:
    payload = {
        "namespace": "agent-memory-bridge",
        "kind": "memory",
        "title": "[[Codex]] closeout",
        "tags": ["source:codex", "phase:closeout"],
        "actor": "cole",
        "session_id": "codex-closeout-1",
        "correlation_id": "closeout-1",
        "source_app": "codex",
        "summary": "This session stabilized the bridge.",
        "bullets": [
            "Built a closeout helper.",
            "Synced a durable note into the bridge.",
        ],
        "next_step": "Use this helper from a future session-end trigger.",
        "session_folder": "2026-04-04-closeout",
    }
    payload_path = tmp_path / "payload.json"
    payload_path.write_text(json.dumps(payload), encoding="utf-8")

    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    result = closeout_session_from_json(store, payload_path, tmp_path / "notes")

    note_path = Path(result["note_path"])
    note_text = note_path.read_text(encoding="utf-8")
    recall = store.recall(namespace="agent-memory-bridge", query="stabilized the bridge", limit=3)

    assert note_path.exists()
    assert "[[Codex]] closeout" in note_text
    assert result["sync_result"]["stored"] is True
    assert recall["count"] == 1

