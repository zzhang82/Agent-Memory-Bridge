from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

import agent_mem_bridge.query as query_module
from agent_mem_bridge.embedding_index import active_embedding_config, embedding_config_is_true_semantic
from agent_mem_bridge.index_health import rebuild_embedding_index
from agent_mem_bridge.paths import resolve_embedding_capability
from agent_mem_bridge.storage import MemoryStore
from agent_mem_bridge.telemetry import hash_label


def _isolate_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("", encoding="utf-8")
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_CONFIG", str(config_path))
    for name in (
        "AGENT_MEMORY_BRIDGE_RETRIEVAL_MODE",
        "AGENT_MEMORY_BRIDGE_EMBEDDING_PROVIDER",
        "AGENT_MEMORY_BRIDGE_EMBEDDING_CAPABILITY",
        "AGENT_MEMORY_BRIDGE_EMBEDDING_COMMAND",
        "AGENT_MEMORY_BRIDGE_EMBEDDING_MODEL",
        "AGENT_MEMORY_BRIDGE_EMBEDDING_DIM",
    ):
        monkeypatch.delenv(name, raising=False)


def _embedding_command(mode: str = "ok") -> str:
    fixture = Path(__file__).parent / "fixtures" / "fake_embedding_gateway.py"
    return f'"{sys.executable}" "{fixture}" {mode}'


def _enable_command_provider(monkeypatch: pytest.MonkeyPatch, *, semantic: bool) -> None:
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_EMBEDDING_PROVIDER", "command")
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_EMBEDDING_COMMAND", _embedding_command())
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_EMBEDDING_MODEL", "fixture-embedding-v1")
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_EMBEDDING_DIM", "4")
    if semantic:
        monkeypatch.setenv("AGENT_MEMORY_BRIDGE_EMBEDDING_CAPABILITY", "semantic")
    else:
        monkeypatch.delenv("AGENT_MEMORY_BRIDGE_EMBEDDING_CAPABILITY", raising=False)


def _store_alpha_beta(store: MemoryStore) -> dict[str, object]:
    alpha = store.store(
        namespace="project:bridge",
        title="Alpha Memory",
        content="Alpha runtime memory should rank first.",
        kind="memory",
    )
    store.store(
        namespace="project:bridge",
        title="Beta Memory",
        content="Beta runtime memory should rank lower for alpha.",
        kind="memory",
    )
    return alpha


def test_default_embedding_capability_is_hashed_lexical_and_hash_alias_is_kept(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _isolate_config(tmp_path, monkeypatch)

    config = active_embedding_config()

    assert resolve_embedding_capability() == "hashed_lexical"
    assert config.provider == "hash"
    assert config.capability == "hashed_lexical"
    assert embedding_config_is_true_semantic(config) is False

    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_EMBEDDING_PROVIDER", "hashed_lexical")
    alias_config = active_embedding_config()

    assert alias_config.provider == "hash"
    assert alias_config.capability == "hashed_lexical"
    assert embedding_config_is_true_semantic(alias_config) is False


@pytest.mark.parametrize("provider", ["hash", "command"])
def test_semantic_mode_rejects_non_semantic_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
) -> None:
    _isolate_config(tmp_path, monkeypatch)
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_RETRIEVAL_MODE", "semantic")
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_EMBEDDING_PROVIDER", provider)
    if provider == "command":
        monkeypatch.setenv("AGENT_MEMORY_BRIDGE_EMBEDDING_COMMAND", _embedding_command())
        monkeypatch.setenv("AGENT_MEMORY_BRIDGE_EMBEDDING_MODEL", "fixture-embedding-v1")
        monkeypatch.setenv("AGENT_MEMORY_BRIDGE_EMBEDDING_DIM", "4")

    store = MemoryStore(tmp_path / f"{provider}.db", log_dir=tmp_path / f"{provider}-logs")
    _store_alpha_beta(store)

    with pytest.raises(RuntimeError, match="embedding_capability='semantic'"):
        store.recall(namespace="project:bridge", query="alpha", limit=5)


