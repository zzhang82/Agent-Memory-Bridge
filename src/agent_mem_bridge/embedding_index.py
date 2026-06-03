from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .paths import (
    resolve_embedding_command,
    resolve_embedding_dim,
    resolve_embedding_model,
    resolve_embedding_provider,
    resolve_embedding_timeout_seconds,
)


DEFAULT_EMBEDDING_MODEL = "local-token-hash-v1"
DEFAULT_EMBEDDING_DIM = 64
TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")
REQUIRED_EMBEDDING_COLUMNS = {
    "memory_id",
    "content_hash",
    "embedding_model",
    "embedding_dim",
    "vector_json",
    "created_at",
}


@dataclass(frozen=True, slots=True)
class EmbeddingConfig:
    provider: str = "hash"
    model: str = DEFAULT_EMBEDDING_MODEL
    dim: int = DEFAULT_EMBEDDING_DIM
    command: str = ""
    timeout_seconds: float = 10.0


def active_embedding_config() -> EmbeddingConfig:
    provider = normalize_embedding_provider(resolve_embedding_provider())
    dim = resolve_embedding_dim()
    if dim <= 0:
        raise ValueError("embedding dim must be greater than 0")
    command = resolve_embedding_command()
    configured_model = resolve_embedding_model()
    if configured_model:
        model = configured_model
    elif provider == "command":
        model = command_embedding_model_id(command)
    else:
        model = DEFAULT_EMBEDDING_MODEL
    return EmbeddingConfig(
        provider=provider,
        model=model,
        dim=dim,
        command=command,
        timeout_seconds=resolve_embedding_timeout_seconds(),
    )


