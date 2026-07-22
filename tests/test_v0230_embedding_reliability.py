from __future__ import annotations

import hashlib
import sqlite3
import sys
from pathlib import Path

import pytest

import agent_mem_bridge.embedding_index as embedding_index
import agent_mem_bridge.query as query_module
from agent_mem_bridge.embedding_index import (
    EmbeddingConfig,
    EmbeddingProviderError,
    hash_embed_text,
    load_vector,
    normalize_command_vectors,
    vector_json,
)
from agent_mem_bridge.embedding_scheduler import EmbeddingSchedulerConfig, run_embedding_sidecar_maintenance
from agent_mem_bridge.index_health import rebuild_embedding_index
from agent_mem_bridge.query import recall_candidates
from agent_mem_bridge.storage import MemoryStore


def _store_memory(store: MemoryStore, title: str, content: str) -> dict[str, object]:
    return store.store(namespace="project:bridge", title=title, content=content, kind="memory")


def _embedding_command(mode: str) -> str:
    fixture = Path(__file__).parent / "fixtures" / "fake_embedding_gateway.py"
    return f'"{sys.executable}" "{fixture}" {mode}'


def _recall_mode(store: MemoryStore, query: str, mode: str) -> list[dict[str, object]]:
    return recall_candidates(
        store,
        namespace="project:bridge",
        query=query,
        limit=5,
        kind=None,
        signal_status=None,
        tags_any=None,
        session_id=None,
        actor=None,
        correlation_id=None,
        since=None,
        retrieval_mode=mode,
    )


def _warm_embeddings(
    store: MemoryStore,
    tmp_path: Path,
    *,
    config: EmbeddingConfig | None = None,
) -> None:
    embedding_config = config or EmbeddingConfig()
    result = run_embedding_sidecar_maintenance(
        store,
        config=EmbeddingSchedulerConfig(
            enabled=True,
            state_path=tmp_path / "embedding-state.json",
            interval_seconds=0,
            batch_size=100,
            embedding_config=embedding_config,
        ),
    )
    assert result["remaining_count"] == 0


def _insert_compatible_sidecar(store: MemoryStore, *, config: EmbeddingConfig) -> None:
    with store._connect() as conn:
        rows = conn.execute("SELECT id, content_hash FROM memories ORDER BY id").fetchall()
        for row in rows:
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
                (
                    row["id"],
                    row["content_hash"],
                    config.model,
                    config.dim,
                    vector_json([1.0, *([0.0] * (config.dim - 1))]),
                    "2026-01-01T00:00:00+00:00",
                ),
            )
        conn.commit()


def test_semantic_recall_is_read_only_and_embeds_only_the_query_when_warm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    _store_memory(store, "Alpha", "Alpha semantic memory.")
    _store_memory(store, "Beta", "Beta semantic memory.")
    config = EmbeddingConfig(model="fixture-hash", dim=8)
    monkeypatch.setattr(query_module, "active_embedding_config", lambda: config)
    _warm_embeddings(store, tmp_path, config=config)

    original_connect = store._connect
    write_actions = {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE}

    def tracked_connect() -> sqlite3.Connection:
        conn = original_connect()

        def deny_writes(
            action: int,
            _arg1: str | None,
            _arg2: str | None,
            _db_name: str | None,
            _trigger: str | None,
        ) -> int:
            if action in write_actions:
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        conn.set_authorizer(deny_writes)
        return conn

    calls: list[list[str]] = []

    def fake_embed_texts(texts: list[str], *, config: EmbeddingConfig) -> list[list[float]]:
        calls.append(list(texts))
        return [hash_embed_text(text, dim=config.dim) for text in texts]

    monkeypatch.setattr(store, "_connect", tracked_connect)
    monkeypatch.setattr(query_module, "embed_texts", fake_embed_texts)

    result = _recall_mode(store, "alpha", "semantic")

    assert result
    assert calls == [["alpha"]]
    with original_connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM memory_embeddings").fetchone()[0] == 2


