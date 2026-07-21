from __future__ import annotations

import sqlite3
from typing import Any

from .embedding_index import (
    DEFAULT_EMBEDDING_DIM,
    DEFAULT_EMBEDDING_MODEL,
    EmbeddingConfig,
    active_embedding_config,
    embedding_health,
    ensure_embedding_schema,
    prepare_embeddings_for_rows,
    upsert_prepared_embeddings,
)


def inspect_indexes(conn: sqlite3.Connection, *, embedding_config: EmbeddingConfig | None = None) -> dict[str, Any]:
    memory_count = conn.execute("SELECT COUNT(*) AS count FROM memories").fetchone()["count"]
    fts_count = conn.execute("SELECT COUNT(*) AS count FROM memories_fts").fetchone()["count"]
    missing_fts_count = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM memories m
        LEFT JOIN memories_fts f ON f.memory_id = m.id
        WHERE f.memory_id IS NULL
        """
    ).fetchone()["count"]
    orphan_fts_count = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM memories_fts f
        LEFT JOIN memories m ON m.id = f.memory_id
        WHERE m.id IS NULL
        """
    ).fetchone()["count"]
    stale_fts_count = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM memories m
        JOIN memories_fts f ON f.memory_id = m.id
        WHERE COALESCE(f.title, '') != COALESCE(m.title, '')
        OR COALESCE(f.content, '') != m.content
        """
    ).fetchone()["count"]
    resolved_embedding_config = embedding_config or active_embedding_config()
    embeddings = embedding_health(conn, config=resolved_embedding_config)
    return {
        "memory_count": memory_count,
        "fts": {
            "index_count": fts_count,
            "missing_count": missing_fts_count,
            "orphan_count": orphan_fts_count,
            "stale_count": stale_fts_count,
            "healthy": missing_fts_count == 0 and orphan_fts_count == 0 and stale_fts_count == 0,
        },
        "embeddings": {
            **embeddings,
            "embedding_provider": resolved_embedding_config.provider,
            "embedding_model": resolved_embedding_config.model,
            "embedding_dim": resolved_embedding_config.dim,
            "healthy": (
                embeddings["missing_embedding_count"] == 0
                and embeddings["stale_embedding_count"] == 0
                and embeddings["orphan_embedding_count"] == 0
            ),
        },
    }


def rebuild_fts_index(conn: sqlite3.Connection) -> dict[str, Any]:
    before_memory_count = conn.execute("SELECT COUNT(*) AS count FROM memories").fetchone()["count"]
    rows = conn.execute(
        """
        SELECT id, COALESCE(title, '') AS title, content
        FROM memories
        ORDER BY created_at ASC
        """
    ).fetchall()
    conn.execute("SAVEPOINT rebuild_fts_index")
    try:
        conn.execute("DROP TABLE IF EXISTS memories_fts")
        conn.execute("CREATE VIRTUAL TABLE memories_fts USING fts5(memory_id UNINDEXED, title, content)")
        for row in rows:
            conn.execute(
                "INSERT INTO memories_fts(memory_id, title, content) VALUES (?, ?, ?)",
                (row["id"], row["title"], row["content"]),
            )
        after_memory_count = conn.execute("SELECT COUNT(*) AS count FROM memories").fetchone()["count"]
        if after_memory_count != before_memory_count:
            raise RuntimeError("memory table changed during FTS rebuild; rolling back derived index rebuild")
        conn.execute("RELEASE rebuild_fts_index")
    except Exception:
        conn.execute("ROLLBACK TO rebuild_fts_index")
        conn.execute("RELEASE rebuild_fts_index")
        raise
    return inspect_indexes(conn)


def rebuild_embedding_index(
    conn: sqlite3.Connection,
    *,
    model: str = DEFAULT_EMBEDDING_MODEL,
    dim: int = DEFAULT_EMBEDDING_DIM,
    config: EmbeddingConfig | None = None,
) -> dict[str, Any]:
    embedding_config = config or (
        active_embedding_config()
        if model == DEFAULT_EMBEDDING_MODEL and dim == DEFAULT_EMBEDDING_DIM
        else EmbeddingConfig(model=model, dim=dim)
    )
    if conn.in_transaction:
        raise RuntimeError("embedding rebuild requires a connection without an active transaction")
    ensure_embedding_schema(conn)
    conn.commit()
    before_memory_count = conn.execute("SELECT COUNT(*) AS count FROM memories").fetchone()["count"]
    rows = conn.execute(
        """
        SELECT id, title, content, content_hash
        FROM memories
        ORDER BY created_at ASC
        """
    ).fetchall()
    prepared = prepare_embeddings_for_rows(rows, config=embedding_config)
    conn.execute("SAVEPOINT rebuild_embedding_index")
    try:
        conn.execute(
            """
            DELETE FROM memory_embeddings
            WHERE embedding_model = ?
            AND embedding_dim = ?
            """,
            (embedding_config.model, embedding_config.dim),
        )
        processed_count = upsert_prepared_embeddings(conn, prepared, config=embedding_config)
        after_memory_count = conn.execute("SELECT COUNT(*) AS count FROM memories").fetchone()["count"]
        if after_memory_count != before_memory_count or processed_count != len(rows):
            raise RuntimeError("memory table changed during embedding rebuild; rolling back derived index rebuild")
        conn.execute("RELEASE rebuild_embedding_index")
    except Exception:
        conn.execute("ROLLBACK TO rebuild_embedding_index")
        conn.execute("RELEASE rebuild_embedding_index")
        raise
    return inspect_indexes(conn, embedding_config=embedding_config)
