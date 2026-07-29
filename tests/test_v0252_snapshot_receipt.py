from __future__ import annotations

import json
from pathlib import Path

import pytest

import agent_mem_bridge.query as query_module
from agent_mem_bridge.embedding_index import EmbeddingConfig, vector_json
from agent_mem_bridge.retrieval_feedback import (
    RECALL_RECEIPT_SCHEMA,
    canonical_retrieval_contract_digest,
    decode_recall_receipt,
    encode_recall_receipt,
    validate_recall_receipt,
)
from agent_mem_bridge.schema import exact_content_hash
from agent_mem_bridge.storage import MemoryStore


def _store_with_receipt_secret(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> MemoryStore:
    config_path = tmp_path / "config.toml"
    config_path.write_text("", encoding="utf-8")
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_CONFIG", str(config_path))
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv(
        "AGENT_MEMORY_BRIDGE_RECALL_RECEIPT_SECRET_PATH",
        str(tmp_path / "receipt-secret.json"),
    )
    for name in (
        "AGENT_MEMORY_BRIDGE_RETRIEVAL_MODE",
        "AGENT_MEMORY_BRIDGE_EMBEDDING_PROVIDER",
        "AGENT_MEMORY_BRIDGE_EMBEDDING_CAPABILITY",
        "AGENT_MEMORY_BRIDGE_EMBEDDING_COMMAND",
        "AGENT_MEMORY_BRIDGE_EMBEDDING_MODEL",
        "AGENT_MEMORY_BRIDGE_EMBEDDING_DIM",
    ):
        monkeypatch.delenv(name, raising=False)
    return MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")


@pytest.mark.parametrize("retrieval_mode", ["lexical", "semantic", "hybrid"])
def test_v2_receipt_rows_epoch_exposure_and_signature_share_one_read_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    retrieval_mode: str,
) -> None:
    store = _store_with_receipt_secret(tmp_path, monkeypatch)
    created = store.store(
        namespace="project:snapshot",
        kind="memory",
        title="snapshot target",
        content="snapshot content",
    )
    original_connect = store._connect
    if retrieval_mode in {"semantic", "hybrid"}:
        embedding_config = EmbeddingConfig(
            provider="command",
            capability="semantic",
            model="snapshot-semantic",
            dim=2,
        )
        monkeypatch.setattr(query_module, "active_embedding_config", lambda: embedding_config)
        monkeypatch.setattr(
            query_module,
            "embed_texts",
            lambda texts, *, config: [[1.0, 0.0] for _text in texts],
        )
        with original_connect() as conn:
            content_hash = conn.execute(
                "SELECT content_hash FROM memories WHERE id = ?",
                (created["id"],),
            ).fetchone()["content_hash"]
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
                    created["id"],
                    content_hash,
                    embedding_config.model,
                    embedding_config.dim,
                    vector_json([1.0, 0.0]),
                    "2026-01-01T00:00:00+00:00",
                ),
            )
            conn.commit()
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_RETRIEVAL_MODE", retrieval_mode)
    opened_connections = 0
    statements: list[str] = []

    def tracked_connect():
        nonlocal opened_connections
        opened_connections += 1
        conn = original_connect()
        conn.set_trace_callback(statements.append)
        return conn

    monkeypatch.setattr(store, "_connect", tracked_connect)
    recalled = store.recall(
        namespace="project:snapshot",
        query="snapshot target",
        kind="memory",
        limit=5,
    )

    assert opened_connections == 1
    assert [statement for statement in statements if statement.strip().upper() == "BEGIN"] == ["BEGIN"]
    assert all(
        {"content_hash", "exact_content_hash", "content_version", "_exact_content_hash"}.isdisjoint(item)
        for item in recalled["items"]
    )

    payload = decode_recall_receipt(
        recalled["recall_receipt"]["token"],
        secret=store.recall_receipt_secret,
    )
    assert payload["schema"] == RECALL_RECEIPT_SCHEMA
    assert payload["retrieval_mode"] == retrieval_mode
    assert payload["results"] == [{"memory_id": created["id"], "rank": 1}]
    assert payload["exposure_set"] == [
        {
            "memory_id": created["id"],
            "rank": 1,
            "exact_content_hash": exact_content_hash("snapshot content"),
            "content_version": exact_content_hash("snapshot content"),
        }
    ]
    assert payload["retrieval_contract_attestation"] == "server"
    assert payload["retrieval_contract_digest"] == canonical_retrieval_contract_digest(payload["retrieval_contract"])
    assert "snapshot target" not in json.dumps(payload, sort_keys=True)
    assert "snapshot content" not in json.dumps(payload, sort_keys=True)


def test_v2_receipt_covers_every_returned_memory_and_validation_checks_exact_hash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store_with_receipt_secret(tmp_path, monkeypatch)
    for index in range(3):
        store.store(
            namespace="project:exposure",
            kind="memory",
            title=f"full exposure target {index}",
            content=f"full exposure body {index}",
        )

    recalled = store.recall(
        namespace="project:exposure",
        query="full exposure target",
        kind="memory",
        limit=3,
    )
    payload = decode_recall_receipt(
        recalled["recall_receipt"]["token"],
        secret=store.recall_receipt_secret,
    )

    assert len(recalled["items"]) == 3
    assert len(payload["exposure_set"]) == len(recalled["items"])
    for rank, (item, exposure) in enumerate(zip(recalled["items"], payload["exposure_set"]), start=1):
        assert exposure == {
            "memory_id": item["id"],
            "rank": rank,
            "exact_content_hash": exact_content_hash(item["content"]),
            "content_version": exact_content_hash(item["content"]),
        }
        validate_recall_receipt(
            store,
            recall_receipt=recalled["recall_receipt"]["token"],
            namespace="project:exposure",
            memory_id=item["id"],
            result_rank=rank,
        )

    mismatched_hash_payload = json.loads(json.dumps(payload))
    mismatched_hash_payload["exposure_set"][0]["exact_content_hash"] = "0" * 64
    mismatched_hash_token = encode_recall_receipt(
        mismatched_hash_payload,
        secret=store.recall_receipt_secret,
    )
    with pytest.raises(ValueError, match="content version mismatch"):
        validate_recall_receipt(
            store,
            recall_receipt=mismatched_hash_token,
            namespace="project:exposure",
            memory_id=recalled["items"][0]["id"],
            result_rank=1,
        )

    stale_version_payload = json.loads(json.dumps(payload))
    stale_version_payload["exposure_set"][0]["exact_content_hash"] = "0" * 64
    stale_version_payload["exposure_set"][0]["content_version"] = "0" * 64
    stale_version_token = encode_recall_receipt(
        stale_version_payload,
        secret=store.recall_receipt_secret,
    )
    with pytest.raises(ValueError, match="memory content hash mismatch"):
        validate_recall_receipt(
            store,
            recall_receipt=stale_version_token,
            namespace="project:exposure",
            memory_id=recalled["items"][0]["id"],
            result_rank=1,
        )

    incomplete_payload = json.loads(json.dumps(payload))
    incomplete_payload["exposure_set"].pop()
    incomplete_token = encode_recall_receipt(incomplete_payload, secret=store.recall_receipt_secret)
    with pytest.raises(ValueError, match="incomplete exposure set"):
        validate_recall_receipt(
            store,
            recall_receipt=incomplete_token,
            namespace="project:exposure",
            memory_id=recalled["items"][0]["id"],
            result_rank=1,
        )
