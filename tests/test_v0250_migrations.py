from __future__ import annotations

import sqlite3

import pytest

from agent_mem_bridge import schema as schema_module
from agent_mem_bridge.schema import CURRENT_SCHEMA_VERSION, SchemaMigration, exact_content_hash, init_db, schema_version

EXPECTED_RETRIEVAL_FEEDBACK_COLUMNS = [
    "feedback_id",
    "idempotency_key",
    "receipt_hash",
    "namespace",
    "memory_id",
    "result_rank",
    "outcome",
    "reason",
    "retrieval_mode",
    "database_epoch",
    "bridge_instance_id",
    "receipt_issued_at",
    "receipt_expires_at",
    "feedback_json",
    "source_app",
    "source_client",
    "source_model",
    "client_session_id",
    "client_workspace",
    "client_transport",
    "actor",
    "created_at",
]

LEGACY_V5_RETRIEVAL_FEEDBACK_CHECKSUM = "4b40b369da475605dd73e9629b8640959cae794fbf3fe46f674a9b15c4c92a01"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {
        row["name"]
        for row in conn.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
            """
        ).fetchall()
    }


def _ledger_rows(conn: sqlite3.Connection) -> list[tuple[int, str, str]]:
    rows = conn.execute(
        """
        SELECT version, name, checksum
        FROM schema_migrations
        ORDER BY version ASC
        """
    ).fetchall()
    return [(int(row["version"]), str(row["name"]), str(row["checksum"])) for row in rows]


def _declared_rows() -> list[tuple[int, str, str]]:
    return [
        (migration.version, migration.name, migration.checksum)
        for migration in (schema_module._coerce_schema_migration(raw) for raw in schema_module.MIGRATIONS)
    ]


def _feedback_column_names(conn: sqlite3.Connection) -> list[str]:
    return [row["name"] for row in conn.execute("PRAGMA table_info(retrieval_feedback)").fetchall()]


def _assert_receipt_bound_feedback_schema(conn: sqlite3.Connection) -> None:
    feedback_columns = _feedback_column_names(conn)
    assert feedback_columns == EXPECTED_RETRIEVAL_FEEDBACK_COLUMNS
    assert {"query_text", "query", "content", "result_content", "feedback_score", "result_position"}.isdisjoint(
        feedback_columns
    )
    assert conn.execute("PRAGMA foreign_key_list(retrieval_feedback)").fetchall() == []
    unique_indexes = {
        row["name"]
        for row in conn.execute("PRAGMA index_list(retrieval_feedback)").fetchall()
        if int(row["unique"]) == 1
    }
    assert "ux_retrieval_feedback_idempotency_key" in unique_indexes


def _apply_schema_version(conn: sqlite3.Connection, target_version: int) -> None:
    for raw_migration in schema_module.MIGRATIONS[:target_version]:
        migration = schema_module._coerce_schema_migration(raw_migration)
        migration.apply(conn)
        conn.execute(f"PRAGMA user_version = {migration.version}")
    conn.commit()


def _insert_memory_row(conn: sqlite3.Connection, memory_id: str = "legacy-row") -> None:
    content = f"{memory_id} survives migration"
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(memories)").fetchall()}
    if "exact_content_hash" in columns:
        conn.execute(
            """
            INSERT INTO memories (
                id, namespace, kind, title, content, tags_json, content_hash, exact_content_hash, created_at
            ) VALUES (?, 'project:bridge', 'memory', 'Legacy', ?, '[]', ?, ?, '2026-01-01T00:00:00+00:00')
            """,
            (memory_id, content, f"semantic-{memory_id}", exact_content_hash(content)),
        )
    else:
        conn.execute(
            """
            INSERT INTO memories (id, namespace, kind, content, tags_json, content_hash, created_at)
            VALUES (?, 'project:bridge', 'memory', ?, '[]', ?, '2026-01-01T00:00:00+00:00')
            """,
            (memory_id, content, f"semantic-{memory_id}"),
        )
    conn.commit()


def _create_v0_fixture(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE memories (
            id TEXT PRIMARY KEY,
            namespace TEXT NOT NULL,
            kind TEXT NOT NULL,
            content TEXT NOT NULL,
            tags_json TEXT NOT NULL DEFAULT '[]',
            content_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE VIRTUAL TABLE memories_fts USING fts5(memory_id UNINDEXED, content);
        """
    )
    _insert_memory_row(conn, "v0-row")


