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


def parse_procedure_artifact(
    content: str,
    *,
    tags: list[str] | tuple[str, ...] | None = None,
    task_domain: str | None = None,
) -> dict[str, Any]:
    fields = parse_content_fields(content)
    status_raw = _first_field_value(fields, PROCEDURE_STATUS_FIELD_NAMES)
    status = normalize_procedure_status(status_raw)
    normalized_task_domain = normalize_task_domain(task_domain)
    explicit_domains = _normalize_domains(_split_pipe_list(fields.get("applies_to_domains", "")))
    inferred_domains = _domains_from_exact_tags(tags or ()) if not explicit_domains else []
    applies_to_domains = explicit_domains or inferred_domains
    scope_source = "explicit" if explicit_domains else "domain-tags" if inferred_domains else "unscoped"
    domain_mismatch = bool(
        normalized_task_domain
        and applies_to_domains
        and normalized_task_domain not in applies_to_domains
    )
    steps = _split_pipe_list(fields.get("steps", "") or fields.get("checklist", ""))
    prerequisites = _split_pipe_list(fields.get("prerequisites", "") or fields.get("requires", ""))
    procedure = {
        "goal": fields.get("goal", ""),
        "applies_to_domains": applies_to_domains,
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
        applies_to_domains=applies_to_domains,
        scope_source=scope_source,
        task_domain=normalized_task_domain,
        domain_mismatch=domain_mismatch,
    )
    ineligible_reasons = []
    if status in INELIGIBLE_PROCEDURE_STATUSES:
        ineligible_reasons.append(f"procedure_status:{status}")
    if domain_mismatch:
        ineligible_reasons.append(f"task_domain_mismatch:{normalized_task_domain}")
    return {
        **procedure,
        "governance": {
            "status": status,
            "raw_status": status_raw,
            "task_domain": normalized_task_domain or None,
            "scope_source": scope_source,
            "eligible": not ineligible_reasons,
            "ineligible_reason": ineligible_reasons[0] if ineligible_reasons else None,
            "ineligible_reasons": ineligible_reasons,
            "missing_recommended_fields": missing_recommended,
            "missing_minimum_fields": missing_minimum,
            "warnings": warnings,
        },
    }


def procedure_governance_status(item: dict[str, Any]) -> str:
    procedure = parse_procedure_artifact(
        str(item.get("content") or ""),
        tags=item.get("tags") or [],
    )
    return str(procedure["governance"]["status"])


def procedure_score_adjustment(item: dict[str, Any], *, task_domain: str | None = None) -> float:
    procedure = parse_procedure_artifact(
        str(item.get("content") or ""),
        tags=item.get("tags") or [],
        task_domain=task_domain,
    )
    governance = procedure["governance"]
    if not governance["eligible"]:
        return -100.0
    status = str(governance["status"])
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


def normalize_task_domain(value: str | None) -> str:
    normalized = " ".join(str(value or "").lower().split()).strip()
    if normalized.startswith("domain:"):
        normalized = normalized.removeprefix("domain:")
    return normalized.replace(" ", "-").replace("_", "-")


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
    applies_to_domains: list[str],
    scope_source: str,
    task_domain: str,
    domain_mismatch: bool,
) -> list[str]:
    warnings: list[str] = []
    if status == "unspecified":
        warnings.append("missing-procedure-status")
    if status == "draft":
        warnings.append("draft-procedure")
    if status in INELIGIBLE_PROCEDURE_STATUSES:
        warnings.append(f"ineligible-procedure-status:{status}")
    if scope_source == "domain-tags":
        warnings.append("procedure-domains-inferred-from-tags")
    if scope_source == "unscoped":
        warnings.append("unscoped-procedure-domains")
    if domain_mismatch:
        warnings.append(f"task-domain-mismatch:{task_domain}")
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


def _normalize_domains(values: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        domain = normalize_task_domain(value)
        if not domain or domain in seen:
            continue
        seen.add(domain)
        normalized.append(domain)
    return normalized


def _domains_from_exact_tags(tags: list[str] | tuple[str, ...]) -> list[str]:
    values: list[str] = []
    for tag in tags:
        cleaned = str(tag).strip()
        if not cleaned.lower().startswith("domain:"):
            continue
        domain = cleaned.split(":", 1)[1].strip()
        if domain:
            values.append(domain)
    return _normalize_domains(values)
