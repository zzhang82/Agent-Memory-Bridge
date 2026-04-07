from pathlib import Path

from datetime import UTC, datetime, timedelta

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
    assert first["signal_status"] == "pending"


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


def test_claim_signal_sets_lease_and_ack_marks_completion(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db", log_dir=tmp_path / "logs")
    created = store.store(
        namespace="project:bridge",
        content="Reviewer needed for release note pass.",
        kind="signal",
        tags=["handoff:review"],
        actor="codex",
        ttl_seconds=600,
    )

    claimed = store.claim_signal(
        namespace="project:bridge",
        consumer="reviewer-a",
        lease_seconds=120,
        signal_id=created["id"],
    )
    acked = store.ack_signal(created["id"], consumer="reviewer-a")
    recalled = store.recall(namespace="project:bridge", kind="signal", limit=5)

    assert claimed["claimed"] is True
    assert claimed["item"]["signal_status"] == "claimed"
    assert claimed["item"]["claimed_by"] == "reviewer-a"
    assert claimed["item"]["lease_expires_at"] is not None
    assert acked["acked"] is True
    assert acked["item"]["signal_status"] == "acked"
    assert recalled["count"] == 1
    assert recalled["items"][0]["signal_status"] == "acked"


def test_extend_signal_lease_allows_current_owner_to_renew(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db", log_dir=tmp_path / "logs")
    created = store.store(
        namespace="project:bridge",
        content="Keep the review lease alive.",
        kind="signal",
        tags=["handoff:review"],
        ttl_seconds=600,
    )

    claimed = store.claim_signal(
        namespace="project:bridge",
        consumer="reviewer-a",
        lease_seconds=60,
        signal_id=created["id"],
    )
    previous = datetime.fromisoformat(claimed["lease_expires_at"])

    extended = store.extend_signal_lease(created["id"], consumer="reviewer-a", lease_seconds=120)

    assert extended["extended"] is True
    assert extended["item"]["signal_status"] == "claimed"
    assert datetime.fromisoformat(extended["lease_expires_at"]) > previous


def test_extend_signal_lease_rejects_wrong_consumer(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db", log_dir=tmp_path / "logs")
    created = store.store(
        namespace="project:bridge",
        content="Wrong worker should not renew.",
        kind="signal",
        tags=["handoff:review"],
        ttl_seconds=600,
    )
    store.claim_signal(
        namespace="project:bridge",
        consumer="reviewer-a",
        lease_seconds=60,
        signal_id=created["id"],
    )

    extended = store.extend_signal_lease(created["id"], consumer="reviewer-b", lease_seconds=120)

    assert extended["extended"] is False
    assert extended["reason"] == "claimed-by-other"


def test_extend_signal_lease_rejects_expired_lease_and_allows_reclaim(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db", log_dir=tmp_path / "logs")
    created = store.store(
        namespace="project:bridge",
        content="Expired lease should be reclaimed, not renewed.",
        kind="signal",
        tags=["handoff:triage"],
        ttl_seconds=600,
    )
    store.claim_signal(
        namespace="project:bridge",
        consumer="worker-a",
        lease_seconds=60,
        signal_id=created["id"],
    )

    with store._connect() as conn:
        conn.execute(
            "UPDATE memories SET lease_expires_at = ? WHERE id = ?",
            ("2000-01-01T00:00:00+00:00", created["id"]),
        )
        conn.commit()

    extended = store.extend_signal_lease(created["id"], consumer="worker-a", lease_seconds=120)
    reclaimed = store.claim_signal(
        namespace="project:bridge",
        consumer="worker-b",
        lease_seconds=120,
        signal_id=created["id"],
    )

    assert extended["extended"] is False
    assert extended["reason"] == "lease-expired"
    assert reclaimed["claimed"] is True
    assert reclaimed["item"]["claimed_by"] == "worker-b"


def test_extend_signal_lease_cannot_outrun_signal_expiry(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db", log_dir=tmp_path / "logs")
    hard_expiry = datetime.now(UTC) + timedelta(seconds=90)
    created = store.store(
        namespace="project:bridge",
        content="Lease cap should stop at signal expiry.",
        kind="signal",
        tags=["handoff:review"],
        expires_at=hard_expiry.isoformat(),
    )
    store.claim_signal(
        namespace="project:bridge",
        consumer="reviewer-a",
        lease_seconds=30,
        signal_id=created["id"],
    )

    extended = store.extend_signal_lease(created["id"], consumer="reviewer-a", lease_seconds=600)

    assert extended["extended"] is True
    assert datetime.fromisoformat(extended["lease_expires_at"]) <= hard_expiry


def test_extend_signal_lease_rejects_acked_signal(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db", log_dir=tmp_path / "logs")
    created = store.store(
        namespace="project:bridge",
        content="Done work should not keep renewing.",
        kind="signal",
        tags=["handoff:done"],
        ttl_seconds=600,
    )
    store.claim_signal(
        namespace="project:bridge",
        consumer="worker-a",
        lease_seconds=60,
        signal_id=created["id"],
    )
    store.ack_signal(created["id"], consumer="worker-a")

    extended = store.extend_signal_lease(created["id"], consumer="worker-a", lease_seconds=120)

    assert extended["extended"] is False
    assert extended["reason"] == "already-acked"


def test_claim_signal_reuses_stale_lease(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db", log_dir=tmp_path / "logs")
    created = store.store(
        namespace="project:bridge",
        content="Pending triage work.",
        kind="signal",
        tags=["handoff:triage"],
    )
    store.claim_signal(
        namespace="project:bridge",
        consumer="worker-a",
        lease_seconds=1,
        signal_id=created["id"],
    )

    import time
    time.sleep(1.1)

    reclaimed = store.claim_signal(
        namespace="project:bridge",
        consumer="worker-b",
        lease_seconds=60,
        signal_id=created["id"],
    )

    assert reclaimed["claimed"] is True
    assert reclaimed["item"]["claimed_by"] == "worker-b"
    assert reclaimed["item"]["signal_status"] == "claimed"


def test_recall_can_filter_by_effective_signal_status(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db", log_dir=tmp_path / "logs")
    pending = store.store(
        namespace="project:bridge",
        content="Open handoff.",
        kind="signal",
        tags=["handoff:open"],
    )
    claimed = store.store(
        namespace="project:bridge",
        content="Claim me.",
        kind="signal",
        tags=["handoff:claimed"],
    )
    acked = store.store(
        namespace="project:bridge",
        content="Already done.",
        kind="signal",
        tags=["handoff:acked"],
    )

    store.claim_signal(namespace="project:bridge", consumer="worker-a", lease_seconds=60, signal_id=claimed["id"])
    store.ack_signal(acked["id"])

    pending_hits = store.recall(namespace="project:bridge", kind="signal", signal_status="pending", limit=10)
    claimed_hits = store.recall(namespace="project:bridge", kind="signal", signal_status="claimed", limit=10)
    acked_hits = store.recall(namespace="project:bridge", kind="signal", signal_status="acked", limit=10)

    assert [item["id"] for item in pending_hits["items"]] == [pending["id"]]
    assert [item["id"] for item in claimed_hits["items"]] == [claimed["id"]]
    assert [item["id"] for item in acked_hits["items"]] == [acked["id"]]


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
    assert stats["signal_status_counts"]["pending"] == 1
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


def test_promote_reclassifies_memory_in_place(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db", log_dir=tmp_path / "logs")
    created = store.store(
        namespace="project:bridge",
        content=(
            "record_type: learn\n"
            "claim: Use one shared bridge DB.\n"
            "scope: global\n"
            "confidence: observed\n"
        ),
        kind="memory",
        tags=["kind:learn", "domain:memory-bridge", "topic:runtime-path"],
        title="[[Learn]] Use one shared bridge DB.",
    )

    promoted = store.promote(created["id"], "gotcha")
    browse = store.browse(namespace="project:bridge", kind="memory", limit=10)

    assert promoted["changed"] is True
    assert promoted["id"] == created["id"]
    assert promoted["record_type"] == "gotcha"
    assert promoted["previous_record_type"] == "learn"
    assert "kind:gotcha" in promoted["item"]["tags"]
    assert "promoted-from:learn" in promoted["item"]["tags"]
    assert "record_type: gotcha" in promoted["item"]["content"]
    assert browse["count"] == 1
    assert browse["items"][0]["id"] == created["id"]
    assert browse["items"][0]["title"].startswith("[[Gotcha]]")


def test_promote_returns_changed_false_when_already_target_kind(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db", log_dir=tmp_path / "logs")
    created = store.store(
        namespace="project:bridge",
        content="record_type: gotcha\nclaim: Bad path split.\n",
        kind="memory",
        tags=["kind:gotcha"],
        title="[[Gotcha]] Bad path split.",
    )

    promoted = store.promote(created["id"], "gotcha")

    assert promoted["changed"] is False
    assert promoted["record_type"] == "gotcha"
    assert promoted["item"]["id"] == created["id"]


def test_export_returns_markdown_json_and_text(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db", log_dir=tmp_path / "logs")
    store.store(
        namespace="project:bridge",
        content="record_type: learn\nclaim: Keep one bridge DB.\n",
        kind="memory",
        tags=["kind:learn", "domain:memory-bridge"],
        title="[[Learn]] Keep one bridge DB.",
    )

    markdown_export = store.export(namespace="project:bridge", format="markdown")
    json_export = store.export(namespace="project:bridge", format="json")
    text_export = store.export(namespace="project:bridge", format="text")

    assert markdown_export["format"] == "markdown"
    assert "# Memory Export: project:bridge" in markdown_export["content"]
    assert json_export["format"] == "json"
    assert "\"namespace\": \"project:bridge\"" in json_export["content"]
    assert text_export["format"] == "text"
    assert "namespace: project:bridge" in text_export["content"]


def test_store_rejects_signal_expiry_for_memory_kind(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db", log_dir=tmp_path / "logs")

    with pytest.raises(ValueError, match="only valid for kind='signal'"):
        store.store(
            namespace="project:bridge",
            content="This is durable memory.",
            kind="memory",
            ttl_seconds=30,
        )

