from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from .learning_policy import evaluate_learning_candidate
from .repository import LEARNING_CANDIDATE_TAG

VALID_CANDIDATE_STATUSES = {"pending", "needs_review", "approved", "rejected", "expired"}
STORABLE_DECISIONS = {"allow", "needs_review"}
DECISION_MATCH_FIELDS = ("schema", "candidate_ref", "decision", "would_write", "reasons")


def store_learning_candidate(
    store: Any,
    candidate: Mapping[str, Any],
    decision: Mapping[str, Any],
    *,
    candidate_status: str = "pending",
) -> dict[str, Any]:
    status = candidate_status.strip().lower()
    if status not in VALID_CANDIDATE_STATUSES:
        raise ValueError(f"candidate_status must be one of {sorted(VALID_CANDIDATE_STATUSES)}")
    if not isinstance(candidate, Mapping):
        raise ValueError("candidate must be a mapping")
    if not isinstance(decision, Mapping):
        raise ValueError("decision must be a mapping")

    expected_decision = evaluate_learning_candidate(candidate)
    if not _decision_matches_expected(decision, expected_decision):
        raise ValueError("decision does not match policy evaluation for candidate")

    decision_value = str(expected_decision.get("decision", "unknown")).strip() or "unknown"
    if decision_value not in STORABLE_DECISIONS:
        raise ValueError("only allow or needs_review decisions can be stored as learning candidates")
    decision = expected_decision
    if status == "approved" and decision_value != "allow":
        raise ValueError("approved requires allow decision")
    if status == "needs_review" and decision_value != "needs_review":
        raise ValueError("needs_review status requires a needs_review decision")
    if decision_value == "allow" and status not in {"pending", "approved"}:
        raise ValueError("allow decision can only be stored with pending or approved status")
    if decision_value == "needs_review" and status != "needs_review":
        raise ValueError("needs_review decision requires needs_review status")

    namespace = str(candidate.get("namespace", "")).strip()
    if not namespace:
        raise ValueError("candidate namespace must not be empty")

    authority_class = str(candidate.get("authority_class", "unknown")).strip() or "unknown"
    source_runtime = str(candidate.get("source_runtime", "unknown")).strip() or "unknown"
    candidate_ref = str(decision.get("candidate_ref", "unknown-candidate")).strip() or "unknown-candidate"
    claim = " ".join(str(candidate.get("claim", "")).split()).strip()
    title_claim = truncate(claim or candidate_ref, limit=72)
    content = build_learning_candidate_record(candidate, decision, candidate_status=status)
    tags = [
        LEARNING_CANDIDATE_TAG,
        f"candidate_status:{status}",
        f"source_runtime:{source_runtime}",
        f"authority_class:{authority_class}",
        f"decision:{decision_value}",
        "schema:memory.candidate.v1",
        "schema:memory.writeback_decision.v1",
    ]

    payload = store.store(
        namespace=namespace,
        kind="memory",
        title=f"[[Learning Candidate]] {title_claim}",
        content=content,
        tags=tags,
        session_id=str(candidate.get("source_session_id") or "") or None,
        actor=str(candidate.get("created_by") or "") or None,
        correlation_id=candidate_ref,
        source_app="amb-learning-layer",
        source_client=source_runtime,
    )
    payload["candidate_status"] = status
    payload["decision"] = decision_value
    return payload


def build_learning_candidate_record(
    candidate: Mapping[str, Any],
    decision: Mapping[str, Any],
    *,
    candidate_status: str,
) -> str:
    fields = {
        "record_type": "learning-candidate",
        "schema": "memory.candidate.v1",
        "decision_schema": "memory.writeback_decision.v1",
        "candidate_status": candidate_status,
        "candidate_ref": str(decision.get("candidate_ref", "")),
        "decision": str(decision.get("decision", "")),
        "would_write": str(bool(decision.get("would_write"))).lower(),
        "authority_class": str(candidate.get("authority_class", "")),
        "source_runtime": str(candidate.get("source_runtime", "")),
        "source_session_id": str(candidate.get("source_session_id", "")),
        "source_task_id": str(candidate.get("source_task_id", candidate.get("source_turn_id", ""))),
        "claim": " ".join(str(candidate.get("claim", "")).split()).strip(),
        "evidence_refs_json": json.dumps(candidate.get("evidence_refs", []), ensure_ascii=True, sort_keys=True),
        "decision_reasons_json": json.dumps(decision.get("reasons", []), ensure_ascii=True, sort_keys=True),
    }
    return "\n".join(f"{key}: {value}" for key, value in fields.items() if str(value).strip())


def _decision_matches_expected(decision: Mapping[str, Any], expected_decision: Mapping[str, Any]) -> bool:
    return all(decision.get(field) == expected_decision.get(field) for field in DECISION_MATCH_FIELDS)


def truncate(text: str, *, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."
