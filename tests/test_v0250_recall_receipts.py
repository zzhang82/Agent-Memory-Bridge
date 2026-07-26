from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_mem_bridge.retrieval_feedback import (
    RECALL_RECEIPT_SCHEMA,
    decode_recall_receipt,
    encode_recall_receipt,
    validate_recall_receipt,
)
from agent_mem_bridge.schema import rotate_database_epoch
from agent_mem_bridge.storage import MemoryStore
from agent_mem_bridge.telemetry import hash_label


def _isolate_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    config_path = tmp_path / "config.toml"
    config_path.write_text("", encoding="utf-8")
    secret_path = tmp_path / "receipt-secret.json"
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_CONFIG", str(config_path))
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_RECALL_RECEIPT_SECRET_PATH", str(secret_path))
    for name in (
        "AGENT_MEMORY_BRIDGE_RETRIEVAL_MODE",
        "AGENT_MEMORY_BRIDGE_EMBEDDING_PROVIDER",
        "AGENT_MEMORY_BRIDGE_EMBEDDING_CAPABILITY",
        "AGENT_MEMORY_BRIDGE_EMBEDDING_COMMAND",
        "AGENT_MEMORY_BRIDGE_EMBEDDING_MODEL",
        "AGENT_MEMORY_BRIDGE_EMBEDDING_DIM",
    ):
        monkeypatch.delenv(name, raising=False)
    return secret_path


def _store_with_receipt_secret(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[MemoryStore, Path]:
    secret_path = _isolate_config(tmp_path, monkeypatch)
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    return store, secret_path


def test_memory_text_recall_issues_signed_redacted_receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, secret_path = _store_with_receipt_secret(tmp_path, monkeypatch)
    raw_query = "receipt target private query"
    raw_content = "sensitive durable content that must not be embedded in the receipt"
    created = store.store(
        namespace="project:receipts",
        kind="memory",
        title=raw_query,
        content=raw_content,
    )

    recalled = store.recall(namespace="project:receipts", query=raw_query, kind="memory", limit=5)

    assert secret_path.exists()
    assert recalled["items"][0]["id"] == created["id"]
    receipt = recalled["recall_receipt"]
    assert receipt["schema"] == RECALL_RECEIPT_SCHEMA
    assert receipt["issued_for"] == "durable_memory_text_recall"
    assert receipt["provenance"] == "server_declared_not_authenticated"
    assert receipt["authenticated_origin"] is False

    payload = decode_recall_receipt(receipt["token"], secret=store.recall_receipt_secret)
    encoded_payload = json.dumps(payload, sort_keys=True)
    assert payload["schema"] == RECALL_RECEIPT_SCHEMA
    assert payload["bridge_instance_id"] == store.recall_receipt_secret.bridge_instance_id
    assert payload["database_epoch"] == store.database_epoch()
    assert payload["namespace"] == "project:receipts"
    assert payload["query_hash"] == hash_label(raw_query)
    assert payload["results"] == [{"memory_id": created["id"], "rank": 1}]
    assert raw_query not in encoded_payload
    assert raw_content not in encoded_payload

    validated = validate_recall_receipt(
        store,
        recall_receipt=receipt["token"],
        namespace="project:receipts",
        memory_id=str(created["id"]),
        result_rank=1,
    )
    assert validated["payload"] == payload


def test_recall_uses_construction_loaded_secret_without_recreating_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, secret_path = _store_with_receipt_secret(tmp_path, monkeypatch)
    store.store(namespace="project:receipts", kind="memory", title="construction secret", content="durable answer")
    assert secret_path.exists()
    secret_path.unlink()

    recalled = store.recall(namespace="project:receipts", query="construction secret", kind="memory", limit=5)

    assert "recall_receipt" in recalled
    assert not secret_path.exists()
    decode_recall_receipt(recalled["recall_receipt"]["token"], secret=store.recall_receipt_secret)


def test_receipt_is_only_emitted_for_nonempty_explicit_memory_text_recall(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, _secret_path = _store_with_receipt_secret(tmp_path, monkeypatch)
    store.store(namespace="project:receipts", kind="memory", title="durable target", content="durable target")
    store.store(namespace="project:receipts", kind="signal", title="signal target", content="signal target")

    assert "recall_receipt" in store.recall(namespace="project:receipts", query="durable target", kind="memory")
    assert "recall_receipt" not in store.recall(namespace="project:receipts", query="", kind="memory")
    assert "recall_receipt" not in store.recall(namespace="project:receipts", query="durable target", kind=None)
    assert "recall_receipt" not in store.recall(namespace="project:receipts", query="signal target", kind="signal")
    assert "recall_receipt" not in store.recall(namespace="project:receipts", kind="signal")
    assert "recall_receipt" not in store.browse(namespace="project:receipts", kind="memory")
    assert "recall_receipt" not in store.export(namespace="project:receipts", query="durable target", kind="memory")


def test_receipt_validation_rejects_tamper_expiry_epoch_namespace_instance_and_member_rank(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, _secret_path = _store_with_receipt_secret(tmp_path, monkeypatch)
    created = store.store(namespace="project:receipts", kind="memory", title="validation target", content="body")
    recalled = store.recall(namespace="project:receipts", query="validation target", kind="memory", limit=5)
    token = str(recalled["recall_receipt"]["token"])
    payload = decode_recall_receipt(token, secret=store.recall_receipt_secret)

    tampered = f"{token[:-1]}{'A' if token[-1] != 'A' else 'B'}"
    with pytest.raises(ValueError, match="signature mismatch"):
        validate_recall_receipt(
            store,
            recall_receipt=tampered,
            namespace="project:receipts",
            memory_id=str(created["id"]),
            result_rank=1,
        )

    expired_payload = {**payload, "expires_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat()}
    expired = encode_recall_receipt(expired_payload, secret=store.recall_receipt_secret)
    with pytest.raises(ValueError, match="expired"):
        validate_recall_receipt(
            store,
            recall_receipt=expired,
            namespace="project:receipts",
            memory_id=str(created["id"]),
            result_rank=1,
        )

    mismatched_instance_payload = {**payload, "bridge_instance_id": "0" * 32}
    mismatched_instance = encode_recall_receipt(mismatched_instance_payload, secret=store.recall_receipt_secret)
    with pytest.raises(ValueError, match="bridge instance mismatch"):
        validate_recall_receipt(
            store,
            recall_receipt=mismatched_instance,
            namespace="project:receipts",
            memory_id=str(created["id"]),
            result_rank=1,
        )

    with pytest.raises(ValueError, match="namespace mismatch"):
        validate_recall_receipt(
            store,
            recall_receipt=token,
            namespace="project:other",
            memory_id=str(created["id"]),
            result_rank=1,
        )

    for memory_id, result_rank in (("not-the-memory", 1), (str(created["id"]), 2)):
        with pytest.raises(ValueError, match="memory id and rank mismatch"):
            validate_recall_receipt(
                store,
                recall_receipt=token,
                namespace="project:receipts",
                memory_id=memory_id,
                result_rank=result_rank,
            )

    with store._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        rotate_database_epoch(conn)
        conn.commit()
    with pytest.raises(ValueError, match="database epoch mismatch"):
        validate_recall_receipt(
            store,
            recall_receipt=token,
            namespace="project:receipts",
            memory_id=str(created["id"]),
            result_rank=1,
        )
