from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from agent_mem_bridge.repository import fetch_row_by_id, fetch_tombstone_metadata
from agent_mem_bridge.storage import MemoryStore


def _store_generated(
    store: MemoryStore,
    *,
    record_type: str,
    content: str,
    tag: str,
) -> str:
    result = store.store(
        namespace="project:lineage",
        kind="memory",
        title=f"Generated {record_type}",
        content=f"record_type: {record_type}\n{content}",
        tags=[tag, "source:consolidation"],
        actor="bridge-consolidation",
        source_app="agent-memory-bridge-consolidation",
    )
    return str(result["id"])


def _add_embedding_sidecars(store: MemoryStore, memory_ids: list[str]) -> None:
    with store._connect() as conn:
        for memory_id in memory_ids:
            content_hash = conn.execute(
                "SELECT content_hash FROM memories WHERE id = ?",
                (memory_id,),
            ).fetchone()[0]
            conn.execute(
                """
                INSERT INTO memory_embeddings (
                    memory_id, content_hash, embedding_model, embedding_dim, vector_json, created_at
                ) VALUES (?, ?, 'test-model', 1, '[1.0]', '2026-07-15T00:00:00+00:00')
                """,
                (memory_id, content_hash),
            )
        conn.commit()


def test_forget_cascades_only_exact_machine_owned_lineage_and_tombstones_every_deletion(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db", log_dir=tmp_path / "logs")
    root_id = str(
        store.store(
            namespace="project:lineage",
            kind="memory",
            title="Sensitive source",
            content="record_type: learn\nclaim: Content that must not enter a tombstone.",
            tags=["kind:learn"],
        )["id"]
    )
    candidate_id = _store_generated(
        store,
        record_type="belief-candidate",
        content=f"evidence_refs: {root_id}",
        tag="kind:belief-candidate",
    )
    belief_id = _store_generated(
        store,
        record_type="belief",
        content=f"derived_from_candidate_id: {candidate_id}",
        tag="kind:belief",
    )
    concept_id = _store_generated(
        store,
        record_type="concept-note",
        content=f"derived_from_belief_id: {belief_id}\ndepends_on: {belief_id}",
        tag="kind:concept-note",
    )
    learning_candidate_id = str(
        store.store(
            namespace="project:lineage",
            kind="memory",
            title="Hidden candidate",
            content=(
                "record_type: learning-candidate\n"
                f'evidence_refs_json: {json.dumps([concept_id])}'
            ),
            tags=["kind:learning-candidate"],
            source_app="amb-learning-layer",
        )["id"]
    )
    review_id = str(
        store.store(
            namespace="project:lineage",
            kind="memory",
            title="Hidden review",
            content=(
                "record_type: learning-review\n"
                f"source_candidate_id: {learning_candidate_id}"
            ),
            tags=["kind:learning-review"],
            source_app="amb-learning-layer",
        )["id"]
    )
    trigger_id = str(
        store.store(
            namespace="project:lineage",
            kind="signal",
            title="Governance trigger",
            content=(
                "record_type: governance-trigger\n"
                f"candidate_id: {learning_candidate_id}"
            ),
            tags=["kind:governance-trigger"],
        )["id"]
    )
    semantic_id = str(
        store.store(
            namespace="project:lineage",
            kind="memory",
            title="Retained semantic dependent",
            content=f"record_type: domain-note\nsupports: {root_id}",
            tags=["kind:domain-note"],
        )["id"]
    )
    audit_id = str(
        store.store(
            namespace="project:lineage",
            kind="memory",
            title="Retained audit dependent",
            content=f"record_type: audit\ntarget_record_id: {candidate_id}",
            tags=["kind:audit"],
        )["id"]
    )
    prose_only_id = str(
        store.store(
            namespace="project:lineage",
            kind="memory",
            title="Prose mention",
            content=f"claim: The forgotten source was {root_id}, but this is not a lineage field.",
            tags=["kind:learn"],
        )["id"]
    )
    near_match_id = _store_generated(
        store,
        record_type="belief-candidate",
        content=f"evidence_refs: {root_id}-suffix",
        tag="kind:belief-candidate",
    )
    deletion_ids = [root_id, candidate_id, belief_id, concept_id, learning_candidate_id, review_id, trigger_id]
    _add_embedding_sidecars(store, deletion_ids)

    result = store.forget(root_id)

    assert result["deleted"] is True
    assert result["item"]["id"] == root_id
    assert result["tombstoned"] is True
    assert result["cascade_deleted_ids"] == deletion_ids[1:]
    assert result["retained_dependent_ids"] == [semantic_id, audit_id]

    with store._connect() as conn:
        remaining_deleted_rows = conn.execute(
            f"SELECT id FROM memories WHERE id IN ({','.join('?' for _ in deletion_ids)})",
            deletion_ids,
        ).fetchall()
        remaining_fts = conn.execute(
            f"SELECT memory_id FROM memories_fts WHERE memory_id IN ({','.join('?' for _ in deletion_ids)})",
            deletion_ids,
        ).fetchall()
        remaining_embeddings = conn.execute(
            f"SELECT memory_id FROM memory_embeddings WHERE memory_id IN ({','.join('?' for _ in deletion_ids)})",
            deletion_ids,
        ).fetchall()
        tombstone_columns = [row["name"] for row in conn.execute("PRAGMA table_info(memory_tombstones)").fetchall()]
        tombstones = conn.execute(
            "SELECT * FROM memory_tombstones ORDER BY deleted_at, forgotten_id"
        ).fetchall()
        semantic = fetch_row_by_id(conn, semantic_id)
        audit = fetch_row_by_id(conn, audit_id)
        prose_only = fetch_row_by_id(conn, prose_only_id)
        near_match = fetch_row_by_id(conn, near_match_id)

    assert remaining_deleted_rows == []
    assert remaining_fts == []
    assert remaining_embeddings == []
    assert tombstone_columns == ["forgotten_id", "namespace", "kind", "deleted_at", "root_forget_id", "cause"]
    assert {row["forgotten_id"] for row in tombstones} == set(deletion_ids)
    assert all(row["root_forget_id"] == root_id for row in tombstones)
    assert {row["cause"] for row in tombstones} == {"explicit_forget", "machine_derived_cascade"}
    assert "Content that must not enter a tombstone" not in json.dumps([dict(row) for row in tombstones])

    assert semantic is not None and semantic["lineage_status"] == "degraded"
    assert json.loads(semantic["lineage_issues_json"]) == [
        {
            "missing_record_id": root_id,
            "relations": ["supports"],
            "root_forget_id": root_id,
            "type": "missing_dependency",
        }
    ]
    assert audit is not None and audit["lineage_status"] == "degraded"
    assert json.loads(audit["lineage_issues_json"])[0]["missing_record_id"] == candidate_id
    assert prose_only is not None and prose_only["lineage_status"] == "intact"
    assert json.loads(prose_only["lineage_issues_json"]) == []
    assert near_match is not None and near_match["lineage_status"] == "intact"
    assert json.loads(near_match["lineage_issues_json"]) == []


def test_forget_retains_unproven_derived_row_and_marks_it_degraded(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db", log_dir=tmp_path / "logs")
    root_id = str(store.store(namespace="project:lineage", content="Source", kind="memory")["id"])
    manual_id = str(
        store.store(
            namespace="project:lineage",
            content=f"record_type: belief\nderived_from_candidate_id: {root_id}",
            kind="memory",
            tags=["kind:belief"],
        )["id"]
    )

    result = store.forget(root_id)

    assert result["cascade_deleted_ids"] == []
    assert result["retained_dependent_ids"] == [manual_id]
    with store._connect() as conn:
        retained = fetch_row_by_id(conn, manual_id)
    assert retained is not None
    assert retained["lineage_status"] == "degraded"


def test_forget_predecessor_keeps_current_superseder_intact(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db", log_dir=tmp_path / "logs")
    predecessor_id = str(
        store.store(namespace="project:lineage", content="record_type: learn\nclaim: Old guidance", kind="memory")["id"]
    )
    superseder_id = str(
        store.store(
            namespace="project:lineage",
            content=f"record_type: learn\nclaim: Current guidance\nsupersedes: {predecessor_id}",
            kind="memory",
        )["id"]
    )

    result = store.forget(predecessor_id)

    assert result["cascade_deleted_ids"] == []
    assert result["retained_dependent_ids"] == []
    with store._connect() as conn:
        superseder = fetch_row_by_id(conn, superseder_id)
    assert superseder is not None
    assert superseder["lineage_status"] == "intact"
    assert json.loads(superseder["lineage_issues_json"]) == []


def test_forget_superseder_retains_predecessor_as_degraded_audit_lineage(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db", log_dir=tmp_path / "logs")
    predecessor_id = str(
        store.store(
            namespace="project:lineage",
            content="record_type: learn\nclaim: Prior guidance retained for audit",
            kind="memory",
        )["id"]
    )
    superseder_id = str(
        store.store(
            namespace="project:lineage",
            content=f"record_type: learn\nclaim: Current guidance\nsupersedes: {predecessor_id}",
            kind="memory",
        )["id"]
    )
    prose_only_id = str(
        store.store(
            namespace="project:lineage",
            content=f"record_type: learn\nclaim: This note replaced {predecessor_id} in prose only",
            kind="memory",
        )["id"]
    )

    result = store.forget(superseder_id)

    assert result["cascade_deleted_ids"] == []
    assert result["retained_dependent_ids"] == [predecessor_id]
    with store._connect() as conn:
        predecessor = fetch_row_by_id(conn, predecessor_id)
        prose_only = fetch_row_by_id(conn, prose_only_id)
        tombstone = fetch_tombstone_metadata(conn, superseder_id)
    assert predecessor is not None and predecessor["lineage_status"] == "degraded"
    assert json.loads(predecessor["lineage_issues_json"]) == [
        {
            "missing_record_id": superseder_id,
            "root_forget_id": superseder_id,
            "type": "forgotten_superseder",
        }
    ]
    assert prose_only is not None and prose_only["lineage_status"] == "intact"
    assert json.loads(prose_only["lineage_issues_json"]) == []
    assert tombstone is not None
    assert "content" not in tombstone
    assert "Current guidance" not in json.dumps(tombstone)


def test_forget_cascade_deleted_superseder_degrades_retained_predecessor(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db", log_dir=tmp_path / "logs")
    root_id = str(store.store(namespace="project:lineage", content="Root source", kind="memory")["id"])
    predecessor_id = str(
        store.store(
            namespace="project:lineage",
            content="record_type: learn\nclaim: Prior guidance retained for audit",
            kind="memory",
        )["id"]
    )
    superseder_id = _store_generated(
        store,
        record_type="belief-candidate",
        content=f"evidence_refs: {root_id}\nsupersedes: {predecessor_id}",
        tag="kind:belief-candidate",
    )

    result = store.forget(root_id)

    assert result["cascade_deleted_ids"] == [superseder_id]
    assert result["retained_dependent_ids"] == [predecessor_id]
    with store._connect() as conn:
        predecessor = fetch_row_by_id(conn, predecessor_id)
        superseder_tombstone = fetch_tombstone_metadata(conn, superseder_id)
    assert predecessor is not None and predecessor["lineage_status"] == "degraded"
    assert json.loads(predecessor["lineage_issues_json"]) == [
        {
            "missing_record_id": superseder_id,
            "root_forget_id": root_id,
            "type": "forgotten_superseder",
        }
    ]
    assert superseder_tombstone is not None
    assert superseder_tombstone["cause"] == "machine_derived_cascade"
    assert "content" not in superseder_tombstone


def test_forget_review_target_retains_receipt_until_source_candidate_is_deleted(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db", log_dir=tmp_path / "logs")
    source_candidate_id = str(
        store.store(
            namespace="project:lineage",
            content="record_type: learning-candidate\nclaim: Candidate under review",
            kind="memory",
            tags=["kind:learning-candidate"],
            source_app="amb-learning-layer",
        )["id"]
    )
    durable_target_id = str(
        store.store(
            namespace="project:lineage",
            content="record_type: learn\nclaim: Reviewed durable target",
            kind="memory",
            tags=["kind:learn"],
        )["id"]
    )
    review_id = str(
        store.store(
            namespace="project:lineage",
            content=(
                "record_type: learning-review\n"
                f"source_candidate_id: {source_candidate_id}\n"
                f"target_record_id: {durable_target_id}\n"
                f"supersedes: {durable_target_id}"
            ),
            kind="memory",
            tags=["kind:learning-review"],
            source_app="amb-learning-layer",
        )["id"]
    )

    target_result = store.forget(durable_target_id)

    assert target_result["cascade_deleted_ids"] == []
    assert target_result["retained_dependent_ids"] == [review_id]
    with store._connect() as conn:
        review = fetch_row_by_id(conn, review_id)
        target_tombstone = fetch_tombstone_metadata(conn, durable_target_id)
    assert review is not None and review["lineage_status"] == "degraded"
    assert json.loads(review["lineage_issues_json"]) == [
        {
            "missing_record_id": durable_target_id,
            "relations": ["target_record_id"],
            "root_forget_id": durable_target_id,
            "type": "missing_dependency",
        }
    ]
    assert target_tombstone is not None
    assert set(target_tombstone) == {
        "forgotten_id",
        "namespace",
        "kind",
        "deleted_at",
        "root_forget_id",
        "cause",
    }
    assert target_tombstone["forgotten_id"] == durable_target_id
    assert target_tombstone["cause"] == "explicit_forget"

    source_result = store.forget(source_candidate_id)

    assert source_result["cascade_deleted_ids"] == [review_id]
    with store._connect() as conn:
        assert fetch_row_by_id(conn, review_id) is None
        review_tombstone = fetch_tombstone_metadata(conn, review_id)
    assert review_tombstone is not None
    assert review_tombstone["root_forget_id"] == source_candidate_id
    assert review_tombstone["cause"] == "machine_derived_cascade"


def test_forget_rolls_back_degradation_tombstones_and_sidecar_deletes_on_error(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db", log_dir=tmp_path / "logs")
    root_id = str(store.store(namespace="project:lineage", content="Rollback source", kind="memory")["id"])
    predecessor_id = str(
        store.store(
            namespace="project:lineage",
            content="record_type: learn\nclaim: Rollback predecessor",
            kind="memory",
        )["id"]
    )
    candidate_id = _store_generated(
        store,
        record_type="belief-candidate",
        content=f"evidence_refs: {root_id}\nsupersedes: {predecessor_id}",
        tag="kind:belief-candidate",
    )
    dependent_id = str(
        store.store(
            namespace="project:lineage",
            content=f"record_type: note\ndepends_on: {root_id}",
            kind="memory",
        )["id"]
    )
    _add_embedding_sidecars(store, [root_id, candidate_id])
    with store._connect() as conn:
        conn.execute(
            """
            CREATE TRIGGER reject_tombstone
            BEFORE INSERT ON memory_tombstones
            BEGIN
                SELECT RAISE(ABORT, 'forced tombstone failure');
            END
            """
        )
        conn.commit()

    with pytest.raises(sqlite3.IntegrityError, match="forced tombstone failure"):
        store.forget(root_id)

    with store._connect() as conn:
        memory_ids = {row["id"] for row in conn.execute("SELECT id FROM memories").fetchall()}
        fts_ids = {row["memory_id"] for row in conn.execute("SELECT memory_id FROM memories_fts").fetchall()}
        embedding_ids = {row["memory_id"] for row in conn.execute("SELECT memory_id FROM memory_embeddings").fetchall()}
        tombstone_count = conn.execute("SELECT COUNT(*) FROM memory_tombstones").fetchone()[0]
        root_tombstone = fetch_tombstone_metadata(conn, root_id)
        dependent = fetch_row_by_id(conn, dependent_id)
        predecessor = fetch_row_by_id(conn, predecessor_id)

    assert {root_id, predecessor_id, candidate_id, dependent_id}.issubset(memory_ids)
    assert {root_id, predecessor_id, candidate_id}.issubset(fts_ids)
    assert embedding_ids == {root_id, candidate_id}
    assert tombstone_count == 0
    assert root_tombstone is None
    assert dependent is not None and dependent["lineage_status"] == "intact"
    assert json.loads(dependent["lineage_issues_json"]) == []
    assert predecessor is not None and predecessor["lineage_status"] == "intact"
    assert json.loads(predecessor["lineage_issues_json"]) == []


def test_forget_missing_id_response_remains_compatible(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db", log_dir=tmp_path / "logs")

    assert store.forget("missing-id") == {"id": "missing-id", "deleted": False, "item": None}
