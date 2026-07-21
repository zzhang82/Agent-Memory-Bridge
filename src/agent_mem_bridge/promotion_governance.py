from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from .learning_policy import REVIEW_REQUIRED_AUTHORITY_CLASSES, evaluate_learning_candidate
from .promotion import parse_structured_record
from .query import normalize_text
from .repository import LEARNING_CANDIDATE_TAG
from .structured_record import parse_structured_content

GOVERNANCE_SCHEMA = "memory.promotion_governance.v1"
MIN_PROMOTION_CONFIDENCE = 0.7

TARGET_RECORD_TYPE_BY_AUTHORITY = {
    "context_hint": "learn",
    "handoff": "learn",
    "status": "learn",
    "decision": "learn",
    "release_evidence": "learn",
    "procedure": "procedure",
    "belief_proposal": "belief-candidate",
    "persona_preference": "belief-candidate",
}


def review_learning_candidate(
    store: Any,
    candidate: Mapping[str, Any],
    *,
    related_limit: int = 8,
    backend_available: bool = True,
) -> dict[str, Any]:
    """Return a deterministic promotion-governance review for a candidate.

    The review is intentionally advisory. It can recommend learn/merge/reject/
    keep_staged, but it never writes durable memory and never promotes records.
    """

    policy_decision = evaluate_learning_candidate(candidate, backend_available=backend_available)
    namespace = str(candidate.get("namespace", "")).strip()
    claim = _compact(candidate.get("claim", ""))
    authority_class = str(candidate.get("authority_class", "")).strip()
    candidate_ref = str(policy_decision.get("candidate_ref") or _candidate_ref(candidate))
    domain_tags = candidate_domain_tags(candidate)
    topic_tags = candidate_topic_tags(candidate)
    cluster_tags = candidate_cluster_tags(candidate)
    confidence = candidate_confidence(candidate)
    related_records = _related_records(
        store,
        namespace=namespace,
        claim=claim,
        domain_tags=domain_tags,
        limit=related_limit,
    )
    duplicate_ids = _duplicate_record_ids(claim, related_records)
    contradiction_ids = _contradiction_record_ids(candidate, related_records)
    supersedes_ids = _record_id_list(candidate, "supersedes_record_ids")
    cluster_scope = assess_cluster_scope(
        domain_tags=domain_tags,
        topic_tags=topic_tags,
        cluster_tags=cluster_tags,
        related_records=related_records,
    )
    cluster_requires_manual = cluster_scope["status"] in {"fragmented", "overbroad"} and (
        authority_class in REVIEW_REQUIRED_AUTHORITY_CLASSES
    )
    manual_gate_required = (
        authority_class in REVIEW_REQUIRED_AUTHORITY_CLASSES
        or bool(candidate.get("manual_approval_required"))
        or bool(candidate.get("manual_gate_required"))
        or cluster_requires_manual
    )

    reasons: list[str] = list(policy_decision.get("reasons") or [])
    recommended_action = "keep_staged"
    durable_write_allowed = False

    if policy_decision.get("decision") in {"deny", "degraded_no_write"}:
        recommended_action = "reject"
    elif confidence < MIN_PROMOTION_CONFIDENCE:
        reasons.append("low_confidence")
    elif not domain_tags:
        reasons.append("missing_domain_scope")
    elif duplicate_ids:
        recommended_action = "merge"
        reasons.append("possible_duplicate")
    elif contradiction_ids:
        reasons.append("possible_contradiction")
    elif manual_gate_required or policy_decision.get("decision") == "needs_review":
        if cluster_requires_manual:
            reasons.append("cluster_scope_ambiguous")
        reasons.append("manual_review_required")
    else:
        recommended_action = "learn"
        durable_write_allowed = True

    reasons = _dedupe(reasons)
    return {
        "schema": GOVERNANCE_SCHEMA,
        "candidate_ref": candidate_ref,
        "recommended_action": recommended_action,
        "durable_write_allowed": durable_write_allowed,
        "target_record_type": TARGET_RECORD_TYPE_BY_AUTHORITY.get(authority_class, "learn"),
        "policy_decision": policy_decision,
        "checks": {
            "dedup": {
                "status": "match" if duplicate_ids else "clear",
                "record_ids": duplicate_ids,
            },
            "contradiction": {
                "status": "match" if contradiction_ids else "clear",
                "record_ids": contradiction_ids,
            },
            "confidence": {
                "score": confidence,
                "threshold": MIN_PROMOTION_CONFIDENCE,
                "status": "pass" if confidence >= MIN_PROMOTION_CONFIDENCE else "low",
            },
            "domain_scope": {
                "status": "present" if domain_tags else "missing",
                "domain_tags": domain_tags,
            },
            "cluster_scope": cluster_scope,
            "manual_gate": {
                "required": manual_gate_required,
                "status": "required" if manual_gate_required else "not_required",
            },
            "lineage": {
                "supersedes_record_ids": supersedes_ids,
                "status": "planned" if supersedes_ids else "none",
            },
        },
        "reason_codes": reasons,
        "related_records": _related_record_summaries(related_records),
        "writeback_boundary": "review_only",
    }