def test_hybrid_without_semantic_provider_preserves_lexical_order_and_skips_semantic_arm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _isolate_config(tmp_path, monkeypatch)
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    _store_alpha_beta(store)
    with store._connect() as conn:
        rebuild_embedding_index(conn)
        conn.commit()

    lexical = store.recall(namespace="project:bridge", query="runtime memory", limit=5)
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_RETRIEVAL_MODE", "hybrid")
    monkeypatch.setattr(
        query_module,
        "recall_via_semantic",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("semantic arm must be skipped")),
    )

    hybrid = store.recall(namespace="project:bridge", query="runtime memory", limit=5)

    assert [item["id"] for item in hybrid["items"]] == [item["id"] for item in lexical["items"]]
    assert all("retrieval" not in item for item in hybrid["items"])
    assert hybrid["retrieval"]["mode"] == "hybrid"
    assert hybrid["retrieval"]["semantic_available"] is False
    assert hybrid["retrieval"]["semantic_skipped"] is True
    assert hybrid["retrieval"]["semantic_skip_reason"] == "semantic-provider-not-declared"
    assert hybrid["retrieval"]["semantic_provider"] == "hash"
    assert hybrid["retrieval"]["semantic_capability"] == "hashed_lexical"
    assert hybrid["retrieval"]["semantic_capability_declared"] is False
    assert hybrid["retrieval"]["semantic_capability_verified"] is False
    assert hybrid["retrieval"]["semantic_capability_verified_deprecated"] is True
    assert hybrid["retrieval"]["semantic_capability_verified_replacement"] == "semantic_capability_declared"
    assert hybrid["retrieval"]["semantic_capability_provenance"] == "configuration_declared_not_runtime_verified"


def test_only_declared_semantic_command_provider_reports_semantic_available(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _isolate_config(tmp_path, monkeypatch)
    _enable_command_provider(monkeypatch, semantic=True)
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_RETRIEVAL_MODE", "semantic")
    store = MemoryStore(tmp_path / "semantic.db", log_dir=tmp_path / "logs")
    alpha = _store_alpha_beta(store)
    with store._connect() as conn:
        rebuild_embedding_index(conn)
        conn.commit()

    result = store.recall(namespace="project:bridge", query="alpha", limit=5)

    assert result["items"][0]["id"] == alpha["id"]
    assert result["retrieval"]["mode"] == "semantic"
    assert result["retrieval"]["semantic_available"] is True
    assert result["retrieval"]["semantic_provider"] == "command"
    assert result["retrieval"]["semantic_capability"] == "semantic"
    assert result["retrieval"]["semantic_capability_declared"] is True
    assert result["retrieval"]["semantic_capability_verified"] is True
    assert result["retrieval"]["semantic_capability_verified_deprecated"] is True
    assert result["retrieval"]["semantic_capability_verified_replacement"] == "semantic_capability_declared"
    assert result["retrieval"]["semantic_capability_provenance"] == "configuration_declared_not_runtime_verified"


def test_recall_operational_log_hashes_query_without_raw_query(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _isolate_config(tmp_path, monkeypatch)
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    store.store(
        namespace="project:bridge",
        title="Private lookup",
        content="Recall operational logging should redact query text.",
        kind="memory",
    )
    raw_query = "private customer token v0250"

    store.recall(namespace="project:bridge", query=raw_query, limit=5)

    log_text = (tmp_path / "logs" / "recall.log").read_text(encoding="utf-8")
    entry = json.loads(log_text.strip().splitlines()[-1])
    assert raw_query not in log_text
    assert "query" not in entry
    assert entry["query_present"] is True
    assert entry["query_hash"] == hash_label(raw_query)
    assert entry["query_length"] == len(raw_query)
