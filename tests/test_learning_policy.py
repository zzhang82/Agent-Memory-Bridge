from __future__ import annotations

from agent_mem_bridge.learning_policy import evaluate_learning_candidate


def _candidate(**overrides):
    candidate = {
        "schema": "memory.candidate.v1",
        "namespace": "project:mem-store",
        "authority_class": "context_hint",
        "claim": "AMB should govern learning candidates before durable writeback.",
        "evidence_refs": ["tool:harness verify"],
        "source_runtime": "hermes",
        "source_session_id": "session-1",
        "source_task_id": "task-1",
        "sensitivity": "safe",
        "created_by": "cole",
    }
    candidate.update(overrides)
    return candidate


def test_policy_allows_safe_context_hint_without_claiming_write() -> None:
    decision = evaluate_learning_candidate(_candidate())

    assert decision["schema"] == "memory.writeback_decision.v1"
    assert decision["decision"] == "allow"
    assert decision["would_write"] is True
    assert "durable_write_performed" not in decision
    assert decision["candidate_ref"] == "project:mem-store:session-1:task-1"


def test_policy_denies_malformed_schema() -> None:
    decision = evaluate_learning_candidate(_candidate(schema="memory_candidate_v1"))

    assert decision["decision"] == "deny"
    assert decision["would_write"] is False
    assert "invalid_schema" in decision["reasons"]


def test_policy_denies_missing_namespace_without_write() -> None:
    decision = evaluate_learning_candidate(_candidate(namespace="  "))

    assert decision["decision"] == "deny"
    assert decision["would_write"] is False
    assert "missing_namespace" in decision["reasons"]


def test_policy_denies_secret_like_candidate() -> None:
    decision = evaluate_learning_candidate(
        _candidate(claim="Store API key sk-1234567890abcdef for the deployment bot.")
    )

    assert decision["decision"] == "deny"
    assert decision["would_write"] is False
    assert "sensitive_content" in decision["reasons"]


def test_policy_denies_raw_transcript_candidate() -> None:
    decision = evaluate_learning_candidate(
        _candidate(
            claim=(
                "User: please remember this whole chat\n"
                "Assistant: sure, I will copy the entire transcript into durable memory"
            )
        )
    )

    assert decision["decision"] == "deny"
    assert decision["would_write"] is False
    assert "raw_transcript" in decision["reasons"]


def test_policy_denies_release_evidence_without_artifact_reference() -> None:
    decision = evaluate_learning_candidate(
        _candidate(
            authority_class="release_evidence",
            claim="Release 0.14.0 passed all tests and is ready.",
            evidence_refs=["operator note"],
        )
    )

    assert decision["decision"] == "deny"
    assert decision["would_write"] is False
    assert "missing_release_evidence" in decision["reasons"]


def test_policy_routes_decision_to_needs_review() -> None:
    decision = evaluate_learning_candidate(
        _candidate(
            authority_class="decision",
            claim="Keep AMB as governed durable learning rather than a runtime memory backend.",
        )
    )

    assert decision["decision"] == "needs_review"
    assert decision["would_write"] is False
    assert "review_required" in decision["reasons"]


def test_policy_routes_duplicate_to_needs_review() -> None:
    decision = evaluate_learning_candidate(
        _candidate(related_records=["mem-existing-1"]),
        duplicate_record_ids=["mem-existing-1"],
    )

    assert decision["decision"] == "needs_review"
    assert decision["would_write"] is False
    assert "possible_duplicate" in decision["reasons"]


def test_policy_denies_contradiction_without_supersession_plan() -> None:
    decision = evaluate_learning_candidate(
        _candidate(contradicts_record_ids=["mem-old-1"]),
    )

    assert decision["decision"] == "deny"
    assert decision["would_write"] is False
    assert "missing_supersession_plan" in decision["reasons"]


def test_policy_allows_contradiction_with_supersession_plan_for_review() -> None:
    decision = evaluate_learning_candidate(
        _candidate(
            authority_class="belief_proposal",
            contradicts_record_ids=["mem-old-1"],
            supersession_plan="Replace stale runtime-memory framing after operator approval.",
        ),
    )

    assert decision["decision"] == "needs_review"
    assert decision["would_write"] is False
    assert "review_required" in decision["reasons"]
    assert "missing_supersession_plan" not in decision["reasons"]


def test_policy_degrades_when_backend_unavailable_and_surfaces_sanitized_unsaved_candidate() -> None:
    candidate = _candidate(
        claim="Never surface token ghp_1234567890abcdef1234567890abcdef123456.",
        evidence_refs=["Bearer eyJhbGciOiJIUzI1NiJ9.secret.payload"],
    )

    decision = evaluate_learning_candidate(candidate, backend_available=False)

    assert decision["decision"] == "degraded_no_write"
    assert decision["would_write"] is False
    assert decision["unsaved_candidate"]["claim"] == "[REDACTED]"
    assert decision["unsaved_candidate"]["evidence_refs"] == ["[REDACTED]"]
    assert "amb_unavailable" in decision["reasons"]


def test_policy_denies_secret_in_evidence_refs() -> None:
    decision = evaluate_learning_candidate(
        _candidate(evidence_refs=["tool output contained password=hunter2"])
    )

    assert decision["decision"] == "deny"
    assert decision["would_write"] is False
    assert "sensitive_content" in decision["reasons"]


def test_policy_denies_raw_transcript_in_evidence_refs() -> None:
    decision = evaluate_learning_candidate(
        _candidate(evidence_refs=["User: copy this\nAssistant: pasted transcript"])
    )

    assert decision["decision"] == "deny"
    assert decision["would_write"] is False
    assert "raw_transcript" in decision["reasons"]


def test_policy_normalizes_candidate_ref_for_audit_safety() -> None:
    decision = evaluate_learning_candidate(
        _candidate(
            namespace="project:mem-store\nBAD",
            source_session_id="session 1\nBAD",
            source_task_id="task/1:BAD",
        )
    )

    assert decision["candidate_ref"] == "project:mem-store_BAD:session-1_BAD:task_1_BAD"


def test_policy_denies_non_mapping_candidate_without_crashing() -> None:
    decision = evaluate_learning_candidate(None)  # type: ignore[arg-type]

    assert decision["decision"] == "deny"
    assert decision["would_write"] is False
    assert "invalid_candidate" in decision["reasons"]


def test_policy_denies_missing_evidence_refs() -> None:
    decision = evaluate_learning_candidate(_candidate(evidence_refs=()))

    assert decision["decision"] == "deny"
    assert decision["would_write"] is False
    assert "missing_evidence_refs" in decision["reasons"]


def test_policy_denies_sensitive_excluded_authority_class() -> None:
    decision = evaluate_learning_candidate(_candidate(authority_class="sensitive_excluded"))

    assert decision["decision"] == "deny"
    assert decision["would_write"] is False
    assert "sensitive_content" in decision["reasons"]


def test_policy_routes_valid_release_evidence_to_review() -> None:
    decision = evaluate_learning_candidate(
        _candidate(
            authority_class="release_evidence",
            claim="Release candidate passed the targeted test gate.",
            evidence_refs=["pytest: tests/test_learning_policy.py 11 passed"],
        )
    )

    assert decision["decision"] == "needs_review"
    assert decision["would_write"] is False
    assert "review_required" in decision["reasons"]
