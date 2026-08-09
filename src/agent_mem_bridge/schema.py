from __future__ import annotations

import hashlib
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from .embedding_index import ensure_embedding_schema
from .record_projection import backfill_record_projections

IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
CURRENT_SCHEMA_VERSION = 9
LEGACY_V5_RETRIEVAL_FEEDBACK_COLUMNS = (
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
)
LEGACY_V6_RETRIEVAL_FEEDBACK_COLUMNS = (
    *LEGACY_V5_RETRIEVAL_FEEDBACK_COLUMNS,
    "feedback_type",
    "supersedes_feedback_id",
)
RETRIEVAL_FEEDBACK_COLUMNS = (
    *LEGACY_V6_RETRIEVAL_FEEDBACK_COLUMNS,
    "feedback_identity_digest",
)


@dataclass(frozen=True, slots=True)
class SchemaMigration:
    version: int
    name: str
    checksum: str
    apply: Callable[[sqlite3.Connection], None]


def quote_identifier(identifier: str) -> str:
    if not IDENTIFIER_RE.fullmatch(identifier):
        raise ValueError(f"invalid SQL identifier: {identifier!r}")
    return f'"{identifier}"'


def init_db(conn: sqlite3.Connection) -> None:
    if conn.in_transaction:
        raise RuntimeError("schema initialization requires a connection without an active transaction")
    conn.execute("BEGIN IMMEDIATE")
    try:
        current_version = schema_version(conn)
        if current_version > CURRENT_SCHEMA_VERSION:
            raise RuntimeError(
                f"database schema version {current_version} is newer than supported version {CURRENT_SCHEMA_VERSION}"
            )
        ledger_exists = _schema_migrations_ledger_exists(conn)
        if ledger_exists:
            _validate_schema_migrations_ledger(conn, current_version)
        else:
            _validate_unledgered_schema_before_backfill(conn, current_version)
            _ensure_schema_migrations_ledger(conn)
            _backfill_schema_migrations_ledger(conn, current_version)
        migrated = False
        expected_version = current_version + 1
        for raw_migration in MIGRATIONS:
            migration = _coerce_schema_migration(raw_migration)
            target_version = migration.version
            if target_version <= current_version:
                continue
            if target_version != expected_version:
                raise RuntimeError(
                    f"schema migration sequence is incomplete: expected version {expected_version}, "
                    f"found {target_version}"
                )
            migration.apply(conn)
            _record_schema_migration(conn, migration)
            conn.execute(f"PRAGMA user_version = {target_version}")
            current_version = target_version
            expected_version += 1
            migrated = True
        if current_version != CURRENT_SCHEMA_VERSION:
            raise RuntimeError(
                f"schema migration sequence stops at version {current_version}; expected {CURRENT_SCHEMA_VERSION}"
            )
        if not migrated:
            _ensure_current_schema(conn)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("PRAGMA user_version").fetchone()
    return int(row[0]) if row is not None else 0


def _migrate_to_v1(conn: sqlite3.Connection) -> None:
    _ensure_v1_schema(conn)


def _migrate_to_v2(conn: sqlite3.Connection) -> None:
    _ensure_projection_schema(conn)
    backfill_record_projections(conn)


def _migrate_to_v3(conn: sqlite3.Connection) -> None:
    _ensure_bridge_metadata_schema(conn)


def _migrate_to_v4(conn: sqlite3.Connection) -> None:
    _ensure_exact_content_identity_schema(conn)


def _migrate_to_v5(conn: sqlite3.Connection) -> None:
    _ensure_retrieval_feedback_schema(conn)


def _migrate_to_v6(conn: sqlite3.Connection) -> None:
    _ensure_retrieval_feedback_effective_vote_schema(conn)


def _migrate_to_v7(conn: sqlite3.Connection) -> None:
    _ensure_retrieval_feedback_identity_schema(conn)


def _migrate_to_v8(conn: sqlite3.Connection) -> None:
    _ensure_episode_schema(conn)


def _migrate_to_v9(conn: sqlite3.Connection) -> None:
    _ensure_episode_recovery_integrity_schema(conn)


MIGRATIONS: tuple[SchemaMigration | tuple[int, Callable[[sqlite3.Connection], None]], ...] = (
    SchemaMigration(
        1,
        "v1_base_schema_snapshot",
        "5175385fa3e27a9b0e025e2e0a59c74ab8b87dd383806ed0700e3f3154a7ccb6",
        _migrate_to_v1,
    ),
    SchemaMigration(
        2,
        "v2_record_projection_schema",
        "19f1742e3d0bd0f408006df94cc75fbcbf388e72f42bd6c5dc08525280f0e4f5",
        _migrate_to_v2,
    ),
    SchemaMigration(
        3,
        "v3_bridge_metadata_schema",
        "33fc37b0ad67cec090165517fc78a192a3acca9ca0c8c88fa3aef86c151779bf",
        _migrate_to_v3,
    ),
    SchemaMigration(
        4,
        "v4_exact_content_identity_schema",
        "64afdd68f8d67a5395dec229378d1add9593e40c0b39e0081150fed8cc473984",
        _migrate_to_v4,
    ),
    SchemaMigration(
        5,
        "v5_retrieval_feedback_append_only",
        "bf1ad8c49e78e15d11962e31a659f8e955dc3fbd8047d801e457183e75163be4",
        _migrate_to_v5,
    ),
    SchemaMigration(
        6,
        "v6_retrieval_feedback_effective_votes",
        "f2fb0b4e3f9dd723fc205ea35f20b1f19ed7cd7e47d922c258871242221f7c94",
        _migrate_to_v6,
    ),
    SchemaMigration(
        7,
        "v7_retrieval_feedback_identity_digest",
        "cc4074acb0aee9f77eae265f3b9940b337737918e9c8d6e31f6ad702e202aa98",
        _migrate_to_v7,
    ),
    SchemaMigration(
        8,
        "v8_closed_loop_episode_authority",
        "398d0a43a418375fa46e54ad645825515c1151470315a6cd94432269b2e5f386",
        _migrate_to_v8,
    ),
    SchemaMigration(
        9,
        "v9_episode_recovery_integrity",
        "c4cf1d0179cc5ee0243fc4bb88b8eaf0148f5f1c3c94d4055cef0e98331d04d1",
        _migrate_to_v9,
    ),
)


def _ensure_current_schema(conn: sqlite3.Connection) -> None:
    _ensure_v1_schema(conn)
    _ensure_projection_schema(conn)
    _ensure_bridge_metadata_schema(conn)
    _ensure_exact_content_identity_schema(conn)
    _ensure_retrieval_feedback_schema(conn)
    _ensure_retrieval_feedback_effective_vote_schema(conn)
    _ensure_retrieval_feedback_identity_schema(conn)
    _ensure_episode_schema(conn)
    _ensure_episode_recovery_integrity_schema(conn)
    backfill_record_projections(conn, only_missing=True)


def _coerce_schema_migration(
    migration: SchemaMigration | tuple[int, Callable[[sqlite3.Connection], None]],
) -> SchemaMigration:
    if isinstance(migration, SchemaMigration):
        return migration
    version, apply = migration
    name = getattr(apply, "__name__", f"migration_{version}")
    checksum = hashlib.sha256(f"test-migration:{version}:{name}".encode("utf-8")).hexdigest()
    return SchemaMigration(version=version, name=name, checksum=checksum, apply=apply)


def _declared_schema_migrations() -> dict[int, SchemaMigration]:
    declared: dict[int, SchemaMigration] = {}
    for raw_migration in MIGRATIONS:
        migration = _coerce_schema_migration(raw_migration)
        if migration.version in declared:
            raise RuntimeError(f"duplicate schema migration declaration for version {migration.version}")
        if migration.version <= 0:
            raise RuntimeError(f"invalid schema migration version {migration.version}")
        if (
            len(migration.checksum) != 64
            or migration.checksum != migration.checksum.lower()
            or any(char not in "0123456789abcdef" for char in migration.checksum)
        ):
            raise RuntimeError(f"invalid schema migration checksum for version {migration.version}")
        declared[migration.version] = migration
    return declared


def _schema_migrations_ledger_exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table'
        AND name = 'schema_migrations'
        LIMIT 1
        """
    ).fetchone()
    return row is not None


def _validate_unledgered_schema_before_backfill(conn: sqlite3.Connection, current_version: int) -> None:
    if current_version < 5:
        return
    feedback_columns = _table_column_names(conn, "retrieval_feedback")
    supported_shapes = {tuple(LEGACY_V5_RETRIEVAL_FEEDBACK_COLUMNS)}
    if current_version >= 7:
        supported_shapes = {tuple(RETRIEVAL_FEEDBACK_COLUMNS)}
    elif current_version == 6:
        supported_shapes = {tuple(LEGACY_V6_RETRIEVAL_FEEDBACK_COLUMNS)}
    elif current_version == 5:
        supported_shapes.add(tuple(LEGACY_V6_RETRIEVAL_FEEDBACK_COLUMNS))
    if tuple(feedback_columns) not in supported_shapes:
        raise RuntimeError(
            "unledgered schema version 5 has an unsupported retrieval_feedback shape; "
            "cannot backfill the migration ledger"
        )


def _ensure_schema_migrations_ledger(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY CHECK (version > 0),
            name TEXT NOT NULL CHECK (length(name) > 0),
            checksum TEXT NOT NULL
                CHECK (
                    length(checksum) = 64
                    AND checksum = lower(checksum)
                    AND checksum NOT GLOB '*[^0-9a-f]*'
                ),
            applied_at TEXT NOT NULL CHECK (julianday(applied_at) IS NOT NULL),
            UNIQUE (name)
        ) WITHOUT ROWID
        """
    )