def normalize_embedding_provider(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in {"hash", "command"}:
        return normalized
    return "hash"


def command_embedding_model_id(command: str) -> str:
    if not command.strip():
        return "local-command-unconfigured"
    digest = hashlib.sha256(command.strip().encode("utf-8")).hexdigest()[:12]
    return f"local-command-sha256:{digest}"


def ensure_embedding_schema(conn: sqlite3.Connection) -> None:
    existing_columns = _embedding_table_columns(conn)
    if existing_columns is not None and not REQUIRED_EMBEDDING_COLUMNS.issubset(existing_columns):
        # This is a derived cache. If an earlier local/dev schema exists, the
        # safest migration is to discard the incompatible cache and rebuild it
        # from authoritative memory rows.
        conn.execute("DROP TABLE IF EXISTS memory_embeddings")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS memory_embeddings (
            memory_id TEXT PRIMARY KEY,
            content_hash TEXT NOT NULL,
            embedding_model TEXT NOT NULL,
            embedding_dim INTEGER NOT NULL,
            vector_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(memory_id) REFERENCES memories(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_memory_embeddings_model
        ON memory_embeddings (embedding_model, embedding_dim)
        """
    )


def _embedding_table_columns(conn: sqlite3.Connection) -> set[str] | None:
    table = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
        AND name = 'memory_embeddings'
        LIMIT 1
        """
    ).fetchone()
    if table is None:
        return None
    return {str(row["name"]) for row in conn.execute("PRAGMA table_info(memory_embeddings)").fetchall()}


def embed_text(text: str, *, dim: int = DEFAULT_EMBEDDING_DIM, config: EmbeddingConfig | None = None) -> list[float]:
    if config is not None:
        return embed_texts([text], config=config)[0]
    return hash_embed_text(text, dim=dim)


def embed_texts(texts: list[str], *, config: EmbeddingConfig | None = None) -> list[list[float]]:
    resolved = config or active_embedding_config()
    if resolved.dim <= 0:
        raise ValueError("embedding dim must be greater than 0")
    if not texts:
        return []
    if resolved.provider == "command":
        return command_embed_texts(texts, config=resolved)
    return [hash_embed_text(text, dim=resolved.dim) for text in texts]


def hash_embed_text(text: str, *, dim: int = DEFAULT_EMBEDDING_DIM) -> list[float]:
    if dim <= 0:
        raise ValueError("embedding dim must be greater than 0")
    vector = [0.0] * dim
    for token in TOKEN_RE.findall(text.lower()):
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        bucket = int.from_bytes(digest[:4], "big") % dim
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[bucket] += sign
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return vector
    return [round(value / norm, 6) for value in vector]


def command_embed_texts(texts: list[str], *, config: EmbeddingConfig) -> list[list[float]]:
    if not config.command.strip():
        raise RuntimeError("embedding command provider requires a configured command")
    payload = {
        "texts": texts,
        "dim": config.dim,
        "model": config.model,
    }
    try:
        completed = subprocess.run(
            config.command,
            shell=True,
            check=False,
            capture_output=True,
            text=True,
            input=json.dumps(payload),
            timeout=config.timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("embedding command timed out") from exc
    except OSError as exc:
        raise RuntimeError(f"embedding command failed to start: {exc.__class__.__name__}") from exc

    if completed.returncode != 0:
        raise RuntimeError(f"embedding command failed with exit code {completed.returncode}")

    try:
        raw = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("embedding command returned invalid JSON") from exc

    vectors = normalize_command_vectors(raw, expected_count=len(texts), expected_dim=config.dim)
    return [normalize_vector(vector, dim=config.dim) for vector in vectors]


def normalize_command_vectors(raw: Any, *, expected_count: int, expected_dim: int) -> list[list[float]]:
    vectors: Any
    if isinstance(raw, dict):
        if "vectors" in raw:
            vectors = raw["vectors"]
        elif "vector" in raw and expected_count == 1:
            vectors = [raw["vector"]]
        else:
            raise RuntimeError("embedding command output must include vectors")
    else:
        vectors = raw

    if not isinstance(vectors, list) or len(vectors) != expected_count:
        raise RuntimeError("embedding command vector count did not match requested texts")

    result: list[list[float]] = []
    for vector in vectors:
        if not isinstance(vector, list) or len(vector) != expected_dim:
            raise RuntimeError("embedding command vector dimension did not match configured dim")
        try:
            result.append([float(value) for value in vector])
        except (TypeError, ValueError) as exc:
            raise RuntimeError("embedding command vector values must be numeric") from exc
    return result


def normalize_vector(vector: list[float], *, dim: int) -> list[float]:
    if len(vector) != dim:
        raise ValueError("embedding vector dimension mismatch")
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return [0.0] * dim
    return [round(value / norm, 6) for value in vector]


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    return round(sum(a * b for a, b in zip(left, right)), 6)


def load_vector(value: str) -> list[float]:
    raw = json.loads(value)
    if not isinstance(raw, list):
        return []
    return [float(item) for item in raw]


def vector_json(vector: list[float]) -> str:
    return json.dumps(vector, separators=(",", ":"))


def upsert_embedding(
    conn: sqlite3.Connection,
    *,
    memory_id: str,
    content_hash: str,
    title: str | None,
    content: str,
    model: str = DEFAULT_EMBEDDING_MODEL,
    dim: int = DEFAULT_EMBEDDING_DIM,
    config: EmbeddingConfig | None = None,
) -> None:
    resolved = config or EmbeddingConfig(model=model, dim=dim)
    vector = embed_text("\n".join(part for part in (title or "", content) if part), config=resolved)
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
            memory_id,
            content_hash,
            resolved.model,
            resolved.dim,
            vector_json(vector),
            datetime.now(UTC).isoformat(),
        ),
    )


def ensure_embeddings_for_rows(
    conn: sqlite3.Connection,
    rows: list[sqlite3.Row],
    *,
    model: str = DEFAULT_EMBEDDING_MODEL,
    dim: int = DEFAULT_EMBEDDING_DIM,
    config: EmbeddingConfig | None = None,
) -> int:
    ensure_embedding_schema(conn)
    resolved = config or EmbeddingConfig(model=model, dim=dim)
    changed = 0
    for row in rows:
        existing = conn.execute(
            """
            SELECT content_hash, embedding_model, embedding_dim
            FROM memory_embeddings
            WHERE memory_id = ?
            LIMIT 1
            """,
            (row["id"],),
        ).fetchone()
        if (
            existing is not None
            and existing["content_hash"] == row["content_hash"]
            and existing["embedding_model"] == resolved.model
            and int(existing["embedding_dim"]) == resolved.dim
        ):
            continue
        upsert_embedding(
            conn,
            memory_id=row["id"],
            content_hash=row["content_hash"],
            title=row["title"],
            content=row["content"],
            config=resolved,
        )
        changed += 1
    return changed


def embedding_health(
    conn: sqlite3.Connection,
    *,
    model: str = DEFAULT_EMBEDDING_MODEL,
    dim: int = DEFAULT_EMBEDDING_DIM,
    config: EmbeddingConfig | None = None,
) -> dict[str, Any]:
    ensure_embedding_schema(conn)
    resolved = config or EmbeddingConfig(model=model, dim=dim)
    total = conn.execute("SELECT COUNT(*) AS count FROM memories").fetchone()["count"]
    embedding_total = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM memory_embeddings
        WHERE embedding_model = ?
        AND embedding_dim = ?
        """,
        (resolved.model, resolved.dim),
    ).fetchone()["count"]
    missing = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM memories m
        LEFT JOIN memory_embeddings e
          ON e.memory_id = m.id
         AND e.embedding_model = ?
         AND e.embedding_dim = ?
        WHERE e.memory_id IS NULL
        """,
        (resolved.model, resolved.dim),
    ).fetchone()["count"]
    stale = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM memories m
        JOIN memory_embeddings e
          ON e.memory_id = m.id
         AND e.embedding_model = ?
         AND e.embedding_dim = ?
        WHERE e.content_hash != m.content_hash
        """,
        (resolved.model, resolved.dim),
    ).fetchone()["count"]
    orphan = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM memory_embeddings e
        LEFT JOIN memories m ON m.id = e.memory_id
        WHERE m.id IS NULL
        AND e.embedding_model = ?
        AND e.embedding_dim = ?
        """,
        (resolved.model, resolved.dim),
    ).fetchone()["count"]
    return {
        "memory_count": total,
        "embedding_count": embedding_total,
        "missing_embedding_count": missing,
        "stale_embedding_count": stale,
        "orphan_embedding_count": orphan,
    }