def test_semantic_recall_reports_cold_index_without_provider_calls_or_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    _store_memory(store, "Alpha", "Alpha semantic memory.")
    _store_memory(store, "Beta", "Beta semantic memory.")
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_RETRIEVAL_MODE", "semantic")
    monkeypatch.setattr(
        query_module,
        "embed_texts",
        lambda texts, *, config: (_ for _ in ()).throw(AssertionError("cold recall must not call provider")),
    )

    result = store.recall(namespace="project:bridge", query="alpha", limit=5)

    assert result["items"] == []
    assert result["retrieval"]["mode"] == "semantic"
    assert result["retrieval"]["semantic_available"] is False
    assert result["retrieval"]["semantic_completeness"] == "cold"
    assert result["retrieval"]["semantic_memory_count"] == 2
    assert result["retrieval"]["semantic_valid_embedding_count"] == 0
    assert result["retrieval"]["semantic_missing_embedding_count"] == 2
    assert result["retrieval"]["semantic_stale_embedding_count"] == 0
    assert result["retrieval"]["degraded"] is True
    assert result["retrieval"]["degraded_reason"] == "semantic-index-cold"
    with store._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM memory_embeddings").fetchone()[0] == 0


def test_semantic_recall_scores_warm_precomputed_index_and_reports_completeness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    target = _store_memory(store, "Old target", "quasarneedle durable fact")
    for index in range(5):
        _store_memory(store, f"Recent decoy {index}", f"unrelated recent memory {index}")
    config = EmbeddingConfig(model="fixture-hash", dim=8)
    monkeypatch.setattr(query_module, "active_embedding_config", lambda: config)
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_RETRIEVAL_MODE", "semantic")
    _warm_embeddings(store, tmp_path, config=config)
    calls: list[list[str]] = []

    def fake_embed_texts(texts: list[str], *, config: EmbeddingConfig) -> list[list[float]]:
        calls.append(list(texts))
        return [hash_embed_text(text, dim=config.dim) for text in texts]

    monkeypatch.setattr(query_module, "embed_texts", fake_embed_texts)

    result = store.recall(namespace="project:bridge", query="quasarneedle", limit=5)

    assert result["items"][0]["id"] == target["id"]
    assert calls == [["quasarneedle"]]
    assert result["retrieval"]["mode"] == "semantic"
    assert result["retrieval"]["semantic_available"] is True
    assert result["retrieval"]["semantic_completeness"] == "complete"
    assert result["retrieval"]["semantic_completeness_ratio"] == 1.0
    assert result["retrieval"]["semantic_memory_count"] == 6
    assert result["retrieval"]["semantic_valid_embedding_count"] == 6
    assert result["retrieval"]["semantic_missing_embedding_count"] == 0
    assert result["retrieval"]["semantic_stale_embedding_count"] == 0
    assert result["retrieval"]["degraded"] is False
    assert result["items"][0]["retrieval"]["semantic_scope"] == "precomputed-valid-only"


def test_hybrid_cold_index_preserves_lexical_fallback_with_degraded_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    created = _store_memory(store, "Lexical fallback", "Alpha fallback remains available.")
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_RETRIEVAL_MODE", "hybrid")
    monkeypatch.setattr(
        query_module,
        "embed_texts",
        lambda texts, *, config: (_ for _ in ()).throw(AssertionError("cold hybrid must not call provider")),
    )

    result = store.recall(namespace="project:bridge", query="alpha fallback", limit=5)

    assert [item["id"] for item in result["items"]] == [created["id"]]
    assert result["retrieval"]["mode"] == "hybrid"
    assert result["retrieval"]["semantic_available"] is False
    assert result["retrieval"]["semantic_completeness"] == "cold"
    assert result["retrieval"]["semantic_missing_embedding_count"] == 1
    assert result["retrieval"]["degraded"] is True
    assert result["retrieval"]["degraded_reason"] == "semantic-index-cold"
    assert result["items"][0]["retrieval"]["mode"] == "hybrid"


