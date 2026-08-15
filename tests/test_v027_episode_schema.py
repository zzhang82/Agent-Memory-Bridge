from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from agent_mem_bridge import schema as schema_module
from agent_mem_bridge.database_maintenance import inspect_database, rebuild_database_projections
from agent_mem_bridge.run_projection import inspect_run_projections, rebuild_run_projections
from agent_mem_bridge.schema import CURRENT_SCHEMA_VERSION, SchemaMigration, exact_content_hash, init_db, schema_version
from agent_mem_bridge.storage import MemoryStore

RUN_ID = "run_" + ("1" * 32)
SECOND_RUN_ID = "run_" + ("2" * 32)
WORK_ITEM_ID = "work_" + ("3" * 32)
SECOND_WORK_ITEM_ID = "work_" + ("4" * 32)
EVENT_ID = "evt_" + ("5" * 32)
SECOND_EVENT_ID = "evt_" + ("a" * 32)
OUTCOME_ID = "outcome_" + ("6" * 32)
SECOND_OUTCOME_ID = "outcome_" + ("9" * 32)
ARTIFACT_ID = "artifact_" + ("7" * 32)
LINK_ID = "link_" + ("8" * 32)
CREATED_AT = "2026-07-30T12:00:00+00:00"
ROOT_OUTCOME_AT = CREATED_AT
CURRENT_OUTCOME_AT = "2026-07-30T12:10:00+00:00"
LEGACY_ENDED_AT = "2026-07-30T12:07:00+00:00"
DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64
DIGEST_D = "d" * 64
DIGEST_E = "e" * 64

EPISODE_TABLES = {
    "agent_runs",
    "run_work_items",
    "run_events",
    "run_artifacts",
    "run_outcomes",
    "run_memory_links",
    "run_state_projection",
    "run_work_item_state_projection",
    "memory_utility_shadow",
}

