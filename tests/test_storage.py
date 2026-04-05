from pathlib import Path

import pytest

from agent_mem_bridge.storage import MemoryStore


def test_store_and_recall_round_trip(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db", log_dir=tmp_path / "logs")

    first = store.store(
        namespace="agent-memory-bridge",
        content="Use SQLite WAL mode for concurrent readers.",
        kind="memory",
        tags=["project:agent-memory-bridge", "topic:storage"],
        session_id="session-1",
        actor="cole",
        title="Storage decision",
        correlation_id="task-123",
        source_app="codex",
    )

    duplicate = store.store(
        namespace="agent-memory-bridge",
        content="Use SQLite WAL mode for concurrent readers.",
        kind="memory",
        tags=["project:agent-memory-bridge", "topic:storage"],
        session_id="session-1",
        actor="cole",
        source_app="codex",
    )

    recall = store.recall(
        namespace="agent-memory-bridge",
        query="SQLite WAL",
        limit=5,
        actor="cole",
    )

    assert first["stored"] is True
    assert duplicate["stored"] is False
    assert duplicate["duplicate_of"] == first["id"]
    assert recall["count"] == 1
    assert recall["items"][0]["kind"] == "memory"
    assert recall["items"][0]["correlation_id"] == "task-123"


def test_signal_entries_are_not_deduplicated(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db", log_dir=tmp_path / "logs")

    first = store.store(
        namespace="bridge",
        content="Reviewer needed for API handoff.",
        kind="signal",
        tags=["handoff", "review"],
        actor="cole",
    )
    second = store.store(
        namespace="bridge",
        content="Reviewer needed for API handoff.",
        kind="signal",
        tags=["handoff", "review"],
        actor="cole",
    )

    assert first["stored"] is True
    assert second["stored"] is True
    assert second["id"] != first["id"]


def test_special_character_query_falls_back_safely(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db", log_dir=tmp_path / "logs")
    store.store(
        namespace="agent-memory-bridge",
        content="The sanitizer must handle values.yaml without crashing FTS.",
        kind="memory",
        tags=["topic:fts"],
    )

    recall = store.recall(
        namespace="agent-memory-bridge",
        query="values.yaml",
        limit=5,
    )

    assert recall["count"] == 1
    assert "values.yaml" in recall["items"][0]["content"]


def test_store_extracts_obsidian_tags_and_wikilinks(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db", log_dir=tmp_path / "logs")
    store.store(
        namespace="bridge",
        title="[[Memory Bridge]] planning",
        content="Use #memory/bridge with [[Codex]] and [[Obsidian Vault|Vault]].",
        kind="memory",
        tags=["manual:seed"],
        actor="cole",
    )

    by_tag = store.recall(
        namespace="bridge",
        tags_any=["tag:memory/bridge"],
        limit=5,
    )
    by_link = store.recall(
        namespace="bridge",
        tags_any=["link:Codex", "link:Memory Bridge"],
        limit=5,
    )

    assert by_tag["count"] == 1
    assert by_link["count"] == 1
    assert "manual:seed" in by_link["items"][0]["tags"]
    assert "tag:memory/bridge" in by_link["items"][0]["tags"]
    assert "link:Codex" in by_link["items"][0]["tags"]
    assert "link:Memory Bridge" in by_link["items"][0]["tags"]
    assert "link:Obsidian Vault" in by_link["items"][0]["tags"]


def test_polling_with_since_returns_only_new_items(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db", log_dir=tmp_path / "logs")
    first = store.store(
        namespace="bridge",
        content="Task ready for review.",
        kind="signal",
        tags=["review"],
        actor="cole",
    )
    second = store.store(
        namespace="bridge",
        content="Security review also needed.",
        kind="signal",
        tags=["review", "security"],
        actor="cole",
    )

    polled = store.recall(
        namespace="bridge",
        kind="signal",
        tags_any=["review"],
        since=first["id"],
        limit=10,
    )

    assert polled["count"] == 1
    assert polled["items"][0]["id"] == second["id"]
    assert polled["next_since"] == second["id"]


def test_filter_only_recall_returns_newest_first(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db", log_dir=tmp_path / "logs")
    first = store.store(
        namespace="bridge",
        content="Older checkpoint.",
        kind="memory",
        tags=["auto-checkpoint"],
    )
    second = store.store(
        namespace="bridge",
        content="Newer checkpoint.",
        kind="memory",
        tags=["auto-checkpoint"],
    )

    recall = store.recall(
        namespace="bridge",
        tags_any=["auto-checkpoint"],
        limit=2,
    )

    assert recall["count"] == 2
    assert recall["items"][0]["id"] == second["id"]
    assert recall["items"][1]["id"] == first["id"]


def test_store_rejects_unknown_kind(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db", log_dir=tmp_path / "logs")

    with pytest.raises(ValueError, match="kind must be one of"):
        store.store(
            namespace="bridge",
            content="This should fail.",
            kind="note",
        )