def test_scheduler_batches_provider_work_outside_write_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    _store_memory(store, "First", "First scheduled embedding.")
    _store_memory(store, "Second", "Second scheduled embedding.")
    embedding_config = EmbeddingConfig(model="fixture-hash", dim=8)
    scheduler_config = EmbeddingSchedulerConfig(
        enabled=True,
        state_path=tmp_path / "embedding-state.json",
        interval_seconds=0,
        batch_size=10,
        embedding_config=embedding_config,
    )

    opened: list[sqlite3.Connection] = []
    original_connect = store._connect

    def tracked_connect() -> sqlite3.Connection:
        conn = original_connect()
        opened.append(conn)
        return conn

    calls: list[list[str]] = []

    def fake_embed_texts(texts: list[str], *, config: EmbeddingConfig) -> list[list[float]]:
        calls.append(list(texts))
        assert not any(conn.in_transaction for conn in opened)
        return [hash_embed_text(text, dim=config.dim) for text in texts]

    monkeypatch.setattr(store, "_connect", tracked_connect)
    monkeypatch.setattr(embedding_index, "embed_texts", fake_embed_texts)

    result = run_embedding_sidecar_maintenance(store, config=scheduler_config)

    assert result["processed_count"] == 2
    assert len(calls) == 1
    assert len(calls[0]) == 2


def test_embedding_rebuild_batches_provider_work_before_write_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    _store_memory(store, "First", "First rebuild embedding.")
    _store_memory(store, "Second", "Second rebuild embedding.")
    config = EmbeddingConfig(model="fixture-hash", dim=8)
    calls: list[list[str]] = []

    with store._connect() as conn:

        def fake_embed_texts(texts: list[str], *, config: EmbeddingConfig) -> list[list[float]]:
            calls.append(list(texts))
            assert conn.in_transaction is False
            return [hash_embed_text(text, dim=config.dim) for text in texts]

        monkeypatch.setattr(embedding_index, "embed_texts", fake_embed_texts)
        report = rebuild_embedding_index(conn, config=config)
        conn.commit()

    assert report["embeddings"]["embedding_count"] == 2
    assert len(calls) == 1
    assert len(calls[0]) == 2


def test_semantic_recall_reports_stale_precomputed_vector_without_refreshing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    created = _store_memory(store, "Original", "Alpha content before provider execution.")
    config = EmbeddingConfig(model="fixture-hash", dim=8)
    monkeypatch.setattr(query_module, "active_embedding_config", lambda: config)
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_RETRIEVAL_MODE", "semantic")
    _warm_embeddings(store, tmp_path, config=config)
    changed_content = "Content changed while the provider was running."
    changed_hash = hashlib.sha256(changed_content.encode("utf-8")).hexdigest()
    with store._connect() as conn:
        conn.execute(
            "UPDATE memories SET content = ?, content_hash = ? WHERE id = ?",
            (changed_content, changed_hash, created["id"]),
        )
        conn.commit()
    monkeypatch.setattr(
        query_module,
        "embed_texts",
        lambda texts, *, config: (_ for _ in ()).throw(AssertionError("stale recall must not refresh embeddings")),
    )

    result = store.recall(namespace="project:bridge", query="alpha", limit=5)

    with store._connect() as conn:
        memory = conn.execute("SELECT content, content_hash FROM memories WHERE id = ?", (created["id"],)).fetchone()
        embedding_count = conn.execute("SELECT COUNT(*) FROM memory_embeddings").fetchone()[0]
    assert result["items"] == []
    assert result["retrieval"]["mode"] == "semantic"
    assert result["retrieval"]["semantic_available"] is False
    assert result["retrieval"]["semantic_completeness"] == "cold"
    assert result["retrieval"]["semantic_valid_embedding_count"] == 0
    assert result["retrieval"]["semantic_missing_embedding_count"] == 0
    assert result["retrieval"]["semantic_stale_embedding_count"] == 1
    assert result["retrieval"]["degraded"] is True
    assert result["retrieval"]["degraded_reason"] == "semantic-index-stale"
    assert memory["content"] == changed_content
    assert memory["content_hash"] == changed_hash
    assert embedding_count == 1


