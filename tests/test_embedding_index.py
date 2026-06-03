from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

from agent_mem_bridge.embedding_index import (
    EmbeddingConfig,
    active_embedding_config,
    command_embedding_model_id,
    embed_text,
    embedding_health,
)
from agent_mem_bridge.index_health import inspect_indexes, rebuild_embedding_index, rebuild_fts_index
from agent_mem_bridge.query import recall_candidates
from agent_mem_bridge.storage import MemoryStore


def _recall_mode(
    store: MemoryStore,
    *,
    namespace: str,
    query: str,
    limit: int,
    retrieval_mode: str,
) -> list[dict[str, object]]:
    return recall_candidates(
        store,
        namespace=namespace,
        query=query,
        limit=limit,
        kind=None,
        signal_status=None,
        tags_any=None,
        session_id=None,
        actor=None,
        correlation_id=None,
        since=None,
        retrieval_mode=retrieval_mode,
    )


def _embedding_command(mode: str = "ok") -> str:
    fixture = Path(__file__).parent / "fixtures" / "fake_embedding_gateway.py"
    return f'"{sys.executable}" "{fixture}" {mode}'


def test_embedding_schema_is_empty_on_default_lexical_store(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    store.store(
        namespace="project:bridge",
        title="Lexical only",
        content="Default lexical storage should not generate embeddings.",
        kind="memory",
    )

    with store._connect() as conn:
        count = conn.execute("SELECT COUNT(*) FROM memory_embeddings").fetchone()[0]
        health = embedding_health(conn)

    assert count == 0
    assert health["memory_count"] == 1
    assert health["missing_embedding_count"] == 1


def test_default_lexical_recall_does_not_generate_embeddings(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    store.store(
        namespace="project:bridge",
        title="Lexical default",
        content="Default recall should use lexical search without warming semantic vectors.",
        kind="memory",
    )

    lexical = _recall_mode(
        store,
        namespace="project:bridge",
        query="lexical search",
        limit=5,
        retrieval_mode="lexical",
    )

    with store._connect() as conn:
        health = embedding_health(conn)

    assert lexical[0]["title"] == "Lexical default"
    assert health["embedding_count"] == 0
    assert health["missing_embedding_count"] == 1


def test_semantic_recall_lazily_builds_sidecar_without_changing_memory_rows(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    created = store.store(
        namespace="project:bridge",
        title="Semantic sidecar",
        content="Semantic sidecar can recover from a missing FTS row.",
        kind="memory",
    )
    with store._connect() as conn:
        before_memory_count = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        conn.execute("DELETE FROM memories_fts WHERE memory_id = ?", (created["id"],))
        conn.commit()

    lexical = store.recall(namespace="project:bridge", query="sidecar recover", limit=5)
    semantic = _recall_mode(
        store,
        namespace="project:bridge",
        query="sidecar recover",
        limit=5,
        retrieval_mode="semantic",
    )

    with store._connect() as conn:
        after_memory_count = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        health = embedding_health(conn)

    assert lexical["count"] == 0
    assert [item["id"] for item in semantic] == [created["id"]]
    assert before_memory_count == after_memory_count == 1
    assert health["embedding_count"] == 1
    assert health["missing_embedding_count"] == 0


def test_forget_removes_embedding_sidecar_row(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    created = store.store(
        namespace="project:bridge",
        title="Delete me",
        content="Embedding row should disappear with forgotten memory.",
        kind="memory",
    )
    _recall_mode(
        store,
        namespace="project:bridge",
        query="delete embedding",
        limit=5,
        retrieval_mode="semantic",
    )

    removed = store.forget(created["id"])

    with store._connect() as conn:
        health = embedding_health(conn)

    assert removed["deleted"] is True
    assert health["memory_count"] == 0
    assert health["embedding_count"] == 0
    assert health["orphan_embedding_count"] == 0


def test_hybrid_recall_combines_lexical_and_semantic_sidecars(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    lexical = store.store(
        namespace="project:bridge",
        title="Exact lexical match",
        content="The release contract should mention index rebuild safety.",
        kind="memory",
    )
    semantic = store.store(
        namespace="project:bridge",
        title="Semantic fallback",
        content="Vector sidecars can recover relevant memory when the full text cache drifts.",
        kind="memory",
    )
    with store._connect() as conn:
        conn.execute("DELETE FROM memories_fts WHERE memory_id = ?", (semantic["id"],))
        conn.commit()

    hybrid = _recall_mode(
        store,
        namespace="project:bridge",
        query="index rebuild safety vector sidecar",
        limit=5,
        retrieval_mode="hybrid",
    )

    ids = [item["id"] for item in hybrid]
    assert lexical["id"] in ids
    assert semantic["id"] in ids
    assert all((item.get("retrieval") or {}).get("mode") == "hybrid" for item in hybrid)


def test_index_health_detects_and_rebuilds_derived_index_drift(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    created = store.store(
        namespace="project:bridge",
        title="Index drift",
        content="Derived indexes should be repairable without changing memories.",
        kind="memory",
    )
    with store._connect() as conn:
        conn.execute("DELETE FROM memories_fts WHERE memory_id = ?", (created["id"],))
        conn.execute(
            "INSERT INTO memories_fts(memory_id, title, content) VALUES (?, ?, ?)",
            ("orphan-id", "orphan", "orphan content"),
        )
        conn.commit()
        unhealthy = inspect_indexes(conn)
        rebuilt = rebuild_fts_index(conn)
        conn.commit()

    assert unhealthy["memory_count"] == 1
    assert unhealthy["fts"]["missing_count"] == 1
    assert unhealthy["fts"]["orphan_count"] == 1
    assert rebuilt["memory_count"] == 1
    assert rebuilt["fts"]["healthy"] is True


def test_index_health_detects_null_fts_stale_rows(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    created = store.store(
        namespace="project:bridge",
        title="Null FTS drift",
        content="FTS rows with null cache values should be treated as stale.",
        kind="memory",
    )
    with store._connect() as conn:
        conn.execute("UPDATE memories_fts SET title = NULL WHERE memory_id = ?", (created["id"],))
        conn.commit()
        report = inspect_indexes(conn)

    assert report["fts"]["stale_count"] == 1
    assert report["fts"]["healthy"] is False


def test_embedding_rebuild_preserves_memory_count(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    store.store(
        namespace="project:bridge",
        title="Embedding rebuild",
        content="Embedding rebuild is a derived cache repair.",
        kind="memory",
    )

    with store._connect() as conn:
        report = rebuild_embedding_index(conn)
        conn.commit()

    assert report["memory_count"] == 1
    assert report["embeddings"]["embedding_count"] == 1
    assert report["embeddings"]["healthy"] is True


def test_embedding_health_requires_active_model_and_dimension(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    created = store.store(
        namespace="project:bridge",
        title="Wrong sidecar model",
        content="A wrong model vector should not satisfy active embedding health.",
        kind="memory",
    )
    with store._connect() as conn:
        content_hash = conn.execute("SELECT content_hash FROM memories WHERE id = ?", (created["id"],)).fetchone()[0]
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
            """,
            (created["id"], content_hash, "wrong-model", 999, "[]", "2026-01-01T00:00:00+00:00"),
        )
        conn.commit()
        health = embedding_health(conn)

    assert health["embedding_count"] == 0
    assert health["missing_embedding_count"] == 1


def test_incompatible_embedding_sidecar_schema_is_recreated(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy-sidecar.db"
    store = MemoryStore(db_path, log_dir=tmp_path / "logs")
    store.store(
        namespace="project:bridge",
        title="Compatible base",
        content="Memory rows survive derived sidecar schema replacement.",
        kind="memory",
    )
    with store._connect() as conn:
        conn.execute("DROP TABLE memory_embeddings")
        conn.execute("CREATE TABLE memory_embeddings (memory_id TEXT PRIMARY KEY, vector_json TEXT)")
        conn.commit()

    upgraded = MemoryStore(db_path, log_dir=tmp_path / "logs")

    with upgraded._connect() as conn:
        memory_count = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(memory_embeddings)").fetchall()}

    assert memory_count == 1
    assert {"content_hash", "embedding_model", "embedding_dim", "created_at"}.issubset(columns)


def test_legacy_db_without_embedding_table_migrates_without_data_loss(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE memories (
            id TEXT PRIMARY KEY,
            namespace TEXT NOT NULL,
            kind TEXT NOT NULL,
            title TEXT,
            content TEXT NOT NULL,
            tags_json TEXT NOT NULL DEFAULT '[]',
            content_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE VIRTUAL TABLE memories_fts USING fts5(memory_id UNINDEXED, title, content);
        INSERT INTO memories (id, namespace, kind, title, content, tags_json, content_hash, created_at)
        VALUES ('legacy-1', 'project:bridge', 'memory', 'Legacy', 'Legacy row survives migration.', '[]', 'hash-1', '2026-01-01T00:00:00+00:00');
        INSERT INTO memories_fts(memory_id, title, content) VALUES ('legacy-1', 'Legacy', 'Legacy row survives migration.');
        """
    )
    conn.commit()
    conn.close()

    store = MemoryStore(db_path, log_dir=tmp_path / "logs")

    with store._connect() as upgraded:
        memory_count = upgraded.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        embedding_table = upgraded.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'memory_embeddings'"
        ).fetchone()

    assert memory_count == 1
    assert embedding_table is not None


def test_command_embedding_provider_retrieves_without_storing_command(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_EMBEDDING_PROVIDER", "command")
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_EMBEDDING_COMMAND", _embedding_command())
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_EMBEDDING_MODEL", "fixture-embedding-v1")
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_EMBEDDING_DIM", "4")

    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    alpha = store.store(
        namespace="project:bridge",
        title="Alpha memory",
        content="Alpha runtime gotcha should be found semantically.",
        kind="memory",
    )
    store.store(
        namespace="project:bridge",
        title="Beta memory",
        content="Beta content should rank lower for alpha.",
        kind="memory",
    )

    semantic = _recall_mode(
        store,
        namespace="project:bridge",
        query="alpha",
        limit=2,
        retrieval_mode="semantic",
    )

    with store._connect() as conn:
        rows = conn.execute("SELECT embedding_model, embedding_dim FROM memory_embeddings").fetchall()

    assert semantic[0]["id"] == alpha["id"]
    assert (semantic[0].get("retrieval") or {})["semantic_model"] == "fixture-embedding-v1"
    assert rows
    assert {row["embedding_model"] for row in rows} == {"fixture-embedding-v1"}
    assert {row["embedding_dim"] for row in rows} == {4}
    assert all("fake_embedding_gateway" not in row["embedding_model"] for row in rows)


def test_command_embedding_provider_uses_command_hash_model_when_unlabeled(monkeypatch) -> None:
    command = _embedding_command()
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_EMBEDDING_PROVIDER", "command")
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_EMBEDDING_COMMAND", command)
    monkeypatch.delenv("AGENT_MEMORY_BRIDGE_EMBEDDING_MODEL", raising=False)

    config = active_embedding_config()

    assert config.model == command_embedding_model_id(command)
    assert command not in config.model


def test_command_embedding_errors_are_sanitized(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_EMBEDDING_PROVIDER", "command")
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_EMBEDDING_COMMAND", _embedding_command("exit"))
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_EMBEDDING_DIM", "4")

    with pytest.raises(RuntimeError) as exc_info:
        embed_text("alpha private memory text should not appear", config=active_embedding_config())

    message = str(exc_info.value)
    assert "private memory text" not in message
    assert "alpha" not in message
    assert "exit code 3" in message


def test_command_embedding_rejects_invalid_json(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_EMBEDDING_PROVIDER", "command")
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_EMBEDDING_COMMAND", _embedding_command("invalid-json"))
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_EMBEDDING_DIM", "4")

    with pytest.raises(RuntimeError, match="invalid JSON"):
        embed_text("alpha", config=active_embedding_config())


def test_embedding_health_can_target_explicit_config(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    store.store(
        namespace="project:bridge",
        title="Explicit config",
        content="Embedding health can check a non-default model.",
        kind="memory",
    )

    config = EmbeddingConfig(model="explicit-model", dim=8)
    with store._connect() as conn:
        report = rebuild_embedding_index(conn, config=config)
        conn.commit()
        default_health = embedding_health(conn)
        explicit_health = embedding_health(conn, config=config)

    assert report["embeddings"]["embedding_model"] == "explicit-model"
    assert report["embeddings"]["embedding_dim"] == 8
    assert default_health["missing_embedding_count"] == 1
    assert explicit_health["missing_embedding_count"] == 0
