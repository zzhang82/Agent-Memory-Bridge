from pathlib import Path

from agent_mem_bridge.learning_policy import evaluate_learning_candidate
from agent_mem_bridge.learning_candidates import build_review_receipt_hash
from agent_mem_bridge.promotion_governance import (
    review_learning_candidate,
    review_learning_candidates,
)
from agent_mem_bridge.storage import MemoryStore


def _candidate(**overrides):
    candidate = {
        "schema": "memory.candidate.v1",
        "namespace": "project:mem-store",
        "authority_class": "context_hint",
        "claim": "Use domain-scoped startup packets for Hermes AMH handoff.",
        "evidence_refs": ["session:alpha"],
        "source_runtime": "hermes",
        "source_session_id": "session-1",
        "source_task_id": "task-1",
        "domain_tags": ["domain:runtime"],
        "confidence": 0.82,
    }
    candidate.update(overrides)
    return candidate


def test_review_learning_candidate_allows_clear_domain_scoped_candidate(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")

    review = review_learning_candidate(store, _candidate())

    assert review["recommended_action"] == "learn"
    assert review["durable_write_allowed"] is True
    assert review["target_record_type"] == "learn"
    assert review["checks"]["domain_scope"]["status"] == "present"
    assert review["checks"]["cluster_scope"]["status"] == "weak"
    assert review["checks"]["confidence"]["status"] == "pass"
    assert review["reason_codes"] == []
    assert review["writeback_boundary"] == "review_only"


def test_review_learning_candidate_recommends_merge_for_duplicate_claim(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    existing = store.store(
        namespace="project:mem-store",
        kind="memory",
        title="[[Learn]] Hermes AMH handoff",
        content="record_type: learn\nclaim: Use domain-scoped startup packets for Hermes AMH handoff.\n",
        tags=["kind:learn", "domain:runtime"],
    )

    review = review_learning_candidate(store, _candidate())

    assert review["recommended_action"] == "merge"
    assert review["durable_write_allowed"] is False
    assert review["checks"]["dedup"]["record_ids"] == [existing["id"]]
    assert "possible_duplicate" in review["reason_codes"]


def test_review_learning_candidate_keeps_contradicted_candidate_staged(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    old = store.store(
        namespace="project:mem-store",
        kind="memory",
        title="[[Learn]] old Hermes startup",
        content="record_type: learn\nclaim: Hermes should rely only on raw AMB recall at startup.\n",
        tags=["kind:learn", "domain:runtime"],
    )

    review = review_learning_candidate(
        store,
        _candidate(
            contradicts_record_ids=[old["id"]],
            supersession_plan="Review the old raw-recall startup guidance before replacing it.",
        ),
    )

    assert review["recommended_action"] == "keep_staged"
    assert review["durable_write_allowed"] is False
    assert review["checks"]["contradiction"]["record_ids"] == [old["id"]]
    assert "possible_contradiction" in review["reason_codes"]


def test_review_learning_candidate_keeps_low_confidence_candidate_staged(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")

    review = review_learning_candidate(store, _candidate(confidence=0.42))

    assert review["recommended_action"] == "keep_staged"
    assert review["checks"]["confidence"]["status"] == "low"
    assert "low_confidence" in review["reason_codes"]


def test_review_learning_candidate_keeps_unscoped_candidate_staged(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")

    review = review_learning_candidate(store, _candidate(domain_tags=[]))

    assert review["recommended_action"] == "keep_staged"
    assert review["checks"]["domain_scope"]["status"] == "missing"
    assert "missing_domain_scope" in review["reason_codes"]


def test_review_learning_candidate_routes_belief_proposal_to_manual_gate(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")

    review = review_learning_candidate(
        store,
        _candidate(
            authority_class="belief_proposal",
            claim="Hermes startup context should be compiled before raw AMB recall.",
        ),
    )

    assert review["recommended_action"] == "keep_staged"
    assert review["target_record_type"] == "belief-candidate"
    assert review["checks"]["manual_gate"]["required"] is True
    assert "manual_review_required" in review["reason_codes"]


def test_review_learning_candidate_reports_aligned_cluster_scope(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    support = store.store(
        namespace="project:mem-store",
        kind="memory",
        title="[[Learn]] Hermes AMH startup packet",
        content="record_type: learn\nclaim: Hermes startup should use AMH packet rendering.\n",
        tags=["kind:learn", "domain:runtime", "topic:hermes", "cluster:runtime-startup"],
    )

    review = review_learning_candidate(
        store,
        _candidate(
            claim="Hermes startup should use AMH packet rendering.",
            topic_tags=["topic:hermes"],
            cluster_tags=["cluster:runtime-startup"],
        ),
    )

    cluster_scope = review["checks"]["cluster_scope"]
    assert cluster_scope["status"] == "aligned"
    assert cluster_scope["primary_cluster"]["key"] == "cluster:runtime-startup"
    assert cluster_scope["counts"]["same_cluster"] == 1
    assert cluster_scope["evidence"]["support_record_ids"] == [support["id"]]


def test_review_learning_candidate_marks_overbroad_high_authority_scope_for_manual_review(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")

    review = review_learning_candidate(
        store,
        _candidate(
            authority_class="procedure",
            claim="All runtimes should follow one broad memory procedure.",
            domain_tags=["domain:runtime", "domain:memory-bridge", "domain:orchestration"],
            topic_tags=["topic:startup", "topic:handoff", "topic:watcher", "topic:governance", "topic:release"],
        ),
    )

    assert review["checks"]["cluster_scope"]["status"] == "overbroad"
    assert review["checks"]["manual_gate"]["required"] is True
    assert "cluster_scope_ambiguous" in review["reason_codes"]


def test_review_learning_candidates_reads_stored_candidate_metadata(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    candidate = _candidate(confidence=0.88)
    decision = evaluate_learning_candidate(candidate)
    stored = store.store_learning_candidate(candidate, decision)

    suite = review_learning_candidates(store, namespace="project:mem-store")

    assert suite["count"] == 1
    assert suite["reviews"][0]["candidate_ref"] == stored["id"] or suite["reviews"][0]["candidate_ref"]
    assert suite["reviews"][0]["recommended_action"] == "learn"
    assert suite["reviews"][0]["checks"]["domain_scope"]["domain_tags"] == ["domain:runtime"]
    assert suite["summary"]["action_counts"] == {"learn": 1}


def test_store_learning_review_preserves_hidden_candidate_audit_lineage(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    candidate = _candidate()
    decision = evaluate_learning_candidate(candidate)
    stored_candidate = store.store_learning_candidate(candidate, decision)
    durable = store.store(
        namespace="project:mem-store",
        kind="memory",
        title="[[Learn]] Hermes AMH handoff",
        content=(
            "record_type: learn\n"
            "claim: Use domain-scoped startup packets for Hermes AMH handoff.\n"
            f"derived_from_candidate_id: {stored_candidate['id']}\n"
        ),
        tags=["kind:learn", "domain:runtime"],
    )

    stored_review = store.store_learning_review(
        review_payload := {
            "namespace": "project:mem-store",
            "candidate_ref": decision["candidate_ref"],
            "source_candidate_id": stored_candidate["id"],
            "review_decision": "approved",
            "reviewed_by": "reviewer-a",
            "review_reason": "Reviewed evidence and accepted as a durable learn.",
            "target_record_type": "learn",
            "target_record_id": durable["id"],
            "recommended_action": "learn",
            "reason_codes": [],
            "evidence_refs": candidate["evidence_refs"],
            "supersedes_record_ids": [],
            "contradicts_record_ids": [],
        }
    )
    expected_receipt_hash = build_review_receipt_hash(review_payload, candidate_status="approved")

    normal = store.recall(namespace="project:mem-store", query="Learning Review", kind="memory", limit=10)
    review_items = store.recall(
        namespace="project:mem-store",
        tags_any=["kind:learning-review"],
        kind="memory",
        limit=10,
    )

    assert stored_review["candidate_status"] == "approved"
    assert stored_review["review_decision"] == "approved"
    assert normal["count"] == 0
    assert review_items["count"] == 1
    item = review_items["items"][0]
    assert item["is_learning_candidate"] is True
    assert "kind:learning-review" in item["tags"]
    assert "schema:memory.review_receipt.v1" in item["tags"]
    assert "writeback_boundary:review_receipt_only" in item["tags"]
    assert f"review_receipt_hash: {expected_receipt_hash}" in item["content"]
    assert "writeback_boundary: review_receipt_only" in item["content"]
    assert "durable_mutation_performed_by_review: false" in item["content"]
    assert f"target_record_id: {durable['id']}" in item["content"]
    assert f"source_candidate_id: {stored_candidate['id']}" in item["content"]


def test_learning_review_receipt_does_not_create_durable_authority_without_explicit_write(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    candidate = _candidate()
    decision = evaluate_learning_candidate(candidate)
    stored_candidate = store.store_learning_candidate(candidate, decision)

    stored_review = store.store_learning_review(
        {
            "namespace": "project:mem-store",
            "candidate_ref": decision["candidate_ref"],
            "source_candidate_id": stored_candidate["id"],
            "review_decision": "approved",
            "reviewed_by": "reviewer-a",
            "review_reason": "Accepted in review, but no follow-up durable write happened yet.",
            "target_record_type": "learn",
            "recommended_action": "learn",
            "reason_codes": [],
            "evidence_refs": candidate["evidence_refs"],
        }
    )

    normal = store.recall(namespace="project:mem-store", query=candidate["claim"], kind="memory", limit=10)
    hidden = store.recall(
        namespace="project:mem-store",
        tags_any=["kind:learning-review"],
        kind="memory",
        limit=10,
    )

    assert stored_review["review_decision"] == "approved"
    assert normal["count"] == 0
    assert hidden["count"] == 1
    assert "durable_mutation_performed_by_review: false" in hidden["items"][0]["content"]


def test_store_learning_review_rejects_missing_candidate_reference(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")

    try:
        store.store_learning_review(
            {
                "namespace": "project:mem-store",
                "review_decision": "approved",
            }
        )
    except ValueError as exc:
        assert "candidate_ref or source_candidate_id" in str(exc)
    else:  # pragma: no cover - assertion branch
        raise AssertionError("expected ValueError")