def test_fresh_schema_records_v5_ledger_and_append_only_feedback_table() -> None:
    conn = _connect()

    init_db(conn)

    assert schema_version(conn) == CURRENT_SCHEMA_VERSION == 5
    assert _ledger_rows(conn) == _declared_rows()
    _assert_receipt_bound_feedback_schema(conn)

    conn.execute(
        """
        INSERT INTO retrieval_feedback (
            idempotency_key,
            receipt_hash,
            namespace,
            memory_id,
            result_rank,
            outcome,
            reason,
            retrieval_mode,
            database_epoch,
            bridge_instance_id,
            receipt_issued_at,
            receipt_expires_at,
            feedback_json,
            source_app,
            source_client,
            source_model,
            client_session_id,
            client_workspace,
            client_transport,
            actor,
            created_at
        ) VALUES (
            ?, ?, 'project:bridge', 'memory-1', 1, 'helpful', 'confirmed useful',
            'lexical', 'epoch-1', 'bridge-1', '2026-01-01T00:00:00+00:00',
            '2026-01-01T00:15:00+00:00',
            '{"provenance":"server_declared_not_authenticated","query_hash":"abc123"}',
            'pytest', 'codex', 'gpt-5.5', 'session-1', 'workspace-1', 'mcp',
            'builder', '2026-01-01T00:01:00+00:00'
        )
        """,
        ("a" * 64, "b" * 64),
    )
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE retrieval_feedback SET outcome = 'not_used' WHERE feedback_id = 1")
    conn.rollback()

    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM retrieval_feedback WHERE feedback_id = 1")
    conn.rollback()

    assert conn.execute("SELECT COUNT(*) FROM retrieval_feedback").fetchone()[0] == 1


def test_retrieval_feedback_enforces_unique_idempotency_and_outcome_enum() -> None:
    conn = _connect()
    init_db(conn)

    insert_sql = """
        INSERT INTO retrieval_feedback (
            idempotency_key, receipt_hash, namespace, memory_id, result_rank, outcome, retrieval_mode,
            database_epoch, bridge_instance_id, receipt_issued_at, receipt_expires_at, feedback_json, created_at
        ) VALUES (
            ?, ?, 'project:bridge', 'memory-1', 1, ?, 'lexical',
            'epoch-1', 'bridge-1', '2026-01-01T00:00:00+00:00',
            '2026-01-01T00:15:00+00:00', '{}', '2026-01-01T00:01:00+00:00'
        )
    """
    conn.execute(insert_sql, ("c" * 64, "d" * 64, "helpful"))
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(insert_sql, ("c" * 64, "e" * 64, "not_used"))
    conn.rollback()

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(insert_sql, ("f" * 64, "e" * 64, "raw_score"))
    conn.rollback()


@pytest.mark.parametrize("fixture_version", [0, 1, 2, 3, 4])
def test_v0_through_v4_fixtures_upgrade_to_v5(fixture_version: int) -> None:
    conn = _connect()
    if fixture_version == 0:
        _create_v0_fixture(conn)
    else:
        _apply_schema_version(conn, fixture_version)
        _insert_memory_row(conn, f"v{fixture_version}-row")

    init_db(conn)

    assert schema_version(conn) == CURRENT_SCHEMA_VERSION
    assert _ledger_rows(conn) == _declared_rows()
    assert "retrieval_feedback" in _table_names(conn)
    _assert_receipt_bound_feedback_schema(conn)
    assert conn.execute("SELECT COUNT(*) FROM memories WHERE namespace = 'project:bridge'").fetchone()[0] == 1