def test_title_annotation_invalidates_precomputed_embedding_until_rebuild(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    created = _store_memory(store, "Original title", "Stable content outside the title token.")
    config = EmbeddingConfig(model="fixture-hash", dim=8)
    monkeypatch.setattr(query_module, "active_embedding_config", lambda: config)
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_RETRIEVAL_MODE", "semantic")
    _warm_embeddings(store, tmp_path, config=config)

    unique_token = "titleonlyneedle-v0230"
    annotated = store.annotate(str(created["id"]), title=f"Reviewed {unique_token}")

    assert annotated["changed"] is True
    assert annotated["title_changed"] is True
    with store._connect() as conn:
        embedding_count = conn.execute(
            "SELECT COUNT(*) FROM memory_embeddings WHERE memory_id = ?",
            (created["id"],),
        ).fetchone()[0]
    assert embedding_count == 0

    degraded = store.recall(namespace="project:bridge", query=unique_token, limit=5)

    assert degraded["items"] == []
    assert degraded["retrieval"]["mode"] == "semantic"
    assert degraded["retrieval"]["semantic_completeness"] != "complete"
    assert degraded["retrieval"]["semantic_valid_embedding_count"] == 0
    assert degraded["retrieval"]["semantic_missing_embedding_count"] == 1
    assert degraded["retrieval"]["semantic_stale_embedding_count"] == 0
    assert degraded["retrieval"]["degraded"] is True

    _warm_embeddings(store, tmp_path, config=config)
    rebuilt = store.recall(namespace="project:bridge", query=unique_token, limit=5)

    assert rebuilt["items"][0]["id"] == created["id"]
    assert rebuilt["retrieval"]["semantic_completeness"] == "complete"
    assert rebuilt["retrieval"]["degraded"] is False


def test_hybrid_provider_failure_returns_lexical_results_with_degraded_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    created = _store_memory(store, "Lexical fallback", "Alpha fallback remains available.")
    config = EmbeddingConfig(model="fixture-hash", dim=8)
    monkeypatch.setattr(query_module, "active_embedding_config", lambda: config)
    _warm_embeddings(store, tmp_path, config=config)
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_RETRIEVAL_MODE", "hybrid")
    monkeypatch.setattr(
        query_module,
        "embed_texts",
        lambda texts, *, config: (_ for _ in ()).throw(EmbeddingProviderError("private provider detail")),
    )

    result = store.recall(namespace="project:bridge", query="alpha fallback", limit=5)

    assert [item["id"] for item in result["items"]] == [created["id"]]
    assert result["retrieval"]["mode"] == "hybrid"
    assert result["retrieval"]["degraded"] is True
    assert result["retrieval"]["degraded_reason"] == "embedding-provider-failure"
    assert result["retrieval"]["semantic_available"] is False
    assert result["retrieval"]["semantic_error_type"] == "EmbeddingProviderError"
    assert result["retrieval"]["semantic_completeness"] == "complete"
    assert result["items"][0]["retrieval"]["degraded_reason"] == "embedding-provider-failure"
    assert "private provider detail" not in str(result)


def test_explicit_semantic_provider_failure_is_clear_and_sanitized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    _store_memory(store, "Semantic failure", "Alpha semantic provider failure.")
    config = EmbeddingConfig(model="fixture-hash", dim=8)
    monkeypatch.setattr(query_module, "active_embedding_config", lambda: config)
    _warm_embeddings(store, tmp_path, config=config)
    monkeypatch.setattr(
        query_module,
        "embed_texts",
        lambda texts, *, config: (_ for _ in ()).throw(EmbeddingProviderError("private provider detail")),
    )

    with pytest.raises(RuntimeError) as exc_info:
        _recall_mode(store, "alpha", "semantic")

    message = str(exc_info.value)
    assert "semantic recall failed" in message
    assert "EmbeddingProviderError" in message
    assert "private provider detail" not in message


def test_invalid_utf8_command_output_degrades_hybrid_and_keeps_semantic_error_typed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_EMBEDDING_PROVIDER", "command")
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_EMBEDDING_COMMAND", _embedding_command("invalid-utf8"))
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_EMBEDDING_MODEL", "fixture-embedding-v1")
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_EMBEDDING_DIM", "4")
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_RETRIEVAL_MODE", "hybrid")
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    created = _store_memory(store, "Lexical fallback", "Alpha fallback remains available.")
    _insert_compatible_sidecar(store, config=EmbeddingConfig(model="fixture-embedding-v1", dim=4))

    hybrid = store.recall(namespace="project:bridge", query="alpha fallback", limit=5)

    assert [item["id"] for item in hybrid["items"]] == [created["id"]]
    assert hybrid["retrieval"]["degraded"] is True
    assert hybrid["retrieval"]["semantic_error_type"] == "EmbeddingProviderError"
    with pytest.raises(RuntimeError) as exc_info:
        _recall_mode(store, "alpha fallback", "semantic")
    message = str(exc_info.value)
    assert "semantic recall failed" in message
    assert "EmbeddingProviderError" in message
    assert "UnicodeDecodeError" not in message


def test_hybrid_does_not_swallow_database_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    _store_memory(store, "Database error", "Alpha lexical candidate.")
    monkeypatch.setattr(
        query_module,
        "recall_via_semantic",
        lambda *args, **kwargs: (_ for _ in ()).throw(sqlite3.DatabaseError("database is corrupt")),
    )

    with pytest.raises(sqlite3.DatabaseError, match="database is corrupt"):
        _recall_mode(store, "alpha", "hybrid")


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_provider_vectors_are_rejected(value: float) -> None:
    with pytest.raises(RuntimeError, match="must be finite"):
        normalize_command_vectors({"vectors": [[value, 0.0]]}, expected_count=1, expected_dim=2)
    with pytest.raises(ValueError, match="must be finite"):
        vector_json([value, 0.0])


def test_corrupt_non_finite_persisted_vector_is_unusable() -> None:
    assert load_vector("[NaN, 0.0]") == []
    assert load_vector("[Infinity, 0.0]") == []


def test_chinese_han_hash_embedding_supports_semantic_retrieval(tmp_path: Path) -> None:
    vector = hash_embed_text("中文记忆检索")
    assert any(value != 0.0 for value in vector)

    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    created = _store_memory(store, "中文记忆", "部署故障需要重建语义索引。")
    _warm_embeddings(store, tmp_path)

    result = _recall_mode(store, "语义索引", "semantic")

    assert [item["id"] for item in result] == [created["id"]]


def test_chinese_substring_recall_ignores_partial_fts_decoy(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    target = _store_memory(store, "Target", "部署故障需要重建语义索引。")
    decoy = _store_memory(store, "语义", "Unrelated content.")

    result = store.recall(namespace="project:bridge", query="语义索引", limit=5)

    assert [item["id"] for item in result["items"]] == [target["id"]]
    assert decoy["id"] not in {item["id"] for item in result["items"]}


def test_embedding_rebuild_rejects_active_transaction_before_schema_repair(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    _store_memory(store, "Active transaction", "Schema repair must not start inside caller work.")

    with store._connect() as conn:
        conn.execute("DROP TABLE memory_embeddings")
        conn.execute("CREATE TABLE memory_embeddings (memory_id TEXT PRIMARY KEY, vector_json TEXT)")
        conn.commit()
        conn.execute("BEGIN IMMEDIATE")

        with pytest.raises(RuntimeError, match="without an active transaction"):
            rebuild_embedding_index(conn)

        columns = {row["name"] for row in conn.execute("PRAGMA table_info(memory_embeddings)").fetchall()}
        conn.rollback()

    assert columns == {"memory_id", "vector_json"}


def test_embedding_rebuild_repairs_schema_before_provider_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    _store_memory(store, "Schema repair", "Provider work follows the short schema repair transaction.")
    config = EmbeddingConfig(model="fixture-hash", dim=8)

    with store._connect() as conn:
        conn.execute("DROP TABLE memory_embeddings")
        conn.execute("CREATE TABLE memory_embeddings (memory_id TEXT PRIMARY KEY, vector_json TEXT)")
        conn.commit()

        def fake_embed_texts(texts: list[str], *, config: EmbeddingConfig) -> list[list[float]]:
            assert conn.in_transaction is False
            return [hash_embed_text(text, dim=config.dim) for text in texts]

        monkeypatch.setattr(embedding_index, "embed_texts", fake_embed_texts)
        report = rebuild_embedding_index(conn, config=config)
        conn.commit()

    assert report["embeddings"]["healthy"] is True