EPISODE_AUTHORITY_TABLES = (
    "agent_runs",
    "run_work_items",
    "run_events",
    "run_artifacts",
    "run_outcomes",
    "run_memory_links",
)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _apply_schema_version(conn: sqlite3.Connection, target_version: int) -> None:
    for raw_migration in schema_module.MIGRATIONS[:target_version]:
        migration = schema_module._coerce_schema_migration(raw_migration)
        migration.apply(conn)
        conn.execute(f"PRAGMA user_version = {migration.version}")
    conn.commit()


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {str(row["name"]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()}


def _table_column_names(conn: sqlite3.Connection, table: str) -> tuple[str, ...]:
    return tuple(str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall())


def _episode_authority_rows(conn: sqlite3.Connection) -> dict[str, list[tuple[object, ...]]]:
    v10_additive_columns = {
        "agent_runs": {
            "evidence_profile",
            "acceptance_criteria_json",
            "acceptance_criteria_digest",
            "constraints_json",
            "non_goals_json",
            "risk_level",
            "continuation_of_run_id",
            "run_generation",
        },
        "run_outcomes": {"verification_profile", "verification_receipt_id"},
    }
    return {
        table: [
            tuple(
                row[column]
                for column in _table_column_names(conn, table)
                if column not in v10_additive_columns.get(table, set())
            )
            for row in conn.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
        ]
        for table in EPISODE_AUTHORITY_TABLES
    }


def _insert_legacy_v7_evidence(conn: sqlite3.Connection) -> None:
    content = "Schema v7 durable memory remains byte-for-byte stable."
    conn.execute(
        """
        INSERT INTO memories (
            id, namespace, kind, title, content, tags_json, content_hash,
            exact_content_hash, created_at
        ) VALUES (?, ?, 'memory', ?, ?, '[]', ?, ?, ?)
        """,
        (
            "legacy-memory",
            "project:bridge",
            "Legacy evidence",
            content,
            "semantic-hash",
            exact_content_hash(content),
            CREATED_AT,
        ),
    )
    conn.execute(
        """
        INSERT INTO retrieval_feedback (
            idempotency_key, receipt_hash, namespace, memory_id, result_rank,
            outcome, retrieval_mode, database_epoch, bridge_instance_id,
            receipt_issued_at, receipt_expires_at, feedback_json, created_at,
            feedback_identity_digest
        ) VALUES (?, ?, ?, ?, 1, 'helpful', 'lexical', ?, ?, ?, ?, '{}', ?, ?)
        """,
        (
            DIGEST_A,
            DIGEST_B,
            "project:bridge",
            "legacy-memory",
            "epoch-v7",
            "bridge-v7",
            CREATED_AT,
            "2026-07-30T12:15:00+00:00",
            CREATED_AT,
            DIGEST_C,
        ),
    )
    conn.commit()


def _insert_run_and_root_work_item(
    conn: sqlite3.Connection, run_id: str = RUN_ID, work_item_id: str = WORK_ITEM_ID
) -> None:
    idempotency_digest = hashlib.sha256(f"idempotency:{run_id}".encode()).hexdigest()
    request_digest = hashlib.sha256(f"request:{run_id}".encode()).hexdigest()
    conn.execute(
        """
        INSERT INTO agent_runs (
            run_id, workspace_key, root_goal, idempotency_key_digest,
            request_digest, created_at
        ) VALUES (?, 'project:bridge', 'Prove the episode ledger.', ?, ?, ?)
        """,
        (run_id, idempotency_digest, request_digest, CREATED_AT),
    )
    conn.execute(
        """
        INSERT INTO run_work_items (work_item_id, run_id, parent_work_item_id, goal, created_at)
        VALUES (?, ?, NULL, 'Execute the root work item.', ?)
        """,
        (work_item_id, run_id, CREATED_AT),
    )
    conn.commit()


def _insert_event(conn: sqlite3.Connection, *, payload_json: str = "{}") -> None:
    conn.execute(
        """
        INSERT INTO run_events (
            event_id, run_id, work_item_id, sequence, event_type, summary,
            payload_json, evidence_json, idempotency_key_digest, request_digest,
            created_at
        ) VALUES (?, ?, ?, 1, 'checkpoint', 'Episode checkpoint.', ?, '[]', ?, ?, ?)
        """,
        (EVENT_ID, RUN_ID, WORK_ITEM_ID, payload_json, DIGEST_A, DIGEST_B, CREATED_AT),
    )


def _insert_episode_authority(conn: sqlite3.Connection) -> None:
    _insert_run_and_root_work_item(conn)
    _insert_event(conn)
    conn.execute(
        """
        INSERT INTO run_outcomes (
            outcome_id, run_id, outcome_type, evaluator_type, evidence_json,
            metrics_json, idempotency_key_digest, request_digest, created_at
        ) VALUES (?, ?, 'unverified', 'agent', '[]', '{}', ?, ?, ?)
        """,
        (OUTCOME_ID, RUN_ID, DIGEST_A, DIGEST_B, CREATED_AT),
    )
    conn.execute(
        """
        INSERT INTO run_artifacts (
            artifact_id, artifact_version, run_id, work_item_id,
            producing_event_id, digest, mime_type, uri, metadata_json, created_at
        ) VALUES (?, 1, ?, ?, ?, ?, 'text/plain', 'artifact://episode-proof', '{}', ?)
        """,
        (ARTIFACT_ID, RUN_ID, WORK_ITEM_ID, EVENT_ID, DIGEST_C, CREATED_AT),
    )
    conn.execute(
        """
        INSERT INTO run_memory_links (
            link_id, run_id, work_item_id, event_id, outcome_id, memory_id,
            exact_content_version, receipt_hash, exposure_rank, relation,
            idempotency_key_digest, request_digest, created_at
        ) VALUES (?, ?, ?, ?, ?, 'memory-1', ?, ?, 1, 'applied', ?, ?, ?)
        """,
        (LINK_ID, RUN_ID, WORK_ITEM_ID, EVENT_ID, OUTCOME_ID, DIGEST_A, DIGEST_B, DIGEST_A, DIGEST_B, CREATED_AT),
    )
    conn.commit()


def _insert_outcome_correction(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        INSERT INTO run_outcomes (
            outcome_id, run_id, outcome_type, evaluator_type, evidence_json,
            metrics_json, supersedes_outcome_id, idempotency_key_digest,
            request_digest, created_at
        ) VALUES (?, ?, 'partial_success', 'agent', '[]', '{}', ?, ?, ?, ?)
        """,
        (SECOND_OUTCOME_ID, RUN_ID, OUTCOME_ID, DIGEST_D, DIGEST_E, CURRENT_OUTCOME_AT),
    )
    conn.commit()


def _insert_v8_run_state_projection(conn: sqlite3.Connection, *, outcome_id: str) -> None:
    conn.execute(
        """
        INSERT INTO run_state_projection (
            run_id, status, last_sequence, unresolved_blocker_count,
            active_work_item_count, outcome_id, ended_at, termination_reason,
            projection_version, rebuilt_at
        ) VALUES (?, 'completed', 1, 0, 0, ?, ?, NULL, 1, ?)
        """,
        (RUN_ID, outcome_id, LEGACY_ENDED_AT, CREATED_AT),
    )
    conn.commit()


def _insert_v8_blocked_resume_history(conn: sqlite3.Connection) -> None:
    blocked_at = "2026-07-30T12:01:00+00:00"
    resumed_at = "2026-07-30T12:02:00+00:00"
    _insert_run_and_root_work_item(conn)
    conn.executemany(
        """
        INSERT INTO run_events (
            event_id, run_id, work_item_id, sequence, event_type, summary,
            payload_json, evidence_json, idempotency_key_digest,
            request_digest, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, '{}', '[]', ?, ?, ?)
        """,
        (
            (
                EVENT_ID,
                RUN_ID,
                WORK_ITEM_ID,
                1,
                "blocker",
                "A v8 blocker paused the work item.",
                DIGEST_A,
                DIGEST_B,
                blocked_at,
            ),
            (
                SECOND_EVENT_ID,
                RUN_ID,
                WORK_ITEM_ID,
                2,
                "work_item_started",
                "A second v8 start event resumed the work item.",
                DIGEST_D,
                DIGEST_E,
                resumed_at,
            ),
        ),
    )
    conn.execute(
        """
        INSERT INTO run_state_projection (
            run_id, status, last_sequence, unresolved_blocker_count,
            active_work_item_count, outcome_id, ended_at, termination_reason,
            projection_version, rebuilt_at
        ) VALUES (?, 'active', 2, 0, 1, NULL, NULL, NULL, 1, ?)
        """,
        (RUN_ID, resumed_at),
    )
    conn.execute(
        """
        INSERT INTO run_work_item_state_projection (
            work_item_id, run_id, status, last_sequence, started_at, ended_at,
            last_summary, projection_version, rebuilt_at
        ) VALUES (?, ?, 'active', 2, ?, NULL, ?, 1, ?)
        """,
        (
            WORK_ITEM_ID,
            RUN_ID,
            CREATED_AT,
            "A second v8 start event resumed the work item.",
            resumed_at,
        ),
    )
    conn.commit()


def test_v7_to_v10_migration_preserves_existing_memory_and_feedback_authority() -> None:
    conn = _connect()
    _apply_schema_version(conn, 7)
    _insert_legacy_v7_evidence(conn)
    memory_before = tuple(conn.execute("SELECT * FROM memories WHERE id = 'legacy-memory'").fetchone())
    feedback_before = tuple(conn.execute("SELECT * FROM retrieval_feedback WHERE feedback_id = 1").fetchone())

    init_db(conn)

    assert schema_version(conn) == CURRENT_SCHEMA_VERSION == 11
    assert EPISODE_TABLES <= _table_names(conn)
    assert tuple(conn.execute("SELECT * FROM memories WHERE id = 'legacy-memory'").fetchone()) == memory_before
    assert tuple(conn.execute("SELECT * FROM retrieval_feedback WHERE feedback_id = 1").fetchone()) == feedback_before
    ledger = conn.execute("SELECT name, checksum FROM schema_migrations WHERE version = 8").fetchone()
    assert tuple(ledger) == (
        "v8_closed_loop_episode_authority",
        "398d0a43a418375fa46e54ad645825515c1151470315a6cd94432269b2e5f386",
    )


def test_injected_v8_failure_rolls_back_ddl_data_ledger_and_user_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _connect()
    _apply_schema_version(conn, 7)
    _insert_legacy_v7_evidence(conn)
    schema_module._ensure_schema_migrations_ledger(conn)
    schema_module._backfill_schema_migrations_ledger(conn, 7)
    conn.commit()
    memory_before = tuple(conn.execute("SELECT * FROM memories WHERE id = 'legacy-memory'").fetchone())
    feedback_before = tuple(conn.execute("SELECT * FROM retrieval_feedback WHERE feedback_id = 1").fetchone())
    ledger_before = [tuple(row) for row in conn.execute("SELECT * FROM schema_migrations ORDER BY version")]
    real_migrations = tuple(schema_module.MIGRATIONS)
    real_v8 = schema_module._coerce_schema_migration(real_migrations[7])

    def failing_v8(connection: sqlite3.Connection) -> None:
        schema_module._migrate_to_v8(connection)
        connection.execute("CREATE TABLE partial_v8_data (id INTEGER PRIMARY KEY)")
        connection.execute("INSERT INTO partial_v8_data(id) VALUES (1)")
        raise RuntimeError("injected v8 failure")

    monkeypatch.setattr(
        schema_module,
        "MIGRATIONS",
        (*real_migrations[:7], SchemaMigration(real_v8.version, real_v8.name, real_v8.checksum, failing_v8)),
    )

    with pytest.raises(RuntimeError, match="injected v8 failure"):
        init_db(conn)

    assert schema_version(conn) == 7
    assert EPISODE_TABLES.isdisjoint(_table_names(conn))
    assert "partial_v8_data" not in _table_names(conn)
    assert tuple(conn.execute("SELECT * FROM memories WHERE id = 'legacy-memory'").fetchone()) == memory_before
    assert tuple(conn.execute("SELECT * FROM retrieval_feedback WHERE feedback_id = 1").fetchone()) == feedback_before
    assert [tuple(row) for row in conn.execute("SELECT * FROM schema_migrations ORDER BY version")] == ledger_before


def test_v8_to_v10_backfills_terminal_times_without_mutating_episode_authority() -> None:
    conn = _connect()
    _apply_schema_version(conn, 8)
    _insert_episode_authority(conn)
    _insert_outcome_correction(conn)
    _insert_v8_run_state_projection(conn, outcome_id=SECOND_OUTCOME_ID)
    authority_before = _episode_authority_rows(conn)

    init_db(conn)

    assert schema_version(conn) == CURRENT_SCHEMA_VERSION == 11
    assert {"terminal_at", "current_outcome_updated_at"} <= set(_table_column_names(conn, "run_state_projection"))
    projection = conn.execute(
        """
        SELECT outcome_id, terminal_at, ended_at, current_outcome_updated_at
        FROM run_state_projection
        WHERE run_id = ?
        """,
        (RUN_ID,),
    ).fetchone()
    assert tuple(projection) == (
        SECOND_OUTCOME_ID,
        ROOT_OUTCOME_AT,
        ROOT_OUTCOME_AT,
        CURRENT_OUTCOME_AT,
    )
    assert _episode_authority_rows(conn) == authority_before
    ledger = conn.execute("SELECT name, checksum FROM schema_migrations WHERE version = 9").fetchone()
    assert tuple(ledger) == (
        "v9_episode_recovery_integrity",
        "c4cf1d0179cc5ee0243fc4bb88b8eaf0148f5f1c3c94d4055cef0e98331d04d1",
    )


def test_v8_blocked_resume_history_migrates_repairs_and_accepts_new_events(tmp_path: Path) -> None:
    db_path = tmp_path / "bridge.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    _apply_schema_version(conn, 8)
    _insert_v8_blocked_resume_history(conn)
    authority_before = _episode_authority_rows(conn)
    conn.close()

    store = MemoryStore(db_path, log_dir=tmp_path / "logs")
    restored = store.get_run(workspace_key="project:bridge", run_id=RUN_ID)
    assert restored["degraded"] is False
    assert restored["snapshot_last_sequence"] == 2
    assert restored["work_items"][0]["status"] == "active"

    repaired = rebuild_database_projections(db_path)
    assert repaired["ok"] is True
    with store._connect() as migrated:
        assert schema_version(migrated) == 11
        assert _episode_authority_rows(migrated) == authority_before
        assert inspect_run_projections(migrated)["ok"] is True

    appended = store.record_run_event(
        workspace_key="project:bridge",
        run_id=RUN_ID,
        work_item_id=WORK_ITEM_ID,
        event_type="checkpoint",
        summary="The migrated v8 episode remains writable.",
        expected_last_sequence=2,
        expected_work_item_status="active",
        idempotency_key="event:v8-resume:migrated-append",
    )
    assert appended["sequence"] == 3


def test_injected_v9_failure_rolls_back_ddl_data_ledger_and_user_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _connect()
    _apply_schema_version(conn, 8)
    _insert_episode_authority(conn)
    _insert_outcome_correction(conn)
    _insert_v8_run_state_projection(conn, outcome_id=SECOND_OUTCOME_ID)
    schema_module._ensure_schema_migrations_ledger(conn)
    schema_module._backfill_schema_migrations_ledger(conn, 8)
    conn.commit()
    authority_before = _episode_authority_rows(conn)
    projection_before = tuple(conn.execute("SELECT * FROM run_state_projection WHERE run_id = ?", (RUN_ID,)).fetchone())
    projection_columns_before = _table_column_names(conn, "run_state_projection")
    ledger_before = [tuple(row) for row in conn.execute("SELECT * FROM schema_migrations ORDER BY version")]
    real_migrations = tuple(schema_module.MIGRATIONS)
    real_v9 = schema_module._coerce_schema_migration(real_migrations[8])

    def failing_v9(connection: sqlite3.Connection) -> None:
        real_v9.apply(connection)
        connection.execute("CREATE TABLE partial_v9_data (id INTEGER PRIMARY KEY)")
        connection.execute("INSERT INTO partial_v9_data(id) VALUES (1)")
        raise RuntimeError("injected v9 failure")

    monkeypatch.setattr(
        schema_module,
        "MIGRATIONS",
        (*real_migrations[:8], SchemaMigration(real_v9.version, real_v9.name, real_v9.checksum, failing_v9)),
    )

    with pytest.raises(RuntimeError, match="injected v9 failure"):
        init_db(conn)

    assert schema_version(conn) == 8
    assert _table_column_names(conn, "run_state_projection") == projection_columns_before
    assert "partial_v9_data" not in _table_names(conn)
    assert (
        tuple(conn.execute("SELECT * FROM run_state_projection WHERE run_id = ?", (RUN_ID,)).fetchone())
        == projection_before
    )
    assert _episode_authority_rows(conn) == authority_before
    assert [tuple(row) for row in conn.execute("SELECT * FROM schema_migrations ORDER BY version")] == ledger_before


def test_v9_to_v10_preserves_authority_and_marks_legacy_run_and_outcome() -> None:
    conn = _connect()
    _apply_schema_version(conn, 9)
    _insert_episode_authority(conn)
    authority_before = _episode_authority_rows(conn)

    init_db(conn)

    assert schema_version(conn) == 11
    assert _episode_authority_rows(conn) == authority_before
    run = conn.execute(
        """
        SELECT evidence_profile, acceptance_criteria_json, run_generation
        FROM agent_runs WHERE run_id = ?
        """,
        (RUN_ID,),
    ).fetchone()
    outcome = conn.execute(
        """
        SELECT verification_profile, verification_receipt_id
        FROM run_outcomes WHERE outcome_id = ?
        """,
        (OUTCOME_ID,),
    ).fetchone()
    assert tuple(run) == ("legacy-v1", "[]", 1)
    assert tuple(outcome) == ("legacy_declared", None)
    assert {"run_event_v2_details", "run_verification_receipts"} <= _table_names(conn)
    assert "not_applicable_count" in _table_column_names(conn, "memory_utility_shadow")
    ledger = conn.execute("SELECT name, checksum FROM schema_migrations WHERE version = 10").fetchone()
    assert tuple(ledger) == (
        "v10_governed_run_v2_authority",
        "acbd48558db0e945ce3ff7608e0e1fbfb4bd58e9574da78cd4efda308583eddd",
    )


def test_injected_v10_failure_rolls_back_additive_schema_and_ledger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _connect()
    _apply_schema_version(conn, 9)
    _insert_episode_authority(conn)
    schema_module._ensure_schema_migrations_ledger(conn)
    schema_module._backfill_schema_migrations_ledger(conn, 9)
    conn.commit()
    authority_before = _episode_authority_rows(conn)
    ledger_before = [tuple(row) for row in conn.execute("SELECT * FROM schema_migrations ORDER BY version")]
    real_migrations = tuple(schema_module.MIGRATIONS)
    real_v10 = schema_module._coerce_schema_migration(real_migrations[9])

    def failing_v10(connection: sqlite3.Connection) -> None:
        real_v10.apply(connection)
        connection.execute("CREATE TABLE partial_v10_data (id INTEGER PRIMARY KEY)")
        raise RuntimeError("injected v10 failure")

    monkeypatch.setattr(
        schema_module,
        "MIGRATIONS",
        (*real_migrations[:9], SchemaMigration(real_v10.version, real_v10.name, real_v10.checksum, failing_v10)),
    )

    with pytest.raises(RuntimeError, match="injected v10 failure"):
        init_db(conn)

    assert schema_version(conn) == 9
    assert "run_event_v2_details" not in _table_names(conn)
    assert "run_verification_receipts" not in _table_names(conn)
    assert "partial_v10_data" not in _table_names(conn)
    assert _episode_authority_rows(conn) == authority_before
    assert [tuple(row) for row in conn.execute("SELECT * FROM schema_migrations ORDER BY version")] == ledger_before


def test_fresh_database_reaches_v11_dynamic_state_schema() -> None:
    conn = _connect()

    init_db(conn)

    assert schema_version(conn) == CURRENT_SCHEMA_VERSION == 11
    assert {"terminal_at", "current_outcome_updated_at"} <= set(_table_column_names(conn, "run_state_projection"))
    assert {"evidence_profile", "run_generation"} <= set(_table_column_names(conn, "agent_runs"))
    assert "not_applicable_count" in _table_column_names(conn, "memory_utility_shadow")
    assert {"state_resources", "state_mutations", "state_heads"} <= _table_names(conn)
    assert conn.execute("SELECT COUNT(*) FROM schema_migrations WHERE version = 10").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM schema_migrations WHERE version = 11").fetchone()[0] == 1


def test_episode_evidence_tables_are_append_only() -> None:
    conn = _connect()
    init_db(conn)
    _insert_episode_authority(conn)

    for statement, message in (
        ("UPDATE run_events SET summary = 'changed' WHERE event_id = ?", "run_events is append-only"),
        ("DELETE FROM run_events WHERE event_id = ?", "run_events is append-only"),
        ("UPDATE run_outcomes SET outcome_type = 'failed' WHERE outcome_id = ?", "run_outcomes is append-only"),
        ("DELETE FROM run_outcomes WHERE outcome_id = ?", "run_outcomes is append-only"),
        ("UPDATE run_artifacts SET uri = 'changed' WHERE artifact_id = ?", "run_artifacts is append-only"),
        ("DELETE FROM run_artifacts WHERE artifact_id = ?", "run_artifacts is append-only"),
        ("UPDATE run_memory_links SET relation = 'rejected' WHERE link_id = ?", "run_memory_links is append-only"),
        ("DELETE FROM run_memory_links WHERE link_id = ?", "run_memory_links is append-only"),
    ):
        identifier = (
            EVENT_ID
            if "run_events" in statement
            else OUTCOME_ID
            if "run_outcomes" in statement
            else ARTIFACT_ID
            if "run_artifacts" in statement
            else LINK_ID
        )
        with pytest.raises(sqlite3.IntegrityError, match=message):
            conn.execute(statement, (identifier,))
        conn.rollback()


def test_v10_run_identity_and_work_items_are_immutable_except_for_generation_increment() -> None:
    conn = _connect()
    init_db(conn)
    _insert_episode_authority(conn)

    with pytest.raises(sqlite3.IntegrityError, match="agent_runs identity and configuration are immutable"):
        conn.execute("UPDATE agent_runs SET root_goal = 'changed' WHERE run_id = ?", (RUN_ID,))
    conn.rollback()
    conn.execute("UPDATE agent_runs SET run_generation = run_generation + 1 WHERE run_id = ?", (RUN_ID,))
    assert conn.execute("SELECT run_generation FROM agent_runs WHERE run_id = ?", (RUN_ID,)).fetchone()[0] == 2
    with pytest.raises(sqlite3.IntegrityError, match="agent_runs identity and configuration are immutable"):
        conn.execute("UPDATE agent_runs SET run_generation = run_generation + 2 WHERE run_id = ?", (RUN_ID,))
    conn.rollback()
    with pytest.raises(sqlite3.IntegrityError, match="agent_runs is immutable"):
        conn.execute("DELETE FROM agent_runs WHERE run_id = ?", (RUN_ID,))
    conn.rollback()
    for statement, message in (
        ("UPDATE run_work_items SET goal = 'changed' WHERE work_item_id = ?", "run_work_items is immutable"),
        ("DELETE FROM run_work_items WHERE work_item_id = ?", "run_work_items is immutable"),
    ):
        with pytest.raises(sqlite3.IntegrityError, match=message):
            conn.execute(statement, (WORK_ITEM_ID,))
        conn.rollback()


def test_episode_payload_privacy_size_and_workspace_combinations_fail_closed() -> None:
    conn = _connect()
    init_db(conn)
    _insert_run_and_root_work_item(conn)

    with pytest.raises(sqlite3.IntegrityError):
        _insert_event(conn, payload_json=json.dumps({"raw_cot": "private reasoning"}))
    conn.rollback()
    with pytest.raises(sqlite3.IntegrityError):
        _insert_event(conn, payload_json=json.dumps({"data": "x" * 32768}))
    conn.rollback()

    _insert_run_and_root_work_item(conn, SECOND_RUN_ID, SECOND_WORK_ITEM_ID)
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
        conn.execute(
            """
            INSERT INTO run_events (
                event_id, run_id, work_item_id, sequence, event_type, summary,
                idempotency_key_digest, request_digest, created_at
            ) VALUES (?, ?, ?, 1, 'checkpoint', 'Cross-run leak.', ?, ?, ?)
            """,
            (EVENT_ID, RUN_ID, SECOND_WORK_ITEM_ID, DIGEST_A, DIGEST_B, CREATED_AT),
        )


def test_verified_success_requires_reviewed_or_deterministic_evidence() -> None:
    conn = _connect()
    init_db(conn)
    _insert_run_and_root_work_item(conn)

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO run_outcomes (
                outcome_id, run_id, outcome_type, evaluator_type, evidence_json,
                idempotency_key_digest, request_digest, created_at
            ) VALUES (?, ?, 'verified_success', 'agent', '[]', ?, ?, ?)
            """,
            (OUTCOME_ID, RUN_ID, DIGEST_A, DIGEST_B, CREATED_AT),
        )
    conn.rollback()

    conn.execute(
        """
        INSERT INTO run_outcomes (
            outcome_id, run_id, outcome_type, evaluator_type, evidence_json,
            idempotency_key_digest, request_digest, created_at
        ) VALUES (?, ?, 'verified_success', 'deterministic_verifier', ?, ?, ?, ?)
        """,
        (
            OUTCOME_ID,
            RUN_ID,
            '[{"kind":"test","reference":"pytest:episode-ledger"}]',
            DIGEST_A,
            DIGEST_B,
            CREATED_AT,
        ),
    )
    assert conn.execute("SELECT outcome_type FROM run_outcomes").fetchone()[0] == "verified_success"


def test_run_work_item_root_and_parent_identity_cannot_form_cycles() -> None:
    conn = _connect()
    init_db(conn)
    _insert_run_and_root_work_item(conn)

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO run_work_items (work_item_id, run_id, goal, created_at)
            VALUES (?, ?, 'Second root is invalid.', ?)
            """,
            (SECOND_WORK_ITEM_ID, RUN_ID, CREATED_AT),
        )
    conn.rollback()
    with pytest.raises(sqlite3.IntegrityError, match="run_work_items is immutable"):
        conn.execute(
            "UPDATE run_work_items SET parent_work_item_id = work_item_id WHERE work_item_id = ?",
            (WORK_ITEM_ID,),
        )


def test_run_projections_are_independently_detected_and_rebuilt() -> None:
    conn = _connect()
    init_db(conn)
    _insert_episode_authority(conn)

    before = inspect_run_projections(conn)
    assert before["ok"] is False
    assert before["counts"]["missing_run_state_projection_count"] == 1
    assert before["counts"]["missing_work_item_state_projection_count"] == 1

    rebuilt = rebuild_run_projections(conn, rebuilt_at=CREATED_AT)
    conn.commit()
    assert rebuilt == {"run_count": 1, "work_item_count": 1}
    assert inspect_run_projections(conn)["ok"] is True
    run_state = conn.execute("SELECT * FROM run_state_projection WHERE run_id = ?", (RUN_ID,)).fetchone()
    assert run_state["status"] == "completed"
    assert run_state["last_sequence"] == 1
    assert run_state["outcome_id"] == OUTCOME_ID

    conn.execute("UPDATE run_state_projection SET status = 'active' WHERE run_id = ?", (RUN_ID,))
    conn.execute("DELETE FROM run_work_item_state_projection WHERE work_item_id = ?", (WORK_ITEM_ID,))
    conn.commit()
    drift = inspect_run_projections(conn)
    assert drift["ok"] is False
    assert drift["counts"]["stale_run_state_projection_count"] == 1
    assert drift["counts"]["missing_work_item_state_projection_count"] == 1

    rebuild_run_projections(conn, rebuilt_at="2026-07-30T12:01:00+00:00")
    conn.commit()
    assert inspect_run_projections(conn)["ok"] is True


def test_database_health_and_projection_repair_include_episode_state(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    with store._connect() as conn:
        _insert_episode_authority(conn)

    unhealthy = inspect_database(store.db_path)
    assert unhealthy["ok"] is False
    assert unhealthy["content"]["counts"]["missing_run_state_projection_count"] == 1
    assert unhealthy["content"]["counts"]["missing_work_item_state_projection_count"] == 1

    repaired = rebuild_database_projections(store.db_path)
    assert repaired["ok"] is True
    assert repaired["run_projection_rebuilt_count"] == 1
    assert repaired["work_item_projection_rebuilt_count"] == 1
    assert repaired["health"]["content"]["counts"]["missing_run_state_projection_count"] == 0
