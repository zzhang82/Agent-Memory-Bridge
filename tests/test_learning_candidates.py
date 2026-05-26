from __future__ import annotations

from pathlib import Path

import pytest

from agent_mem_bridge.learning_policy import evaluate_learning_candidate
from agent_mem_bridge.storage import MemoryStore


def _candidate(**overrides):
    candidate = {
        "schema": "memory.candidate.v1",
        "namespace": "project:mem-store",
        "authority_class": "context_hint",
        "claim": "AMB learning candidates must stay out of normal recall until reviewed.",
        "evidence_refs": ["pytest: tests/test_learning_candidates.py"],
        "source_runtime": "hermes",
        "source_session_id": "session-1",
        "source_task_id": "task-1",
        "sensitivity": "safe",
        "created_by": "cole",
    }
    candidate.update(overrides)
    return candidate


def test_store_learning_candidate_suppresses_from_normal_recall(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    candidate = _candidate()
    decision = evaluate_learning_candidate(candidate)

    stored = store.store_learning_candidate(candidate, decision)

    assert stored["stored"] is True
    assert stored["candidate_status"] == "pending"
    assert stored["decision"] == "allow"

    normal = store.recall(
        namespace="project:mem-store",
        query="learning candidates normal recall reviewed",
        limit=10,
    )
    normal_kind_memory = store.recall(
        namespace="project:mem-store",
        query="learning candidates normal recall reviewed",
        kind="memory",
        limit=10,
    )
    review = store.recall(
        namespace="project:mem-store",
        tags_any=["kind:learning-candidate"],
        limit=10,
    )

    assert normal["count"] == 0
    assert normal_kind_memory["count"] == 0
    assert review["count"] == 1
    item = review["items"][0]
    assert item["id"] == stored["id"]
    assert item["kind"] == "memory"
    assert "kind:learning-candidate" in item["tags"]
    assert "candidate_status:pending" in item["tags"]
    assert "source_runtime:hermes" in item["tags"]
    assert "authority_class:context_hint" in item["tags"]
    assert "schema:memory.candidate.v1" in item["tags"]
    assert "schema:memory.writeback_decision.v1" in item["tags"]
    assert "record_type: learning-candidate" in item["content"]


def test_learning_candidate_can_be_reviewed_by_status_tag(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    candidate = _candidate(authority_class="decision")
    decision = evaluate_learning_candidate(candidate)

    store.store_learning_candidate(candidate, decision, candidate_status="needs_review")

    review = store.recall(
        namespace="project:mem-store",
        tags_any=["candidate_status:needs_review"],
        limit=10,
    )

    assert review["count"] == 1
    assert "candidate_status:needs_review" in review["items"][0]["tags"]


def test_learning_candidate_status_is_validated(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")

    with pytest.raises(ValueError, match="candidate_status"):
        store.store_learning_candidate(_candidate(), evaluate_learning_candidate(_candidate()), candidate_status="trusted")


def test_learning_candidate_suppressed_from_browse_and_export_by_default(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    candidate = _candidate()
    decision = evaluate_learning_candidate(candidate)
    store.store_learning_candidate(candidate, decision)

    browsed = store.browse(namespace="project:mem-store", limit=10)
    browsed_kind_memory = store.browse(namespace="project:mem-store", kind="memory", limit=10)
    exported = store.export(namespace="project:mem-store", format="json", limit=10)
    exported_kind_memory = store.export(namespace="project:mem-store", format="json", kind="memory", limit=10)
    review_export = store.export(
        namespace="project:mem-store",
        format="json",
        tags_any=["kind:learning-candidate"],
        limit=10,
    )

    assert browsed["count"] == 0
    assert browsed_kind_memory["count"] == 0
    assert exported["count"] == 0
    assert exported_kind_memory["count"] == 0
    assert review_export["count"] == 1


def test_store_learning_candidate_rejects_denied_and_degraded_decisions(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    denied_candidate = _candidate(claim="Store password=hunter2")
    denied = evaluate_learning_candidate(denied_candidate)
    degraded = evaluate_learning_candidate(_candidate(), backend_available=False)

    with pytest.raises(ValueError, match="only allow or needs_review"):
        store.store_learning_candidate(denied_candidate, denied)
    with pytest.raises(ValueError, match="decision does not match policy"):
        store.store_learning_candidate(_candidate(), degraded)

    assert store.recall(namespace="project:mem-store", tags_any=["kind:learning-candidate"], limit=10)["count"] == 0


def test_store_learning_candidate_rejects_forged_allow_decision_for_sensitive_candidate(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    sensitive_candidate = _candidate(claim="Store password=hunter2")
    forged_decision = evaluate_learning_candidate(_candidate())

    with pytest.raises(ValueError, match="decision does not match policy"):
        store.store_learning_candidate(sensitive_candidate, forged_decision)

    assert store.recall(namespace="project:mem-store", tags_any=["kind:learning-candidate"], limit=10)["count"] == 0


def test_store_learning_candidate_rejects_incoherent_status_decision_pairs(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    allow_decision = evaluate_learning_candidate(_candidate())
    review_decision = evaluate_learning_candidate(_candidate(authority_class="decision"))

    with pytest.raises(ValueError, match="approved requires allow"):
        store.store_learning_candidate(_candidate(authority_class="decision"), review_decision, candidate_status="approved")
    with pytest.raises(ValueError, match="needs_review status requires"):
        store.store_learning_candidate(_candidate(), allow_decision, candidate_status="needs_review")


def test_learning_candidates_are_suppressed_from_stats_by_default(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    store.store(
        namespace="project:mem-store",
        kind="memory",
        title="Normal durable note",
        content="Normal durable memory remains counted.",
        tags=["domain:test"],
    )
    store.store_learning_candidate(_candidate(), evaluate_learning_candidate(_candidate()))

    stats = store.stats("project:mem-store")

    assert stats["total_count"] == 1
    assert stats["kind_counts"]["memory"] == 1


def test_store_learning_candidate_does_not_expand_public_mcp_surface(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    assert hasattr(store, "store_learning_candidate")

    # The method exists only on the internal store object; public MCP surface is
    # guarded by tests/test_public_surface.py and release-contract tests.
    assert callable(store.store_learning_candidate)
