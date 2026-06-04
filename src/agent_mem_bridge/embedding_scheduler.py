from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .embedding_index import EmbeddingConfig, active_embedding_config, embedding_health, ensure_embedding_schema
from .index_health import inspect_indexes
from .paths import (
    resolve_embedding_scheduler_batch_size,
    resolve_embedding_scheduler_enabled,
    resolve_embedding_scheduler_interval_seconds,
    resolve_embedding_scheduler_state_path,
)
from .storage import MemoryStore


@dataclass(frozen=True, slots=True)
class EmbeddingSchedulerConfig:
    enabled: bool = False
    state_path: Path | None = None
    interval_seconds: float = 3600.0
    batch_size: int = 100
    embedding_config: EmbeddingConfig | None = None


def build_default_embedding_scheduler_config() -> EmbeddingSchedulerConfig:
    enabled = resolve_embedding_scheduler_enabled()
    return EmbeddingSchedulerConfig(
        enabled=enabled,
        state_path=resolve_embedding_scheduler_state_path(),
        interval_seconds=resolve_embedding_scheduler_interval_seconds(),
        batch_size=resolve_embedding_scheduler_batch_size(),
        embedding_config=active_embedding_config() if enabled else None,
    )


def run_embedding_sidecar_maintenance(
    store: MemoryStore,
    *,
    config: EmbeddingSchedulerConfig | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    resolved = config or build_default_embedding_scheduler_config()
    state_path = resolved.state_path
    timestamp = now or datetime.now(UTC)

    if not resolved.enabled:
        embedding_config = resolved.embedding_config or EmbeddingConfig()
        return {
            "enabled": False,
            "due": False,
            "processed_count": 0,
            "remaining_count": None,
            "embedding_model": embedding_config.model,
            "embedding_dim": embedding_config.dim,
            "reason": "disabled",
        }

    embedding_config = resolved.embedding_config or active_embedding_config()
    state = _load_state(state_path)
    due, due_reason = _is_due(
        state,
        timestamp=timestamp,
        interval_seconds=resolved.interval_seconds,
        embedding_config=embedding_config,
    )
    if not due:
        return {
            "enabled": True,
            "due": False,
            "processed_count": 0,
            "remaining_count": None,
            "embedding_model": embedding_config.model,
            "embedding_dim": embedding_config.dim,
            "reason": due_reason,
        }

    try:
        with store._connect() as conn:
            result = _run_due_batch(
                conn,
                embedding_config=embedding_config,
                batch_size=max(1, resolved.batch_size),
            )
            conn.commit()
    except Exception as exc:
        _write_state(
            state_path,
            {
                **state,
                "last_checked_at": timestamp.isoformat(),
                "last_error_at": timestamp.isoformat(),
                "last_error_type": exc.__class__.__name__,
                "embedding_model": embedding_config.model,
                "embedding_dim": embedding_config.dim,
            },
        )
        raise

    _write_state(
        state_path,
        {
            **state,
            "last_checked_at": timestamp.isoformat(),
            "last_completed_at": timestamp.isoformat(),
            "last_error_at": None,
            "last_error_type": None,
            "embedding_model": embedding_config.model,
            "embedding_dim": embedding_config.dim,
            "processed_total": int(state.get("processed_total") or 0) + int(result["processed_count"]),
        },
    )
    return {
        "enabled": True,
        "due": True,
        "processed_count": result["processed_count"],
        "remaining_count": result["remaining_count"],
        "orphan_removed_count": result["orphan_removed_count"],
        "memory_count": result["memory_count"],
        "embedding_count": result["embedding_count"],
        "embedding_model": embedding_config.model,
        "embedding_dim": embedding_config.dim,
        "reason": due_reason,
    }


def _run_due_batch(
    conn: sqlite3.Connection,
    *,
    embedding_config: EmbeddingConfig,
    batch_size: int,
) -> dict[str, int]:
    ensure_embedding_schema(conn)
    orphan_removed_count = conn.execute(
        """
        DELETE FROM memory_embeddings
        WHERE embedding_model = ?
        AND embedding_dim = ?
        AND memory_id NOT IN (SELECT id FROM memories)
        """,
        (embedding_config.model, embedding_config.dim),
    ).rowcount
    rows = conn.execute(
        """
        SELECT m.id, m.title, m.content, m.content_hash
        FROM memories m
        LEFT JOIN memory_embeddings e
          ON e.memory_id = m.id
         AND e.embedding_model = ?
         AND e.embedding_dim = ?
        WHERE e.memory_id IS NULL
        OR e.content_hash != m.content_hash
        ORDER BY m.created_at ASC, m.id ASC
        LIMIT ?
        """,
        (embedding_config.model, embedding_config.dim, batch_size),
    ).fetchall()

    processed_count = _ensure_embeddings_for_rows_batch(conn, rows, config=embedding_config)
    health = embedding_health(conn, config=embedding_config)
    return {
        "processed_count": processed_count,
        "remaining_count": int(health["missing_embedding_count"]) + int(health["stale_embedding_count"]),
        "orphan_removed_count": max(0, int(orphan_removed_count or 0)),
        "memory_count": int(health["memory_count"]),
        "embedding_count": int(health["embedding_count"]),
    }


def _ensure_embeddings_for_rows_batch(
    conn: sqlite3.Connection,
    rows: list[sqlite3.Row],
    *,
    config: EmbeddingConfig,
) -> int:
    # Import locally to keep the scheduler module focused on orchestration and
    # avoid broadening the public embedding-index surface before benchmarks say
    # this batching policy should become reusable API.
    from .embedding_index import embed_texts, vector_json

    if not rows:
        return 0
    texts = ["\n".join(part for part in (row["title"] or "", row["content"]) if part) for row in rows]
    vectors = embed_texts(texts, config=config)
    now = datetime.now(UTC).isoformat()
    for row, vector in zip(rows, vectors, strict=True):
        conn.execute(
            """
            INSERT INTO memory_embeddings (
                memory_id,
                content_hash,
                embedding_model,
                embedding_dim,
                vector_json,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(memory_id) DO UPDATE SET
                content_hash = excluded.content_hash,
                embedding_model = excluded.embedding_model,
                embedding_dim = excluded.embedding_dim,
                vector_json = excluded.vector_json,
                created_at = excluded.created_at
            """,
            (
                row["id"],
                row["content_hash"],
                config.model,
                config.dim,
                vector_json(vector),
                now,
            ),
        )
    return len(rows)


def _is_due(
    state: dict[str, Any],
    *,
    timestamp: datetime,
    interval_seconds: float,
    embedding_config: EmbeddingConfig,
) -> tuple[bool, str]:
    if not state:
        return True, "never-completed"
    if state.get("embedding_model") != embedding_config.model or int(state.get("embedding_dim") or 0) != embedding_config.dim:
        return True, "embedding-config-changed"
    last_completed_at = _parse_datetime(state.get("last_completed_at"))
    if last_completed_at is None:
        return True, "never-completed"
    if interval_seconds <= 0:
        return True, "interval-disabled"
    elapsed = (timestamp - last_completed_at).total_seconds()
    if elapsed >= interval_seconds:
        return True, "interval-elapsed"
    return False, "interval-not-elapsed"


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _load_state(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _write_state(path: Path | None, state: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    tmp_path.replace(path)


def embedding_sidecar_snapshot(store: MemoryStore, *, config: EmbeddingConfig | None = None) -> dict[str, Any]:
    with store._connect() as conn:
        return inspect_indexes(conn, embedding_config=config or active_embedding_config())
