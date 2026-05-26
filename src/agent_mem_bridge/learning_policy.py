from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

CANDIDATE_SCHEMA = "memory.candidate.v1"
WRITEBACK_DECISION_SCHEMA = "memory.writeback_decision.v1"

REVIEW_REQUIRED_AUTHORITY_CLASSES = {
    "belief_proposal",
    "decision",
    "procedure",
    "release_evidence",
}

ALLOWED_AUTHORITY_CLASSES = {
    "context_hint",
    "handoff",
    "status",
    "procedure",
    "decision",
    "belief_proposal",
    "release_evidence",
    "persona_preference",
    "sensitive_excluded",
}

RELEASE_EVIDENCE_MARKERS = (
    "git:",
    "git ",
    "commit:",
    "sha:",
    "test:",
    "pytest",
    "version:",
    "artifact:",
    "release:",
)

SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\bghp_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._=-]{12,}\b", re.IGNORECASE),
    re.compile(r"\b(api[_ -]?key|token|password|passwd|secret|private[_ -]?key)\b", re.IGNORECASE),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)

AUDIT_REF_PATTERN = re.compile(r"[^A-Za-z0-9_.-]+")
AUDIT_REF_PATTERN_WITH_COLON = re.compile(r"[^A-Za-z0-9_.:-]+")

RAW_TRANSCRIPT_PATTERNS = (
    re.compile(r"(^|\n)\s*user\s*:", re.IGNORECASE),
    re.compile(r"(^|\n)\s*assistant\s*:", re.IGNORECASE),
    re.compile(r"(^|\n)\s*tool\s*:", re.IGNORECASE),
)


