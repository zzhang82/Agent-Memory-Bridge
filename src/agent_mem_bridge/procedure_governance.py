from __future__ import annotations

from typing import Any

from .relation_metadata import parse_content_fields


PROCEDURE_STATUS_FIELD_NAMES = (
    "procedure_status",
    "governance_status",
    "lifecycle_status",
    "status",
)
PROCEDURE_STATUS_ALIASES = {
    "": "unspecified",
    "active": "validated",
    "approved": "validated",
    "current": "validated",
    "ok": "validated",
    "ready": "validated",
    "safe": "validated",
    "valid": "validated",
    "verified": "validated",
    "validated": "validated",
    "candidate": "draft",
    "draft": "draft",
    "experimental": "draft",
    "review": "draft",
    "stale": "stale",
    "old": "stale",
    "deprecated": "stale",
    "obsolete": "stale",
    "retired": "replaced",
    "replaced": "replaced",
    "superseded": "replaced",
    "unsafe": "unsafe",
    "blocked": "unsafe",
    "invalid": "unsafe",
}
ELIGIBLE_PROCEDURE_STATUSES = {"validated", "draft", "unspecified"}
INELIGIBLE_PROCEDURE_STATUSES = {"stale", "replaced", "unsafe"}
RECOMMENDED_PROCEDURE_FIELDS = (
    "goal",
    "when_to_use",
    "when_not_to_use",
    "prerequisites",
    "steps",
    "failure_mode",
    "rollback_path",
)
MINIMUM_PROCEDURE_FIELDS = ("goal", "when_to_use", "steps")


def parse_procedure_artifact(content: str) -> dict[str, Any]:
    fields = parse_content_fields(content)
    status_raw = _first_field_value(fields, PROCEDURE_STATUS_FIELD_NAMES)
    status = normalize_procedure_status(status_raw)
    steps = _split_pipe_list(fields.get("steps", "") or fields.get("checklist", ""))
    prerequisites = _split_pipe_list(fields.get("prerequisites", "") or fields.get("requires", ""))
    procedure = {
        "goal": fields.get("goal", ""),
        "when_to_use": fields.get("when_to_use", "") or fields.get("applies_when", ""),
        "when_not_to_use": fields.get("when_not_to_use", ""),
        "prerequisites": prerequisites,
        "steps": steps,
        "failure_mode": fields.get("failure_mode", ""),
        "rollback_path": fields.get("rollback_path", "") or fields.get("rollback", ""),
    }
    missing_recommended = [
        field_name
        for field_name in RECOMMENDED_PROCEDURE_FIELDS
        if not _procedure_field_present(procedure, field_name)
    ]
    missing_minimum = [
        field_name
        for field_name in MINIMUM_PROCEDURE_FIELDS
        if not _procedure_field_present(procedure, field_name)
    ]
    warnings = _procedure_warnings(
        content=content,
        status=status,
        steps=steps,
        missing_minimum=missing_minimum,
    )
    return {
        **procedure,
        "governance": {
            "status": status,
            "raw_status": status_raw,
            "eligible": status in ELIGIBLE_PROCEDURE_STATUSES,
            "missing_recommended_fields": missing_recommended,
            "missing_minimum_fields": missing_minimum,
            "warnings": warnings,
        },
    }


def procedure_governance_status(item: dict[str, Any]) -> str:
    procedure = parse_procedure_artifact(str(item.get("content") or ""))
    return str(procedure["governance"]["status"])


def procedure_score_adjustment(item: dict[str, Any]) -> float:
    status = procedure_governance_status(item)
    if status == "validated":
        return 14.0
    if status == "draft":
        return -18.0
    if status == "unspecified":
        return 0.0
    return -100.0


def normalize_procedure_status(value: str | None) -> str:
    normalized = " ".join(str(value or "").lower().replace("-", "_").split()).strip()
    normalized = normalized.replace(" ", "_")
    return PROCEDURE_STATUS_ALIASES.get(normalized, "unspecified")


def _first_field_value(fields: dict[str, str], names: tuple[str, ...]) -> str:
    for name in names:
        value = fields.get(name)
        if value:
            return value
    return ""


def _procedure_field_present(procedure: dict[str, Any], field_name: str) -> bool:
    value = procedure.get(field_name)
    if isinstance(value, list):
        return bool(value)
    return bool(str(value or "").strip())


def _procedure_warnings(
    *,
    content: str,
    status: str,
    steps: list[str],
    missing_minimum: list[str],
) -> list[str]:
    warnings: list[str] = []
    if status == "unspecified":
        warnings.append("missing-procedure-status")
    if status == "draft":
        warnings.append("draft-procedure")
    if status in INELIGIBLE_PROCEDURE_STATUSES:
        warnings.append(f"ineligible-procedure-status:{status}")
    if missing_minimum:
        warnings.append("missing-minimum-fields")
    if len(steps) > 12:
        warnings.append("too-many-steps")
    if any(len(step) > 180 for step in steps):
        warnings.append("long-step")
    if len(content) > 2500:
        warnings.append("long-procedure-content")
    lowered = content.lower()
    if "user:" in lowered or "assistant:" in lowered:
        warnings.append("transcript-like-procedure")
    return warnings


def _split_pipe_list(value: str) -> list[str]:
    if not value:
        return []
    parts: list[str] = []
    seen: set[str] = set()
    for raw_part in value.split("|"):
        cleaned = " ".join(raw_part.split()).strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        parts.append(cleaned)
    return parts