def review_learning_candidates(
    store: Any,
    *,
    namespace: str,
    limit: int = 10,
    status_tags: list[str] | None = None,
) -> dict[str, Any]:
    tags_any = status_tags or [LEARNING_CANDIDATE_TAG]
    recalled = store.recall(
        namespace=namespace,
        query="",
        kind="memory",
        tags_any=tags_any,
        limit=limit,
    )
    reviews = [
        review_learning_candidate(store, candidate_from_learning_candidate_item(item))
        for item in recalled.get("items", [])
    ]
    return {
        "schema": "memory.promotion_governance_suite.v1",
        "namespace": namespace,
        "count": len(reviews),
        "reviews": reviews,
        "summary": _review_summary(reviews),
    }


def candidate_from_learning_candidate_item(item: Mapping[str, Any]) -> dict[str, Any]:
    record = parse_structured_content(str(item.get("content") or ""))
    fields = record.as_compat_dict()
    tags = [str(tag) for tag in (item.get("tags") or [])]
    evidence_refs = list(record.values("evidence_refs"))
    domain_tags = _json_list(fields.get("domain_tags_json")) or [tag for tag in tags if tag.startswith("domain:")]
    return {
        "schema": fields.get("schema", "memory.candidate.v1"),
        "namespace": str(item.get("namespace") or ""),
        "authority_class": fields.get("authority_class", ""),
        "claim": fields.get("claim", ""),
        "evidence_refs": evidence_refs,
        "source_runtime": fields.get("source_runtime", ""),
        "source_session_id": fields.get("source_session_id", ""),
        "source_task_id": fields.get("source_task_id", ""),
        "confidence": fields.get("confidence", ""),
        "domain_tags": domain_tags,
        "topic_tags": [tag for tag in tags if tag.startswith("topic:")],
        "cluster_tags": [tag for tag in tags if tag.startswith("cluster:")],
        "supersedes_record_ids": list(record.values("supersedes")),
        "contradicts_record_ids": list(record.values("contradicts")),
        "supersession_plan": fields.get("supersession_plan", ""),
    }


def candidate_domain_tags(candidate: Mapping[str, Any]) -> list[str]:
    return _candidate_tags(candidate, keys=("domain_tags", "tags"), scalar_key="domain", prefix="domain:")


def candidate_topic_tags(candidate: Mapping[str, Any]) -> list[str]:
    return _candidate_tags(candidate, keys=("topic_tags", "tags"), scalar_key="topic", prefix="topic:")


def candidate_cluster_tags(candidate: Mapping[str, Any]) -> list[str]:
    return _candidate_tags(candidate, keys=("cluster_tags", "tags"), scalar_key="cluster", prefix="cluster:")


