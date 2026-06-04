from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .promotion import parse_structured_record
from .storage import MemoryStore
from .task_memory import assemble_task_memory

FAILURE_REPORT_SCHEMA = "memory.failure_report.v1"


def build_failure_report(
    store: MemoryStore,
    *,
    query: str,
    project_namespace: str,
    global_namespace: str = "global",
    limit: int = 10,
) -> dict[str, Any]:
    """Build a read-only failure hygiene view for a task.

    This report aggregates existing gotchas, procedure failure modes, and
    task-memory suppression reasons. It does not create or modify records.
    """

    task_report = assemble_task_memory(
        store,
        query=query,
        project_namespace=project_namespace,
        global_namespace=global_namespace,
        procedure_limit=limit,
        domain_limit=limit,
    )
    gotchas = _recall_gotchas(
        store,
        query=query,
        namespaces=[project_namespace, global_namespace],
        limit=limit,
    )
    procedure_items = list(task_report.get("procedure_hits") or [])
    concept_items = list(task_report.get("concept_hits") or [])
    belief_items = list(task_report.get("belief_hits") or [])
    suppressed_items = list(task_report.get("suppressed_items") or [])
    failure_modes = [
        *_failure_entries(gotchas, source="gotcha"),
        *_failure_entries(procedure_items, source="procedure"),
        *_failure_entries(concept_items, source="concept-note"),
        *_failure_entries(belief_items, source="belief"),
    ]
    warnings = _warnings(failure_modes=failure_modes, suppressed_items=suppressed_items)
    return {
        "schema": FAILURE_REPORT_SCHEMA,
        "query": query,
        "project_namespace": project_namespace,
        "global_namespace": global_namespace,
        "generated_at": datetime.now(UTC).isoformat(),
        "source_counts": {
            "gotchas": len(gotchas),
            "procedures": len(procedure_items),
            "beliefs": len(belief_items),
            "concept_notes": len(concept_items),
            "suppressed_items": len(suppressed_items),
        },
        "failure_modes": failure_modes,
        "suppressed_items": suppressed_items,
        "warnings": warnings,
        "writeback_boundary": "read_only_no_writeback",
    }


def _recall_gotchas(
    store: MemoryStore,
    *,
    query: str,
    namespaces: list[str],
    limit: int,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for namespace in namespaces:
        recalled = store.recall(
            namespace=namespace,
            query=query,
            kind="memory",
            tags_any=["kind:gotcha"],
            limit=limit,
        )
        for item in recalled.get("items") or []:
            item_id = str(item.get("id") or "")
            if not item_id or item_id in seen:
                continue
            seen.add(item_id)
            items.append(item)
    return items


def _failure_entries(items: list[dict[str, Any]], *, source: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for item in items:
        fields = parse_structured_record(str(item.get("content") or ""))
        trigger = fields.get("trigger")
        symptom = fields.get("symptom")
        failure_mode = fields.get("failure_mode")
        if source == "procedure":
            procedure = item.get("procedure") or {}
            if isinstance(procedure, dict):
                failure_mode = failure_mode or procedure.get("failure_mode")
        if not any([trigger, symptom, failure_mode, fields.get("fix"), fields.get("rollback_path")]):
            continue
        tags = [str(tag) for tag in (item.get("tags") or [])]
        entries.append(
            {
                "id": str(item.get("id") or ""),
                "title": item.get("title"),
                "namespace": item.get("namespace"),
                "record_kind": item.get("kind"),
                "record_type": fields.get("record_type"),
                "source": source,
                "domain_tags": [tag for tag in tags if tag.startswith("domain:")],
                "topic_tags": [tag for tag in tags if tag.startswith("topic:")],
                "trigger": trigger,
                "symptom": symptom,
                "failure_mode": failure_mode,
                "anti_pattern": fields.get("anti_pattern"),
                "fix": fields.get("fix"),
                "rollback_path": fields.get("rollback_path"),
                "status": fields.get("procedure_status") or fields.get("status"),
                "confidence": fields.get("confidence"),
                "evidence_refs": _split_refs(fields.get("evidence_refs", "")),
            }
        )
    return entries


def _split_refs(value: str) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split("|") if part.strip()]


def _warnings(*, failure_modes: list[dict[str, Any]], suppressed_items: list[dict[str, Any]]) -> list[str]:
    warnings: list[str] = []
    if not failure_modes:
        warnings.append("no-failure-memory-found")
    if any(str(item.get("reason") or "").startswith("procedure_status:unsafe") for item in suppressed_items):
        warnings.append("unsafe-procedure-suppressed")
    if any(str(item.get("reason") or "") == "contradicted" for item in suppressed_items):
        warnings.append("contradicted-memory-suppressed")
    if any(str(item.get("reason") or "") == "superseded" for item in suppressed_items):
        warnings.append("superseded-memory-suppressed")
    return warnings