def _table_column_names(conn: sqlite3.Connection, table: str) -> list[str]:
    table_sql = quote_identifier(table)
    return [row["name"] for row in conn.execute(f"PRAGMA table_info({table_sql})").fetchall()]


def _validate_schema_migrations_ledger(conn: sqlite3.Connection, current_version: int) -> None:
    declared = _declared_schema_migrations()
    try:
        rows = conn.execute(
            """
            SELECT version, name, checksum
            FROM schema_migrations
            ORDER BY version ASC
            """
        ).fetchall()
    except sqlite3.DatabaseError as exc:
        raise RuntimeError("schema_migrations ledger is invalid") from exc

    observed_versions: set[int] = set()
    for row in rows:
        version = int(row["version"])
        declared_migration = declared.get(version)
        if declared_migration is None or version > CURRENT_SCHEMA_VERSION:
            raise RuntimeError(f"schema_migrations ledger contains unknown version {version}")
        if version > current_version:
            raise RuntimeError(f"schema_migrations ledger version {version} is ahead of user_version {current_version}")
        if row["name"] != declared_migration.name or row["checksum"] != declared_migration.checksum:
            raise RuntimeError(f"schema_migrations ledger mismatch for version {version}")
        observed_versions.add(version)

    for version in range(1, current_version + 1):
        if version not in observed_versions:
            raise RuntimeError(f"schema_migrations ledger is missing version {version}")


def _backfill_schema_migrations_ledger(conn: sqlite3.Connection, current_version: int) -> None:
    declared = _declared_schema_migrations()
    for version in range(1, current_version + 1):
        migration = declared.get(version)
        if migration is None:
            raise RuntimeError(f"schema_migrations ledger cannot backfill unknown version {version}")
        _record_schema_migration(conn, migration)


def _record_schema_migration(conn: sqlite3.Connection, migration: SchemaMigration) -> None:
    conn.execute(
        """
        INSERT INTO schema_migrations (version, name, checksum, applied_at)
        VALUES (?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        """,
        (migration.version, migration.name, migration.checksum),
    )


def _ensure_v1_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS memories (
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
            is_learning_candidate INTEGER NOT NULL DEFAULT 0,
            lineage_status TEXT NOT NULL DEFAULT 'intact',
            lineage_issues_json TEXT NOT NULL DEFAULT '[]',
            content_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS memory_tombstones (
            forgotten_id TEXT PRIMARY KEY,
            namespace TEXT NOT NULL,
            kind TEXT NOT NULL,
            deleted_at TEXT NOT NULL,
            root_forget_id TEXT NOT NULL,
            cause TEXT NOT NULL
        ) WITHOUT ROWID
        """
    )
    ensure_column(conn, "memories", "title", "ALTER TABLE memories ADD COLUMN title TEXT")
    ensure_column(conn, "memories", "session_id", "ALTER TABLE memories ADD COLUMN session_id TEXT")
    ensure_column(conn, "memories", "actor", "ALTER TABLE memories ADD COLUMN actor TEXT")
    ensure_column(conn, "memories", "correlation_id", "ALTER TABLE memories ADD COLUMN correlation_id TEXT")
    ensure_column(conn, "memories", "source_app", "ALTER TABLE memories ADD COLUMN source_app TEXT")
    ensure_column(conn, "memories", "source_client", "ALTER TABLE memories ADD COLUMN source_client TEXT")
    ensure_column(conn, "memories", "source_model", "ALTER TABLE memories ADD COLUMN source_model TEXT")
    ensure_column(conn, "memories", "client_session_id", "ALTER TABLE memories ADD COLUMN client_session_id TEXT")
    ensure_column(conn, "memories", "client_workspace", "ALTER TABLE memories ADD COLUMN client_workspace TEXT")
    ensure_column(conn, "memories", "client_transport", "ALTER TABLE memories ADD COLUMN client_transport TEXT")
    ensure_column(conn, "memories", "signal_status", "ALTER TABLE memories ADD COLUMN signal_status TEXT")
    ensure_column(conn, "memories", "claimed_by", "ALTER TABLE memories ADD COLUMN claimed_by TEXT")
    ensure_column(conn, "memories", "claimed_at", "ALTER TABLE memories ADD COLUMN claimed_at TEXT")
    ensure_column(conn, "memories", "lease_expires_at", "ALTER TABLE memories ADD COLUMN lease_expires_at TEXT")
    ensure_column(conn, "memories", "expires_at", "ALTER TABLE memories ADD COLUMN expires_at TEXT")
    ensure_column(conn, "memories", "acknowledged_at", "ALTER TABLE memories ADD COLUMN acknowledged_at TEXT")
    ensure_column(
        conn,
        "memories",
        "is_learning_candidate",
        "ALTER TABLE memories ADD COLUMN is_learning_candidate INTEGER NOT NULL DEFAULT 0",
    )
    ensure_column(
        conn,
        "memories",
        "lineage_status",
        "ALTER TABLE memories ADD COLUMN lineage_status TEXT NOT NULL DEFAULT 'intact'",
    )
    ensure_column(
        conn,
        "memories",
        "lineage_issues_json",
        "ALTER TABLE memories ADD COLUMN lineage_issues_json TEXT NOT NULL DEFAULT '[]'",
    )
    conn.execute(
        """
        UPDATE memories
        SET is_learning_candidate = 1
        WHERE is_learning_candidate = 0
        AND (
            (
                tags_json LIKE '%"kind:learning-candidate"%'
                AND EXISTS (SELECT 1 FROM json_each(memories.tags_json) WHERE value = 'kind:learning-candidate')
            )
            OR (
                tags_json LIKE '%"kind:learning-review"%'
                AND EXISTS (SELECT 1 FROM json_each(memories.tags_json) WHERE value = 'kind:learning-review')
            )
        )
        """
    )
    ensure_fts_columns(conn)
    ensure_embedding_schema(conn)
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_memories_dedup
        ON memories (namespace, content_hash)
        WHERE kind != 'signal'
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_memories_namespace_created_at
        ON memories (namespace, created_at DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_memories_session_id_created_at
        ON memories (session_id, created_at DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_memories_kind_namespace_created_at
        ON memories (kind, namespace, created_at DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_memories_actor_created_at
        ON memories (actor, created_at DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_memories_correlation_id_created_at
        ON memories (correlation_id, created_at DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_memories_source_client_created_at
        ON memories (source_client, created_at DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_memories_source_model_created_at
        ON memories (source_model, created_at DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_memories_signal_status_created_at
        ON memories (namespace, signal_status, created_at DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_memories_signal_claimed_by_created_at
        ON memories (claimed_by, created_at DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_memories_learning_candidate_visible
        ON memories (namespace, is_learning_candidate, created_at DESC)
        """
    )