def evaluate_learning_candidate(
    candidate: Mapping[str, Any],
    *,
    backend_available: bool = True,
    duplicate_record_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Return a v0 AMB learning-layer writeback policy decision.

    The decision is intentionally pre-write only. It never performs durable
    mutation and never claims that a durable write happened.
    """

    if not isinstance(candidate, Mapping):
        return _decision(
            candidate_ref="unknown-namespace:unknown-session:unknown-task",
            decision="deny",
            would_write=False,
            reasons=["invalid_candidate"],
        )

    candidate_ref = _candidate_ref(candidate)
    if not backend_available:
        return _decision(
            candidate_ref=candidate_ref,
            decision="degraded_no_write",
            would_write=False,
            reasons=["amb_unavailable"],
            unsaved_candidate=_sanitize_candidate_for_surface(candidate),
        )

    reasons: list[str] = []
    schema = str(candidate.get("schema", "")).strip()
    namespace = str(candidate.get("namespace", "")).strip()
    authority_class = str(candidate.get("authority_class", "")).strip()
    claim = str(candidate.get("claim", ""))
    evidence_refs = candidate.get("evidence_refs")
    sensitivity = str(candidate.get("sensitivity", "")).strip().lower()

    if schema != CANDIDATE_SCHEMA:
        reasons.append("invalid_schema")
    if not namespace:
        reasons.append("missing_namespace")
    if authority_class not in ALLOWED_AUTHORITY_CLASSES:
        reasons.append("invalid_authority_class")
    if not claim.strip():
        reasons.append("missing_claim")
    if not _has_evidence_refs(evidence_refs):
        reasons.append("missing_evidence_refs")
    if authority_class == "sensitive_excluded" or sensitivity in {"secret", "credential", "sensitive_excluded"}:
        reasons.append("sensitive_content")
    scan_text = _candidate_scan_text(candidate)
    if _looks_sensitive(scan_text):
        reasons.append("sensitive_content")
    if _looks_like_raw_transcript(scan_text):
        reasons.append("raw_transcript")
    if authority_class == "release_evidence" and not _has_release_evidence(evidence_refs):
        reasons.append("missing_release_evidence")
    if candidate.get("contradicts_record_ids") and not str(candidate.get("supersession_plan", "")).strip():
        reasons.append("missing_supersession_plan")

    deny_reasons = [
        reason
        for reason in reasons
        if reason
        in {
            "invalid_schema",
            "missing_namespace",
            "invalid_authority_class",
            "missing_claim",
            "missing_evidence_refs",
            "sensitive_content",
            "raw_transcript",
            "missing_release_evidence",
            "missing_supersession_plan",
        }
    ]
    if deny_reasons:
        return _decision(candidate_ref=candidate_ref, decision="deny", would_write=False, reasons=_dedupe(deny_reasons))

    review_reasons: list[str] = []
    if duplicate_record_ids:
        review_reasons.append("possible_duplicate")
    if authority_class in REVIEW_REQUIRED_AUTHORITY_CLASSES:
        review_reasons.append("review_required")
    if candidate.get("contradicts_record_ids"):
        review_reasons.append("review_required")

    if review_reasons:
        return _decision(
            candidate_ref=candidate_ref,
            decision="needs_review",
            would_write=False,
            reasons=_dedupe(review_reasons),
        )

    return _decision(candidate_ref=candidate_ref, decision="allow", would_write=True, reasons=[])


def _decision(
    *,
    candidate_ref: str,
    decision: str,
    would_write: bool,
    reasons: list[str],
    unsaved_candidate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema": WRITEBACK_DECISION_SCHEMA,
        "candidate_ref": candidate_ref,
        "decision": decision,
        "would_write": would_write,
        "reasons": reasons,
    }
    if unsaved_candidate is not None:
        payload["unsaved_candidate"] = unsaved_candidate
    return payload


def _candidate_ref(candidate: Mapping[str, Any]) -> str:
    namespace = _safe_ref_part(candidate.get("namespace"), "unknown-namespace", allow_colon=True)
    session = _safe_ref_part(candidate.get("source_session_id"), "unknown-session")
    task = _safe_ref_part(candidate.get("source_task_id") or candidate.get("source_turn_id"), "unknown-task")
    return f"{namespace}:{session}:{task}"


def _safe_ref_part(value: Any, fallback: str, *, allow_colon: bool = False) -> str:
    text = str(value or "").strip() or fallback
    text = re.sub(r"[ \t]+", "-", text)
    pattern = AUDIT_REF_PATTERN_WITH_COLON if allow_colon else AUDIT_REF_PATTERN
    text = pattern.sub("_", text)
    return text[:120] or fallback


def _candidate_scan_text(candidate: Mapping[str, Any]) -> str:
    fields: list[str] = []
    for key in ("claim", "evidence_refs", "supersession_plan", "namespace", "source_session_id", "source_task_id", "source_turn_id"):
        value = candidate.get(key)
        if isinstance(value, list):
            fields.extend(str(item) for item in value)
        elif value is not None:
            fields.append(str(value))
    return "\n".join(fields)


def _sanitize_candidate_for_surface(candidate: Mapping[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key, value in candidate.items():
        if isinstance(value, str):
            sanitized[key] = "[REDACTED]" if _looks_sensitive(value) or _looks_like_raw_transcript(value) else value
        elif isinstance(value, list):
            sanitized[key] = [
                "[REDACTED]" if _looks_sensitive(str(item)) or _looks_like_raw_transcript(str(item)) else item
                for item in value
            ]
        else:
            sanitized[key] = value
    return sanitized


def _has_evidence_refs(value: Any) -> bool:
    return isinstance(value, list) and any(str(item).strip() for item in value)


def _has_release_evidence(value: Any) -> bool:
    if not _has_evidence_refs(value):
        return False
    joined = "\n".join(str(item).strip().lower() for item in value)
    return any(marker in joined for marker in RELEASE_EVIDENCE_MARKERS)


def _looks_sensitive(text: str) -> bool:
    return any(pattern.search(text) for pattern in SECRET_PATTERNS)


def _looks_like_raw_transcript(text: str) -> bool:
    matches = sum(1 for pattern in RAW_TRANSCRIPT_PATTERNS if pattern.search(text))
    return matches >= 2


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result