def assess_cluster_scope(
    *,
    domain_tags: list[str],
    topic_tags: list[str],
    cluster_tags: list[str],
    related_records: list[dict[str, Any]],
) -> dict[str, Any]:
    if not domain_tags:
        status = "missing_scope"
    elif len(domain_tags) > 2 or len(topic_tags) > 4:
        status = "overbroad"
    else:
        same_domain = _records_with_any_tag(related_records, set(domain_tags))
        same_topic = _records_with_any_tag(related_records, set(topic_tags))
        same_cluster = _records_with_any_tag(related_records, set(cluster_tags))
        competing_domains = sorted(_record_tags(related_records, "domain:").difference(domain_tags))
        if competing_domains and same_domain == 0:
            status = "fragmented"
        elif same_cluster or same_topic or same_domain:
            status = "aligned"
        else:
            status = "weak"

    counts = {
        "related_records": len(related_records),
        "same_domain": _records_with_any_tag(related_records, set(domain_tags)),
        "same_topic": _records_with_any_tag(related_records, set(topic_tags)),
        "same_cluster": _records_with_any_tag(related_records, set(cluster_tags)),
        "competing_domains": len(_record_tags(related_records, "domain:").difference(domain_tags)),
    }
    support_record_ids = [
        str(item.get("id") or "")
        for item in related_records
        if _item_has_any_tag(item, set(domain_tags + topic_tags + cluster_tags))
    ]
    score = _cluster_scope_score(status=status, counts=counts)
    return {
        "schema": "memory.cluster_governance.v1",
        "status": status,
        "score": score,
        "basis": {
            "domain_tags": domain_tags,
            "topic_tags": topic_tags,
            "cluster_tags": cluster_tags,
        },
        "primary_cluster": {
            "key": _primary_cluster_key(domain_tags=domain_tags, topic_tags=topic_tags, cluster_tags=cluster_tags),
            "domain": domain_tags[0] if domain_tags else "",
            "topic": topic_tags[0] if topic_tags else "",
            "cluster": cluster_tags[0] if cluster_tags else "",
            "confidence": "observed" if related_records else "proposed",
        },
        "counts": counts,
        "evidence": {
            "support_record_ids": [item_id for item_id in support_record_ids if item_id],
            "competing_domain_tags": sorted(_record_tags(related_records, "domain:").difference(domain_tags)),
            "related_topic_tags": sorted(_record_tags(related_records, "topic:")),
        },
        "governance_effect": {
            "mode": "manual_review_required" if status in {"fragmented", "overbroad"} else "advisory",
            "reason_codes": ["cluster_scope_ambiguous"] if status in {"fragmented", "overbroad"} else [],
        },
    }


def _candidate_tags(
    candidate: Mapping[str, Any],
    *,
    keys: tuple[str, ...],
    scalar_key: str,
    prefix: str,
) -> list[str]:
    values: list[Any] = []
    for key in keys:
        raw = candidate.get(key)
        if isinstance(raw, list):
            values.extend(raw)
    raw_scalar = candidate.get(scalar_key)
    if raw_scalar:
        values.append(raw_scalar)

    tags: list[str] = []
    seen: set[str] = set()
    for value in values:
        tag = str(value).strip()
        if not tag:
            continue
        if tag.startswith(prefix) or (tag.startswith("domain:") or tag.startswith("topic:") or tag.startswith("cluster:")):
            if not tag.startswith(prefix):
                continue
        else:
            tag = f"{prefix}{tag}"
        if tag not in seen:
            tags.append(tag)
            seen.add(tag)
    return tags


def _records_with_any_tag(records: list[dict[str, Any]], tags: set[str]) -> int:
    if not tags:
        return 0
    return sum(1 for item in records if _item_has_any_tag(item, tags))


def _item_has_any_tag(item: Mapping[str, Any], tags: set[str]) -> bool:
    if not tags:
        return False
    item_tags = {str(tag) for tag in (item.get("tags") or [])}
    return bool(item_tags.intersection(tags))


def _record_tags(records: list[dict[str, Any]], prefix: str) -> set[str]:
    tags: set[str] = set()
    for item in records:
        for tag in item.get("tags") or []:
            normalized = str(tag)
            if normalized.startswith(prefix):
                tags.add(normalized)
    return tags


def _cluster_scope_score(*, status: str, counts: Mapping[str, int]) -> float:
    base = {
        "aligned": 0.85,
        "weak": 0.45,
        "missing_scope": 0.0,
        "fragmented": 0.25,
        "overbroad": 0.2,
    }.get(status, 0.0)
    support_bonus = min(float(counts.get("same_topic", 0)) * 0.03, 0.09)
    cluster_bonus = min(float(counts.get("same_cluster", 0)) * 0.04, 0.12)
    return round(min(base + support_bonus + cluster_bonus, 1.0), 3)


def _primary_cluster_key(*, domain_tags: list[str], topic_tags: list[str], cluster_tags: list[str]) -> str:
    if cluster_tags:
        return cluster_tags[0]
    parts = []
    if domain_tags:
        parts.append(domain_tags[0])
    if topic_tags:
        parts.append(topic_tags[0])
    return "/".join(parts)


