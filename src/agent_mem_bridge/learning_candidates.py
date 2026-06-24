from __future__ import annotations

import json
import hashlib
from collections.abc import Mapping
from typing import Any

from .learning_policy import evaluate_learning_candidate
from .repository import LEARNING_CANDIDATE_TAG

VALID_CANDIDATE_STATUSES = {"pending", "needs_review", "approved", "rejected", "expired"}
STORABLE_DECISIONS = {"allow", "needs_review"}
DECISION_MATCH_FIELDS = ("schema", "candidate_ref", "decision", "would_write", "reasons")
VALID_REVIEW_DECISIONS = {"approved", "rejected", "merged", "kept_staged", "expired"}
REVIEW_RECEIPT_SCHEMA = "memory.review_receipt.v1"


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
    tags.extend(_candidate_domain_tags(candidate))
    tags.extend(_prefixed_list(candidate.get("topic_tags"), prefix="topic:"))

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


def store_learning_review(
    store: Any,
    review: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(review, Mapping):
        raise ValueError("review must be a mapping")
    namespace = str(review.get("namespace", "")).strip()
    if not namespace:
        raise ValueError("review namespace must not be empty")
    candidate_ref = str(review.get("candidate_ref", "")).strip()
    source_candidate_id = str(review.get("source_candidate_id", "")).strip()
    if not candidate_ref and not source_candidate_id:
        raise ValueError("review requires candidate_ref or source_candidate_id")
    review_decision = str(review.get("review_decision", "")).strip().lower()
    if review_decision not in VALID_REVIEW_DECISIONS:
        raise ValueError(f"review_decision must be one of {sorted(VALID_REVIEW_DECISIONS)}")

    candidate_status = _candidate_status_for_review(review_decision)
    target_record_id = str(review.get("target_record_id", "")).strip()
    title_ref = truncate(candidate_ref or source_candidate_id, limit=72)
    content = build_learning_review_record(review, candidate_status=candidate_status)
    tags = [
        LEARNING_CANDIDATE_TAG,
        "kind:learning-review",
        f"candidate_status:{candidate_status}",
        f"review_decision:{review_decision}",
        "schema:memory.learning_review.v1",
        f"schema:{REVIEW_RECEIPT_SCHEMA}",
        "writeback_boundary:review_receipt_only",
    ]
    target_record_type = str(review.get("target_record_type", "")).strip()
    if target_record_type:
        tags.append(f"target_record_type:{target_record_type}")

    payload = store.store(
        namespace=namespace,
        kind="memory",
        title=f"[[Learning Review]] {title_ref}",
        content=content,
        tags=tags,
        session_id=str(review.get("review_session_id") or "") or None,
        actor=str(review.get("reviewed_by") or "") or None,
        correlation_id=candidate_ref or source_candidate_id,
        source_app="amb-learning-layer",
        source_client=str(review.get("review_runtime") or "") or None,
    )
    payload["candidate_status"] = candidate_status
    payload["review_decision"] = review_decision
    payload["target_record_id"] = target_record_id or None
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
        "domain_tags_json": json.dumps(_candidate_domain_tags(candidate), ensure_ascii=True, sort_keys=True),
        "confidence": str(candidate.get("confidence", candidate.get("confidence_score", candidate.get("confidence_band", "")))),
        "supersedes_record_ids_json": json.dumps(
            _list_value(candidate.get("supersedes_record_ids")),
            ensure_ascii=True,
            sort_keys=True,
        ),
        "contradicts_record_ids_json": json.dumps(
            _list_value(candidate.get("contradicts_record_ids")),
            ensure_ascii=True,
            sort_keys=True,
        ),
        "supersession_plan": str(candidate.get("supersession_plan", "")),
    }
    return "\n".join(f"{key}: {value}" for key, value in fields.items() if str(value).strip())


