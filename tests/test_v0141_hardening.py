from __future__ import annotations

import sqlite3
from pathlib import Path
from threading import Thread

import pytest

from agent_mem_bridge import server
from agent_mem_bridge.learning_policy import evaluate_learning_candidate
from agent_mem_bridge.schema import ensure_column, init_db
from agent_mem_bridge.storage import MemoryStore


def _candidate(**overrides):
    candidate = {
        "schema": "memory.candidate.v1",
        "namespace": "project:mem-store",
        "authority_class": "context_hint",
        "claim": "AMB hardening must prevent schema and learning-candidate bypasses.",
        "evidence_refs": ["pytest: tests/test_v0141_hardening.py"],
        "source_runtime": "hermes",
        "source_session_id": "session-1",
        "source_task_id": "task-1",
        "sensitivity": "safe",
        "created_by": "cole",
    }
    candidate.update(overrides)
    return candidate


def test_store_normalizes_static_schema_empty_optional_fields(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")

    created = store.store(
        namespace=" project:mem-store ",
        content="Static-schema clients may send empty strings for absent optional fields.",
        kind="memory",
        tags=[],
        session_id="",
        actor="",
        correlation_id="",
        source_app="",
        source_client="",
        source_model="",
        client_session_id="",
        client_workspace="",
        client_transport="",
        expires_at="",
    )
    item = store.recall(namespace="project:mem-store", query="static schema", limit=1)["items"][0]

    assert created["stored"] is True
    assert item["session_id"] is None
    assert item["actor"] is None
    assert item["correlation_id"] is None
    assert item["source_app"] is None
    assert item["source_client"] is None
    assert item["source_model"] is None
    assert item["client_session_id"] is None
    assert item["client_workspace"] is None
    assert item["client_transport"] is None
    assert item["signal_status"] is None


def test_server_store_normalizes_static_schema_empty_optional_fields(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = MemoryStore(tmp_path / "server.db", log_dir=tmp_path / "logs")
    monkeypatch.setattr(server, "bridge", store)

    created = server.store(
        namespace="project:mem-store",
        content="MCP static schema sent empty optional placeholders.",
        kind="memory",
        tags=[],
        session_id="",
        actor="",
        title="",
        correlation_id="",
        source_app="",
        source_client="",
        source_model="",
        client_session_id="",
        client_workspace="",
        client_transport="",
        expires_at="",
        ttl_seconds=None,
    )
    item = store.recall(namespace="project:mem-store", query="static schema", limit=1)["items"][0]

    assert created["stored"] is True
    assert item["actor"] is None
    assert item["session_id"] is None
    assert item["correlation_id"] is None


def test_claim_signal_normalizes_empty_static_schema_filters(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    created = store.store(
        namespace="project:bridge",
        content="Claim filters with empty placeholders must behave as absent filters.",
        kind="signal",
        tags=["handoff:review"],
        correlation_id="real-correlation",
    )

    claimed = store.claim_signal(
        namespace="project:bridge",
        consumer="worker-a",
        lease_seconds=60,
        signal_id="",
        tags_any=[],
        correlation_id="",
    )

    assert claimed["claimed"] is True
    assert claimed["signal_id"] == created["id"]


def test_server_claim_signal_normalizes_empty_static_schema_filters(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = MemoryStore(tmp_path / "server.db", log_dir=tmp_path / "logs")
    monkeypatch.setattr(server, "bridge", store)
    created = server.store(
        namespace="project:bridge",
        content="Server claim filters with empty placeholders must behave as absent filters.",
        kind="signal",
        tags=["handoff:review"],
        correlation_id="real-correlation",
    )

    claimed = server.claim_signal(
        namespace="project:bridge",
        consumer="worker-a",
        lease_seconds=60,
        signal_id="",
        tags_any=[],
        correlation_id="",
    )

    assert claimed["claimed"] is True
    assert claimed["signal_id"] == created["id"]


def test_sqlite_connection_sets_busy_timeout(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")

    with store._connect() as conn:
        busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]

    assert busy_timeout >= 5000


def test_legacy_learning_candidate_rows_are_backfilled_hidden(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE memories (
            id TEXT PRIMARY KEY,
            namespace TEXT NOT NULL,
            kind TEXT NOT NULL,
            title TEXT,
            content TEXT NOT NULL,
            tags_json TEXT NOT NULL DEFAULT '[]',
            session_id TEXT,
            actor TEXT,
            correlation_id TEXT,
            source_app TEXT,
            source_client TEXT,
            source_model TEXT,
            client_session_id TEXT,
            client_workspace TEXT,
            client_transport TEXT,
            signal_status TEXT,
            claimed_by TEXT,
            claimed_at TEXT,
            lease_expires_at TEXT,
            expires_at TEXT,
            acknowledged_at TEXT,
            content_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE VIRTUAL TABLE memories_fts USING fts5(memory_id UNINDEXED, content);
        INSERT INTO memories (id, namespace, kind, title, content, tags_json, content_hash, created_at)
        VALUES ('legacy-candidate', 'project:mem-store', 'memory', 'Legacy candidate', 'legacy candidate hardening row', '["kind:learning-candidate"]', 'hash-1', '2026-01-01T00:00:00+00:00');
        INSERT INTO memories (id, namespace, kind, title, content, tags_json, content_hash, created_at)
        VALUES ('legacy-review', 'project:mem-store', 'memory', 'Legacy review', 'legacy review hardening row', '["kind:learning-review"]', 'hash-2', '2026-01-01T00:00:01+00:00');
        INSERT INTO memories_fts(memory_id, content) VALUES ('legacy-candidate', 'legacy candidate hardening row');
        INSERT INTO memories_fts(memory_id, content) VALUES ('legacy-review', 'legacy review hardening row');
        """
    )
    conn.commit()
    conn.close()

    with sqlite3.connect(db_path) as upgraded:
        upgraded.row_factory = sqlite3.Row
        init_db(upgraded)
        candidate_row = upgraded.execute("SELECT is_learning_candidate FROM memories WHERE id = 'legacy-candidate'").fetchone()
        review_row = upgraded.execute("SELECT is_learning_candidate FROM memories WHERE id = 'legacy-review'").fetchone()

    store = MemoryStore(db_path, log_dir=tmp_path / "logs")

    assert candidate_row["is_learning_candidate"] == 1
    assert review_row["is_learning_candidate"] == 1
    assert store.recall(namespace="project:mem-store", query="legacy", limit=10)["count"] == 0
    assert store.recall(namespace="project:mem-store", tags_any=["kind:learning-candidate"], limit=10)["count"] == 1
    assert store.recall(namespace="project:mem-store", tags_any=["kind:learning-review"], limit=10)["count"] == 1


def test_new_schema_has_learning_candidate_visibility_column(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")

    with store._connect() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(memories)")}
        indexes = {row["name"] for row in conn.execute("PRAGMA index_list(memories)")}

    assert "is_learning_candidate" in columns
    assert "idx_memories_learning_candidate_visible" in indexes


def test_fts_startup_rebuild_is_concurrency_safe_smoke(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    store.store(namespace="project:bridge", kind="memory", content="FTS rebuild concurrency smoke row.")

    with store._connect() as conn:
        conn.execute("DROP TABLE memories_fts")
        conn.commit()

    errors: list[BaseException] = []

    def open_store() -> None:
        try:
            MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
        except BaseException as exc:  # pragma: no cover - surfaced by assertion below
            errors.append(exc)

    threads = [Thread(target=open_store) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert store.recall(namespace="project:bridge", query="concurrency", limit=1)["count"] == 1


def test_ensure_column_rejects_untrusted_identifiers() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE memories (id TEXT PRIMARY KEY)")

    with pytest.raises(ValueError, match="SQL identifier"):
        ensure_column(conn, "memories); DROP TABLE memories; --", "actor", "ALTER TABLE memories ADD COLUMN actor TEXT")


def test_ensure_column_handles_concurrent_duplicate_column_race() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE memories (id TEXT PRIMARY KEY, actor TEXT)")

    ensure_column(conn, "memories", "actor", "ALTER TABLE memories ADD COLUMN actor TEXT")

    columns = {row["name"] for row in conn.execute("PRAGMA table_info(memories)")}
    assert "actor" in columns


def test_learning_candidate_suppression_uses_exact_tag_membership(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    forged = store.store(
        namespace="project:mem-store",
        kind="memory",
        title="Forged candidate-looking durable note",
        content="This contains the text kind:learning-candidate but is not tagged as one.",
        tags=["domain:test"],
    )
    candidate = _candidate()
    stored_candidate = store.store_learning_candidate(candidate, evaluate_learning_candidate(candidate))

    normal = store.recall(namespace="project:mem-store", query="candidate", limit=10)
    review = store.recall(namespace="project:mem-store", tags_any=["kind:learning-candidate"], limit=10)

    assert {item["id"] for item in normal["items"]} == {forged["id"]}
    assert {item["id"] for item in review["items"]} == {stored_candidate["id"]}


def test_external_ladder_candidate_tag_is_hidden_by_default(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    stored = store.store(
        namespace="project:mem-store",
        kind="memory",
        title="[[Learning Candidate]] Hermes memory add: memory",
        content="\n".join(
            [
                "record_type: learning-candidate",
                "schema: memory.candidate.v1",
                "candidate_status: needs_review",
                "candidate_ref: project:mem-store:session-1:memory-add-memory:abc123",
                "decision: needs_review",
                "would_write: false",
                "authority_class: belief_proposal",
                "source_runtime: hermes",
                "source_session_id: session-1",
                "source_task_id: memory-add-memory",
                "claim: Hermes built-in memory add proposal for target=memory: external ladder candidate",
                'evidence_refs_json: ["hermes-memory:session-1:add:memory"]',
                'decision_reasons_json: ["review_required"]',
                'domain_tags_json: ["domain:hermes-memory", "domain:agent-memory"]',
                "confidence: tentative",
            ]
        ),
        tags=[
            "kind:learning-candidate",
            "candidate_status:needs_review",
            "source_runtime:hermes",
            "authority_class:belief_proposal",
            "decision:needs_review",
            "schema:memory.candidate.v1",
            "schema:memory.writeback_decision.v1",
            "hermes-memory",
            "memory-ladder",
        ],
    )

    recalled = store.recall(namespace="project:mem-store", query="external ladder candidate", kind="memory", limit=10)
    browsed = store.browse(namespace="project:mem-store", kind="memory", limit=10)
    exported = store.export(namespace="project:mem-store", format="json", kind="memory", limit=10)
    review = store.recall(namespace="project:mem-store", tags_any=["kind:learning-candidate"], limit=10)

    assert recalled["count"] == 0
    assert browsed["count"] == 0
    assert exported["count"] == 0
    assert review["count"] == 1
    assert review["items"][0]["id"] == stored["id"]
    assert review["items"][0]["is_learning_candidate"] is True


def test_server_recall_and_export_keep_candidates_hidden_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = MemoryStore(tmp_path / "server.db", log_dir=tmp_path / "logs")
    monkeypatch.setattr(server, "bridge", store)
    candidate = _candidate()
    store.store_learning_candidate(candidate, evaluate_learning_candidate(candidate))

    recalled = server.recall(namespace="project:mem-store", query="hardening", kind="memory", tags_any=[], limit=10)
    exported = server.export(namespace="project:mem-store", format="json", kind="memory", tags_any=[], limit=10)
    review = server.recall(namespace="project:mem-store", tags_any=["kind:learning-candidate"], limit=10)

    assert recalled["count"] == 0
    assert exported["count"] == 0
    assert review["count"] == 1


def test_learning_candidate_suppression_is_not_bypassed_by_empty_kind_filter(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    candidate = _candidate()
    store.store_learning_candidate(candidate, evaluate_learning_candidate(candidate))

    recalled = store.recall(namespace="project:mem-store", query="hardening", kind="memory", limit=10)
    browsed = store.browse(namespace="project:mem-store", kind="memory", limit=10)
    exported = store.export(namespace="project:mem-store", format="json", kind="memory", limit=10)

    assert recalled["count"] == 0
    assert browsed["count"] == 0
    assert exported["count"] == 0


def test_promote_rejects_learning_candidate_records(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    candidate = _candidate()
    stored = store.store_learning_candidate(candidate, evaluate_learning_candidate(candidate))

    with pytest.raises(ValueError, match="learning candidates cannot be promoted"):
        store.promote(stored["id"], "learn")


def test_promote_does_not_trust_forged_kind_tags(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    forged = store.store(
        namespace="project:mem-store",
        kind="memory",
        title="Forged reflex position",
        content="record_type: learning-candidate\nclaim: A forged tag must not make this promotable.",
        tags=["kind:learn", "confidence:manual"],
    )

    with pytest.raises(ValueError, match="learning candidates cannot be promoted"):
        store.promote(forged["id"], "gotcha")
