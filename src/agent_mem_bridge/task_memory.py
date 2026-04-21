from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .repository import MemoryRow, fetch_row_by_id
from .storage import MemoryStore


@dataclass(frozen=True, slots=True)
class TaskMemoryConfig:
    project_namespace: str
    global_namespace: str = "global"
    procedure_limit: int = 3
    concept_limit: int = 3
    belief_limit: int = 3
    domain_limit: int = 2
    support_limit: int = 6


def assemble_task_memory(
    store: MemoryStore,
    *,
    query: str,
    project_namespace: str,
    global_namespace: str = "global",
    procedure_limit: int = 3,
    concept_limit: int = 3,
    belief_limit: int = 3,
    domain_limit: int = 2,
    support_limit: int = 6,
) -> dict[str, Any]:
    config = TaskMemoryConfig(
        project_namespace=project_namespace.strip(),
        global_namespace=global_namespace.strip() or "global",
        procedure_limit=procedure_limit,
        concept_limit=concept_limit,
        belief_limit=belief_limit,
        domain_limit=domain_limit,
        support_limit=support_limit,
    )
    procedure_hits = _merge_hits(
        _recall_hits(
            store,
            namespace=config.project_namespace,
            query=query,
            tags_any=["kind:procedure"],
            limit=config.procedure_limit,
        ),
        _recall_hits(
            store,
            namespace=config.global_namespace,
            query=query,
            tags_any=["kind:procedure"],
            limit=config.procedure_limit,
        ),
        config.procedure_limit,
    )
    concept_hits = _recall_hits(
        store,
        namespace=config.global_namespace,
        query=query,
        tags_any=["kind:concept-note"],
        limit=config.concept_limit,
    )
    belief_hits = _recall_hits(
        store,
        namespace=config.global_namespace,
        query=query,
        tags_any=["kind:belief"],
        limit=config.belief_limit,
    )
    domain_hits = _recall_hits(
        store,
        namespace=config.global_namespace,
        query=query,
        tags_any=["kind:domain-note"],
        limit=config.domain_limit,
    )

    enriched_procedures = [_enrich_procedure_hit(item) for item in procedure_hits]
    supporting_ids = _collect_supporting_ids([*procedure_hits, *concept_hits])
    supporting_hits = _fetch_items_by_id(store, supporting_ids, limit=config.support_limit)

    report = {
        "query": query.strip(),
        "project_namespace": config.project_namespace,
        "global_namespace": config.global_namespace,
        "procedure_hits": enriched_procedures,
        "concept_hits": concept_hits,
        "belief_hits": belief_hits,
        "domain_hits": domain_hits,
        "supporting_hits": supporting_hits,
    }
    report["summary"] = render_task_memory_text(report)
    return report


def render_task_memory_text(report: dict[str, Any]) -> str:
    lines = [
        f"Task memory for: {report['query']}",
        "",
        "Procedures:",
    ]
    procedures = report.get("procedure_hits") or []
    if not procedures:
        lines.append("(none)")
    for item in procedures:
        procedure = item.get("procedure") or {}
        lines.append(f"- {item.get('title') or item['id']}")
        if procedure.get("goal"):
            lines.append(f"  goal: {procedure['goal']}")
        if procedure.get("when_to_use"):
            lines.append(f"  when_to_use: {procedure['when_to_use']}")
        if procedure.get("steps"):
            lines.append(f"  steps: {' | '.join(procedure['steps'])}")

    lines.extend(["", "Concepts:"])
    concept_hits = report.get("concept_hits") or []
    if not concept_hits:
        lines.append("(none)")
    for item in concept_hits:
        lines.append(f"- {item.get('title') or item['id']}")

    lines.extend(["", "Beliefs:"])
    belief_hits = report.get("belief_hits") or []
    if not belief_hits:
        lines.append("(none)")
    for item in belief_hits:
        lines.append(f"- {item.get('title') or item['id']}")

    lines.extend(["", "Supporting:"])
    supporting_hits = report.get("supporting_hits") or []
    if not supporting_hits:
        lines.append("(none)")
    for item in supporting_hits:
        lines.append(f"- {item.get('title') or item['id']}")
    return "\n".join(lines)


def _recall_hits(
    store: MemoryStore,
    *,
    namespace: str,
    query: str,
    tags_any: list[str],
    limit: int,
) -> list[dict[str, Any]]:
    if not namespace:
        return []
    hits = store.recall(
        namespace=namespace,
        query=query,
        tags_any=tags_any,
        limit=limit,
    )["items"]
    if hits or not query.strip():
        return hits
    return store.recall(
        namespace=namespace,
        tags_any=tags_any,
        limit=limit,
    )["items"]


def _merge_hits(primary: list[dict[str, Any]], secondary: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in [*primary, *secondary]:
        item_id = str(item.get("id") or "")
        if item_id and item_id in seen:
            continue
        if item_id:
            seen.add(item_id)
        merged.append(item)
        if len(merged) >= limit:
            break
    return merged


def _enrich_procedure_hit(item: dict[str, Any]) -> dict[str, Any]:
    fields = _parse_fields(item.get("content") or "")
    return {
        **item,
        "procedure": {
            "goal": fields.get("goal", ""),
            "when_to_use": fields.get("when_to_use", "") or fields.get("applies_when", ""),
            "steps": _split_pipe_list(fields.get("steps", "") or fields.get("checklist", "")),
        },
    }


def _collect_supporting_ids(items: list[dict[str, Any]]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for item in items:
        relations = item.get("relations") or {}
        for relation_name in ("depends_on", "supports"):
            for target in relations.get(relation_name, []) or []:
                cleaned = str(target).strip()
                if not cleaned or cleaned in seen:
                    continue
                seen.add(cleaned)
                ordered.append(cleaned)
    return ordered


def _fetch_items_by_id(store: MemoryStore, ids: list[str], *, limit: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if not ids:
        return items
    with store._connect() as conn:
        for memory_id in ids[:limit]:
            row = fetch_row_by_id(conn, memory_id)
            if row is None:
                continue
            items.append(MemoryRow.from_sqlite(row).as_dict())
    return items


def _parse_fields(content: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for raw_line in content.splitlines():
        label, separator, remainder = raw_line.partition(":")
        if not separator:
            continue
        key = label.strip().lower().replace("-", "_")
        value = " ".join(remainder.split()).strip()
        if not key or not value:
            continue
        fields.setdefault(key, value)
    return fields


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
