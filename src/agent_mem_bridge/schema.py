from __future__ import annotations

import sqlite3


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
            signal_status TEXT,
            claimed_by TEXT,
            claimed_at TEXT,
            lease_expires_at TEXT,
            expires_at TEXT,
            acknowledged_at TEXT,
            content_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )
    ensure_column(conn, "memories", "title", "ALTER TABLE memories ADD COLUMN title TEXT")
    ensure_column(conn, "memories", "actor", "ALTER TABLE memories ADD COLUMN actor TEXT")
    ensure_column(conn, "memories", "correlation_id", "ALTER TABLE memories ADD COLUMN correlation_id TEXT")
    ensure_column(conn, "memories", "signal_status", "ALTER TABLE memories ADD COLUMN signal_status TEXT")
    ensure_column(conn, "memories", "claimed_by", "ALTER TABLE memories ADD COLUMN claimed_by TEXT")
    ensure_column(conn, "memories", "claimed_at", "ALTER TABLE memories ADD COLUMN claimed_at TEXT")
    ensure_column(conn, "memories", "lease_expires_at", "ALTER TABLE memories ADD COLUMN lease_expires_at TEXT")
    ensure_column(conn, "memories", "expires_at", "ALTER TABLE memories ADD COLUMN expires_at TEXT")
    ensure_column(conn, "memories", "acknowledged_at", "ALTER TABLE memories ADD COLUMN acknowledged_at TEXT")
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

        CREATE INDEX IF NOT EXISTS idx_memories_signal_status_created_at
        ON memories (namespace, signal_status, created_at DESC);

        CREATE INDEX IF NOT EXISTS idx_memories_signal_claimed_by_created_at
        ON memories (claimed_by, created_at DESC);
        """
    )
    ensure_fts_columns(conn)
    conn.commit()


def ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(ddl)


def ensure_fts_columns(conn: sqlite3.Connection) -> None:
    columns = [row["name"] for row in conn.execute("PRAGMA table_info(memories_fts)").fetchall()]
    if "title" in columns:
        return

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
