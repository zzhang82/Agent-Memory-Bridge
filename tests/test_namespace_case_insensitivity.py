from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from agent_mem_bridge.repository_snapshot_store import BINDING_STORE_SCHEMA, RepositorySnapshotStore
from agent_mem_bridge.schema import exact_content_hash
from agent_mem_bridge.storage import MemoryStore


def test_store_deduplication_canonicalizes_namespace(tmp_path: Path) -> None:
    home = tmp_path / "amb-home"
    home.mkdir()
    store = MemoryStore(home / "bridge.db", log_dir=home / "logs")

    first = store.store(
        namespace="project:Moebius",
        content="Important trading rule for Moebius",
        title="Moebius rule",
        kind="memory",
    )
    assert first["id"] is not None
    assert first["duplicate_of"] is None

    second = store.store(
        namespace="project:moebius",
        content="Important trading rule for Moebius",
        title="Moebius rule duplicate",
        kind="memory",
    )
    assert second["duplicate_of"] == first["id"]

    recalled = store.recall(
        namespace="project:moebius",
        query="trading rule",
        kind="memory",
    )
    assert recalled["count"] == 1
    assert recalled["items"][0]["id"] == first["id"]


def test_feedback_with_mixed_case_namespaces(tmp_path: Path) -> None:
    home = tmp_path / "amb-home"
    home.mkdir()
    store = MemoryStore(home / "bridge.db", log_dir=home / "logs")

    stored = store.store(
        namespace="project:Moebius",
        content="Alpha generation pattern",
        title="Alpha pattern",
        kind="memory",
    )
    memory_id = stored["id"]

    recalled = store.recall(
        namespace="project:moebius",
        query="Alpha generation",
        kind="memory",
    )
    receipt = recalled["recall_receipt"]["token"]

    result = store.feedback(
        namespace="project:moebius",
        recall_receipt=receipt,
        memory_id=memory_id,
        result_rank=1,
        outcome="helpful",
    )
    assert result["stored"] is True
    assert result["duplicate"] is False

    # A retry of feedback under upper case namespace is identified as a duplicate of the same vote
    result_upper = store.feedback(
        namespace="project:MOEBIUS",
        recall_receipt=receipt,
        memory_id=memory_id,
        result_rank=1,
        outcome="helpful",
    )
    assert result_upper["duplicate"] is True
    assert result_upper["feedback_id"] == result["feedback_id"]


def test_signals_poll_and_claim_canonicalize_namespace(tmp_path: Path) -> None:
    home = tmp_path / "amb-home"
    home.mkdir()
    store = MemoryStore(home / "bridge.db", log_dir=home / "logs")

    stored = store.store(
        namespace="project:Moebius",
        content="Rebalance portfolio signal",
        title="Rebalance signal",
        kind="signal",
    )
    signal_id = stored["id"]

    polled = store.recall(
        namespace="project:moebius",
        kind="signal",
        signal_status="pending",
    )
    assert polled["count"] == 1
    assert polled["items"][0]["id"] == signal_id

    claim_result = store.claim_signal(
        namespace="project:moebius",
        consumer="agent-trader",
        lease_seconds=60,
        signal_id=signal_id,
    )
    assert claim_result["claimed"] is True
    assert claim_result["item"]["id"] == signal_id

    ack_result = store.ack_signal(
        memory_id=signal_id,
        consumer="agent-trader",
    )
    assert ack_result["acked"] is True