def candidate_confidence(candidate: Mapping[str, Any]) -> float:
    value = candidate.get("confidence")
    if value is None:
        value = candidate.get("confidence_score")
    try:
        score = float(value)
    except (TypeError, ValueError):
        band = str(candidate.get("confidence_band") or value or "").strip().lower()
        score = {
            "high": 0.85,
            "strong": 0.85,
            "medium": 0.7,
            "tentative": 0.55,
            "low": 0.35,
        }.get(band, 0.7)
    return max(0.0, min(score, 1.0))


def _related_records(
    store: Any,
    *,
    namespace: str,
    claim: str,
    domain_tags: list[str],
    limit: int,
) -> list[dict[str, Any]]:
    if not namespace or not claim:
        return []
    query = claim[:240]
    recalled = store.recall(
        namespace=namespace,
        query=query,
        kind="memory",
        tags_any=domain_tags or None,
        limit=max(1, min(limit, 25)),
    )
    return [item for item in recalled.get("items", []) if not item.get("is_learning_candidate")]


def _duplicate_record_ids(claim: str, related_records: list[dict[str, Any]]) -> list[str]:
    normalized_claim = normalize_text(claim)
    if not normalized_claim:
        return []
    matches: list[str] = []
    for item in related_records:
        fields = parse_structured_record(str(item.get("content") or ""))
        item_claim = normalize_text(fields.get("claim") or str(item.get("title") or item.get("content") or ""))
        if item_claim == normalized_claim:
            matches.append(str(item["id"]))
    return matches


def _contradiction_record_ids(candidate: Mapping[str, Any], related_records: list[dict[str, Any]]) -> list[str]:
    explicit = _record_id_list(candidate, "contradicts_record_ids")
    matches = list(explicit)
    support_ids = set(_record_id_list(candidate, "support_record_ids"))
    supersedes_ids = set(_record_id_list(candidate, "supersedes_record_ids"))
    for item in related_records:
        item_id = str(item.get("id") or "")
        relations = item.get("relations") or {}
        targets: list[str] = []
        if isinstance(relations, Mapping):
            raw_targets = relations.get("contradicts") or []
            if isinstance(raw_targets, list):
                targets = [str(target) for target in raw_targets]
        if item_id in explicit:
            continue
        if item_id and (item_id in support_ids or item_id in supersedes_ids):
            continue
        if targets and (support_ids.intersection(targets) or supersedes_ids.intersection(targets)):
            matches.append(item_id)
    return _dedupe(matches)


def _record_id_list(candidate: Mapping[str, Any], key: str) -> list[str]:
    raw = candidate.get(key)
    if isinstance(raw, list):
        return _dedupe([str(item).strip() for item in raw if str(item).strip()])
    if isinstance(raw, str) and raw.strip():
        return _dedupe([part.strip() for part in raw.split("|") if part.strip()])
    return []


def _related_record_summaries(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": str(item.get("id") or ""),
            "title": item.get("title"),
            "tags": item.get("tags") or [],
            "relations": item.get("relations") or {},
        }
        for item in records
    ]


def _review_summary(reviews: list[dict[str, Any]]) -> dict[str, Any]:
    action_counts: dict[str, int] = {}
    reason_counts: dict[str, int] = {}
    for review in reviews:
        action = str(review.get("recommended_action") or "unknown")
        action_counts[action] = action_counts.get(action, 0) + 1
        for reason in review.get("reason_codes") or []:
            reason_text = str(reason)
            reason_counts[reason_text] = reason_counts.get(reason_text, 0) + 1
    return {
        "action_counts": dict(sorted(action_counts.items())),
        "reason_counts": dict(sorted(reason_counts.items())),
    }


def _candidate_ref(candidate: Mapping[str, Any]) -> str:
    namespace = str(candidate.get("namespace") or "unknown-namespace").strip()
    session = str(candidate.get("source_session_id") or "unknown-session").strip()
    task = str(candidate.get("source_task_id") or candidate.get("source_turn_id") or "unknown-task").strip()
    return f"{namespace}:{session}:{task}"


def _json_list(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if str(item).strip()]


def _compact(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result
