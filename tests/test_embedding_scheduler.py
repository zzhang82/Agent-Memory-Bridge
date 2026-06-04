from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agent_mem_bridge.embedding_index import EmbeddingConfig, embedding_health
from agent_mem_bridge.embedding_scheduler import (
    EmbeddingSchedulerConfig,
    run_embedding_sidecar_maintenance,
)
from agent_mem_bridge.storage import MemoryStore


def _store_memory(store: MemoryStore, title: str, content: str) -> dict[str, object]:
    return store.store(namespace="project:bridge", title=title, content=content, kind="memory")


def test_embedding_scheduler_disabled_does_not_warm_sidecar(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    _store_memory(store, "Disabled scheduler", "Disabled scheduler should not write embedding cache rows.")

    result = run_embedding_sidecar_maintenance(
        store,
        config=EmbeddingSchedulerConfig(
            enabled=False,
            state_path=tmp_path / "embedding-state.json",
            embedding_config=EmbeddingConfig(model="fixture-hash", dim=8),
        ),
    )

    with store._connect() as conn:
        health = embedding_health(conn, config=EmbeddingConfig(model="fixture-hash", dim=8))

    assert result["reason"] == "disabled"
    assert result["processed_count"] == 0
    assert health["embedding_count"] == 0
    assert health["missing_embedding_count"] == 1


def test_disabled_default_scheduler_does_not_require_valid_embedding_config(tmp_path: Path, monkeypatch) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    _store_memory(store, "Disabled invalid config", "Disabled scheduler should not parse active embeddings.")
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_EMBEDDING_SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_EMBEDDING_SCHEDULER_STATE_PATH", str(tmp_path / "embedding-state.json"))
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_EMBEDDING_DIM", "0")

    result = run_embedding_sidecar_maintenance(store)

    assert result["enabled"] is False
    assert result["processed_count"] == 0


def test_embedding_scheduler_processes_due_batch_and_preserves_memories(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    _store_memory(store, "First", "The first memory should be embedded.")
    _store_memory(store, "Second", "The second memory should wait for the next batch.")
    config = EmbeddingSchedulerConfig(
        enabled=True,
        state_path=tmp_path / "embedding-state.json",
        interval_seconds=3600,
        batch_size=1,
        embedding_config=EmbeddingConfig(model="fixture-hash", dim=8),
    )

    result = run_embedding_sidecar_maintenance(
        store,
        config=config,
        now=datetime(2026, 6, 4, 12, 0, tzinfo=UTC),
    )

    with store._connect() as conn:
        memory_count = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        health = embedding_health(conn, config=EmbeddingConfig(model="fixture-hash", dim=8))
    state = json.loads((tmp_path / "embedding-state.json").read_text(encoding="utf-8"))

    assert result["reason"] == "never-completed"
    assert result["processed_count"] == 1
    assert result["remaining_count"] == 1
    assert memory_count == 2
    assert health["embedding_count"] == 1
    assert state["embedding_model"] == "fixture-hash"
    assert state["embedding_dim"] == 8


def test_embedding_scheduler_respects_interval_when_not_due(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    _store_memory(store, "Interval", "Interval should prevent repeated embedding work.")
    now = datetime(2026, 6, 4, 12, 0, tzinfo=UTC)
    config = EmbeddingSchedulerConfig(
        enabled=True,
        state_path=tmp_path / "embedding-state.json",
        interval_seconds=3600,
        batch_size=10,
        embedding_config=EmbeddingConfig(model="fixture-hash", dim=8),
    )

    first = run_embedding_sidecar_maintenance(store, config=config, now=now)
    second = run_embedding_sidecar_maintenance(store, config=config, now=now + timedelta(seconds=30))

    assert first["processed_count"] == 1
    assert second["due"] is False
    assert second["reason"] == "interval-not-elapsed"
    assert second["processed_count"] == 0


def test_embedding_scheduler_refreshes_stale_sidecar_rows(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    created = _store_memory(store, "Stale", "Original memory content.")
    config = EmbeddingSchedulerConfig(
        enabled=True,
        state_path=tmp_path / "embedding-state.json",
        interval_seconds=0,
        batch_size=10,
        embedding_config=EmbeddingConfig(model="fixture-hash", dim=8),
    )
    run_embedding_sidecar_maintenance(store, config=config)

    changed_content = "Changed memory content should refresh the sidecar row."
    changed_hash = hashlib.sha256(changed_content.encode("utf-8")).hexdigest()
    with store._connect() as conn:
        conn.execute(
            "UPDATE memories SET content = ?, content_hash = ? WHERE id = ?",
            (changed_content, changed_hash, created["id"]),
        )
        conn.commit()

    result = run_embedding_sidecar_maintenance(store, config=config)

    with store._connect() as conn:
        health = embedding_health(conn, config=EmbeddingConfig(model="fixture-hash", dim=8))

    assert result["processed_count"] == 1
    assert health["stale_embedding_count"] == 0
    assert health["missing_embedding_count"] == 0
