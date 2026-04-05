from pathlib import Path

from agent_mem_bridge.storage import MemoryStore
from agent_mem_bridge.sync_notes import sync_markdown_file


def test_sync_markdown_file_loads_obsidian_note(tmp_path: Path) -> None:
    note = tmp_path / "session-note.md"
    note.write_text(
        """---
namespace: session
kind: memory
title: [[Codex]] Session
tags:
  - source:codex
  - project:agent-memory-bridge
actor: cole
session_id: session-42
correlation_id: handoff-42
source_app: codex
---

We decided to store #memory/bridge context with [[Codex]] and [[Memory Bridge]] links.
""",
        encoding="utf-8",
    )

    store = MemoryStore(tmp_path / "memory.db", log_dir=tmp_path / "logs")
    result = sync_markdown_file(store, note)
    recall = store.recall(namespace="session", tags_any=["tag:memory/bridge", "link:Codex"], limit=5)

    assert result["stored"] is True
    assert recall["count"] == 1
    assert recall["items"][0]["session_id"] == "session-42"