def _ensure_projection_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS memory_insertions (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            memory_id TEXT NOT NULL UNIQUE
                REFERENCES memories(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO memory_insertions (memory_id)
        SELECT id
        FROM memories
        ORDER BY rowid ASC
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS memory_metadata (
            memory_id TEXT PRIMARY KEY
                REFERENCES memories(id) ON DELETE CASCADE,
            record_type TEXT,
            status TEXT,
            confidence REAL
                CHECK (confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0)),
            confidence_label TEXT,
            valid_from TEXT,
            valid_until TEXT,
            metadata_schema_version INTEGER NOT NULL DEFAULT 1
                CHECK (metadata_schema_version > 0),
            validation_issues_json TEXT NOT NULL DEFAULT '[]'
                CHECK (json_valid(validation_issues_json))
        ) WITHOUT ROWID
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS memory_tags (
            memory_id TEXT NOT NULL
                REFERENCES memories(id) ON DELETE CASCADE,
            tag TEXT NOT NULL CHECK (length(tag) > 0),
            prefix TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (memory_id, tag)
        ) WITHOUT ROWID
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS memory_edges (
            source_id TEXT NOT NULL
                REFERENCES memories(id) ON DELETE CASCADE,
            target_id TEXT NOT NULL CHECK (length(target_id) > 0),
            relation TEXT NOT NULL CHECK (length(relation) > 0),
            position INTEGER NOT NULL DEFAULT 0 CHECK (position >= 0),
            machine_owned INTEGER NOT NULL DEFAULT 0
                CHECK (machine_owned IN (0, 1)),
            target_namespace TEXT,
            target_exists INTEGER NOT NULL DEFAULT 0
                CHECK (target_exists IN (0, 1)),
            PRIMARY KEY (source_id, target_id, relation)
        ) WITHOUT ROWID
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS memory_annotations (
            annotation_id INTEGER PRIMARY KEY AUTOINCREMENT,
            memory_id TEXT NOT NULL
                REFERENCES memories(id) ON DELETE CASCADE,
            title_before TEXT,
            title_after TEXT,
            added_tags_json TEXT NOT NULL DEFAULT '[]'
                CHECK (json_valid(added_tags_json)),
            provenance_json TEXT NOT NULL DEFAULT '{}'
                CHECK (json_valid(provenance_json)),
            actor TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS memory_revisions (
            revision_id INTEGER PRIMARY KEY AUTOINCREMENT,
            predecessor_id TEXT NOT NULL,
            successor_id TEXT NOT NULL,
            actor TEXT,
            reason TEXT,
            created_at TEXT NOT NULL,
            UNIQUE (predecessor_id, successor_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS signal_repairs (
            repair_id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id TEXT NOT NULL,
            previous_state_json TEXT NOT NULL CHECK (json_valid(previous_state_json)),
            repaired_state_json TEXT NOT NULL CHECK (json_valid(repaired_state_json)),
            reason TEXT NOT NULL CHECK (length(reason) > 0),
            actor TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute("DROP TRIGGER IF EXISTS validate_claimed_signal_insert")
    conn.execute("DROP TRIGGER IF EXISTS validate_claimed_signal_update")
    conn.execute("DROP TRIGGER IF EXISTS validate_signal_state_insert")
    conn.execute("DROP TRIGGER IF EXISTS validate_signal_state_update")
    conn.execute(
        """
        CREATE TRIGGER validate_signal_state_insert
        BEFORE INSERT ON memories
        WHEN NEW.kind = 'signal' AND (
            NEW.signal_status NOT IN ('pending', 'claimed', 'acked')
            OR NEW.signal_status IS NULL
            OR (NEW.expires_at IS NOT NULL AND julianday(NEW.expires_at) IS NULL)
            OR (NEW.claimed_at IS NOT NULL AND julianday(NEW.claimed_at) IS NULL)
            OR (NEW.lease_expires_at IS NOT NULL AND julianday(NEW.lease_expires_at) IS NULL)
            OR (NEW.acknowledged_at IS NOT NULL AND julianday(NEW.acknowledged_at) IS NULL)
            OR (NEW.signal_status = 'claimed' AND (
                COALESCE(trim(NEW.claimed_by), '') = ''
                OR NEW.claimed_at IS NULL
                OR NEW.lease_expires_at IS NULL
                OR NEW.acknowledged_at IS NOT NULL
                OR julianday(NEW.lease_expires_at) < julianday(NEW.claimed_at)
                OR (NEW.expires_at IS NOT NULL
                    AND julianday(NEW.lease_expires_at) > julianday(NEW.expires_at))
            ))
            OR (NEW.signal_status = 'pending' AND (
                NEW.claimed_by IS NOT NULL
                OR NEW.claimed_at IS NOT NULL
                OR NEW.lease_expires_at IS NOT NULL
                OR NEW.acknowledged_at IS NOT NULL
            ))
            OR (NEW.signal_status = 'acked' AND (
                NEW.acknowledged_at IS NULL
                OR NEW.lease_expires_at IS NOT NULL
                OR (NEW.claimed_by IS NULL AND NEW.claimed_at IS NOT NULL)
                OR (NEW.claimed_by IS NOT NULL AND COALESCE(trim(NEW.claimed_by), '') = '')
                OR (NEW.claimed_by IS NOT NULL AND NEW.claimed_at IS NULL)
                OR (NEW.claimed_at IS NOT NULL
                    AND julianday(NEW.acknowledged_at) < julianday(NEW.claimed_at))
            ))
            OR (NEW.signal_status != 'acked' AND NEW.acknowledged_at IS NOT NULL)
        )
        BEGIN
            SELECT RAISE(ABORT, 'invalid signal state or timestamp');
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER validate_signal_state_update
        BEFORE UPDATE OF signal_status, claimed_by, claimed_at, lease_expires_at, expires_at, acknowledged_at
        ON memories
        WHEN NEW.kind = 'signal' AND (
            NEW.signal_status NOT IN ('pending', 'claimed', 'acked')
            OR NEW.signal_status IS NULL
            OR (NEW.expires_at IS NOT NULL AND julianday(NEW.expires_at) IS NULL)
            OR (NEW.claimed_at IS NOT NULL AND julianday(NEW.claimed_at) IS NULL)
            OR (NEW.lease_expires_at IS NOT NULL AND julianday(NEW.lease_expires_at) IS NULL)
            OR (NEW.acknowledged_at IS NOT NULL AND julianday(NEW.acknowledged_at) IS NULL)
            OR (NEW.signal_status = 'claimed' AND (
                COALESCE(trim(NEW.claimed_by), '') = ''
                OR NEW.claimed_at IS NULL
                OR NEW.lease_expires_at IS NULL
                OR NEW.acknowledged_at IS NOT NULL
                OR julianday(NEW.lease_expires_at) < julianday(NEW.claimed_at)
                OR (NEW.expires_at IS NOT NULL
                    AND julianday(NEW.lease_expires_at) > julianday(NEW.expires_at))
            ))
            OR (NEW.signal_status = 'pending' AND (
                NEW.claimed_by IS NOT NULL
                OR NEW.claimed_at IS NOT NULL
                OR NEW.lease_expires_at IS NOT NULL
                OR NEW.acknowledged_at IS NOT NULL
            ))
            OR (NEW.signal_status = 'acked' AND (
                NEW.acknowledged_at IS NULL
                OR NEW.lease_expires_at IS NOT NULL
                OR (NEW.claimed_by IS NULL AND NEW.claimed_at IS NOT NULL)
                OR (NEW.claimed_by IS NOT NULL AND COALESCE(trim(NEW.claimed_by), '') = '')
                OR (NEW.claimed_by IS NOT NULL AND NEW.claimed_at IS NULL)
                OR (NEW.claimed_at IS NOT NULL
                    AND julianday(NEW.acknowledged_at) < julianday(NEW.claimed_at))
            ))
            OR (NEW.signal_status != 'acked' AND NEW.acknowledged_at IS NOT NULL)
        )
        BEGIN
            SELECT RAISE(ABORT, 'invalid signal state or timestamp');
        END
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_memory_metadata_record_type
        ON memory_metadata (record_type, memory_id)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_memory_metadata_status
        ON memory_metadata (status, memory_id)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_memory_tags_tag
        ON memory_tags (tag, memory_id)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_memory_tags_prefix
        ON memory_tags (prefix, tag, memory_id)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_memory_edges_target_machine
        ON memory_edges (target_id, machine_owned, source_id)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_memory_edges_source_relation
        ON memory_edges (source_id, relation, position, target_id)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_memory_edges_target_relation
        ON memory_edges (target_id, relation, source_id)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_memory_annotations_memory_created
        ON memory_annotations (memory_id, created_at, annotation_id)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_memory_revisions_predecessor
        ON memory_revisions (predecessor_id, created_at, revision_id)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_memory_revisions_successor
        ON memory_revisions (successor_id, created_at, revision_id)
        """
    )


def _ensure_bridge_metadata_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS bridge_metadata (
            key TEXT PRIMARY KEY CHECK (length(key) > 0),
            value TEXT NOT NULL CHECK (length(value) > 0)
        ) WITHOUT ROWID
        """
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO bridge_metadata (key, value)
        VALUES ('database_epoch', lower(hex(randomblob(16))))
        """
    )


def _ensure_retrieval_feedback_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS retrieval_feedback (
            feedback_id INTEGER PRIMARY KEY AUTOINCREMENT,
            idempotency_key TEXT NOT NULL
                CHECK (
                    length(idempotency_key) = 64
                    AND idempotency_key = lower(idempotency_key)
                    AND idempotency_key NOT GLOB '*[^0-9a-f]*'
                ),
            receipt_hash TEXT NOT NULL
                CHECK (
                    length(receipt_hash) = 64
                    AND receipt_hash = lower(receipt_hash)
                    AND receipt_hash NOT GLOB '*[^0-9a-f]*'
                ),
            namespace TEXT NOT NULL CHECK (length(trim(namespace)) > 0),
            memory_id TEXT NOT NULL CHECK (length(trim(memory_id)) > 0),
            result_rank INTEGER NOT NULL CHECK (result_rank > 0),
            outcome TEXT NOT NULL
                CHECK (outcome IN ('helpful', 'misleading', 'outdated', 'not_applicable', 'not_used')),
            reason TEXT CHECK (reason IS NULL OR (length(trim(reason)) > 0 AND length(reason) <= 280)),
            retrieval_mode TEXT NOT NULL CHECK (length(trim(retrieval_mode)) > 0),
            database_epoch TEXT NOT NULL CHECK (length(trim(database_epoch)) > 0),
            bridge_instance_id TEXT NOT NULL CHECK (length(trim(bridge_instance_id)) > 0),
            receipt_issued_at TEXT NOT NULL CHECK (julianday(receipt_issued_at) IS NOT NULL),
            receipt_expires_at TEXT NOT NULL
                CHECK (
                    julianday(receipt_expires_at) IS NOT NULL
                    AND julianday(receipt_expires_at) >= julianday(receipt_issued_at)
                ),
            feedback_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(feedback_json)),
            source_app TEXT CHECK (source_app IS NULL OR length(trim(source_app)) > 0),
            source_client TEXT CHECK (source_client IS NULL OR length(trim(source_client)) > 0),
            source_model TEXT CHECK (source_model IS NULL OR length(trim(source_model)) > 0),
            client_session_id TEXT CHECK (client_session_id IS NULL OR length(trim(client_session_id)) > 0),
            client_workspace TEXT CHECK (client_workspace IS NULL OR length(trim(client_workspace)) > 0),
            client_transport TEXT CHECK (client_transport IS NULL OR length(trim(client_transport)) > 0),
            actor TEXT CHECK (actor IS NULL OR length(trim(actor)) > 0),
            created_at TEXT NOT NULL CHECK (julianday(created_at) IS NOT NULL)
        )
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_retrieval_feedback_idempotency_key
        ON retrieval_feedback (idempotency_key)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_retrieval_feedback_namespace_created
        ON retrieval_feedback (namespace, created_at, feedback_id)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_retrieval_feedback_memory_created
        ON retrieval_feedback (memory_id, created_at, feedback_id)
        WHERE memory_id IS NOT NULL
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_retrieval_feedback_receipt_rank
        ON retrieval_feedback (receipt_hash, result_rank, feedback_id)
        """
    )
    conn.execute("DROP TRIGGER IF EXISTS prevent_retrieval_feedback_update")
    conn.execute("DROP TRIGGER IF EXISTS prevent_retrieval_feedback_delete")
    conn.execute(
        """
        CREATE TRIGGER prevent_retrieval_feedback_update
        BEFORE UPDATE ON retrieval_feedback
        BEGIN
            SELECT RAISE(ABORT, 'retrieval_feedback is append-only');
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER prevent_retrieval_feedback_delete
        BEFORE DELETE ON retrieval_feedback
        BEGIN
            SELECT RAISE(ABORT, 'retrieval_feedback is append-only');
        END
        """
    )


def _ensure_retrieval_feedback_effective_vote_schema(conn: sqlite3.Connection) -> None:
    ensure_column(
        conn,
        "retrieval_feedback",
        "feedback_type",
        """
        ALTER TABLE retrieval_feedback
        ADD COLUMN feedback_type TEXT NOT NULL DEFAULT 'vote'
            CHECK (feedback_type IN ('vote', 'correction', 'retraction'))
        """,
    )
    ensure_column(
        conn,
        "retrieval_feedback",
        "supersedes_feedback_id",
        """
        ALTER TABLE retrieval_feedback
        ADD COLUMN supersedes_feedback_id INTEGER
            CHECK (supersedes_feedback_id IS NULL OR supersedes_feedback_id > 0)
        """,
    )
    conn.execute("DROP INDEX IF EXISTS idx_retrieval_feedback_vote_identity")
    conn.execute(
        """
        CREATE INDEX idx_retrieval_feedback_vote_identity
        ON retrieval_feedback (receipt_hash, namespace, memory_id, result_rank, feedback_id)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_retrieval_feedback_supersedes
        ON retrieval_feedback (supersedes_feedback_id)
        WHERE supersedes_feedback_id IS NOT NULL
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_retrieval_feedback_supersedes
        ON retrieval_feedback (supersedes_feedback_id)
        WHERE supersedes_feedback_id IS NOT NULL
        """
    )
    conn.execute("DROP TRIGGER IF EXISTS validate_retrieval_feedback_insert")
    conn.execute(
        """
        CREATE TRIGGER validate_retrieval_feedback_insert
        BEFORE INSERT ON retrieval_feedback
        BEGIN
            SELECT CASE
                WHEN NEW.feedback_type = 'vote' AND NEW.supersedes_feedback_id IS NOT NULL
                THEN RAISE(ABORT, 'vote feedback cannot supersede another event')
            END;
            SELECT CASE
                WHEN NEW.feedback_type IN ('correction', 'retraction')
                    AND NEW.supersedes_feedback_id IS NULL
                THEN RAISE(ABORT, 'feedback correction or retraction must supersede the current head')
            END;
            SELECT CASE
                WHEN NEW.feedback_type = 'vote' AND EXISTS (
                    SELECT 1
                    FROM retrieval_feedback existing
                    WHERE existing.receipt_hash = NEW.receipt_hash
                      AND existing.namespace = NEW.namespace
                      AND existing.memory_id = NEW.memory_id
                      AND existing.result_rank = NEW.result_rank
                )
                THEN RAISE(ABORT, 'feedback subject already has a root vote')
            END;
            SELECT CASE
                WHEN NEW.feedback_type IN ('correction', 'retraction')
                    AND NOT EXISTS (
                        SELECT 1
                        FROM retrieval_feedback parent
                        WHERE parent.feedback_id = NEW.supersedes_feedback_id
                          AND parent.receipt_hash = NEW.receipt_hash
                          AND parent.namespace = NEW.namespace
                          AND parent.memory_id = NEW.memory_id
                          AND parent.result_rank = NEW.result_rank
                    )
                THEN RAISE(ABORT, 'superseded feedback must have the same subject')
            END;
            SELECT CASE
                WHEN NEW.feedback_type IN ('correction', 'retraction')
                    AND NEW.supersedes_feedback_id != (
                        SELECT head.feedback_id
                        FROM retrieval_feedback head
                        WHERE head.receipt_hash = NEW.receipt_hash
                          AND head.namespace = NEW.namespace
                          AND head.memory_id = NEW.memory_id
                          AND head.result_rank = NEW.result_rank
                          AND NOT EXISTS (
                              SELECT 1
                              FROM retrieval_feedback child
                              WHERE child.supersedes_feedback_id = head.feedback_id
                          )
                        ORDER BY head.feedback_id DESC
                        LIMIT 1
                    )
                THEN RAISE(ABORT, 'feedback event must supersede the current head')
            END;
        END
        """
    )
    conn.execute("DROP VIEW IF EXISTS retrieval_feedback_effective_votes")
    conn.execute(
        """
        CREATE VIEW retrieval_feedback_effective_votes AS
        SELECT rf.*
        FROM retrieval_feedback rf
        WHERE rf.feedback_type != 'retraction'
          AND rf.feedback_id = (
            SELECT head.feedback_id
            FROM retrieval_feedback head
            LEFT JOIN retrieval_feedback child
              ON child.supersedes_feedback_id = head.feedback_id
            WHERE head.receipt_hash = rf.receipt_hash
              AND head.namespace = rf.namespace
              AND head.memory_id = rf.memory_id
              AND head.result_rank = rf.result_rank
              AND child.feedback_id IS NULL
            ORDER BY head.feedback_id DESC
            LIMIT 1
        )
        """
    )


def _ensure_retrieval_feedback_identity_schema(conn: sqlite3.Connection) -> None:
    conn.execute("DROP TRIGGER IF EXISTS prevent_retrieval_feedback_update")
    conn.execute("DROP TRIGGER IF EXISTS prevent_retrieval_feedback_delete")
    ensure_column(
        conn,
        "retrieval_feedback",
        "feedback_identity_digest",
        """
        ALTER TABLE retrieval_feedback
        ADD COLUMN feedback_identity_digest TEXT
            CHECK (
                feedback_identity_digest IS NULL
                OR (
                    length(feedback_identity_digest) = 64
                    AND feedback_identity_digest = lower(feedback_identity_digest)
                    AND feedback_identity_digest NOT GLOB '*[^0-9a-f]*'
                )
            )
        """,
    )
    conn.execute(
        """
        UPDATE retrieval_feedback
        SET feedback_identity_digest = receipt_hash
        WHERE feedback_identity_digest IS NULL
        """
    )
    _ensure_retrieval_feedback_schema(conn)

    conn.execute("DROP INDEX IF EXISTS idx_retrieval_feedback_vote_identity")
    conn.execute(
        """
        CREATE INDEX idx_retrieval_feedback_vote_identity
        ON retrieval_feedback (
            feedback_identity_digest,
            namespace,
            memory_id,
            result_rank,
            feedback_id
        )
        """
    )
    conn.execute("DROP TRIGGER IF EXISTS validate_retrieval_feedback_insert")
    conn.execute(
        """
        CREATE TRIGGER validate_retrieval_feedback_insert
        BEFORE INSERT ON retrieval_feedback
        BEGIN
            SELECT CASE
                WHEN NEW.feedback_identity_digest IS NULL
                THEN RAISE(ABORT, 'feedback identity digest is required')
            END;
            SELECT CASE
                WHEN NEW.feedback_type = 'vote' AND NEW.supersedes_feedback_id IS NOT NULL
                THEN RAISE(ABORT, 'vote feedback cannot supersede another event')
            END;
            SELECT CASE
                WHEN NEW.feedback_type IN ('correction', 'retraction')
                    AND NEW.supersedes_feedback_id IS NULL
                THEN RAISE(ABORT, 'feedback correction or retraction must supersede the current head')
            END;
            SELECT CASE
                WHEN NEW.feedback_type = 'vote' AND EXISTS (
                    SELECT 1
                    FROM retrieval_feedback existing
                    WHERE existing.feedback_identity_digest = NEW.feedback_identity_digest
                      AND existing.namespace = NEW.namespace
                      AND existing.memory_id = NEW.memory_id
                      AND existing.result_rank = NEW.result_rank
                )
                THEN RAISE(ABORT, 'feedback subject already has a root vote')
            END;
            SELECT CASE
                WHEN NEW.feedback_type IN ('correction', 'retraction')
                    AND NOT EXISTS (
                        SELECT 1
                        FROM retrieval_feedback parent
                        WHERE parent.feedback_id = NEW.supersedes_feedback_id
                          AND parent.feedback_identity_digest = NEW.feedback_identity_digest
                          AND parent.namespace = NEW.namespace
                          AND parent.memory_id = NEW.memory_id
                          AND parent.result_rank = NEW.result_rank
                    )
                THEN RAISE(ABORT, 'superseded feedback must have the same subject')
            END;
            SELECT CASE
                WHEN NEW.feedback_type IN ('correction', 'retraction')
                    AND NEW.supersedes_feedback_id != (
                        SELECT head.feedback_id
                        FROM retrieval_feedback head
                        WHERE head.feedback_identity_digest = NEW.feedback_identity_digest
                          AND head.namespace = NEW.namespace
                          AND head.memory_id = NEW.memory_id
                          AND head.result_rank = NEW.result_rank
                          AND NOT EXISTS (
                              SELECT 1
                              FROM retrieval_feedback child
                              WHERE child.supersedes_feedback_id = head.feedback_id
                          )
                        ORDER BY head.feedback_id DESC
                        LIMIT 1
                    )
                THEN RAISE(ABORT, 'feedback event must supersede the current head')
            END;
        END
        """
    )
    conn.execute("DROP VIEW IF EXISTS retrieval_feedback_effective_votes")
    conn.execute(
        """
        CREATE VIEW retrieval_feedback_effective_votes AS
        SELECT rf.*
        FROM retrieval_feedback rf
        WHERE rf.feedback_type != 'retraction'
          AND rf.feedback_id = (
            SELECT head.feedback_id
            FROM retrieval_feedback head
            LEFT JOIN retrieval_feedback child
              ON child.supersedes_feedback_id = head.feedback_id
            WHERE head.feedback_identity_digest = rf.feedback_identity_digest
              AND head.namespace = rf.namespace
              AND head.memory_id = rf.memory_id
              AND head.result_rank = rf.result_rank
              AND child.feedback_id IS NULL
            ORDER BY head.feedback_id DESC
            LIMIT 1
        )
        """
    )


def _ensure_episode_schema(conn: sqlite3.Connection) -> None:
    _ensure_episode_authority_tables(conn)
    _ensure_episode_authority_indexes(conn)
    _ensure_episode_authority_triggers(conn)
    _ensure_episode_projection_tables(conn)


def _ensure_episode_authority_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_runs (
            run_id TEXT PRIMARY KEY
                CHECK (
                    length(run_id) = 36
                    AND substr(run_id, 1, 4) = 'run_'
                    AND substr(run_id, 5) NOT GLOB '*[^0-9a-f]*'
                ),
            workspace_key TEXT NOT NULL
                CHECK (length(trim(workspace_key)) > 0 AND length(workspace_key) <= 512),
            root_goal TEXT NOT NULL
                CHECK (
                    length(trim(root_goal)) > 0
                    AND length(CAST(root_goal AS BLOB)) <= 8192
                ),
            model_digest TEXT
                CHECK (
                    model_digest IS NULL
                    OR (
                        length(model_digest) = 64
                        AND model_digest = lower(model_digest)
                        AND model_digest NOT GLOB '*[^0-9a-f]*'
                    )
                ),
            harness_digest TEXT
                CHECK (
                    harness_digest IS NULL
                    OR (
                        length(harness_digest) = 64
                        AND harness_digest = lower(harness_digest)
                        AND harness_digest NOT GLOB '*[^0-9a-f]*'
                    )
                ),
            chat_template_digest TEXT
                CHECK (
                    chat_template_digest IS NULL
                    OR (
                        length(chat_template_digest) = 64
                        AND chat_template_digest = lower(chat_template_digest)
                        AND chat_template_digest NOT GLOB '*[^0-9a-f]*'
                    )
                ),
            tool_schema_digest TEXT
                CHECK (
                    tool_schema_digest IS NULL
                    OR (
                        length(tool_schema_digest) = 64
                        AND tool_schema_digest = lower(tool_schema_digest)
                        AND tool_schema_digest NOT GLOB '*[^0-9a-f]*'
                    )
                ),
            agent_id TEXT CHECK (agent_id IS NULL OR (length(trim(agent_id)) > 0 AND length(agent_id) <= 128)),
            thread_id TEXT CHECK (thread_id IS NULL OR (length(trim(thread_id)) > 0 AND length(thread_id) <= 256)),
            memory_scopes_json TEXT NOT NULL DEFAULT '[]'
                CHECK (
                    json_valid(memory_scopes_json)
                    AND json_type(memory_scopes_json) = 'array'
                    AND length(CAST(memory_scopes_json AS BLOB)) <= 4096
                ),
            budget_json TEXT NOT NULL DEFAULT '{}'
                CHECK (
                    json_valid(budget_json)
                    AND json_type(budget_json) = 'object'
                    AND length(CAST(budget_json AS BLOB)) <= 8192
                ),
            idempotency_key_digest TEXT NOT NULL
                CHECK (
                    length(idempotency_key_digest) = 64
                    AND idempotency_key_digest = lower(idempotency_key_digest)
                    AND idempotency_key_digest NOT GLOB '*[^0-9a-f]*'
                ),
            request_digest TEXT NOT NULL
                CHECK (
                    length(request_digest) = 64
                    AND request_digest = lower(request_digest)
                    AND request_digest NOT GLOB '*[^0-9a-f]*'
                ),
            actor TEXT CHECK (actor IS NULL OR (length(trim(actor)) > 0 AND length(actor) <= 128)),
            source_app TEXT
                CHECK (source_app IS NULL OR (length(trim(source_app)) > 0 AND length(source_app) <= 128)),
            source_client TEXT
                CHECK (source_client IS NULL OR (length(trim(source_client)) > 0 AND length(source_client) <= 128)),
            source_model TEXT
                CHECK (source_model IS NULL OR (length(trim(source_model)) > 0 AND length(source_model) <= 128)),
            client_session_id TEXT
                CHECK (
                    client_session_id IS NULL
                    OR (length(trim(client_session_id)) > 0 AND length(client_session_id) <= 256)
                ),
            client_workspace TEXT
                CHECK (
                    client_workspace IS NULL
                    OR (length(trim(client_workspace)) > 0 AND length(client_workspace) <= 512)
                ),
            client_transport TEXT
                CHECK (
                    client_transport IS NULL
                    OR (length(trim(client_transport)) > 0 AND length(client_transport) <= 64)
                ),
            created_at TEXT NOT NULL CHECK (julianday(created_at) IS NOT NULL),
            UNIQUE (workspace_key, idempotency_key_digest)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS run_work_items (
            work_item_id TEXT PRIMARY KEY
                CHECK (
                    length(work_item_id) = 37
                    AND substr(work_item_id, 1, 5) = 'work_'
                    AND substr(work_item_id, 6) NOT GLOB '*[^0-9a-f]*'
                ),
            run_id TEXT NOT NULL,
            parent_work_item_id TEXT,
            goal TEXT NOT NULL
                CHECK (length(trim(goal)) > 0 AND length(CAST(goal AS BLOB)) <= 8192),
            owner_agent_id TEXT
                CHECK (
                    owner_agent_id IS NULL
                    OR (length(trim(owner_agent_id)) > 0 AND length(owner_agent_id) <= 128)
                ),
            created_at TEXT NOT NULL CHECK (julianday(created_at) IS NOT NULL),
            UNIQUE (run_id, work_item_id),
            FOREIGN KEY (run_id) REFERENCES agent_runs (run_id) ON DELETE RESTRICT,
            FOREIGN KEY (run_id, parent_work_item_id)
                REFERENCES run_work_items (run_id, work_item_id) ON DELETE RESTRICT,
            CHECK (parent_work_item_id IS NULL OR parent_work_item_id != work_item_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS run_events (
            event_id TEXT PRIMARY KEY
                CHECK (
                    length(event_id) = 36
                    AND substr(event_id, 1, 4) = 'evt_'
                    AND substr(event_id, 5) NOT GLOB '*[^0-9a-f]*'
                ),
            run_id TEXT NOT NULL,
            work_item_id TEXT NOT NULL,
            sequence INTEGER NOT NULL CHECK (sequence > 0),
            event_type TEXT NOT NULL
                CHECK (
                    event_type IN (
                        'plan_created',
                        'work_item_started',
                        'checkpoint',
                        'observation',
                        'hypothesis',
                        'hypothesis_confirmed',
                        'hypothesis_rejected',
                        'tool_result',
                        'test_failure',
                        'decision',
                        'blocker',
                        'memory_recalled',
                        'memory_applied',
                        'memory_rejected',
                        'artifact_created',
                        'compaction_boundary',
                        'work_item_completed',
                        'work_item_failed',
                        'work_item_abandoned'
                    )
                ),
            event_schema_version INTEGER NOT NULL DEFAULT 1 CHECK (event_schema_version = 1),
            summary TEXT NOT NULL
                CHECK (
                    length(trim(summary)) > 0
                    AND length(CAST(summary AS BLOB)) <= 4096
                ),
            payload_json TEXT NOT NULL DEFAULT '{}'
                CHECK (
                    json_valid(payload_json)
                    AND json_type(payload_json) = 'object'
                    AND length(CAST(payload_json AS BLOB)) <= 32768
                    AND json_type(payload_json, '$.raw_cot') IS NULL
                    AND json_type(payload_json, '$.chain_of_thought') IS NULL
                    AND json_type(payload_json, '$.transcript') IS NULL
                    AND json_type(payload_json, '$.messages') IS NULL
                ),
            evidence_json TEXT NOT NULL DEFAULT '[]'
                CHECK (
                    json_valid(evidence_json)
                    AND json_type(evidence_json) = 'array'
                    AND length(CAST(evidence_json AS BLOB)) <= 32768
                ),
            idempotency_key_digest TEXT NOT NULL
                CHECK (
                    length(idempotency_key_digest) = 64
                    AND idempotency_key_digest = lower(idempotency_key_digest)
                    AND idempotency_key_digest NOT GLOB '*[^0-9a-f]*'
                ),
            request_digest TEXT NOT NULL
                CHECK (
                    length(request_digest) = 64
                    AND request_digest = lower(request_digest)
                    AND request_digest NOT GLOB '*[^0-9a-f]*'
                ),
            agent_id TEXT CHECK (agent_id IS NULL OR (length(trim(agent_id)) > 0 AND length(agent_id) <= 128)),
            thread_id TEXT CHECK (thread_id IS NULL OR (length(trim(thread_id)) > 0 AND length(thread_id) <= 256)),
            actor TEXT CHECK (actor IS NULL OR (length(trim(actor)) > 0 AND length(actor) <= 128)),
            source_app TEXT
                CHECK (source_app IS NULL OR (length(trim(source_app)) > 0 AND length(source_app) <= 128)),
            source_client TEXT
                CHECK (source_client IS NULL OR (length(trim(source_client)) > 0 AND length(source_client) <= 128)),
            source_model TEXT
                CHECK (source_model IS NULL OR (length(trim(source_model)) > 0 AND length(source_model) <= 128)),
            client_session_id TEXT
                CHECK (
                    client_session_id IS NULL
                    OR (length(trim(client_session_id)) > 0 AND length(client_session_id) <= 256)
                ),
            client_workspace TEXT
                CHECK (
                    client_workspace IS NULL
                    OR (length(trim(client_workspace)) > 0 AND length(client_workspace) <= 512)
                ),
            client_transport TEXT
                CHECK (
                    client_transport IS NULL
                    OR (length(trim(client_transport)) > 0 AND length(client_transport) <= 64)
                ),
            created_at TEXT NOT NULL CHECK (julianday(created_at) IS NOT NULL),
            UNIQUE (run_id, event_id),
            UNIQUE (run_id, sequence),
            UNIQUE (run_id, idempotency_key_digest),
            FOREIGN KEY (run_id) REFERENCES agent_runs (run_id) ON DELETE RESTRICT,
            FOREIGN KEY (run_id, work_item_id)
                REFERENCES run_work_items (run_id, work_item_id) ON DELETE RESTRICT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS run_artifacts (
            artifact_id TEXT NOT NULL
                CHECK (
                    length(artifact_id) = 41
                    AND substr(artifact_id, 1, 9) = 'artifact_'
                    AND substr(artifact_id, 10) NOT GLOB '*[^0-9a-f]*'
                ),
            artifact_version INTEGER NOT NULL CHECK (artifact_version > 0),
            run_id TEXT NOT NULL,
            work_item_id TEXT NOT NULL,
            producing_event_id TEXT NOT NULL,
            digest TEXT NOT NULL
                CHECK (
                    length(digest) = 64
                    AND digest = lower(digest)
                    AND digest NOT GLOB '*[^0-9a-f]*'
                ),
            mime_type TEXT NOT NULL
                CHECK (length(trim(mime_type)) > 0 AND length(mime_type) <= 255),
            uri TEXT NOT NULL
                CHECK (length(trim(uri)) > 0 AND length(CAST(uri AS BLOB)) <= 2048),
            metadata_json TEXT NOT NULL DEFAULT '{}'
                CHECK (
                    json_valid(metadata_json)
                    AND json_type(metadata_json) = 'object'
                    AND length(CAST(metadata_json AS BLOB)) <= 8192
                ),
            created_at TEXT NOT NULL CHECK (julianday(created_at) IS NOT NULL),
            PRIMARY KEY (artifact_id, artifact_version),
            FOREIGN KEY (run_id) REFERENCES agent_runs (run_id) ON DELETE RESTRICT,
            FOREIGN KEY (run_id, work_item_id)
                REFERENCES run_work_items (run_id, work_item_id) ON DELETE RESTRICT,
            FOREIGN KEY (run_id, producing_event_id)
                REFERENCES run_events (run_id, event_id) ON DELETE RESTRICT
        ) WITHOUT ROWID
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS run_outcomes (
            outcome_id TEXT PRIMARY KEY
                CHECK (
                    length(outcome_id) = 40
                    AND substr(outcome_id, 1, 8) = 'outcome_'
                    AND substr(outcome_id, 9) NOT GLOB '*[^0-9a-f]*'
                ),
            run_id TEXT NOT NULL,
            outcome_type TEXT NOT NULL
                CHECK (
                    outcome_type IN (
                        'verified_success',
                        'partial_success',
                        'unverified',
                        'user_corrected',
                        'regression',
                        'failed',
                        'abandoned'
                    )
                ),
            evaluator_type TEXT NOT NULL
                CHECK (evaluator_type IN ('agent', 'deterministic_verifier', 'human', 'system')),
            evaluator_digest TEXT
                CHECK (
                    evaluator_digest IS NULL
                    OR (
                        length(evaluator_digest) = 64
                        AND evaluator_digest = lower(evaluator_digest)
                        AND evaluator_digest NOT GLOB '*[^0-9a-f]*'
                    )
                ),
            evaluator_version TEXT
                CHECK (
                    evaluator_version IS NULL
                    OR (length(trim(evaluator_version)) > 0 AND length(evaluator_version) <= 128)
                ),
            evidence_json TEXT NOT NULL DEFAULT '[]'
                CHECK (
                    json_valid(evidence_json)
                    AND json_type(evidence_json) = 'array'
                    AND length(CAST(evidence_json AS BLOB)) <= 32768
                ),
            metrics_json TEXT NOT NULL DEFAULT '{}'
                CHECK (
                    json_valid(metrics_json)
                    AND json_type(metrics_json) = 'object'
                    AND length(CAST(metrics_json AS BLOB)) <= 32768
                ),
            supersedes_outcome_id TEXT,
            regression_of_run_id TEXT,
            termination_reason TEXT
                CHECK (
                    termination_reason IS NULL
                    OR (length(trim(termination_reason)) > 0 AND length(CAST(termination_reason AS BLOB)) <= 1024)
                ),
            idempotency_key_digest TEXT NOT NULL
                CHECK (
                    length(idempotency_key_digest) = 64
                    AND idempotency_key_digest = lower(idempotency_key_digest)
                    AND idempotency_key_digest NOT GLOB '*[^0-9a-f]*'
                ),
            request_digest TEXT NOT NULL
                CHECK (
                    length(request_digest) = 64
                    AND request_digest = lower(request_digest)
                    AND request_digest NOT GLOB '*[^0-9a-f]*'
                ),
            actor TEXT CHECK (actor IS NULL OR (length(trim(actor)) > 0 AND length(actor) <= 128)),
            source_app TEXT
                CHECK (source_app IS NULL OR (length(trim(source_app)) > 0 AND length(source_app) <= 128)),
            source_client TEXT
                CHECK (source_client IS NULL OR (length(trim(source_client)) > 0 AND length(source_client) <= 128)),
            source_model TEXT
                CHECK (source_model IS NULL OR (length(trim(source_model)) > 0 AND length(source_model) <= 128)),
            client_session_id TEXT
                CHECK (
                    client_session_id IS NULL
                    OR (length(trim(client_session_id)) > 0 AND length(client_session_id) <= 256)
                ),
            client_workspace TEXT
                CHECK (
                    client_workspace IS NULL
                    OR (length(trim(client_workspace)) > 0 AND length(client_workspace) <= 512)
                ),
            client_transport TEXT
                CHECK (
                    client_transport IS NULL
                    OR (length(trim(client_transport)) > 0 AND length(client_transport) <= 64)
                ),
            created_at TEXT NOT NULL CHECK (julianday(created_at) IS NOT NULL),
            UNIQUE (run_id, outcome_id),
            UNIQUE (run_id, idempotency_key_digest),
            FOREIGN KEY (run_id) REFERENCES agent_runs (run_id) ON DELETE RESTRICT,
            FOREIGN KEY (supersedes_outcome_id) REFERENCES run_outcomes (outcome_id) ON DELETE RESTRICT,
            FOREIGN KEY (regression_of_run_id) REFERENCES agent_runs (run_id) ON DELETE RESTRICT,
            CHECK (
                outcome_type != 'verified_success'
                OR (
                    evaluator_type IN ('deterministic_verifier', 'human')
                    AND json_array_length(evidence_json) > 0
                )
            ),
            CHECK (outcome_type != 'regression' OR regression_of_run_id IS NOT NULL)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS run_memory_links (
            link_id TEXT PRIMARY KEY
                CHECK (
                    length(link_id) = 37
                    AND substr(link_id, 1, 5) = 'link_'
                    AND substr(link_id, 6) NOT GLOB '*[^0-9a-f]*'
                ),
            run_id TEXT NOT NULL,
            work_item_id TEXT NOT NULL,
            event_id TEXT,
            outcome_id TEXT,
            memory_id TEXT NOT NULL CHECK (length(trim(memory_id)) > 0 AND length(memory_id) <= 256),
            exact_content_version TEXT NOT NULL
                CHECK (
                    length(exact_content_version) = 64
                    AND exact_content_version = lower(exact_content_version)
                    AND exact_content_version NOT GLOB '*[^0-9a-f]*'
                ),
            receipt_hash TEXT
                CHECK (
                    receipt_hash IS NULL
                    OR (
                        length(receipt_hash) = 64
                        AND receipt_hash = lower(receipt_hash)
                        AND receipt_hash NOT GLOB '*[^0-9a-f]*'
                    )
                ),
            exposure_rank INTEGER CHECK (exposure_rank IS NULL OR exposure_rank > 0),
            feedback_id INTEGER CHECK (feedback_id IS NULL OR feedback_id > 0),
            relation TEXT NOT NULL
                CHECK (relation IN ('recalled', 'applied', 'rejected', 'contradicted')),
            review_required INTEGER NOT NULL DEFAULT 0 CHECK (review_required IN (0, 1)),
            idempotency_key_digest TEXT NOT NULL
                CHECK (
                    length(idempotency_key_digest) = 64
                    AND idempotency_key_digest = lower(idempotency_key_digest)
                    AND idempotency_key_digest NOT GLOB '*[^0-9a-f]*'
                ),
            request_digest TEXT NOT NULL
                CHECK (
                    length(request_digest) = 64
                    AND request_digest = lower(request_digest)
                    AND request_digest NOT GLOB '*[^0-9a-f]*'
                ),
            created_at TEXT NOT NULL CHECK (julianday(created_at) IS NOT NULL),
            UNIQUE (run_id, idempotency_key_digest),
            FOREIGN KEY (run_id) REFERENCES agent_runs (run_id) ON DELETE RESTRICT,
            FOREIGN KEY (run_id, work_item_id)
                REFERENCES run_work_items (run_id, work_item_id) ON DELETE RESTRICT,
            FOREIGN KEY (run_id, event_id) REFERENCES run_events (run_id, event_id) ON DELETE RESTRICT,
            FOREIGN KEY (run_id, outcome_id) REFERENCES run_outcomes (run_id, outcome_id) ON DELETE RESTRICT,
            FOREIGN KEY (feedback_id) REFERENCES retrieval_feedback (feedback_id) ON DELETE RESTRICT,
            CHECK (
                (receipt_hash IS NULL AND exposure_rank IS NULL)
                OR (receipt_hash IS NOT NULL AND exposure_rank IS NOT NULL)
            ),
            CHECK (relation != 'recalled' OR receipt_hash IS NOT NULL),
            CHECK (receipt_hash IS NOT NULL OR review_required = 1)
        )
        """
    )


def _ensure_episode_authority_indexes(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_agent_runs_workspace_created
        ON agent_runs (workspace_key, created_at, run_id)
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_run_work_items_single_root
        ON run_work_items (run_id)
        WHERE parent_work_item_id IS NULL
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_run_work_items_parent
        ON run_work_items (run_id, parent_work_item_id, created_at, work_item_id)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_run_events_type_sequence
        ON run_events (run_id, event_type, sequence)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_run_artifacts_event
        ON run_artifacts (run_id, producing_event_id, artifact_id, artifact_version)
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_run_outcomes_single_root
        ON run_outcomes (run_id)
        WHERE supersedes_outcome_id IS NULL
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_run_outcomes_supersedes
        ON run_outcomes (supersedes_outcome_id)
        WHERE supersedes_outcome_id IS NOT NULL
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_run_outcomes_run_created
        ON run_outcomes (run_id, created_at, outcome_id)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_run_memory_links_memory
        ON run_memory_links (memory_id, exact_content_version, created_at, link_id)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_run_memory_links_run_relation
        ON run_memory_links (run_id, relation, created_at, link_id)
        """
    )


def _ensure_episode_authority_triggers(conn: sqlite3.Connection) -> None:
    for trigger_name in (
        "prevent_run_events_update",
        "prevent_run_events_delete",
        "prevent_run_outcomes_update",
        "prevent_run_outcomes_delete",
        "prevent_run_artifacts_update",
        "prevent_run_artifacts_delete",
        "prevent_run_memory_links_update",
        "prevent_run_memory_links_delete",
        "prevent_run_work_item_identity_update",
        "validate_run_outcome_insert",
    ):
        conn.execute(f"DROP TRIGGER IF EXISTS {quote_identifier(trigger_name)}")
    conn.execute(
        """
        CREATE TRIGGER prevent_run_events_update
        BEFORE UPDATE ON run_events
        BEGIN
            SELECT RAISE(ABORT, 'run_events is append-only');
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER prevent_run_events_delete
        BEFORE DELETE ON run_events
        BEGIN
            SELECT RAISE(ABORT, 'run_events is append-only');
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER prevent_run_outcomes_update
        BEFORE UPDATE ON run_outcomes
        BEGIN
            SELECT RAISE(ABORT, 'run_outcomes is append-only');
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER prevent_run_outcomes_delete
        BEFORE DELETE ON run_outcomes
        BEGIN
            SELECT RAISE(ABORT, 'run_outcomes is append-only');
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER prevent_run_artifacts_update
        BEFORE UPDATE ON run_artifacts
        BEGIN
            SELECT RAISE(ABORT, 'run_artifacts is append-only');
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER prevent_run_artifacts_delete
        BEFORE DELETE ON run_artifacts
        BEGIN
            SELECT RAISE(ABORT, 'run_artifacts is append-only');
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER prevent_run_memory_links_update
        BEFORE UPDATE ON run_memory_links
        BEGIN
            SELECT RAISE(ABORT, 'run_memory_links is append-only');
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER prevent_run_memory_links_delete
        BEFORE DELETE ON run_memory_links
        BEGIN
            SELECT RAISE(ABORT, 'run_memory_links is append-only');
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER prevent_run_work_item_identity_update
        BEFORE UPDATE OF work_item_id, run_id, parent_work_item_id ON run_work_items
        BEGIN
            SELECT RAISE(ABORT, 'run work-item identity and parent are immutable');
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER validate_run_outcome_insert
        BEFORE INSERT ON run_outcomes
        BEGIN
            SELECT CASE
                WHEN NEW.supersedes_outcome_id IS NOT NULL
                    AND NOT EXISTS (
                        SELECT 1
                        FROM run_outcomes parent
                        WHERE parent.outcome_id = NEW.supersedes_outcome_id
                          AND parent.run_id = NEW.run_id
                    )
                THEN RAISE(ABORT, 'superseded outcome must belong to the same run')
            END;
            SELECT CASE
                WHEN NEW.supersedes_outcome_id IS NOT NULL
                    AND EXISTS (
                        SELECT 1
                        FROM run_outcomes child
                        WHERE child.supersedes_outcome_id = NEW.supersedes_outcome_id
                    )
                THEN RAISE(ABORT, 'outcome correction must supersede the current head')
            END;
        END
        """
    )


def _ensure_episode_projection_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS run_state_projection (
            run_id TEXT PRIMARY KEY,
            status TEXT NOT NULL CHECK (status IN ('active', 'completed', 'failed', 'abandoned')),
            last_sequence INTEGER NOT NULL DEFAULT 0 CHECK (last_sequence >= 0),
            unresolved_blocker_count INTEGER NOT NULL DEFAULT 0 CHECK (unresolved_blocker_count >= 0),
            active_work_item_count INTEGER NOT NULL DEFAULT 0 CHECK (active_work_item_count >= 0),
            outcome_id TEXT,
            ended_at TEXT CHECK (ended_at IS NULL OR julianday(ended_at) IS NOT NULL),
            termination_reason TEXT
                CHECK (
                    termination_reason IS NULL
                    OR (length(trim(termination_reason)) > 0 AND length(CAST(termination_reason AS BLOB)) <= 1024)
                ),
            projection_version INTEGER NOT NULL DEFAULT 1 CHECK (projection_version = 1),
            rebuilt_at TEXT NOT NULL CHECK (julianday(rebuilt_at) IS NOT NULL),
            FOREIGN KEY (run_id) REFERENCES agent_runs (run_id) ON DELETE CASCADE,
            FOREIGN KEY (run_id, outcome_id) REFERENCES run_outcomes (run_id, outcome_id) ON DELETE RESTRICT
        ) WITHOUT ROWID
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS run_work_item_state_projection (
            work_item_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            status TEXT NOT NULL
                CHECK (status IN ('pending', 'active', 'blocked', 'completed', 'failed', 'abandoned')),
            last_sequence INTEGER NOT NULL DEFAULT 0 CHECK (last_sequence >= 0),
            started_at TEXT CHECK (started_at IS NULL OR julianday(started_at) IS NOT NULL),
            ended_at TEXT CHECK (ended_at IS NULL OR julianday(ended_at) IS NOT NULL),
            last_summary TEXT
                CHECK (last_summary IS NULL OR length(CAST(last_summary AS BLOB)) <= 4096),
            projection_version INTEGER NOT NULL DEFAULT 1 CHECK (projection_version = 1),
            rebuilt_at TEXT NOT NULL CHECK (julianday(rebuilt_at) IS NOT NULL),
            UNIQUE (run_id, work_item_id),
            FOREIGN KEY (run_id, work_item_id)
                REFERENCES run_work_items (run_id, work_item_id) ON DELETE CASCADE
        ) WITHOUT ROWID
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS memory_utility_shadow (
            memory_id TEXT NOT NULL CHECK (length(trim(memory_id)) > 0 AND length(memory_id) <= 256),
            exact_content_version TEXT NOT NULL
                CHECK (
                    length(exact_content_version) = 64
                    AND exact_content_version = lower(exact_content_version)
                    AND exact_content_version NOT GLOB '*[^0-9a-f]*'
                ),
            helpful_count INTEGER NOT NULL DEFAULT 0 CHECK (helpful_count >= 0),
            misleading_count INTEGER NOT NULL DEFAULT 0 CHECK (misleading_count >= 0),
            outdated_count INTEGER NOT NULL DEFAULT 0 CHECK (outdated_count >= 0),
            not_used_count INTEGER NOT NULL DEFAULT 0 CHECK (not_used_count >= 0),
            supporting_run_count INTEGER NOT NULL DEFAULT 0 CHECK (supporting_run_count >= 0),
            contradicting_run_count INTEGER NOT NULL DEFAULT 0 CHECK (contradicting_run_count >= 0),
            shadow_score REAL NOT NULL DEFAULT 0.0,
            projection_version INTEGER NOT NULL DEFAULT 1 CHECK (projection_version = 1),
            computed_at TEXT NOT NULL CHECK (julianday(computed_at) IS NOT NULL),
            PRIMARY KEY (memory_id, exact_content_version)
        ) WITHOUT ROWID
        """
    )


def _ensure_episode_recovery_integrity_schema(conn: sqlite3.Connection) -> None:
    ensure_column(
        conn,
        "run_state_projection",
        "terminal_at",
        """
        ALTER TABLE run_state_projection
        ADD COLUMN terminal_at TEXT
            CHECK (terminal_at IS NULL OR julianday(terminal_at) IS NOT NULL)
        """,
    )
    ensure_column(
        conn,
        "run_state_projection",
        "current_outcome_updated_at",
        """
        ALTER TABLE run_state_projection
        ADD COLUMN current_outcome_updated_at TEXT
            CHECK (
                current_outcome_updated_at IS NULL
                OR julianday(current_outcome_updated_at) IS NOT NULL
            )
        """,
    )
    conn.execute(
        """
        UPDATE run_state_projection AS projection
        SET terminal_at = (
                SELECT root_outcome.created_at
                FROM run_outcomes AS root_outcome
                WHERE root_outcome.run_id = projection.run_id
                  AND root_outcome.supersedes_outcome_id IS NULL
                ORDER BY root_outcome.created_at, root_outcome.outcome_id
                LIMIT 1
            ),
            ended_at = (
                SELECT root_outcome.created_at
                FROM run_outcomes AS root_outcome
                WHERE root_outcome.run_id = projection.run_id
                  AND root_outcome.supersedes_outcome_id IS NULL
                ORDER BY root_outcome.created_at, root_outcome.outcome_id
                LIMIT 1
            ),
            current_outcome_updated_at = (
                SELECT head_outcome.created_at
                FROM run_outcomes AS head_outcome
                WHERE head_outcome.run_id = projection.run_id
                  AND NOT EXISTS (
                      SELECT 1
                      FROM run_outcomes AS child_outcome
                      WHERE child_outcome.supersedes_outcome_id = head_outcome.outcome_id
                  )
                ORDER BY head_outcome.created_at DESC, head_outcome.outcome_id DESC
                LIMIT 1
            )
        """
    )


def _ensure_exact_content_identity_schema(conn: sqlite3.Connection) -> None:
    ensure_column(
        conn,
        "memories",
        "exact_content_hash",
        "ALTER TABLE memories ADD COLUMN exact_content_hash TEXT NOT NULL DEFAULT ''",
    )
    rows = conn.execute(
        """
        SELECT id, content
        FROM memories
        WHERE exact_content_hash IS NULL OR length(trim(exact_content_hash)) = 0
        ORDER BY rowid ASC
        """
    ).fetchall()
    for row in rows:
        conn.execute(
            "UPDATE memories SET exact_content_hash = ? WHERE id = ?",
            (exact_content_hash(str(row["content"])), row["id"]),
        )
    invalid = conn.execute(
        """
        SELECT id
        FROM memories
        WHERE exact_content_hash IS NULL OR length(trim(exact_content_hash)) = 0
        LIMIT 1
        """
    ).fetchone()
    if invalid is not None:
        raise RuntimeError("exact_content_hash migration left an empty identity")
    conn.execute("DROP INDEX IF EXISTS idx_memories_dedup")
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_memories_dedup
        ON memories (namespace, exact_content_hash)
        WHERE kind != 'signal'
        """
    )
    _ensure_exact_content_identity_triggers(conn)


def _ensure_exact_content_identity_triggers(conn: sqlite3.Connection) -> None:
    conn.execute("DROP TRIGGER IF EXISTS validate_exact_content_identity_insert")
    conn.execute("DROP TRIGGER IF EXISTS validate_exact_content_identity_update")
    conn.execute(
        """
        CREATE TRIGGER validate_exact_content_identity_insert
        BEFORE INSERT ON memories
        WHEN NEW.exact_content_hash IS NULL OR length(trim(NEW.exact_content_hash)) = 0
        BEGIN
            SELECT RAISE(ABORT, 'exact_content_hash must not be empty');
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER validate_exact_content_identity_update
        BEFORE UPDATE OF exact_content_hash ON memories
        WHEN NEW.exact_content_hash IS NULL OR length(trim(NEW.exact_content_hash)) = 0
        BEGIN
            SELECT RAISE(ABORT, 'exact_content_hash must not be empty');
        END
        """
    )


def normalize_exact_content(content: str) -> str:
    return content.replace("\r\n", "\n").replace("\r", "\n")


def exact_content_hash(content: str) -> str:
    return hashlib.sha256(normalize_exact_content(content).encode("utf-8")).hexdigest()


def database_epoch(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT value FROM bridge_metadata WHERE key = 'database_epoch'").fetchone()
    if row is None or not str(row[0]).strip():
        raise RuntimeError("database epoch is missing")
    return str(row[0]).strip()


def rotate_database_epoch(conn: sqlite3.Connection) -> str:
    value_row = conn.execute("SELECT lower(hex(randomblob(16)))").fetchone()
    value = str(value_row[0]) if value_row is not None else ""
    if not value:
        raise RuntimeError("failed to generate database epoch")
    conn.execute(
        """
        INSERT INTO bridge_metadata (key, value)
        VALUES ('database_epoch', ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (value,),
    )
    return value


def ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    table_sql = quote_identifier(table)
    quote_identifier(column)
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table_sql})").fetchall()}
    if column in columns:
        return
    try:
        conn.execute(ddl)
    except sqlite3.OperationalError as exc:
        if "duplicate column name" in str(exc).lower():
            return
        raise


def ensure_fts_columns(conn: sqlite3.Connection) -> None:
    columns = [row["name"] for row in conn.execute("PRAGMA table_info(memories_fts)").fetchall()]
    if "title" in columns:
        return

    conn.execute("SAVEPOINT ensure_fts_columns")
    try:
        existing_rows = conn.execute(
            """
            SELECT id, COALESCE(title, '') AS title, content
            FROM memories
            ORDER BY created_at ASC
            """
        ).fetchall()
        conn.execute("DROP TABLE IF EXISTS memories_fts")
        conn.execute("CREATE VIRTUAL TABLE memories_fts USING fts5(memory_id UNINDEXED, title, content)")
        for row in existing_rows:
            conn.execute(
                "INSERT INTO memories_fts(memory_id, title, content) VALUES (?, ?, ?)",
                (row["id"], row["title"], row["content"]),
            )
        conn.execute("RELEASE ensure_fts_columns")
    except Exception:
        conn.execute("ROLLBACK TO ensure_fts_columns")
        conn.execute("RELEASE ensure_fts_columns")
        raise
