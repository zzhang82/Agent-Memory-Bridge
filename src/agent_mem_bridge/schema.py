from __future__ import annotations

import re
import sqlite3

from .embedding_index import ensure_embedding_schema

IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def quote_identifier(identifier: str) -> str:
    if not IDENTIFIER_RE.fullmatch(identifier):
        raise ValueError(f"invalid SQL identifier: {identifier!r}")
    return f'"{identifier}"'


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
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
            content_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
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
    ensure_column(conn, "memories", "is_learning_candidate", "ALTER TABLE memories ADD COLUMN is_learning_candidate INTEGER NOT NULL DEFAULT 0")
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
    conn.executescript(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_memories_dedup
        ON memories (namespace, content_hash)
        WHERE kind != 'signal';

        CREATE INDEX IF NOT EXISTS idx_memories_namespace_created_at
        ON memories (namespace, created_at DESC);

        CREATE INDEX IF NOT EXISTS idx_memories_session_id_created_at
        ON memories (session_id, created_at DESC);

        CREATE INDEX IF NOT EXISTS idx_memories_kind_namespace_created_at
        ON memories (kind, namespace, created_at DESC);

        CREATE INDEX IF NOT EXISTS idx_memories_actor_created_at
        ON memories (actor, created_at DESC);

        CREATE INDEX IF NOT EXISTS idx_memories_correlation_id_created_at
        ON memories (correlation_id, created_at DESC);

        CREATE INDEX IF NOT EXISTS idx_memories_source_client_created_at
        ON memories (source_client, created_at DESC);

        CREATE INDEX IF NOT EXISTS idx_memories_source_model_created_at
        ON memories (source_model, created_at DESC);

        CREATE INDEX IF NOT EXISTS idx_memories_signal_status_created_at
        ON memories (namespace, signal_status, created_at DESC);

        CREATE INDEX IF NOT EXISTS idx_memories_signal_claimed_by_created_at
        ON memories (claimed_by, created_at DESC);

        CREATE INDEX IF NOT EXISTS idx_memories_learning_candidate_visible
        ON memories (namespace, is_learning_candidate, created_at DESC);
        """
    )
    conn.commit()


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
