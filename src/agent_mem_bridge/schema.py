from __future__ import annotations

import hashlib
import re
import sqlite3
from collections.abc import Callable

from .embedding_index import ensure_embedding_schema
from .record_projection import backfill_record_projections

IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
CURRENT_SCHEMA_VERSION = 4


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
        migrated = False
        expected_version = current_version + 1
        for target_version, migration in MIGRATIONS:
            if target_version <= current_version:
                continue
            if target_version != expected_version:
                raise RuntimeError(
                    f"schema migration sequence is incomplete: expected version {expected_version}, "
                    f"found {target_version}"
                )
            migration(conn)
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
    _ensure_current_schema(conn)


def _migrate_to_v2(conn: sqlite3.Connection) -> None:
    _ensure_projection_schema(conn)
    backfill_record_projections(conn)


def _migrate_to_v3(conn: sqlite3.Connection) -> None:
    _ensure_bridge_metadata_schema(conn)


def _migrate_to_v4(conn: sqlite3.Connection) -> None:
    _ensure_exact_content_identity_schema(conn)


MIGRATIONS: tuple[tuple[int, Callable[[sqlite3.Connection], None]], ...] = (
    (1, _migrate_to_v1),
    (2, _migrate_to_v2),
    (3, _migrate_to_v3),
    (4, _migrate_to_v4),
)


def _ensure_current_schema(conn: sqlite3.Connection) -> None:
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
            exact_content_hash TEXT NOT NULL CHECK (length(trim(exact_content_hash)) > 0),
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
    _ensure_exact_content_identity_schema(conn)
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
    _ensure_projection_schema(conn)
    _ensure_bridge_metadata_schema(conn)
    backfill_record_projections(conn, only_missing=True)


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