def test_existing_v4_database_upgrades_to_v5_without_rewriting_memory_schema() -> None:
    conn = _connect()
    _apply_schema_version(conn, 4)
    _insert_memory_row(conn, "v4-existing-row")
    memory_columns_before = [row["name"] for row in conn.execute("PRAGMA table_info(memories)").fetchall()]
    memory_indexes_before = {row["name"] for row in conn.execute("PRAGMA index_list(memories)").fetchall()}

    init_db(conn)

    memory_columns_after = [row["name"] for row in conn.execute("PRAGMA table_info(memories)").fetchall()]
    memory_indexes_after = {row["name"] for row in conn.execute("PRAGMA index_list(memories)").fetchall()}
    assert schema_version(conn) == CURRENT_SCHEMA_VERSION
    assert memory_columns_after == memory_columns_before
    assert memory_indexes_after == memory_indexes_before
    assert conn.execute("SELECT content FROM memories WHERE id = 'v4-existing-row'").fetchone()["content"] == (
        "v4-existing-row survives migration"
    )


def test_injected_v5_failure_rolls_back_ddl_data_user_version_and_ledger(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _connect()
    _apply_schema_version(conn, 4)
    _insert_memory_row(conn, "rollback-row")
    real_migrations = tuple(schema_module.MIGRATIONS)
    real_v5 = schema_module._coerce_schema_migration(real_migrations[4])

    def failing_v5(connection: sqlite3.Connection) -> None:
        schema_module._migrate_to_v5(connection)
        connection.execute(
            """
            INSERT INTO retrieval_feedback (
                idempotency_key,
                receipt_hash,
                namespace,
                memory_id,
                result_rank,
                outcome,
                retrieval_mode,
                database_epoch,
                bridge_instance_id,
                receipt_issued_at,
                receipt_expires_at,
                feedback_json,
                created_at
            ) VALUES (
                ?, ?, 'project:bridge', 'rollback-row', 1, 'helpful', 'lexical',
                'epoch-1', 'bridge-1', '2026-01-01T00:00:00+00:00',
                '2026-01-01T00:15:00+00:00', '{}', '2026-01-01T00:01:00+00:00'
            )
            """,
            ("1" * 64, "2" * 64),
        )
        connection.execute("CREATE TABLE partial_v5_data (id INTEGER PRIMARY KEY)")
        connection.execute("INSERT INTO partial_v5_data(id) VALUES (1)")
        raise RuntimeError("injected v5 failure")

    monkeypatch.setattr(
        schema_module,
        "MIGRATIONS",
        (*real_migrations[:4], SchemaMigration(real_v5.version, real_v5.name, real_v5.checksum, failing_v5)),
    )

    with pytest.raises(RuntimeError, match="injected v5 failure"):
        init_db(conn)

    assert schema_version(conn) == 4
    assert "schema_migrations" not in _table_names(conn)
    assert "retrieval_feedback" not in _table_names(conn)
    assert "partial_v5_data" not in _table_names(conn)
    assert conn.execute("SELECT content FROM memories WHERE id = 'rollback-row'").fetchone()["content"] == (
        "rollback-row survives migration"
    )


def test_schema_migration_checksum_mismatch_fails_closed_without_repair() -> None:
    conn = _connect()
    init_db(conn)
    conn.execute(
        """
        UPDATE schema_migrations
        SET checksum = ?
        WHERE version = 4
        """,
        ("0" * 64,),
    )
    conn.commit()

    with pytest.raises(RuntimeError, match="ledger mismatch for version 4"):
        init_db(conn)

    assert schema_version(conn) == CURRENT_SCHEMA_VERSION
    assert conn.execute("SELECT checksum FROM schema_migrations WHERE version = 4").fetchone()["checksum"] == "0" * 64


def test_unledgered_legacy_v5_retrieval_feedback_shape_fails_closed_with_clear_error() -> None:
    conn = _connect()
    _apply_schema_version(conn, 4)
    conn.execute(
        """
        CREATE TABLE retrieval_feedback (
            feedback_id INTEGER PRIMARY KEY AUTOINCREMENT,
            namespace TEXT NOT NULL,
            memory_id TEXT,
            query_text TEXT NOT NULL,
            feedback_score INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute("PRAGMA user_version = 5")
    conn.commit()

    with pytest.raises(RuntimeError, match="unsupported retrieval_feedback shape"):
        init_db(conn)

    assert schema_version(conn) == 5
    assert "schema_migrations" not in _table_names(conn)
    assert "query_text" in _feedback_column_names(conn)
    assert "idempotency_key" not in _feedback_column_names(conn)


def test_legacy_v5_checksum_mismatch_fails_closed_without_schema_repair() -> None:
    conn = _connect()
    _apply_schema_version(conn, 4)
    schema_module._ensure_schema_migrations_ledger(conn)
    for version, name, checksum in _declared_rows()[:4]:
        conn.execute(
            """
            INSERT INTO schema_migrations (version, name, checksum, applied_at)
            VALUES (?, ?, ?, '2026-01-01T00:00:00Z')
            """,
            (version, name, checksum),
        )
    conn.execute(
        """
        CREATE TABLE retrieval_feedback (
            feedback_id INTEGER PRIMARY KEY AUTOINCREMENT,
            namespace TEXT NOT NULL,
            memory_id TEXT,
            query_text TEXT NOT NULL,
            feedback_score INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO schema_migrations (version, name, checksum, applied_at)
        VALUES (5, 'v5_retrieval_feedback_append_only', ?, '2026-01-01T00:00:00Z')
        """,
        (LEGACY_V5_RETRIEVAL_FEEDBACK_CHECKSUM,),
    )
    conn.execute("PRAGMA user_version = 5")
    conn.commit()

    with pytest.raises(RuntimeError, match="ledger mismatch for version 5"):
        init_db(conn)

    assert "query_text" in _feedback_column_names(conn)
    assert "idempotency_key" not in _feedback_column_names(conn)


def test_newer_schema_fails_closed_before_creating_ledger() -> None:
    conn = _connect()
    conn.execute("CREATE TABLE marker (value TEXT)")
    conn.execute("INSERT INTO marker(value) VALUES ('stable')")
    conn.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION + 1}")
    conn.commit()

    with pytest.raises(RuntimeError, match="newer than supported"):
        init_db(conn)

    assert schema_version(conn) == CURRENT_SCHEMA_VERSION + 1
    assert "schema_migrations" not in _table_names(conn)
    assert conn.execute("SELECT value FROM marker").fetchone()["value"] == "stable"


def test_v1_migration_does_not_call_mutable_current_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _connect()
    v1_migration = schema_module._coerce_schema_migration(schema_module.MIGRATIONS[0])

    def fail_current_schema(connection: sqlite3.Connection) -> None:
        raise AssertionError(f"mutable current schema called with {connection!r}")

    monkeypatch.setattr(schema_module, "CURRENT_SCHEMA_VERSION", 1)
    monkeypatch.setattr(schema_module, "MIGRATIONS", (v1_migration,))
    monkeypatch.setattr(schema_module, "_ensure_current_schema", fail_current_schema)

    init_db(conn)

    memory_columns = {row["name"] for row in conn.execute("PRAGMA table_info(memories)").fetchall()}
    dedup_columns = [row["name"] for row in conn.execute("PRAGMA index_info(idx_memories_dedup)").fetchall()]
    assert schema_version(conn) == 1
    assert _ledger_rows(conn) == [(v1_migration.version, v1_migration.name, v1_migration.checksum)]
    assert "exact_content_hash" not in memory_columns
    assert "memory_metadata" not in _table_names(conn)
    assert "bridge_metadata" not in _table_names(conn)
    assert "retrieval_feedback" not in _table_names(conn)
    assert dedup_columns == ["namespace", "content_hash"]
