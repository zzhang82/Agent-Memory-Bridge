from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from agent_mem_bridge.schema import CURRENT_SCHEMA_VERSION, schema_version
from agent_mem_bridge.storage import MemoryStore


def test_schema_v2_persists_canonical_metadata_tags_and_indexed_edges(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    source_id = str(
        store.store(
            namespace="project:projection",
            content="record_type: learn\nclaim: Source evidence.",
            tags=["kind:learn", "domain:test"],
        )["id"]
    )
    dependent_id = str(
        store.store(
            namespace="project:projection",
            content=(
                "record_type: belief-candidate\n"
                "status: active\n"
                "confidence: 0.82\n"
                "valid_from: 2026-01-01T00:00:00+00:00\n"
                "valid_until: 2027-01-01T00:00:00+00:00\n"
                f"evidence_refs: {source_id}"
            ),
            tags=["kind:belief-candidate", "source:consolidation", "domain:test"],
            actor="bridge-consolidation",
        )["id"]
    )

    with store._connect() as conn:
        assert schema_version(conn) == CURRENT_SCHEMA_VERSION == 4
        metadata = conn.execute(
            "SELECT * FROM memory_metadata WHERE memory_id = ?",
            (dependent_id,),
        ).fetchone()
        tags = {
            row["tag"]
            for row in conn.execute(
                "SELECT tag FROM memory_tags WHERE memory_id = ?",
                (dependent_id,),
            ).fetchall()
        }
        edge = conn.execute(
            "SELECT * FROM memory_edges WHERE source_id = ? AND target_id = ?",
            (dependent_id, source_id),
        ).fetchone()
        indexes = {row["name"] for row in conn.execute("PRAGMA index_list(memory_edges)").fetchall()}

    assert metadata["record_type"] == "belief-candidate"
    assert metadata["status"] == "active"
    assert metadata["confidence"] == pytest.approx(0.82)
    assert metadata["valid_from"] == "2026-01-01T00:00:00+00:00"
    assert metadata["valid_until"] == "2027-01-01T00:00:00+00:00"
    assert metadata["validation_issues_json"] == "[]"
    assert tags == {
        "kind:belief-candidate",
        "source:consolidation",
        "domain:test",
        "validity:bounded",
    }
    assert edge["relation"] == "evidence_refs"
    assert edge["machine_owned"] == 1
    assert edge["target_namespace"] == "project:projection"
    assert edge["target_exists"] == 1
    assert "idx_memory_edges_target_machine" in indexes


@pytest.mark.parametrize(
    ("content", "issue"),
    [
        (
            "record_type: learn\nstatus: definitely-not-a-status\nclaim: Invalid status.",
            "invalid_status",
        ),
        (
            "record_type: learn\nconfidence: NaN\nclaim: Invalid confidence.",
            "invalid_confidence",
        ),
        (
            "record_type: learn\nvalid_from: 2027-01-01T00:00:00+00:00\n"
            "valid_until: 2026-01-01T00:00:00+00:00\nclaim: Invalid interval.",
            "invalid_validity_interval",
        ),
    ],
)
def test_new_writes_reject_invalid_canonical_metadata(
    tmp_path: Path,
    content: str,
    issue: str,
) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")

    with pytest.raises(ValueError, match=issue):
        store.store(
            namespace="project:projection",
            content=content,
            tags=["kind:learn"],
        )

    with store._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM memory_metadata").fetchone()[0] == 0


def test_new_writes_reject_cross_namespace_internal_lineage_targets(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    target_id = str(
        store.store(
            namespace="project:other",
            content="record_type: learn\nclaim: Other namespace evidence.",
            tags=["kind:learn"],
        )["id"]
    )

    with pytest.raises(ValueError, match="cross_namespace_lineage_target"):
        store.store(
            namespace="project:projection",
            content=f"record_type: learning-candidate\nevidence_refs: {target_id}",
            tags=["kind:learning-candidate"],
        )


def test_v1_upgrade_backfills_projection_tables_transactionally(tmp_path: Path) -> None:
    db_path = tmp_path / "bridge.db"
    store = MemoryStore(db_path, log_dir=tmp_path / "logs")
    memory_id = str(
        store.store(
            namespace="project:projection",
            content="record_type: gotcha\nconfidence: observed\nclaim: Backfill this row.",
            tags=["kind:gotcha", "topic:migration"],
        )["id"]
    )
    with store._connect() as conn:
        conn.execute("DELETE FROM memory_edges")
        conn.execute("DELETE FROM memory_tags")
        conn.execute("DELETE FROM memory_metadata")
        conn.execute("PRAGMA user_version = 1")
        conn.commit()

    upgraded = MemoryStore(db_path, log_dir=tmp_path / "logs")

    with upgraded._connect() as conn:
        metadata = conn.execute(
            "SELECT record_type, confidence_label FROM memory_metadata WHERE memory_id = ?",
            (memory_id,),
        ).fetchone()
        tags = conn.execute(
            "SELECT tag FROM memory_tags WHERE memory_id = ? ORDER BY tag",
            (memory_id,),
        ).fetchall()
    assert tuple(metadata) == ("gotcha", "observed")
    assert [row["tag"] for row in tags] == ["kind:gotcha", "topic:migration"]


def test_forget_uses_indexed_projection_instead_of_full_memory_scan(tmp_path: Path) -> None:
    statements: list[str] = []

    class TracedStore(MemoryStore):
        def _connect(self) -> sqlite3.Connection:
            conn = super()._connect()
            conn.set_trace_callback(statements.append)
            return conn

    store = TracedStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    for index in range(500):
        store.store(
            namespace="project:unrelated",
            content=f"record_type: learn\nclaim: Unrelated row {index}.",
            tags=["kind:learn"],
        )
    root_id = str(
        store.store(
            namespace="project:projection",
            content="record_type: learn\nclaim: Forget root.",
            tags=["kind:learn"],
        )["id"]
    )
    child_id = str(
        store.store(
            namespace="project:projection",
            content=f"record_type: belief-candidate\nevidence_refs: {root_id}",
            tags=["kind:belief-candidate", "source:consolidation"],
            actor="bridge-consolidation",
        )["id"]
    )
    statements.clear()

    result = store.forget(root_id)

    normalized = [" ".join(statement.upper().split()) for statement in statements]
    assert result["cascade_deleted_ids"] == [child_id]
    assert any("IDX_MEMORY_EDGES_TARGET_MACHINE" in statement for statement in _query_plans(store))
    assert not any(
        "FROM MEMORIES ORDER BY CREATED_AT" in statement or "FROM MEMORIES ORDER BY CREATED_AT ASC" in statement
        for statement in normalized
    )


def _query_plans(store: MemoryStore) -> list[str]:
    with store._connect() as conn:
        rows = conn.execute(
            """
            EXPLAIN QUERY PLAN
            SELECT e.source_id
            FROM memory_edges e
            JOIN memories m ON m.id = e.source_id
            WHERE e.machine_owned = 1 AND e.target_id = ?
            """,
            ("probe",),
        ).fetchall()
    return [str(row["detail"]).upper() for row in rows]
