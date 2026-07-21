import json
from pathlib import Path

from agent_mem_bridge.governance_trigger import GovernanceTriggerConfig, GovernanceTriggerEngine
from agent_mem_bridge.learning_policy import evaluate_learning_candidate
from agent_mem_bridge.storage import MemoryStore


def _candidate(**overrides):
    candidate = {
        "schema": "memory.candidate.v1",
        "namespace": "project:mem-store",
        "authority_class": "context_hint",
        "claim": "Use governance triggers to surface learning candidates without auto-promoting them.",
        "evidence_refs": ["pytest: tests/test_governance_trigger.py"],
        "source_runtime": "codex",
        "source_session_id": "session-1",
        "source_task_id": "task-1",
        "domain_tags": ["domain:memory-bridge"],
        "confidence": 0.82,
    }
    candidate.update(overrides)
    return candidate


def _engine(store: MemoryStore, tmp_path: Path) -> GovernanceTriggerEngine:
    return GovernanceTriggerEngine(
        store=store,
        config=GovernanceTriggerConfig(state_path=tmp_path / "governance-state.json"),
    )


def test_governance_trigger_creates_review_signal_for_pending_candidate(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    candidate = _candidate()
    stored = store.store_learning_candidate(candidate, evaluate_learning_candidate(candidate))

    report = _engine(store, tmp_path).run_once()

    assert report["processed_count"] == 1
    assert report["reviewed_count"] == 1
    created = report["created"][0]
    assert created["candidate_id"] == stored["id"]
    assert created["recommended_action"] == "learn"

    signals = store.recall(
        namespace="project:mem-store",
        kind="signal",
        tags_any=["kind:governance-trigger"],
        limit=10,
    )
    assert signals["count"] == 1
    signal = signals["items"][0]
    assert signal["correlation_id"] == f"governance-trigger:{stored['id']}"
    assert "mutation_boundary: signal_only_no_promotion" in signal["content"]

    review_candidates = store.recall(
        namespace="project:mem-store",
        kind="memory",
        tags_any=["kind:learning-candidate"],
        limit=10,
    )
    assert review_candidates["count"] == 1
    assert review_candidates["items"][0]["id"] == stored["id"]


def test_governance_trigger_does_not_duplicate_signals(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    candidate = _candidate()
    store.store_learning_candidate(candidate, evaluate_learning_candidate(candidate))
    engine = _engine(store, tmp_path)

    first = engine.run_once()
    second = engine.run_once()

    assert first["processed_count"] == 1
    assert second["processed_count"] == 0
    assert (
        store.recall(
            namespace="project:mem-store",
            kind="signal",
            tags_any=["kind:governance-trigger"],
            limit=10,
        )["count"]
        == 1
    )


def test_governance_trigger_drains_candidates_beyond_scan_limit(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    for index in range(3):
        candidate = _candidate(
            claim=f"Drain governance candidate {index} without starvation.",
            source_task_id=f"task-drain-{index}",
        )
        store.store_learning_candidate(candidate, evaluate_learning_candidate(candidate))
    engine = GovernanceTriggerEngine(
        store=store,
        config=GovernanceTriggerConfig(
            state_path=tmp_path / "governance-state.json",
            scan_limit=2,
        ),
    )

    first = engine.run_once()
    second = engine.run_once()

    assert first["processed_count"] == 2
    assert second["processed_count"] == 1
    assert (
        store.recall(
            namespace="project:mem-store",
            kind="signal",
            tags_any=["kind:governance-trigger"],
            limit=10,
        )["count"]
        == 3
    )


def test_governance_trigger_state_does_not_accumulate_candidate_ids(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    state_path = tmp_path / "governance-state.json"
    state_path.write_text(
        json.dumps({"signaled_candidate_ids": [f"legacy-{index}" for index in range(1_000)]}),
        encoding="utf-8",
    )
    for index in range(3):
        candidate = _candidate(
            claim=f"Bound governance trigger state for candidate {index}.",
            source_task_id=f"task-{index}",
        )
        store.store_learning_candidate(candidate, evaluate_learning_candidate(candidate))

    report = _engine(store, tmp_path).run_once()
    state = json.loads(state_path.read_text(encoding="utf-8"))

    assert report["processed_count"] == 3
    assert "signaled_candidate_ids" not in state
    assert state["state_schema_version"] == 2
    assert state["last_scanned_candidate_count"] == 3
    assert state["last_created_count"] == 3


def test_governance_trigger_skips_approved_candidate(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    candidate = _candidate()
    store.store_learning_candidate(
        candidate,
        evaluate_learning_candidate(candidate),
        candidate_status="approved",
    )

    report = _engine(store, tmp_path).run_once()

    assert report["processed_count"] == 0
    assert (
        store.recall(
            namespace="project:mem-store",
            kind="signal",
            tags_any=["kind:governance-trigger"],
            limit=10,
        )["count"]
        == 0
    )
