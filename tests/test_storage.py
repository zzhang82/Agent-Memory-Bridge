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


def test_browse_lists_recent_items_and_can_filter_by_domain(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db", log_dir=tmp_path / "logs")
    older = store.store(
        namespace="project:bridge",
        content="Older storage note.",
        kind="memory",
        tags=["domain:storage"],
    )
    newer = store.store(
        namespace="project:bridge",
        content="Newer orchestration note.",
        kind="memory",
        tags=["domain:orchestration"],
    )

    browse_all = store.browse(namespace="project:bridge", limit=10)
    browse_domain = store.browse(namespace="project:bridge", domain="orchestration", limit=10)

    assert browse_all["count"] == 2
    assert browse_all["items"][0]["id"] == newer["id"]
    assert browse_all["items"][1]["id"] == older["id"]
    assert browse_domain["count"] == 1
    assert browse_domain["items"][0]["id"] == newer["id"]


def test_stats_returns_kind_counts_and_top_domains(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db", log_dir=tmp_path / "logs")
    first = store.store(
        namespace="project:bridge",
        content="Storage claim one.",
        kind="memory",
        tags=["domain:storage"],
    )
    store.store(
        namespace="project:bridge",
        content="Storage claim two.",
        kind="memory",
        tags=["domain:storage", "domain:retrieval"],
    )
    signal = store.store(
        namespace="project:bridge",
        content="Reviewer ready.",
        kind="signal",
        tags=["domain:orchestration"],
    )

    stats = store.stats(namespace="project:bridge")

    assert stats["total_count"] == 3
    assert stats["kind_counts"]["memory"] == 2
    assert stats["kind_counts"]["signal"] == 1
    assert stats["top_domains"][0] == {"domain": "storage", "count": 2}
    assert stats["oldest_entry_at"] == first["created_at"]
    assert stats["newest_entry_at"] == signal["created_at"]


def test_forget_removes_existing_item_and_returns_deleted_metadata(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db", log_dir=tmp_path / "logs")
    created = store.store(
        namespace="project:bridge",
        content="Wrong durable memory.",
        kind="memory",
        tags=["domain:storage"],
        title="Bad record",
    )

    removed = store.forget(created["id"])
    recall = store.recall(namespace="project:bridge", query="Wrong durable memory", limit=5)

    assert removed["deleted"] is True
    assert removed["item"]["id"] == created["id"]
    assert removed["item"]["title"] == "Bad record"
    assert recall["count"] == 0


def test_forget_returns_deleted_false_for_missing_id(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db", log_dir=tmp_path / "logs")

    removed = store.forget("missing-id")

    assert removed == {"id": "missing-id", "deleted": False, "item": None}