def build_learning_review_record(review: Mapping[str, Any], *, candidate_status: str) -> str:
    receipt_hash = build_review_receipt_hash(review, candidate_status=candidate_status)
    fields = {
        "record_type": "learning-review",
        "schema": "memory.learning_review.v1",
        "review_receipt_schema": REVIEW_RECEIPT_SCHEMA,
        "candidate_status": candidate_status,
        "candidate_ref": str(review.get("candidate_ref", "")),
        "source_candidate_id": str(review.get("source_candidate_id", "")),
        "review_decision": str(review.get("review_decision", "")).strip().lower(),
        "review_receipt_hash": receipt_hash,
        "writeback_boundary": "review_receipt_only",
        "durable_mutation_performed_by_review": "false",
        "reviewed_by": str(review.get("reviewed_by", "")),
        "review_reason": " ".join(str(review.get("review_reason", "")).split()).strip(),
        "target_record_type": str(review.get("target_record_type", "")),
        "target_record_id": str(review.get("target_record_id", "")),
        "recommended_action": str(review.get("recommended_action", "")),
        "reason_codes_json": json.dumps(_list_value(review.get("reason_codes")), ensure_ascii=True, sort_keys=True),
        "evidence_refs_json": json.dumps(_list_value(review.get("evidence_refs")), ensure_ascii=True, sort_keys=True),
        "supersedes_record_ids_json": json.dumps(
            _list_value(review.get("supersedes_record_ids")),
            ensure_ascii=True,
            sort_keys=True,
        ),
        "contradicts_record_ids_json": json.dumps(
            _list_value(review.get("contradicts_record_ids")),
            ensure_ascii=True,
            sort_keys=True,
        ),
    }
    return "\n".join(f"{key}: {value}" for key, value in fields.items() if str(value).strip())


def build_review_receipt_hash(review: Mapping[str, Any], *, candidate_status: str) -> str:
    stable_payload = {
        "schema": REVIEW_RECEIPT_SCHEMA,
        "candidate_status": candidate_status,
        "candidate_ref": str(review.get("candidate_ref", "")).strip(),
        "source_candidate_id": str(review.get("source_candidate_id", "")).strip(),
        "review_decision": str(review.get("review_decision", "")).strip().lower(),
        "target_record_type": str(review.get("target_record_type", "")).strip(),
        "target_record_id": str(review.get("target_record_id", "")).strip(),
        "recommended_action": str(review.get("recommended_action", "")).strip(),
        "reason_codes": _list_value(review.get("reason_codes")),
        "evidence_refs": _list_value(review.get("evidence_refs")),
        "supersedes_record_ids": _list_value(review.get("supersedes_record_ids")),
        "contradicts_record_ids": _list_value(review.get("contradicts_record_ids")),
        "writeback_boundary": "review_receipt_only",
    }
    encoded = json.dumps(stable_payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _decision_matches_expected(decision: Mapping[str, Any], expected_decision: Mapping[str, Any]) -> bool:
    return all(decision.get(field) == expected_decision.get(field) for field in DECISION_MATCH_FIELDS)


def truncate(text: str, *, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _candidate_status_for_review(review_decision: str) -> str:
    if review_decision == "approved":
        return "approved"
    if review_decision in {"rejected", "merged", "kept_staged"}:
        return "rejected" if review_decision == "rejected" else "needs_review"
    if review_decision == "expired":
        return "expired"
    return "needs_review"


def _list_value(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [part.strip() for part in value.split("|") if part.strip()]
    return []


def _prefixed_list(value: Any, *, prefix: str) -> list[str]:
    tags: list[str] = []
    seen: set[str] = set()
    for item in _list_value(value):
        tag = item if item.startswith(prefix) else f"{prefix}{item}"
        if tag not in seen:
            tags.append(tag)
            seen.add(tag)
    return tags


def _candidate_domain_tags(candidate: Mapping[str, Any]) -> list[str]:
    values: list[Any] = []
    for key in ("domain_tags", "tags"):
        raw = candidate.get(key)
        if isinstance(raw, list):
            values.extend(raw)
    raw_domain = candidate.get("domain")
    if raw_domain:
        values.append(raw_domain)

    tags: list[str] = []
    seen: set[str] = set()
    for value in values:
        tag = str(value).strip()
        if not tag:
            continue
        if not tag.startswith("domain:"):
            tag = f"domain:{tag}"
        if tag not in seen:
            tags.append(tag)
            seen.add(tag)
    return tags