def test_database_migration_normalizes_existing_mixed_case_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        from agent_mem_bridge.schema import init_db

        init_db(conn)
        content = "Legacy memory with mixed case namespace"
        conn.execute(
            """
            INSERT INTO memories (
                id, namespace, kind, title, content, content_hash, exact_content_hash, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-1",
                "project:LegacyMoebius",
                "memory",
                "Legacy Title",
                content,
                "dummy_hash",
                exact_content_hash(content),
                "2026-01-01T00:00:00Z",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    store = MemoryStore(db_path, log_dir=tmp_path / "logs")

    recalled = store.recall(
        namespace="project:legacymoebius",
        query="Legacy memory",
        kind="memory",
    )
    assert recalled["count"] == 1
    assert recalled["items"][0]["id"] == "legacy-1"
    assert recalled["items"][0]["namespace"] == "project:legacymoebius"


def test_migration_collapses_multiple_mixed_case_duplicates_into_survivor(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy_dups.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        from agent_mem_bridge.schema import init_db

        init_db(conn)
        content = "Identical content in Moebius and MOEBIUS"
        h = exact_content_hash(content)

        # Older record: project:Moebius (survivor)
        conn.execute(
            """
            INSERT INTO memories (id, namespace, kind, title, content, content_hash, exact_content_hash, created_at)
            VALUES ('m-orig', 'project:Moebius', 'memory', 'Original Title', ?, 'hash1', ?, '2026-01-01T00:00:00Z')
            """,
            (content, h),
        )
        conn.execute("INSERT INTO memory_metadata (memory_id, metadata_schema_version) VALUES ('m-orig', 1)")
        conn.execute(
            "INSERT INTO memories_fts (memory_id, title, content) VALUES ('m-orig', 'Original Title', ?)",
            (content,),
        )

        # Newer record: project:MOEBIUS (duplicate to be merged)
        conn.execute(
            """
            INSERT INTO memories (id, namespace, kind, title, content, content_hash, exact_content_hash, created_at)
            VALUES ('m-dup', 'project:MOEBIUS', 'memory', 'Duplicate Title', ?, 'hash1', ?, '2026-01-02T00:00:00Z')
            """,
            (content, h),
        )
        conn.execute("INSERT INTO memory_metadata (memory_id, metadata_schema_version) VALUES ('m-dup', 1)")
        conn.execute(
            "INSERT INTO memories_fts (memory_id, title, content) VALUES ('m-dup', 'Duplicate Title', ?)",
            (content,),
        )

        # Outgoing edge on duplicate
        conn.execute(
            """
            INSERT INTO memory_edges (source_id, target_id, relation, position, machine_owned, target_namespace, target_exists)
            VALUES ('m-dup', 'target-node', 'supersedes', 0, 0, 'project:Moebius', 1)
            """
        )

        # Incoming edge pointing to duplicate from another node
        conn.execute(
            """
            INSERT INTO memories (id, namespace, kind, title, content, content_hash, exact_content_hash, created_at)
            VALUES ('m-other', 'project:other', 'memory', 'Other', 'Other content', 'h2', 'h2', '2026-01-03T00:00:00Z')
            """
        )
        conn.execute("INSERT INTO memory_metadata (memory_id, metadata_schema_version) VALUES ('m-other', 1)")
        conn.execute(
            """
            INSERT INTO memory_edges (source_id, target_id, relation, position, machine_owned, target_namespace, target_exists)
            VALUES ('m-other', 'm-dup', 'depends_on', 0, 0, 'project:MOEBIUS', 1)
            """
        )

        # Annotation on duplicate
        conn.execute(
            """
            INSERT INTO memory_annotations (memory_id, title_before, title_after, added_tags_json, provenance_json, created_at)
            VALUES ('m-dup', 'Title A', 'Title B', '[]', '{}', '2026-01-02T00:00:00Z')
            """
        )
        conn.commit()
    finally:
        conn.close()

    # MemoryStore init must not raise UNIQUE constraint failed
    store = MemoryStore(db_path, log_dir=tmp_path / "logs")

    with store._connect() as check_conn:
        mems = check_conn.execute("SELECT id, namespace FROM memories").fetchall()
        mem_map = {row["id"]: row["namespace"] for row in mems}
        # Original survived, duplicate removed
        assert "m-orig" in mem_map
        assert "m-dup" not in mem_map
        assert mem_map["m-orig"] == "project:moebius"

        # Outgoing edge moved to survivor
        out_edge = check_conn.execute(
            "SELECT source_id, target_id, relation, target_namespace FROM memory_edges WHERE target_id = 'target-node'"
        ).fetchone()
        assert out_edge["source_id"] == "m-orig"
        assert out_edge["target_namespace"] == "project:moebius"

        # Incoming edge moved to point to survivor
        in_edge = check_conn.execute(
            "SELECT source_id, target_id, relation, target_namespace FROM memory_edges WHERE source_id = 'm-other'"
        ).fetchone()
        assert in_edge["target_id"] == "m-orig"
        assert in_edge["target_namespace"] == "project:moebius"

        # Annotation moved to survivor
        ann = check_conn.execute(
            "SELECT memory_id, title_after FROM memory_annotations WHERE memory_id = 'm-orig'"
        ).fetchone()
        assert ann is not None
        assert ann["title_after"] == "Title B"

        # memories_fts has survivor and no orphan
        fts_orig = check_conn.execute(
            "SELECT COUNT(*) AS count FROM memories_fts WHERE memory_id = 'm-orig'"
        ).fetchone()["count"]
        assert fts_orig == 1
        fts_dup = check_conn.execute("SELECT COUNT(*) AS count FROM memories_fts WHERE memory_id = 'm-dup'").fetchone()[
            "count"
        ]
        assert fts_dup == 0


def test_migration_preserves_earlier_original_when_lowercase_already_exists(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy_orig.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        from agent_mem_bridge.schema import init_db

        init_db(conn)
        content = "Shared rule"
        h = exact_content_hash(content)

        # Earlier original: project:Moebius
        conn.execute(
            """
            INSERT INTO memories (id, namespace, kind, title, content, content_hash, exact_content_hash, created_at)
            VALUES ('id-early', 'project:Moebius', 'memory', 'Early Rule', ?, 'h1', ?, '2026-01-01T00:00:00Z')
            """,
            (content, h),
        )
        # Later copy: project:moebius
        conn.execute(
            """
            INSERT INTO memories (id, namespace, kind, title, content, content_hash, exact_content_hash, created_at)
            VALUES ('id-later', 'project:moebius', 'memory', 'Later Rule', ?, 'h1', ?, '2026-01-02T00:00:00Z')
            """,
            (content, h),
        )
        conn.commit()
    finally:
        conn.close()

    store = MemoryStore(db_path, log_dir=tmp_path / "logs")

    with store._connect() as check_conn:
        rows = check_conn.execute("SELECT id, namespace, created_at FROM memories").fetchall()
        assert len(rows) == 1
        assert rows[0]["id"] == "id-early"
        assert rows[0]["namespace"] == "project:moebius"
        assert rows[0]["created_at"] == "2026-01-01T00:00:00Z"


def test_migration_handles_unicode_case_folding(tmp_path: Path) -> None:
    db_path = tmp_path / "unicode.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        from agent_mem_bridge.schema import init_db

        init_db(conn)
        content = "Turkey market strategy"
        conn.execute(
            """
            INSERT INTO memories (id, namespace, kind, title, content, content_hash, exact_content_hash, created_at)
            VALUES ('tr-1', 'project:İstanbul', 'memory', 'Istanbul Strategy', ?, 'h1', ?, '2026-01-01T00:00:00Z')
            """,
            (content, exact_content_hash(content)),
        )
        conn.commit()
    finally:
        conn.close()

    store = MemoryStore(db_path, log_dir=tmp_path / "logs")

    # Both mixed-case and lowercase forms find the migrated memory
    recalled_1 = store.recall(
        namespace="project:İstanbul",
        query="Turkey market",
        kind="memory",
    )
    assert recalled_1["count"] == 1
    assert recalled_1["items"][0]["id"] == "tr-1"

    recalled_2 = store.recall(
        namespace="project:i̇stanbul",
        query="Turkey market",
        kind="memory",
    )
    assert recalled_2["count"] == 1
    assert recalled_2["items"][0]["id"] == "tr-1"


def test_bindings_file_with_conflicting_case_variants_raises_collision(tmp_path: Path) -> None:
    snapshot_root = tmp_path / "repository"
    snapshot_root.mkdir(parents=True)
    bindings_file = snapshot_root / "bindings.json"

    # Conflicting case variants pointing to different repositories
    bindings_file.write_text(
        json.dumps(
            {
                "store_schema": BINDING_STORE_SCHEMA,
                "bindings": {
                    "project:Moebius": {"repository_id": "repo-A"},
                    "project:moebius": {"repository_id": "repo-B"},
                },
            }
        ),
        encoding="utf-8",
    )

    store = RepositorySnapshotStore(snapshot_root)
    with pytest.raises(ValueError, match="binding collision"):
        store.bindings()

    # Identical repository ID across case variants collapses cleanly without error
    bindings_file.write_text(
        json.dumps(
            {
                "store_schema": BINDING_STORE_SCHEMA,
                "bindings": {
                    "project:Moebius": {"repository_id": "repo-A"},
                    "project:moebius": {"repository_id": "repo-A"},
                },
            }
        ),
        encoding="utf-8",
    )
    resolved = store.bindings()
    assert "project:moebius" in resolved["bindings"]
    assert resolved["bindings"]["project:moebius"]["repository_id"] == "repo-A"


def test_repository_binding_collision_across_case_variations(tmp_path: Path) -> None:
    snapshot_root = tmp_path / "repository"
    store = RepositorySnapshotStore(snapshot_root)
    store.bind_namespace("project:Moebius", "repo-111")

    # Attempting to bind project:moebius to a different repo must fail without allow_rebind
    with pytest.raises(ValueError, match="already bound to a different repository"):
        store.bind_namespace("project:moebius", "repo-222")

    # Rebinding with allow_rebind succeeds and updates the canonical lowercase entry
    rebound = store.bind_namespace("project:moebius", "repo-222", allow_rebind=True)
    assert rebound["rebound"] is True
    assert store.bindings()["bindings"]["project:moebius"]["repository_id"] == "repo-222"

    # Unbinding with mixed case removes the canonical lowercase binding
    assert store.unbind_namespace("project:MOEBIUS") is True
    assert "project:moebius" not in store.bindings()["bindings"]


def test_migration_with_existing_feedback_succeeds_and_preserves_triggers(tmp_path: Path) -> None:
    db_path = tmp_path / "feedback_migration.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        from agent_mem_bridge.schema import init_db

        init_db(conn)
        content = "Feedback memory content"
        h = exact_content_hash(content)

        # Survivor: project:Moebius
        conn.execute(
            """
            INSERT INTO memories (id, namespace, kind, title, content, content_hash, exact_content_hash, created_at)
            VALUES ('fb-mem-1', 'project:Moebius', 'memory', 'Title 1', ?, 'h1', ?, '2026-01-01T00:00:00Z')
            """,
            (content, h),
        )
        # Duplicate: project:MOEBIUS
        conn.execute(
            """
            INSERT INTO memories (id, namespace, kind, title, content, content_hash, exact_content_hash, created_at)
            VALUES ('fb-mem-dup', 'project:MOEBIUS', 'memory', 'Title Dup', ?, 'h1', ?, '2026-01-02T00:00:00Z')
            """,
            (content, h),
        )
        # Feedback on survivor
        conn.execute(
            """
            INSERT INTO retrieval_feedback (
                idempotency_key, receipt_hash, feedback_identity_digest,
                namespace, memory_id, result_rank, outcome, reason,
                retrieval_mode, database_epoch, bridge_instance_id,
                receipt_issued_at, receipt_expires_at, feedback_json, created_at
            ) VALUES (
                ?, ?, ?, 'project:Moebius', 'fb-mem-1', 1, 'helpful', 'confirmed useful',
                'lexical', 'epoch-1', 'bridge-1', '2026-01-01T00:00:00Z',
                '2026-01-01T00:15:00Z', '{}', '2026-01-01T00:01:00Z'
            )
            """,
            ("1" * 64, "2" * 64, "2" * 64),
        )
        # Feedback on duplicate
        conn.execute(
            """
            INSERT INTO retrieval_feedback (
                idempotency_key, receipt_hash, feedback_identity_digest,
                namespace, memory_id, result_rank, outcome, reason,
                retrieval_mode, database_epoch, bridge_instance_id,
                receipt_issued_at, receipt_expires_at, feedback_json, created_at
            ) VALUES (
                ?, ?, ?, 'project:MOEBIUS', 'fb-mem-dup', 1, 'helpful', 'also useful',
                'lexical', 'epoch-1', 'bridge-1', '2026-01-01T00:00:00Z',
                '2026-01-01T00:15:00Z', '{}', '2026-01-01T00:02:00Z'
            )
            """,
            ("3" * 64, "4" * 64, "4" * 64),
        )
        conn.commit()
    finally:
        conn.close()

    # Opening with MemoryStore triggers migration; must not raise IntegrityError
    store = MemoryStore(db_path, log_dir=tmp_path / "logs")

    with store._connect() as check_conn:
        fbs = check_conn.execute("SELECT feedback_id, namespace, memory_id FROM retrieval_feedback").fetchall()
        assert len(fbs) == 2
        for fb in fbs:
            assert fb["namespace"] == "project:moebius"
            assert fb["memory_id"] == "fb-mem-1"

        # Verify append-only trigger is preserved and active
        with pytest.raises(sqlite3.IntegrityError, match="retrieval_feedback is append-only"):
            check_conn.execute("UPDATE retrieval_feedback SET outcome = 'misleading' WHERE feedback_id = 1")


def test_duplicate_merging_preserves_tags_lineage_revisions_across_rebuild(tmp_path: Path) -> None:
    from agent_mem_bridge.database_maintenance import rebuild_database_projections
    from agent_mem_bridge.record_projection import sync_record_projection

    db_path = tmp_path / "dup_integrity.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        from agent_mem_bridge.schema import init_db

        init_db(conn)
        shared_content = "Core rule content"
        h = exact_content_hash(shared_content)

        # Survivor: project:Moebius with tag tag:orig
        conn.execute(
            """
            INSERT INTO memories (id, namespace, kind, title, content, tags_json, content_hash, exact_content_hash, created_at)
            VALUES ('m-surv', 'project:Moebius', 'memory', 'Surv', ?, '["tag:orig"]', 'h1', ?, '2026-01-01T00:00:00Z')
            """,
            (shared_content, h),
        )
        sync_record_projection(
            conn,
            memory_id="m-surv",
            namespace="project:Moebius",
            content=shared_content,
            tags=["tag:orig"],
            kind="memory",
            actor=None,
            source_app=None,
            is_learning_candidate=False,
        )

        # Duplicate: project:moebius with tag topic:keep-me
        conn.execute(
            """
            INSERT INTO memories (id, namespace, kind, title, content, tags_json, content_hash, exact_content_hash, is_learning_candidate, created_at)
            VALUES ('m-dup2', 'project:moebius', 'memory', 'Dup', ?, '["topic:keep-me"]', 'h1', ?, 1, '2026-01-02T00:00:00Z')
            """,
            (shared_content, h),
        )
        sync_record_projection(
            conn,
            memory_id="m-dup2",
            namespace="project:moebius",
            content=shared_content,
            tags=["topic:keep-me"],
            kind="memory",
            actor=None,
            source_app=None,
            is_learning_candidate=True,
        )
        # Edge between m-dup2 and m-surv to verify self-edge pruning
        conn.execute(
            """
            INSERT INTO memory_edges (source_id, target_id, relation, position, machine_owned, target_namespace, target_exists)
            VALUES ('m-dup2', 'm-surv', 'relates_to', 0, 1, 'project:moebius', 1)
            """
        )

        # A third memory with content-based lineage pointing to m-dup2
        content_ref = "Rule depending on duplicate\ndepends_on: m-dup2"
        conn.execute(
            """
            INSERT INTO memories (id, namespace, kind, title, content, tags_json, content_hash, exact_content_hash, created_at)
            VALUES ('m-ref', 'project:moebius', 'memory', 'Ref', ?, '[]', 'href', ?, '2026-01-03T00:00:00Z')
            """,
            (content_ref, exact_content_hash(content_ref)),
        )
        sync_record_projection(
            conn,
            memory_id="m-ref",
            namespace="project:moebius",
            content=content_ref,
            tags=[],
            kind="memory",
            actor=None,
            source_app=None,
            is_learning_candidate=False,
        )

        # A successor memory that revised m-dup2 before upgrading
        content_succ = "Successor rule\nsupersedes: m-dup2"
        conn.execute(
            """
            INSERT INTO memories (id, namespace, kind, title, content, tags_json, content_hash, exact_content_hash, created_at)
            VALUES ('m-succ', 'project:moebius', 'memory', 'Succ', ?, '[]', 'hsucc', ?, '2026-01-04T00:00:00Z')
            """,
            (content_succ, exact_content_hash(content_succ)),
        )
        sync_record_projection(
            conn,
            memory_id="m-succ",
            namespace="project:moebius",
            content=content_succ,
            tags=[],
            kind="memory",
            actor=None,
            source_app=None,
            is_learning_candidate=False,
        )
        conn.execute(
            """
            INSERT INTO memory_revisions (predecessor_id, successor_id, actor, reason, created_at)
            VALUES ('m-dup2', 'm-succ', 'tester', 'updated rule', '2026-01-04T00:00:00Z')
            """
        )
        conn.commit()
    finally:
        conn.close()

    # Open with MemoryStore to run migration
    store = MemoryStore(db_path, log_dir=tmp_path / "logs")

    with store._connect() as check_conn:
        # Check survivor tags_json merged
        surv_row = check_conn.execute(
            "SELECT tags_json, is_learning_candidate FROM memories WHERE id = 'm-surv'"
        ).fetchone()
        surv_tags = json.loads(surv_row["tags_json"])
        assert "topic:keep-me" in surv_tags
        assert "tag:orig" in surv_tags
        assert surv_row["is_learning_candidate"] == 1

        # Check self-edge was pruned
        self_edge = check_conn.execute("SELECT 1 FROM memory_edges WHERE source_id = target_id").fetchone()
        assert self_edge is None

        # Check revisions repointed to survivor
        rev = check_conn.execute("SELECT predecessor_id, successor_id FROM memory_revisions").fetchone()
        assert rev["predecessor_id"] == "m-surv"
        assert rev["successor_id"] == "m-succ"

        # Check tombstone recorded for duplicate
        tomb = check_conn.execute(
            "SELECT forgotten_id, root_forget_id, cause FROM memory_tombstones WHERE forgotten_id = 'm-dup2'"
        ).fetchone()
        assert tomb is not None
        assert tomb["root_forget_id"] == "m-surv"
        assert tomb["cause"] == "namespace_case_collapse"

        # Check content references repointed in memories
        ref_row = check_conn.execute("SELECT content FROM memories WHERE id = 'm-ref'").fetchone()
        assert "depends_on: m-surv" in ref_row["content"]
        assert "m-dup2" not in ref_row["content"]

        succ_row = check_conn.execute("SELECT content FROM memories WHERE id = 'm-succ'").fetchone()
        assert "supersedes: m-surv" in succ_row["content"]
        assert "m-dup2" not in succ_row["content"]

    # Rebuild database projections to ensure nothing reverts or drops
    rebuild_database_projections(db_path)

    with store._connect() as check_conn:
        # topic:keep-me still in memory_tags table
        tag_rows = check_conn.execute("SELECT tag FROM memory_tags WHERE memory_id = 'm-surv'").fetchall()
        tags = {r["tag"] for r in tag_rows}
        assert "topic:keep-me" in tags
        assert "tag:orig" in tags

        # depends_on edge still points to m-surv with target_exists=1
        ref_edge = check_conn.execute(
            "SELECT target_id, target_exists FROM memory_edges WHERE source_id = 'm-ref'"
        ).fetchone()
        assert ref_edge["target_id"] == "m-surv"
        assert ref_edge["target_exists"] == 1

        # supersedes edge still points to m-surv with target_exists=1
        succ_edge = check_conn.execute(
            "SELECT target_id, target_exists FROM memory_edges WHERE source_id = 'm-succ'"
        ).fetchone()
        assert succ_edge["target_id"] == "m-surv"
        assert succ_edge["target_exists"] == 1


def test_older_schema_v11_upgrade_normalizes_on_first_open(tmp_path: Path) -> None:
    from agent_mem_bridge import schema as schema_module

    db_path = tmp_path / "v11_upgrade.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        # Apply schema up to version 11
        for raw_migration in schema_module.MIGRATIONS[:11]:
            migration = schema_module._coerce_schema_migration(raw_migration)
            migration.apply(conn)
            conn.execute(f"PRAGMA user_version = {migration.version}")
        conn.commit()

        # Insert a memory with mixed-case project namespace
        content = "v11 memory content"
        h = exact_content_hash(content)
        conn.execute(
            """
            INSERT INTO memories (id, namespace, kind, title, content, content_hash, exact_content_hash, created_at)
            VALUES ('v11-mem', 'project:Moebius', 'memory', 'v11 title', ?, 'h1', ?, '2026-01-01T00:00:00Z')
            """,
            (content, h),
        )
        conn.execute("INSERT INTO memory_insertions (memory_id) VALUES ('v11-mem')")
        conn.execute(
            "INSERT INTO memories_fts (memory_id, title, content) VALUES ('v11-mem', 'v11 title', ?)",
            (content,),
        )
        conn.commit()
    finally:
        conn.close()

    # FIRST OPEN with MemoryStore
    store = MemoryStore(db_path, log_dir=tmp_path / "logs")

    # Recall on first open must find the memory
    recalled = store.recall(
        namespace="project:moebius",
        query="v11 memory",
        kind="memory",
    )
    assert recalled["count"] == 1
    assert recalled["items"][0]["id"] == "v11-mem"
    assert recalled["items"][0]["namespace"] == "project:moebius"

    with store._connect() as check_conn:
        assert check_conn.execute("PRAGMA user_version").fetchone()[0] == 12
        row = check_conn.execute("SELECT namespace FROM memories WHERE id = 'v11-mem'").fetchone()
        assert row["namespace"] == "project:moebius"


def test_project_resolution_with_legacy_mixed_case_bindings(tmp_path: Path) -> None:
    import subprocess

    from agent_mem_bridge.project_resolution import namespace_for_host_adapter, resolve_project_context
    from agent_mem_bridge.repository_snapshot_store import repository_identity

    # Mock git repository directory
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init", str(repo_dir)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo_dir), "commit", "--allow-empty", "-m", "init"],
        check=True,
        capture_output=True,
    )

    identity = repository_identity(repo_dir)
    repo_id = identity["repository_id"]

    snapshot_root = tmp_path / "snapshot"
    snapshot_root.mkdir()
    bindings_file = snapshot_root / "bindings.json"

    # Legacy bindings file containing mixed-case key
    bindings_file.write_text(
        json.dumps(
            {
                "store_schema": BINDING_STORE_SCHEMA,
                "bindings": {
                    "project:Moebius": {"repository_id": repo_id},
                },
            }
        ),
        encoding="utf-8",
    )

    resolved = resolve_project_context(repo_dir, snapshot_root=snapshot_root)
    assert resolved["status"] == "bound"
    assert resolved["namespace"] == "project:moebius"
    assert namespace_for_host_adapter(resolved) == "project:moebius"

    # Binding file with collision between case variants
    bindings_file.write_text(
        json.dumps(
            {
                "store_schema": BINDING_STORE_SCHEMA,
                "bindings": {
                    "project:Moebius": {"repository_id": repo_id},
                    "project:moebius": {"repository_id": "different-repo-id"},
                },
            }
        ),
        encoding="utf-8",
    )
    collision_res = resolve_project_context(repo_dir, snapshot_root=snapshot_root)
    assert collision_res["status"] == "ambiguous_binding"
    assert collision_res["ambiguity"]["reason"] == "binding_collision"


def test_migration_with_existing_run_memory_links_preserves_attribution_and_triggers(tmp_path: Path) -> None:
    db_path = tmp_path / "run_links.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        from agent_mem_bridge.schema import init_db

        init_db(conn)
        content = "Attributed memory content"
        h = exact_content_hash(content)

        # Survivor: project:Moebius
        conn.execute(
            """
            INSERT INTO memories (id, namespace, kind, title, content, content_hash, exact_content_hash, created_at)
            VALUES ('mem-surv-run', 'project:Moebius', 'memory', 'Surv', ?, 'h1', ?, '2026-01-01T00:00:00Z')
            """,
            (content, h),
        )
        # Duplicate: project:MOEBIUS
        conn.execute(
            """
            INSERT INTO memories (id, namespace, kind, title, content, content_hash, exact_content_hash, created_at)
            VALUES ('mem-dup-run', 'project:MOEBIUS', 'memory', 'Dup', ?, 'h1', ?, '2026-01-02T00:00:00Z')
            """,
            (content, h),
        )

        # Set up an agent run and work item
        run_id = "run_" + "1" * 32
        work_item_id = "work_" + "1" * 32
        event_id = "evt_" + "1" * 32
        conn.execute(
            """
            INSERT INTO agent_runs (
                run_id, workspace_key, root_goal, idempotency_key_digest, request_digest, created_at
            ) VALUES (?, 'default', 'Test run goal', ?, ?, '2026-01-02T00:00:00Z')
            """,
            (run_id, "a" * 64, "b" * 64),
        )
        conn.execute(
            """
            INSERT INTO run_work_items (
                work_item_id, run_id, goal, created_at
            ) VALUES (?, ?, 'Item goal', '2026-01-02T00:00:00Z')
            """,
            (work_item_id, run_id),
        )
        conn.execute(
            """
            INSERT INTO run_events (
                event_id, run_id, work_item_id, sequence, event_type, summary,
                idempotency_key_digest, request_digest, created_at
            ) VALUES (?, ?, ?, 1, 'memory_recalled', 'Recalled memory', ?, ?, '2026-01-02T00:00:01Z')
            """,
            (event_id, run_id, work_item_id, "c" * 64, "d" * 64),
        )

        # Insert run_memory_links referencing duplicate mem-dup-run
        link_id = "link_" + "1" * 32
        conn.execute(
            """
            INSERT INTO run_memory_links (
                link_id, run_id, work_item_id, event_id, outcome_id, memory_id,
                exact_content_version, receipt_hash, exposure_rank, feedback_id,
                relation, review_required, idempotency_key_digest, request_digest, created_at
            ) VALUES (?, ?, ?, ?, NULL, 'mem-dup-run', ?, ?, 1, NULL, 'recalled', 0, ?, ?, '2026-01-02T00:00:01Z')
            """,
            (link_id, run_id, work_item_id, event_id, h, "e" * 64, "f" * 64, "1" * 64),
        )
        conn.commit()
    finally:
        conn.close()

    # Open with MemoryStore: migration runs and MUST NOT raise IntegrityError
    store = MemoryStore(db_path, log_dir=tmp_path / "logs")

    with store._connect() as check_conn:
        # Original run attribution preserved
        link_row = check_conn.execute(
            "SELECT memory_id, relation FROM run_memory_links WHERE link_id = ?", (link_id,)
        ).fetchone()
        assert link_row["memory_id"] == "mem-dup-run"
        assert link_row["relation"] == "recalled"

        # run_memory_links append-only trigger remains active
        with pytest.raises(sqlite3.IntegrityError, match="run_memory_links is append-only"):
            check_conn.execute("UPDATE run_memory_links SET relation = 'applied' WHERE link_id = ?", (link_id,))

        # Tombstone recorded for mem-dup-run
        tomb = check_conn.execute(
            "SELECT forgotten_id, root_forget_id, cause FROM memory_tombstones WHERE forgotten_id = 'mem-dup-run'"
        ).fetchone()
        assert tomb is not None
        assert tomb["root_forget_id"] == "mem-surv-run"
        assert tomb["cause"] == "namespace_case_collapse"


def test_reference_rewriting_handles_exact_content_collisions_safely(tmp_path: Path) -> None:
    from agent_mem_bridge.database_maintenance import rebuild_database_projections
    from agent_mem_bridge.record_projection import sync_record_projection

    db_path = tmp_path / "collision_rewrite.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        from agent_mem_bridge.schema import init_db

        init_db(conn)
        base_content = "Base rule content"
        h_base = exact_content_hash(base_content)

        # A in project:Moebius
        conn.execute(
            """
            INSERT INTO memories (id, namespace, kind, title, content, content_hash, exact_content_hash, created_at)
            VALUES ('mem-A', 'project:Moebius', 'memory', 'A', ?, 'ha', ?, '2026-01-01T00:00:00Z')
            """,
            (base_content, h_base),
        )
        # B in project:MOEBIUS
        conn.execute(
            """
            INSERT INTO memories (id, namespace, kind, title, content, content_hash, exact_content_hash, created_at)
            VALUES ('mem-B', 'project:MOEBIUS', 'memory', 'B', ?, 'hb', ?, '2026-01-02T00:00:00Z')
            """,
            (base_content, h_base),
        )

        # C1 in project:consumer depending on mem-A
        c1_content = "Consumer rule\ndepends_on: mem-A"
        h_c1 = exact_content_hash(c1_content)
        conn.execute(
            """
            INSERT INTO memories (id, namespace, kind, title, content, tags_json, content_hash, exact_content_hash, created_at)
            VALUES ('mem-C1', 'project:consumer', 'memory', 'C1', ?, '["tag:c1"]', 'hc1', ?, '2026-01-03T00:00:00Z')
            """,
            (c1_content, h_c1),
        )
        sync_record_projection(
            conn,
            memory_id="mem-C1",
            namespace="project:consumer",
            content=c1_content,
            tags=["tag:c1"],
            kind="memory",
            actor=None,
            source_app=None,
            is_learning_candidate=False,
        )

        # C2 in project:consumer depending on mem-B
        c2_content = "Consumer rule\ndepends_on: mem-B"
        h_c2 = exact_content_hash(c2_content)
        conn.execute(
            """
            INSERT INTO memories (id, namespace, kind, title, content, tags_json, content_hash, exact_content_hash, created_at)
            VALUES ('mem-C2', 'project:consumer', 'memory', 'C2', ?, '["tag:c2-extra"]', 'hc2', ?, '2026-01-04T00:00:00Z')
            """,
            (c2_content, h_c2),
        )
        sync_record_projection(
            conn,
            memory_id="mem-C2",
            namespace="project:consumer",
            content=c2_content,
            tags=["tag:c2-extra"],
            kind="memory",
            actor=None,
            source_app=None,
            is_learning_candidate=False,
        )
        conn.commit()
    finally:
        conn.close()

    # Open with MemoryStore: MUST NOT raise UNIQUE constraint failed
    store = MemoryStore(db_path, log_dir=tmp_path / "logs")

    with store._connect() as check_conn:
        # C1 survived and merged C2's tag
        c1_row = check_conn.execute("SELECT tags_json, content FROM memories WHERE id = 'mem-C1'").fetchone()
        assert c1_row is not None
        c1_tags = json.loads(c1_row["tags_json"])
        assert "tag:c1" in c1_tags
        assert "tag:c2-extra" in c1_tags
        assert "depends_on: mem-A" in c1_row["content"]

        # C2 was collapsed
        c2_row = check_conn.execute("SELECT 1 FROM memories WHERE id = 'mem-C2'").fetchone()
        assert c2_row is None

        # Tombstone recorded for C2 pointing to C1
        tomb_c2 = check_conn.execute(
            "SELECT forgotten_id, root_forget_id, cause FROM memory_tombstones WHERE forgotten_id = 'mem-C2'"
        ).fetchone()
        assert tomb_c2 is not None
        assert tomb_c2["root_forget_id"] == "mem-C1"
        assert tomb_c2["cause"] == "reference_rewrite_collapse"

    # Verify projections rebuild cleanly
    rebuild_database_projections(db_path)

    with store._connect() as check_conn:
        tag_rows = check_conn.execute("SELECT tag FROM memory_tags WHERE memory_id = 'mem-C1'").fetchall()
        tags = {r["tag"] for r in tag_rows}
        assert "tag:c1" in tags
        assert "tag:c2-extra" in tags

        edge = check_conn.execute(
            "SELECT target_id, target_exists FROM memory_edges WHERE source_id = 'mem-C1'"
        ).fetchone()
        assert edge["target_id"] == "mem-A"
        assert edge["target_exists"] == 1


def test_migration_leaves_unrelated_historical_prose_and_commands_intact(tmp_path: Path) -> None:
    db_path = tmp_path / "prose_intact.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        from agent_mem_bridge.schema import init_db

        init_db(conn)
        base = "Base rule"
        h = exact_content_hash(base)
        conn.execute(
            """
            INSERT INTO memories (id, namespace, kind, title, content, content_hash, exact_content_hash, created_at)
            VALUES ('mem-orig', 'project:Moebius', 'memory', 'Orig', ?, 'h1', ?, '2026-01-01T00:00:00Z')
            """,
            (base, h),
        )
        conn.execute(
            """
            INSERT INTO memories (id, namespace, kind, title, content, content_hash, exact_content_hash, created_at)
            VALUES ('mem-dup-old', 'project:MOEBIUS', 'memory', 'Dup', ?, 'h1', ?, '2026-01-02T00:00:00Z')
            """,
            (base, h),
        )

        # Historical incident notes in domain:incident-notes mentioning mem-dup-old
        incident_content = (
            "Incident description for outage\n"
            "File affected: /backups/mem-dup-old.json\n"
            "Action taken: inspect --id mem-dup-old\n"
            "Notes: mem-dup-old investigated."
        )
        conn.execute(
            """
            INSERT INTO memories (id, namespace, kind, title, content, content_hash, exact_content_hash, created_at)
            VALUES ('inc-1', 'domain:incident-notes', 'memory', 'Incident 1', ?, 'hinc', ?, '2026-01-03T00:00:00Z')
            """,
            (incident_content, exact_content_hash(incident_content)),
        )

        # Memory with BOTH a lineage relation, compound reference path, and notes
        dep_content = (
            "Rule with dependency\n"
            "depends_on: mem-dup-old\n"
            "evidence_refs: /backups/mem-dup-old.json\n"
            "notes: mentions mem-dup-old in note line\n"
            "command: run mem-dup-old"
        )
        conn.execute(
            """
            INSERT INTO memories (id, namespace, kind, title, content, content_hash, exact_content_hash, created_at)
            VALUES ('dep-1', 'project:consumer', 'memory', 'Dep 1', ?, 'hdep', ?, '2026-01-04T00:00:00Z')
            """,
            (dep_content, exact_content_hash(dep_content)),
        )
        conn.commit()
    finally:
        conn.close()

    # Open with MemoryStore to run migration
    store = MemoryStore(db_path, log_dir=tmp_path / "logs")

    with store._connect() as check_conn:
        inc_row = check_conn.execute("SELECT content FROM memories WHERE id = 'inc-1'").fetchone()
        # All literal prose, filenames, and commands in inc-1 remain completely untouched
        assert inc_row["content"] == incident_content

        dep_row = check_conn.execute("SELECT content FROM memories WHERE id = 'dep-1'").fetchone()
        # depends_on was rewritten to mem-orig
        assert "depends_on: mem-orig" in dep_row["content"]
        # Compound evidence_refs path containing duplicate ID is NOT partially rewritten
        assert "evidence_refs: /backups/mem-dup-old.json" in dep_row["content"]
        # notes and command remain untouched with mem-dup-old
        assert "notes: mentions mem-dup-old in note line" in dep_row["content"]
        assert "command: run mem-dup-old" in dep_row["content"]


def test_reference_collision_preserves_signals_and_durable_memories_separately(tmp_path: Path) -> None:
    db_path = tmp_path / "signal_collision.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        from agent_mem_bridge.schema import init_db

        init_db(conn)
        base = "Base task rule"
        h_base = exact_content_hash(base)

        # A in project:Moebius, B in project:MOEBIUS
        conn.execute(
            """
            INSERT INTO memories (id, namespace, kind, title, content, content_hash, exact_content_hash, created_at)
            VALUES ('mem-A', 'project:Moebius', 'memory', 'A', ?, 'ha', ?, '2026-01-01T00:00:00Z')
            """,
            (base, h_base),
        )
        conn.execute(
            """
            INSERT INTO memories (id, namespace, kind, title, content, content_hash, exact_content_hash, created_at)
            VALUES ('mem-B', 'project:MOEBIUS', 'memory', 'B', ?, 'hb', ?, '2026-01-02T00:00:00Z')
            """,
            (base, h_base),
        )

        sig1_content = "Pending signal payload\ndepends_on: mem-A"
        sig2_content = "Pending signal payload\ndepends_on: mem-B"
        mem_content = "Pending signal payload\ndepends_on: mem-A"

        # Signal 1 (created 2026-01-03)
        conn.execute(
            """
            INSERT INTO memories (
                id, namespace, kind, title, content, content_hash, exact_content_hash,
                correlation_id, signal_status, created_at
            ) VALUES (
                'sig-1', 'project:consumer', 'signal', 'Sig 1', ?, 'hs1', ?,
                'job-a', 'pending', '2026-01-03T00:00:00Z'
            )
            """,
            (sig1_content, exact_content_hash(sig1_content)),
        )

        # Signal 2 (created 2026-01-04)
        conn.execute(
            """
            INSERT INTO memories (
                id, namespace, kind, title, content, content_hash, exact_content_hash,
                correlation_id, signal_status, created_at
            ) VALUES (
                'sig-2', 'project:consumer', 'signal', 'Sig 2', ?, 'hs2', ?,
                'job-b', 'pending', '2026-01-04T00:00:00Z'
            )
            """,
            (sig2_content, exact_content_hash(sig2_content)),
        )

        # Durable memory matching Signal 1's content (created 2026-01-05)
        conn.execute(
            """
            INSERT INTO memories (
                id, namespace, kind, title, content, content_hash, exact_content_hash, created_at
            ) VALUES (
                'mem-consumer', 'project:consumer', 'memory', 'Consumer Mem', ?, 'hm', ?, '2026-01-05T00:00:00Z'
            )
            """,
            (mem_content, exact_content_hash(mem_content)),
        )

        # Signal 3 (older than durable memory, created 2026-01-06) with depends_on: mem-A
        # And Durable memory 2 (created 2026-01-07) with depends_on: mem-B (which rewrites to mem-A)
        sig3_content = "Cross kind task\ndepends_on: mem-A"
        mem2_content = "Cross kind task\ndepends_on: mem-B"
        conn.execute(
            """
            INSERT INTO memories (
                id, namespace, kind, title, content, content_hash, exact_content_hash,
                correlation_id, signal_status, created_at
            ) VALUES (
                'sig-3', 'project:consumer', 'signal', 'Sig 3', ?, 'hs3', ?,
                'job-c', 'pending', '2026-01-06T00:00:00Z'
            )
            """,
            (sig3_content, exact_content_hash(sig3_content)),
        )
        conn.execute(
            """
            INSERT INTO memories (
                id, namespace, kind, title, content, content_hash, exact_content_hash, created_at
            ) VALUES (
                'mem-cross', 'project:consumer', 'memory', 'Cross Mem', ?, 'hm2', ?, '2026-01-07T00:00:00Z'
            )
            """,
            (mem2_content, exact_content_hash(mem2_content)),
        )
        conn.commit()
    finally:
        conn.close()

    # Open store: migration runs and MUST NOT delete any Signal or merge Signal with Memory
    store = MemoryStore(db_path, log_dir=tmp_path / "logs")

    with store._connect() as check_conn:
        # Both original signals in project:consumer survive
        s1 = check_conn.execute("SELECT kind, content FROM memories WHERE id = 'sig-1'").fetchone()
        s2 = check_conn.execute("SELECT kind, content FROM memories WHERE id = 'sig-2'").fetchone()
        assert s1 is not None and s1["kind"] == "signal"
        assert s2 is not None and s2["kind"] == "signal"
        assert "depends_on: mem-A" in s1["content"]
        assert "depends_on: mem-A" in s2["content"]

        # Durable memory matching Signal content also survives
        m = check_conn.execute("SELECT kind, content FROM memories WHERE id = 'mem-consumer'").fetchone()
        assert m is not None and m["kind"] == "memory"
        assert "depends_on: mem-A" in m["content"]

        # Cross-kind Signal 3 and Durable Memory 2 both survive
        s3 = check_conn.execute("SELECT kind, content FROM memories WHERE id = 'sig-3'").fetchone()
        m2 = check_conn.execute("SELECT kind, content FROM memories WHERE id = 'mem-cross'").fetchone()
        assert s3 is not None and s3["kind"] == "signal"
        assert m2 is not None and m2["kind"] == "memory"
        assert "depends_on: mem-A" in s3["content"]
        assert "depends_on: mem-A" in m2["content"]

        # Signals are never recorded as tombstones
        tombs = check_conn.execute(
            "SELECT forgotten_id FROM memory_tombstones WHERE forgotten_id IN ('sig-1', 'sig-2', 'sig-3', 'mem-consumer', 'mem-cross')"
        ).fetchall()
        assert len(tombs) == 0


def test_reference_collision_both_creation_orderings_preserve_merged_tags_in_active_recall(tmp_path: Path) -> None:
    from agent_mem_bridge.record_projection import sync_record_projection

    db_path = tmp_path / "tags_orderings.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        from agent_mem_bridge.schema import init_db

        init_db(conn)
        base = "Base rule content"
        h_base = exact_content_hash(base)

        # A in project:Moebius, B in project:MOEBIUS
        conn.execute(
            """
            INSERT INTO memories (id, namespace, kind, title, content, content_hash, exact_content_hash, created_at)
            VALUES ('mem-A', 'project:Moebius', 'memory', 'A', ?, 'ha', ?, '2026-01-01T00:00:00Z')
            """,
            (base, h_base),
        )
        conn.execute(
            """
            INSERT INTO memories (id, namespace, kind, title, content, content_hash, exact_content_hash, created_at)
            VALUES ('mem-B', 'project:MOEBIUS', 'memory', 'B', ?, 'hb', ?, '2026-01-02T00:00:00Z')
            """,
            (base, h_base),
        )

        # Ordering 1 (col_order <= ref_order):
        # ord1-surv created earlier (2026-01-03) with depends_on: mem-A and tag:ord1-surv
        # ord1-dup created later (2026-01-04) with depends_on: mem-B and tag:ord1-dup
        c1_content = "Order 1 Consumer\ndepends_on: mem-A"
        c2_content = "Order 1 Consumer\ndepends_on: mem-B"
        conn.execute(
            """
            INSERT INTO memories (id, namespace, kind, title, content, tags_json, content_hash, exact_content_hash, created_at)
            VALUES ('ord1-surv', 'project:consumer-1', 'memory', 'Ord 1 Surv', ?, '["tag:ord1-surv"]', 'ho1', ?, '2026-01-03T00:00:00Z')
            """,
            (c1_content, exact_content_hash(c1_content)),
        )
        sync_record_projection(
            conn,
            memory_id="ord1-surv",
            namespace="project:consumer-1",
            content=c1_content,
            tags=["tag:ord1-surv"],
            kind="memory",
            actor=None,
            source_app=None,
            is_learning_candidate=False,
        )
        conn.execute(
            """
            INSERT INTO memories (id, namespace, kind, title, content, tags_json, content_hash, exact_content_hash, created_at)
            VALUES ('ord1-dup', 'project:consumer-1', 'memory', 'Ord 1 Dup', ?, '["tag:ord1-dup"]', 'ho2', ?, '2026-01-04T00:00:00Z')
            """,
            (c2_content, exact_content_hash(c2_content)),
        )
        sync_record_projection(
            conn,
            memory_id="ord1-dup",
            namespace="project:consumer-1",
            content=c2_content,
            tags=["tag:ord1-dup"],
            kind="memory",
            actor=None,
            source_app=None,
            is_learning_candidate=False,
        )

        # Ordering 2 (col_order > ref_order - maintainer reproduction):
        # ord2-surv created earlier (2026-01-05) with depends_on: mem-B and topic:original
        # ord2-col created later (2026-01-06) with depends_on: mem-A and topic:keep-me
        c3_content = "Order 2 Consumer\ndepends_on: mem-B"
        c4_content = "Order 2 Consumer\ndepends_on: mem-A"
        conn.execute(
            """
            INSERT INTO memories (id, namespace, kind, title, content, tags_json, content_hash, exact_content_hash, created_at)
            VALUES ('ord2-surv', 'project:consumer-2', 'memory', 'Ord 2 Surv', ?, '["topic:original"]', 'ho3', ?, '2026-01-05T00:00:00Z')
            """,
            (c3_content, exact_content_hash(c3_content)),
        )
        sync_record_projection(
            conn,
            memory_id="ord2-surv",
            namespace="project:consumer-2",
            content=c3_content,
            tags=["topic:original"],
            kind="memory",
            actor=None,
            source_app=None,
            is_learning_candidate=False,
        )
        conn.execute(
            """
            INSERT INTO memories (id, namespace, kind, title, content, tags_json, content_hash, exact_content_hash, created_at)
            VALUES ('ord2-col', 'project:consumer-2', 'memory', 'Ord 2 Col', ?, '["topic:keep-me"]', 'ho4', ?, '2026-01-06T00:00:00Z')
            """,
            (c4_content, exact_content_hash(c4_content)),
        )
        sync_record_projection(
            conn,
            memory_id="ord2-col",
            namespace="project:consumer-2",
            content=c4_content,
            tags=["topic:keep-me"],
            kind="memory",
            actor=None,
            source_app=None,
            is_learning_candidate=False,
        )
        conn.commit()
    finally:
        conn.close()

    # Open store - migration runs
    store = MemoryStore(db_path, log_dir=tmp_path / "logs")

    # BEFORE any rebuild_database_projections: verify tag-filtered recall works immediately
    # Ordering 1
    recall_ord1_surv = store.recall(query="Consumer", namespace="project:consumer-1", tags_any=["tag:ord1-surv"])
    recall_ord1_dup = store.recall(query="Consumer", namespace="project:consumer-1", tags_any=["tag:ord1-dup"])
    assert len(recall_ord1_surv["items"]) == 1
    assert len(recall_ord1_dup["items"]) == 1
    assert recall_ord1_surv["items"][0]["id"] == "ord1-surv"
    assert recall_ord1_dup["items"][0]["id"] == "ord1-surv"

    # Ordering 2
    recall_ord2_orig = store.recall(query="Consumer", namespace="project:consumer-2", tags_any=["topic:original"])
    recall_ord2_keep = store.recall(query="Consumer", namespace="project:consumer-2", tags_any=["topic:keep-me"])
    assert len(recall_ord2_orig["items"]) == 1
    assert len(recall_ord2_keep["items"]) == 1
    assert recall_ord2_orig["items"][0]["id"] == "ord2-surv"
    assert recall_ord2_keep["items"][0]["id"] == "ord2-surv"


def test_migration_lineage_rewriting_matches_canonical_parser(tmp_path: Path) -> None:
    from agent_mem_bridge.database_maintenance import rebuild_database_projections
    from agent_mem_bridge.lineage import parse_lineage
    from agent_mem_bridge.record_projection import sync_record_projection

    db_path = tmp_path / "canonical_lineage.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        from agent_mem_bridge.schema import init_db

        init_db(conn)
        base = "Base rule content"
        h_base = exact_content_hash(base)

        # A in project:Moebius, B in project:MOEBIUS
        conn.execute(
            """
            INSERT INTO memories (id, namespace, kind, title, content, content_hash, exact_content_hash, created_at)
            VALUES ('mem-A', 'project:Moebius', 'memory', 'A', ?, 'ha', ?, '2026-01-01T00:00:00Z')
            """,
            (base, h_base),
        )
        conn.execute(
            """
            INSERT INTO memories (id, namespace, kind, title, content, content_hash, exact_content_hash, created_at)
            VALUES ('mem-B', 'project:MOEBIUS', 'memory', 'B', ?, 'hb', ?, '2026-01-02T00:00:00Z')
            """,
            (base, h_base),
        )

        # Consumer 1: Valid JSON dependency with internal whitespace: [" mem-B "]
        c1_content = 'Rule with JSON dependency\ndepends_on_record_ids_json: [" mem-B "]'
        conn.execute(
            """
            INSERT INTO memories (id, namespace, kind, title, content, content_hash, exact_content_hash, created_at)
            VALUES ('mem-consumer-1', 'project:consumer', 'memory', 'Consumer 1', ?, 'hc1', ?, '2026-01-03T00:00:00Z')
            """,
            (c1_content, exact_content_hash(c1_content)),
        )
        sync_record_projection(
            conn,
            memory_id="mem-consumer-1",
            namespace="project:consumer",
            content=c1_content,
            tags=[],
            kind="memory",
            actor=None,
            source_app=None,
            is_learning_candidate=False,
        )

        # Consumer 2: Non-JSON ordinary pipe-list field containing array-shaped literal: ["mem-B"]
        # The canonical parser treats evidence_refs as a pipe-list value, so references_to("mem-B") is 0.
        c2_content = 'Rule with literal evidence refs\nevidence_refs: ["mem-B"]'
        conn.execute(
            """
            INSERT INTO memories (id, namespace, kind, title, content, content_hash, exact_content_hash, created_at)
            VALUES ('mem-consumer-2', 'project:consumer', 'memory', 'Consumer 2', ?, 'hc2', ?, '2026-01-04T00:00:00Z')
            """,
            (c2_content, exact_content_hash(c2_content)),
        )
        sync_record_projection(
            conn,
            memory_id="mem-consumer-2",
            namespace="project:consumer",
            content=c2_content,
            tags=[],
            kind="memory",
            actor=None,
            source_app=None,
            is_learning_candidate=False,
        )
        conn.commit()
    finally:
        conn.close()

    # Open store: migration runs and collapses mem-B into mem-A
    store = MemoryStore(db_path, log_dir=tmp_path / "logs")

    with store._connect() as check_conn:
        # Case 1 (immediate): depends_on_record_ids_json was rewritten to ["mem-A"]
        c1_row = check_conn.execute("SELECT content FROM memories WHERE id = 'mem-consumer-1'").fetchone()
        assert 'depends_on_record_ids_json: ["mem-A"]' in c1_row["content"]
        edge_1 = check_conn.execute(
            "SELECT target_id, target_exists FROM memory_edges WHERE source_id = 'mem-consumer-1'"
        ).fetchone()
        assert edge_1 is not None
        assert edge_1["target_id"] == "mem-A"
        assert edge_1["target_exists"] == 1

        # Case 2 (immediate): evidence_refs: ["mem-B"] remains completely intact and unrewritten
        c2_row = check_conn.execute("SELECT content FROM memories WHERE id = 'mem-consumer-2'").fetchone()
        assert c2_row["content"] == c2_content
        lineage_2 = parse_lineage(c2_row["content"])
        assert len(lineage_2.references_to("mem-B")) == 0
        assert len(lineage_2.references_to("mem-A")) == 0

    # Rebuild database projections: verify state persists across rebuild without regressions
    rebuild_database_projections(db_path)

    with store._connect() as check_conn:
        # Case 1 (post-rebuild): projection edge still points to mem-A with target_exists=1
        edge_1_post = check_conn.execute(
            "SELECT target_id, target_exists FROM memory_edges WHERE source_id = 'mem-consumer-1'"
        ).fetchone()
        assert edge_1_post is not None
        assert edge_1_post["target_id"] == "mem-A"
        assert edge_1_post["target_exists"] == 1

        # Case 2 (post-rebuild): content still completely intact
        c2_row_post = check_conn.execute("SELECT content FROM memories WHERE id = 'mem-consumer-2'").fetchone()
        assert c2_row_post["content"] == c2_content


def test_migration_does_not_rewrite_ignored_singleton_fields(tmp_path: Path) -> None:
    from agent_mem_bridge.database_maintenance import rebuild_database_projections
    from agent_mem_bridge.record_projection import sync_record_projection

    db_path = tmp_path / "singleton_lineage.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        from agent_mem_bridge.schema import init_db

        init_db(conn)
        base = "Base rule content"
        h_base = exact_content_hash(base)

        # A in project:Moebius, B in project:MOEBIUS -> B collapses to A
        conn.execute(
            """
            INSERT INTO memories (id, namespace, kind, title, content, content_hash, exact_content_hash, created_at)
            VALUES ('mem-A', 'project:Moebius', 'memory', 'A', ?, 'ha', ?, '2026-01-01T00:00:00Z')
            """,
            (base, h_base),
        )
        conn.execute(
            """
            INSERT INTO memories (id, namespace, kind, title, content, content_hash, exact_content_hash, created_at)
            VALUES ('mem-B', 'project:MOEBIUS', 'memory', 'B', ?, 'hb', ?, '2026-01-02T00:00:00Z')
            """,
            (base, h_base),
        )

        # Consumer 1: Second line of singleton target_record_id is ignored by canonical parser
        c1_content = "Rule with duplicate singleton\ntarget_record_id: mem-unrelated\ntarget_record_id: mem-B"
        h_c1_exact = exact_content_hash(c1_content)
        conn.execute(
            """
            INSERT INTO memories (id, namespace, kind, title, content, content_hash, exact_content_hash, created_at)
            VALUES ('mem-ignored-singleton', 'project:consumer', 'memory', 'Consumer Ignored', ?, 'hc1', ?, '2026-01-03T00:00:00Z')
            """,
            (c1_content, h_c1_exact),
        )
        sync_record_projection(
            conn,
            memory_id="mem-ignored-singleton",
            namespace="project:consumer",
            content=c1_content,
            tags=[],
            kind="memory",
            actor=None,
            source_app=None,
            is_learning_candidate=False,
        )

        # Consumer 2: First line of singleton target_record_id is active reference, second is ignored
        c2_content = "Rule with active first singleton\ntarget_record_id: mem-B\ntarget_record_id: mem-unrelated"
        h_c2_exact = exact_content_hash(c2_content)
        conn.execute(
            """
            INSERT INTO memories (id, namespace, kind, title, content, content_hash, exact_content_hash, created_at)
            VALUES ('mem-active-singleton', 'project:consumer', 'memory', 'Consumer Active', ?, 'hc2', ?, '2026-01-04T00:00:00Z')
            """,
            (c2_content, h_c2_exact),
        )
        sync_record_projection(
            conn,
            memory_id="mem-active-singleton",
            namespace="project:consumer",
            content=c2_content,
            tags=[],
            kind="memory",
            actor=None,
            source_app=None,
            is_learning_candidate=False,
        )
        conn.commit()
    finally:
        conn.close()

    # Open store: migration runs
    store = MemoryStore(db_path, log_dir=tmp_path / "logs")

    with store._connect() as check_conn:
        # Consumer 1: Ignored line must NOT be rewritten; content and exact hash remain identical
        row_c1 = check_conn.execute(
            "SELECT content, exact_content_hash FROM memories WHERE id = 'mem-ignored-singleton'"
        ).fetchone()
        assert row_c1["content"] == c1_content
        assert row_c1["exact_content_hash"] == h_c1_exact
        assert "target_record_id: mem-B" in row_c1["content"]
        assert "target_record_id: mem-A" not in row_c1["content"]

        # Consumer 2: First line WAS active reference and must be rewritten to mem-A; second line untouched
        row_c2 = check_conn.execute("SELECT content FROM memories WHERE id = 'mem-active-singleton'").fetchone()
        assert "target_record_id: mem-A\ntarget_record_id: mem-unrelated" in row_c2["content"]

    # Rebuild database projections to ensure stability across rebuild
    rebuild_database_projections(db_path)

    with store._connect() as check_conn:
        # Consumer 1: still intact post-rebuild
        row_c1_post = check_conn.execute(
            "SELECT content, exact_content_hash FROM memories WHERE id = 'mem-ignored-singleton'"
        ).fetchone()
        assert row_c1_post["content"] == c1_content
        assert row_c1_post["exact_content_hash"] == h_c1_exact

        # Consumer 2: edge points to mem-A
        edge_c2 = check_conn.execute(
            "SELECT target_id, target_exists FROM memory_edges WHERE source_id = 'mem-active-singleton'"
        ).fetchone()
        assert edge_c2 is not None
        assert edge_c2["target_id"] == "mem-A"
        assert edge_c2["target_exists"] == 1


def test_migration_scopes_self_reference_cleanup_and_preserves_unrelated_historical_self_rows(tmp_path: Path) -> None:
    from agent_mem_bridge.record_projection import sync_record_projection

    db_path = tmp_path / "scoped_self_cleanup.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        from agent_mem_bridge.schema import init_db

        init_db(conn)
        base = "Base rule content"
        h_base = exact_content_hash(base)

        # A in project:Moebius, B in project:MOEBIUS -> B collapses into A
        conn.execute(
            """
            INSERT INTO memories (id, namespace, kind, title, content, content_hash, exact_content_hash, created_at)
            VALUES ('mem-A', 'project:Moebius', 'memory', 'A', ?, 'ha', ?, '2026-01-01T00:00:00Z')
            """,
            (base, h_base),
        )
        conn.execute(
            """
            INSERT INTO memories (id, namespace, kind, title, content, content_hash, exact_content_hash, created_at)
            VALUES ('mem-B', 'project:MOEBIUS', 'memory', 'B', ?, 'hb', ?, '2026-01-02T00:00:00Z')
            """,
            (base, h_base),
        )

        # Collapse-related edge: B -> A, which after repointing B -> A would become self-edge A -> A
        conn.execute(
            """
            INSERT INTO memory_edges (source_id, target_id, relation, position, machine_owned, target_namespace, target_exists)
            VALUES ('mem-B', 'mem-A', 'depends_on', 0, 1, 'project:moebius', 1)
            """
        )

        # Collapse-related revision: B -> A, which after repointing B -> A would become self-revision A -> A
        conn.execute(
            """
            INSERT INTO memory_revisions (predecessor_id, successor_id, actor, reason, created_at)
            VALUES ('mem-B', 'mem-A', 'tester', 'case merge', '2026-01-03T00:00:00Z')
            """
        )

        # Unrelated historical self-edge on 'mem-unrelated'
        u_content = "Historical loop note\ndepends_on: mem-unrelated"
        conn.execute(
            """
            INSERT INTO memories (id, namespace, kind, title, content, content_hash, exact_content_hash, created_at)
            VALUES ('mem-unrelated', 'project:other', 'memory', 'Unrelated', ?, 'hu', ?, '2026-01-01T00:00:00Z')
            """,
            (u_content, exact_content_hash(u_content)),
        )
        sync_record_projection(
            conn,
            memory_id="mem-unrelated",
            namespace="project:other",
            content=u_content,
            tags=[],
            kind="memory",
            actor=None,
            source_app=None,
            is_learning_candidate=False,
        )
        # Unrelated historical self-revision on 'mem-unrelated'
        conn.execute(
            """
            INSERT INTO memory_revisions (predecessor_id, successor_id, actor, reason, created_at)
            VALUES ('mem-unrelated', 'mem-unrelated', 'tester', 'historical loop test', '2026-01-01T00:00:00Z')
            """
        )
        conn.commit()
    finally:
        conn.close()

    # Open store: migration runs and collapses mem-B into mem-A
    store = MemoryStore(db_path, log_dir=tmp_path / "logs")

    with store._connect() as check_conn:
        # Collapse-generated self-edge A -> A must be deleted
        collapse_self_edge = check_conn.execute(
            "SELECT 1 FROM memory_edges WHERE source_id = 'mem-A' AND target_id = 'mem-A'"
        ).fetchone()
        assert collapse_self_edge is None

        # Collapse-generated self-revision A -> A must be deleted
        collapse_self_rev = check_conn.execute(
            "SELECT 1 FROM memory_revisions WHERE predecessor_id = 'mem-A' AND successor_id = 'mem-A'"
        ).fetchone()
        assert collapse_self_rev is None

        # Unrelated historical self-edge must be PRESERVED
        unrelated_edge = check_conn.execute(
            "SELECT 1 FROM memory_edges WHERE source_id = 'mem-unrelated' AND target_id = 'mem-unrelated'"
        ).fetchone()
        assert unrelated_edge is not None

        # Unrelated historical self-revision must be PRESERVED
        unrelated_rev = check_conn.execute(
            "SELECT 1 FROM memory_revisions WHERE predecessor_id = 'mem-unrelated' AND successor_id = 'mem-unrelated'"
        ).fetchone()
        assert unrelated_rev is not None
