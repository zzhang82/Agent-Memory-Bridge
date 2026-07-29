from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_mem_bridge.embedding_index import EmbeddingConfig
from agent_mem_bridge.query import semantic_index_metadata, semantic_unavailable_metadata
from agent_mem_bridge.retrieval_feedback import decode_recall_receipt
from agent_mem_bridge.storage import MemoryStore

NAMESPACE = "project:v0252-context"


def _new_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> MemoryStore:
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


def _receipt_payload(store: MemoryStore, response: dict[str, object]) -> dict[str, object]:
    receipt = response["recall_receipt"]
    assert isinstance(receipt, dict)
    return decode_recall_receipt(str(receipt["token"]), secret=store.recall_receipt_secret)


def test_evidence_context_is_digest_only_deterministic_and_not_vote_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _new_store(tmp_path, monkeypatch)
    created = store.store(
        namespace=NAMESPACE,
        kind="memory",
        title="Evidence context target",
        content="Evidence context must never alter retrieval order or vote identity.",
    )
    baseline = store.recall(
        namespace=NAMESPACE,
        query="evidence context target",
        kind="memory",
        limit=5,
    )
    context = {
        "model": "private-model-v1",
        "harness": "private-harness-v1",
        "chat_template": "private-template-v1",
    }
    first = store.recall(
        namespace=NAMESPACE,
        query="evidence context target",
        kind="memory",
        limit=5,
        evidence_context=context,
    )
    repeated = store.recall(
        namespace=NAMESPACE,
        query="evidence context target",
        kind="memory",
        limit=5,
        evidence_context=context,
    )
    changed = store.recall(
        namespace=NAMESPACE,
        query="evidence context target",
        kind="memory",
        limit=5,
        evidence_context={**context, "model": "private-model-v2"},
    )

    baseline_order = [str(item["id"]) for item in baseline["items"]]
    assert [str(item["id"]) for item in first["items"]] == baseline_order
    assert [str(item["id"]) for item in changed["items"]] == baseline_order

    first_payload = _receipt_payload(store, first)
    repeated_payload = _receipt_payload(store, repeated)
    changed_payload = _receipt_payload(store, changed)
    first_context = first_payload["evidence_context"]
    repeated_context = repeated_payload["evidence_context"]
    changed_context = changed_payload["evidence_context"]
    assert isinstance(first_context, dict)
    assert isinstance(repeated_context, dict)
    assert isinstance(changed_context, dict)
    assert first_context["digests"] == repeated_context["digests"]
    assert first_context["digests"]["model"] != changed_context["digests"]["model"]
    assert first_context["digests"]["harness"] == changed_context["digests"]["harness"]
    assert first_context["digests"]["chat_template"] == changed_context["digests"]["chat_template"]
    assert first_context["provenance"] == "caller_declared_not_authenticated"
    assert first_context["authenticated_origin"] is False
    assert first_context["digest_algorithm"] == "sha256"
    assert all(isinstance(digest, str) and len(digest) == 64 for digest in first_context["digests"].values())
    encoded_payloads = json.dumps(
        [first_payload, repeated_payload, changed_payload],
        sort_keys=True,
    )
    for raw_value in (*context.values(), "private-model-v2"):
        assert raw_value not in encoded_payloads

    first_receipt = first["recall_receipt"]
    changed_receipt = changed["recall_receipt"]
    assert isinstance(first_receipt, dict)
    assert isinstance(changed_receipt, dict)
    first_vote = store.feedback(
        namespace=NAMESPACE,
        recall_receipt=str(first_receipt["token"]),
        memory_id=str(created["id"]),
        result_rank=baseline_order.index(str(created["id"])) + 1,
        outcome="helpful",
    )
    duplicate_vote = store.feedback(
        namespace=NAMESPACE,
        recall_receipt=str(changed_receipt["token"]),
        memory_id=str(created["id"]),
        result_rank=baseline_order.index(str(created["id"])) + 1,
        outcome="helpful",
    )
    assert duplicate_vote["stored"] is False
    assert duplicate_vote["duplicate"] is True
    assert duplicate_vote["feedback_id"] == first_vote["feedback_id"]


def test_evidence_context_rejects_unknown_and_oversized_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _new_store(tmp_path, monkeypatch)
    store.store(
        namespace=NAMESPACE,
        kind="memory",
        title="Bounded evidence context",
        content="Only the declared evidence context fields are accepted.",
    )

    with pytest.raises(ValueError, match="only supports"):
        store.recall(
            namespace=NAMESPACE,
            query="bounded evidence context",
            kind="memory",
            evidence_context={"session": "not-allowed"},
        )
    with pytest.raises(ValueError, match="256 characters or fewer"):
        store.recall(
            namespace=NAMESPACE,
            query="bounded evidence context",
            kind="memory",
            evidence_context={"chat_template": "x" * 257},
        )


@pytest.mark.parametrize(
    "config",
    [
        EmbeddingConfig(provider="hash", capability="hashed_lexical"),
        EmbeddingConfig(provider="hashed_lexical", capability="hashed_lexical"),
    ],
)
def test_non_semantic_capabilities_remain_explicitly_undeclared(config: EmbeddingConfig) -> None:
    unavailable = semantic_unavailable_metadata(mode="hybrid", config=config)
    indexed = semantic_index_metadata(
        config=config,
        memory_count=1,
        valid_embedding_count=1,
        missing_embedding_count=0,
        stale_embedding_count=0,
        invalid_embedding_count=0,
    )

    for metadata in (unavailable, indexed):
        assert metadata["semantic_capability"] == "hashed_lexical"
        assert metadata["semantic_capability_declared"] is False
        assert metadata["semantic_capability_verified"] is False
        assert metadata["semantic_capability_verified_deprecated"] is True
        assert metadata["semantic_capability_verified_replacement"] == "semantic_capability_declared"
        assert metadata["semantic_capability_provenance"] == "configuration_declared_not_runtime_verified"
    assert indexed["semantic_available"] is False


def test_semantic_declaration_is_boolean_and_legacy_verified_is_deprecated_alias() -> None:
    metadata = semantic_index_metadata(
        config=EmbeddingConfig(
            provider="command",
            capability="semantic",
            model="fixture-semantic",
            dim=4,
            command=("fixture",),
        ),
        memory_count=1,
        valid_embedding_count=1,
        missing_embedding_count=0,
        stale_embedding_count=0,
        invalid_embedding_count=0,
    )

    assert metadata["semantic_available"] is True
    assert metadata["semantic_capability"] == "semantic"
    assert metadata["semantic_capability_declared"] is True
    assert metadata["semantic_capability_verified"] is metadata["semantic_capability_declared"]
    assert metadata["semantic_capability_verified_deprecated"] is True
    assert metadata["semantic_capability_verified_replacement"] == "semantic_capability_declared"
    assert metadata["semantic_capability_provenance"] == "configuration_declared_not_runtime_verified"
