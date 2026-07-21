from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .procedure_governance import (
    normalize_task_domain,
    parse_procedure_artifact,
    procedure_governance_status,
    procedure_score_adjustment,
)
from .relation_metadata import parse_content_fields, parse_relation_metadata
from .repository import MemoryRow, fetch_row_by_id, fetch_tombstone_metadata
from .storage import MemoryStore

SECTION_LIMIT_KEYS = {
    "procedure": "procedure_limit",
    "concept": "concept_limit",
    "belief": "belief_limit",
    "domain": "domain_limit",
}
SECTION_TAGS = {
    "procedure": "kind:procedure",
    "concept": "kind:concept-note",
    "belief": "kind:belief",
    "domain": "kind:domain-note",
}
SECTION_BASE_SCORES = {
    "procedure": 100.0,
    "concept": 70.0,
    "belief": 55.0,
    "domain": 40.0,
    "support": 20.0,
}
SECTION_PRIORITY = {
    "procedure": 0,
    "concept": 1,
    "belief": 2,
    "domain": 3,
    "support": 4,
}
ELIGIBLE_VALIDITY_STATUSES = {"unbounded", "current"}
INELIGIBLE_VALIDITY_STATUSES = {"expired", "future", "invalid"}
DEPENDENCY_BLOCKING_REASONS = {
    "depends_on:ineligible",
    "depends_on:unresolved",
    "lineage_status:degraded",
}
MAX_RELATION_TRAVERSAL_DEPTH = 8
MAX_RELATION_GRAPH_RECORDS = 96
RECORD_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{2,127}$")
GENERATED_RECORD_ID_PATTERN = re.compile(r"^\d{20,}-[0-9a-fA-F]{8,}$")


@dataclass(frozen=True, slots=True)
class TaskMemoryConfig:
    project_namespace: str
    global_namespace: str = "global"
    procedure_limit: int = 3
    concept_limit: int = 3
    belief_limit: int = 3
    domain_limit: int = 2
    support_limit: int = 6
    relation_aware: bool = True
    as_of: datetime | None = None
    task_domain: str = ""


@dataclass(frozen=True, slots=True)
class TaskCandidate:
    item: dict[str, Any]
    section: str
    raw_rank: int
    direct: bool
    namespace_role: str
    reasons: tuple[str, ...]


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
    relation_aware: bool = True,
    as_of: datetime | str | None = None,
    task_domain: str | None = None,
) -> dict[str, Any]:
    config = TaskMemoryConfig(
        project_namespace=project_namespace.strip(),
        global_namespace=global_namespace.strip() or "global",
        procedure_limit=procedure_limit,
        concept_limit=concept_limit,
        belief_limit=belief_limit,
        domain_limit=domain_limit,
        support_limit=support_limit,
        relation_aware=relation_aware,
        as_of=_parse_as_of(as_of),
        task_domain=normalize_task_domain(task_domain),
    )
    if not config.relation_aware:
        return _assemble_flat_task_memory(store, query=query, config=config)

    return _assemble_relation_aware_task_memory(store, query=query, config=config)


def _assemble_flat_task_memory(store: MemoryStore, *, query: str, config: TaskMemoryConfig) -> dict[str, Any]:
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

    procedure_hits, suppressed = _filter_flat_procedures(procedure_hits, config=config)
    enriched_procedures = [_enrich_procedure_hit(item, task_domain=config.task_domain) for item in procedure_hits]
    supporting_ids = _collect_supporting_ids([*procedure_hits, *concept_hits])
    supporting_hits = _fetch_items_by_id(store, supporting_ids, limit=config.support_limit)

    report = {
        "query": query.strip(),
        "project_namespace": config.project_namespace,
        "global_namespace": config.global_namespace,
        "as_of": config.as_of.isoformat() if config.as_of else None,
        "task_domain": config.task_domain or None,
        "procedure_hits": enriched_procedures,
        "concept_hits": concept_hits,
        "belief_hits": belief_hits,
        "domain_hits": domain_hits,
        "supporting_hits": supporting_hits,
        "corrective_items": [],
        "suppressed_items": suppressed,
        "unresolved_relation_targets": [],
        "descriptive_dependencies": [],
        "assembly_mode": "flat",
    }
    report["summary"] = render_task_memory_text(report)
    return report


def _assemble_relation_aware_task_memory(
    store: MemoryStore,
    *,
    query: str,
    config: TaskMemoryConfig,
) -> dict[str, Any]:
    candidate_limit = _candidate_limit(
        max(config.procedure_limit, config.concept_limit, config.belief_limit, config.domain_limit)
    )
    raw_procedure_hits = _merge_hits(
        _recall_hits(
            store,
            namespace=config.project_namespace,
            query=query,
            tags_any=[SECTION_TAGS["procedure"]],
            limit=candidate_limit,
        ),
        _recall_hits(
            store,
            namespace=config.global_namespace,
            query=query,
            tags_any=[SECTION_TAGS["procedure"]],
            limit=candidate_limit,
        ),
        candidate_limit * 2,
    )
    raw_concept_hits = _recall_hits(
        store,
        namespace=config.global_namespace,
        query=query,
        tags_any=[SECTION_TAGS["concept"]],
        limit=candidate_limit,
    )
    raw_belief_hits = _recall_hits(
        store,
        namespace=config.global_namespace,
        query=query,
        tags_any=[SECTION_TAGS["belief"]],
        limit=candidate_limit,
    )
    raw_domain_hits = _recall_hits(
        store,
        namespace=config.global_namespace,
        query=query,
        tags_any=[SECTION_TAGS["domain"]],
        limit=max(config.domain_limit, candidate_limit),
    )
    raw_state_change_hits = _merge_hits(
        _recall_hits(
            store,
            namespace=config.project_namespace,
            query=query,
            tags_any=["kind:state-change"],
            limit=candidate_limit,
        ),
        _recall_hits(
            store,
            namespace=config.global_namespace,
            query=query,
            tags_any=["kind:state-change"],
            limit=candidate_limit,
        ),
        candidate_limit * 2,
    )

    candidates = _build_direct_candidates(
        {
            "procedure": raw_procedure_hits,
            "concept": raw_concept_hits,
            "belief": raw_belief_hits,
            "domain": raw_domain_hits,
            "support": raw_state_change_hits,
        },
        project_namespace=config.project_namespace,
    )
    relation_edges, unresolved_targets, descriptive_dependencies = _resolve_relation_graph(
        store,
        candidates,
    )

    suppressed: list[dict[str, Any]] = []
    corrective_items: list[dict[str, Any]] = []
    active_ids = set(candidates)
    _apply_validity_suppression(candidates, active_ids, suppressed, as_of=config.as_of)
    _apply_lineage_suppression(candidates, active_ids, suppressed)
    _apply_procedure_governance_suppression(
        candidates,
        active_ids,
        suppressed,
        task_domain=config.task_domain,
    )
    _apply_dependency_suppression(
        candidates,
        active_ids,
        relation_edges,
        unresolved_targets,
        suppressed,
    )
    scores = _score_candidates(candidates, relation_edges, active_ids, task_domain=config.task_domain)
    _apply_supersession(
        candidates,
        active_ids,
        scores,
        relation_edges,
        suppressed,
        corrective_items,
    )
    _apply_dependency_suppression(
        candidates,
        active_ids,
        relation_edges,
        unresolved_targets,
        suppressed,
    )
    scores = _score_candidates(candidates, relation_edges, active_ids, task_domain=config.task_domain)
    _apply_contradictions(candidates, active_ids, scores, relation_edges, suppressed)
    _apply_dependency_suppression(
        candidates,
        active_ids,
        relation_edges,
        unresolved_targets,
        suppressed,
    )
    scores = _score_candidates(candidates, relation_edges, active_ids, task_domain=config.task_domain)

    selected_ids: set[str] = set()
    procedure_hits = _select_section(
        candidates,
        active_ids,
        scores,
        section="procedure",
        limit=config.procedure_limit,
        selected_as="procedure-anchor",
    )
    selected_ids.update(_collect_ids(procedure_hits))
    concept_hits = _select_section(
        candidates,
        active_ids,
        scores,
        section="concept",
        limit=config.concept_limit,
        selected_as="concept-overlay",
    )
    selected_ids.update(_collect_ids(concept_hits))
    belief_hits = _select_section(
        candidates,
        active_ids,
        scores,
        section="belief",
        limit=config.belief_limit,
        selected_as="belief-overlay",
    )
    selected_ids.update(_collect_ids(belief_hits))
    domain_hits = _select_section(
        candidates,
        active_ids,
        scores,
        section="domain",
        limit=config.domain_limit,
        selected_as="domain-context",
    )
    selected_ids.update(_collect_ids(domain_hits))

    support_ids = _collect_supporting_ids_for_selected(
        candidates=candidates,
        selected_primary_ids=selected_ids,
        active_ids=active_ids,
        relation_edges=relation_edges,
    )
    supporting_hits = [
        _annotate_candidate(candidates[item_id], selected_as="supporting-record", score=scores[item_id])
        for item_id in support_ids[: config.support_limit]
        if item_id in candidates and item_id not in selected_ids
    ]

    enriched_procedures = [_enrich_procedure_hit(item, task_domain=config.task_domain) for item in procedure_hits]
    report = {
        "query": query.strip(),
        "project_namespace": config.project_namespace,
        "global_namespace": config.global_namespace,
        "as_of": config.as_of.isoformat() if config.as_of else None,
        "task_domain": config.task_domain or None,
        "procedure_hits": enriched_procedures,
        "concept_hits": concept_hits,
        "belief_hits": belief_hits,
        "domain_hits": domain_hits,
        "supporting_hits": supporting_hits,
        "corrective_items": corrective_items,
        "suppressed_items": suppressed,
        "unresolved_relation_targets": unresolved_targets,
        "descriptive_dependencies": descriptive_dependencies,
        "assembly_mode": "relation-aware",
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
        decision = item.get("task_memory") or {}
        if decision:
            lines.append(f"  selected_as: {decision.get('selected_as')} score={decision.get('score')}")
        if procedure.get("goal"):
            lines.append(f"  goal: {procedure['goal']}")
        if procedure.get("when_to_use"):
            lines.append(f"  when_to_use: {procedure['when_to_use']}")
        if procedure.get("when_not_to_use"):
            lines.append(f"  when_not_to_use: {procedure['when_not_to_use']}")
        if procedure.get("prerequisites"):
            lines.append(f"  prerequisites: {' | '.join(procedure['prerequisites'])}")
        if procedure.get("steps"):
            lines.append(f"  steps: {' | '.join(procedure['steps'])}")
        if procedure.get("failure_mode"):
            lines.append(f"  failure_mode: {procedure['failure_mode']}")
        if procedure.get("rollback_path"):
            lines.append(f"  rollback_path: {procedure['rollback_path']}")
        governance = procedure.get("governance") or {}
        if governance and governance.get("status") != "unspecified":
            lines.append(f"  procedure_status: {governance['status']}")

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

    lines.extend(["", "Domains:"])
    domain_hits = report.get("domain_hits") or []
    if not domain_hits:
        lines.append("(none)")
    for item in domain_hits:
        lines.append(f"- {item.get('title') or item['id']}")

    lines.extend(["", "Supporting:"])
    supporting_hits = report.get("supporting_hits") or []
    if not supporting_hits:
        lines.append("(none)")
    for item in supporting_hits:
        lines.append(f"- {item.get('title') or item['id']}")

    suppressed_items = report.get("suppressed_items") or []
    unresolved_targets = report.get("unresolved_relation_targets") or []
    if suppressed_items or unresolved_targets:
        lines.extend(["", "Relation Decisions:"])
        if suppressed_items:
            lines.append(f"- suppressed: {len(suppressed_items)}")
        if unresolved_targets:
            lines.append(f"- unresolved_relation_targets: {len(unresolved_targets)}")
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


def _enrich_procedure_hit(item: dict[str, Any], *, task_domain: str = "") -> dict[str, Any]:
    return {
        **item,
        "procedure": parse_procedure_artifact(
            item.get("content") or "",
            tags=item.get("tags") or [],
            task_domain=task_domain,
        ),
    }


def _filter_flat_procedures(
    items: list[dict[str, Any]],
    *,
    config: TaskMemoryConfig,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if config.as_of is None and not config.task_domain:
        return items, []
    eligible: list[dict[str, Any]] = []
    suppressed: list[dict[str, Any]] = []
    for item in items:
        reason: str | None = None
        if config.as_of is not None:
            validity = str(parse_relation_metadata(str(item.get("content") or ""), now=config.as_of)["validity_status"])
            if validity in INELIGIBLE_VALIDITY_STATUSES:
                reason = f"validity:{validity}"
        if reason is None and config.task_domain:
            governance = parse_procedure_artifact(
                str(item.get("content") or ""),
                tags=item.get("tags") or [],
                task_domain=config.task_domain,
            )["governance"]
            if not governance["eligible"]:
                reason = str(governance["ineligible_reason"])
        if reason is None:
            eligible.append(item)
            continue
        suppressed.append(
            {
                "id": _item_id(item),
                "title": item.get("title"),
                "section": "procedure",
                "reason": reason,
                "by_id": None,
                "by_title": None,
                "by_record_type": None,
                "score": None,
            }
        )
    return eligible, suppressed


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


def _candidate_limit(limit: int) -> int:
    return max(limit, min(max(limit * 3, 8), 30))


def _build_direct_candidates(
    section_hits: dict[str, list[dict[str, Any]]],
    *,
    project_namespace: str,
) -> dict[str, TaskCandidate]:
    candidates: dict[str, TaskCandidate] = {}
    for section, items in section_hits.items():
        for raw_rank, item in enumerate(items, start=1):
            item_id = _item_id(item)
            if not item_id:
                continue
            namespace_role = "project" if item.get("namespace") == project_namespace else "global"
            candidate = TaskCandidate(
                item=item,
                section=section,
                raw_rank=raw_rank,
                direct=True,
                namespace_role=namespace_role,
                reasons=(f"direct:{section}", f"namespace:{namespace_role}"),
            )
            existing = candidates.get(item_id)
            if existing is None or _candidate_sort_tuple(
                candidate, {item_id: _base_score(candidate)}
            ) < _candidate_sort_tuple(
                existing,
                {item_id: _base_score(existing)},
            ):
                candidates[item_id] = candidate
    return candidates


def _resolve_relation_graph(
    store: MemoryStore,
    candidates: dict[str, TaskCandidate],
) -> tuple[list[dict[str, str]], list[dict[str, Any]], list[dict[str, str]]]:
    edges: list[dict[str, str]] = []
    unresolved_by_id: dict[str, dict[str, Any]] = {}
    descriptive_dependencies: list[dict[str, str]] = []
    seen_edges: set[tuple[str, str, str]] = set()
    seen_descriptions: set[tuple[str, str]] = set()
    frontier = list(candidates)

    with store._connect() as conn:
        for _depth in range(MAX_RELATION_TRAVERSAL_DEPTH):
            if not frontier:
                break
            next_frontier: list[str] = []
            for source_id in frontier:
                candidate = candidates[source_id]
                relations = candidate.item.get("relations") or {}
                for relation_name in ("depends_on", "supports", "contradicts", "supersedes"):
                    for raw_target in relations.get(relation_name, []) or []:
                        target_id = str(raw_target).strip()
                        if not target_id:
                            continue
                        row = fetch_row_by_id(conn, target_id)
                        tombstone = fetch_tombstone_metadata(conn, target_id)
                        if row is None and tombstone is None and not _looks_like_record_id(target_id):
                            if relation_name == "depends_on":
                                description_key = (source_id, target_id)
                                if description_key not in seen_descriptions:
                                    seen_descriptions.add(description_key)
                                    descriptive_dependencies.append(
                                        {
                                            "source_id": source_id,
                                            "source_title": str(candidate.item.get("title") or ""),
                                            "relation": relation_name,
                                            "value": target_id,
                                            "reason": "descriptive-not-record-id",
                                        }
                                    )
                            continue

                        edge_key = (source_id, relation_name, target_id)
                        if edge_key not in seen_edges:
                            seen_edges.add(edge_key)
                            edges.append(
                                {
                                    "source_id": source_id,
                                    "relation": relation_name,
                                    "target_id": target_id,
                                }
                            )

                        if row is not None:
                            if target_id in candidates:
                                continue
                            if len(candidates) >= MAX_RELATION_GRAPH_RECORDS:
                                unresolved_by_id.setdefault(
                                    target_id,
                                    {
                                        "target_id": target_id,
                                        "reason": "relation-expansion-limit",
                                    },
                                )
                                continue
                            item = MemoryRow.from_sqlite(row).as_dict()
                            candidates[target_id] = TaskCandidate(
                                item=item,
                                section="support",
                                raw_rank=999,
                                direct=False,
                                namespace_role=(
                                    "project" if str(item.get("namespace") or "").startswith("project:") else "global"
                                ),
                                reasons=("relation-target",),
                            )
                            next_frontier.append(target_id)
                            continue

                        if tombstone is not None:
                            unresolved_by_id.setdefault(
                                target_id,
                                {
                                    "target_id": target_id,
                                    "reason": "forgotten",
                                    "tombstone_namespace": tombstone["namespace"],
                                    "tombstone_kind": tombstone["kind"],
                                    "tombstone_deleted_at": tombstone["deleted_at"],
                                    "tombstone_root_forget_id": tombstone["root_forget_id"],
                                    "tombstone_cause": tombstone["cause"],
                                },
                            )
                        else:
                            unresolved_by_id.setdefault(
                                target_id,
                                {"target_id": target_id, "reason": "not-found"},
                            )
            frontier = next_frontier

    return edges, list(unresolved_by_id.values()), descriptive_dependencies


def _looks_like_record_id(value: str) -> bool:
    if GENERATED_RECORD_ID_PATTERN.fullmatch(value):
        return True
    if not RECORD_ID_PATTERN.fullmatch(value):
        return False
    return any(marker in value for marker in ("-", "_", "/", ":"))


def _apply_validity_suppression(
    candidates: dict[str, TaskCandidate],
    active_ids: set[str],
    suppressed: list[dict[str, Any]],
    *,
    as_of: datetime | None,
) -> None:
    for item_id, candidate in candidates.items():
        validity = str(parse_relation_metadata(str(candidate.item.get("content") or ""), now=as_of)["validity_status"])
        if validity not in INELIGIBLE_VALIDITY_STATUSES:
            continue
        if item_id in active_ids:
            active_ids.remove(item_id)
            suppressed.append(_suppressed_payload(candidate, reason=f"validity:{validity}"))


def _apply_lineage_suppression(
    candidates: dict[str, TaskCandidate],
    active_ids: set[str],
    suppressed: list[dict[str, Any]],
) -> None:
    for item_id, candidate in candidates.items():
        if item_id not in active_ids:
            continue
        fields = parse_content_fields(str(candidate.item.get("content") or ""))
        persisted_status = _normalize_field_value(candidate.item.get("lineage_status"))
        declared_status = _normalize_field_value(fields.get("lineage_status"))
        if persisted_status != "degraded" and declared_status != "degraded":
            continue
        active_ids.remove(item_id)
        suppressed.append(_suppressed_payload(candidate, reason="lineage_status:degraded"))


def _apply_dependency_suppression(
    candidates: dict[str, TaskCandidate],
    active_ids: set[str],
    relation_edges: list[dict[str, str]],
    unresolved_targets: list[dict[str, Any]],
    suppressed: list[dict[str, Any]],
) -> None:
    unresolved_ids = {item["target_id"] for item in unresolved_targets}
    changed = True
    while changed:
        changed = False
        blocking_inactive_ids = {
            str(item.get("id") or "") for item in suppressed if item.get("reason") in DEPENDENCY_BLOCKING_REASONS
        }
        for edge in relation_edges:
            source_id = edge["source_id"]
            target_id = edge["target_id"]
            if edge["relation"] != "depends_on" or source_id not in active_ids:
                continue
            target_is_unresolved = target_id in unresolved_ids
            target_is_ineligible = target_id in blocking_inactive_ids
            if not target_is_unresolved and not target_is_ineligible:
                continue
            active_ids.remove(source_id)
            suppressed.append(
                _suppressed_payload(
                    candidates[source_id],
                    reason=("depends_on:unresolved" if target_is_unresolved else "depends_on:ineligible"),
                    by_id=target_id,
                )
            )
            changed = True
            break


def _apply_procedure_governance_suppression(
    candidates: dict[str, TaskCandidate],
    active_ids: set[str],
    suppressed: list[dict[str, Any]],
    *,
    task_domain: str,
) -> None:
    for item_id, candidate in candidates.items():
        if candidate.section != "procedure" or item_id not in active_ids:
            continue
        procedure = parse_procedure_artifact(
            str(candidate.item.get("content") or ""),
            tags=candidate.item.get("tags") or [],
            task_domain=task_domain,
        )
        governance = procedure["governance"]
        if governance["eligible"]:
            continue
        active_ids.remove(item_id)
        suppressed.append(
            _suppressed_payload(
                candidate,
                reason=str(governance["ineligible_reason"]),
            )
        )


def _score_candidates(
    candidates: dict[str, TaskCandidate],
    relation_edges: list[dict[str, str]],
    active_ids: set[str],
    *,
    task_domain: str,
) -> dict[str, float]:
    inbound: dict[str, dict[str, int]] = {}
    for edge in relation_edges:
        if edge["source_id"] not in active_ids:
            continue
        if edge["target_id"] not in active_ids:
            continue
        inbound.setdefault(edge["target_id"], {}).setdefault(edge["relation"], 0)
        inbound[edge["target_id"]][edge["relation"]] += 1

    scores: dict[str, float] = {}
    for item_id, candidate in candidates.items():
        if item_id not in active_ids:
            continue
        relation_bonus = (
            inbound.get(item_id, {}).get("depends_on", 0) * 18.0 + inbound.get(item_id, {}).get("supports", 0) * 10.0
        )
        scores[item_id] = round(_base_score(candidate, task_domain=task_domain) + relation_bonus, 3)
    return scores


def _base_score(candidate: TaskCandidate, *, task_domain: str = "") -> float:
    rank_penalty = 5.0 if candidate.section in {"procedure", "concept", "belief"} else 4.0
    namespace_bonus = 4.0 if candidate.namespace_role == "project" else 0.0
    direct_bonus = 5.0 if candidate.direct else 0.0
    procedure_governance_bonus = _procedure_governance_score(candidate, task_domain=task_domain)
    return (
        SECTION_BASE_SCORES[candidate.section]
        - (rank_penalty * max(candidate.raw_rank - 1, 0))
        + namespace_bonus
        + direct_bonus
        + procedure_governance_bonus
    )


def _procedure_governance_score(candidate: TaskCandidate, *, task_domain: str = "") -> float:
    if candidate.section != "procedure":
        return 0.0
    return procedure_score_adjustment(candidate.item, task_domain=task_domain)


def _apply_supersession(
    candidates: dict[str, TaskCandidate],
    active_ids: set[str],
    scores: dict[str, float],
    relation_edges: list[dict[str, str]],
    suppressed: list[dict[str, Any]],
    corrective_items: list[dict[str, Any]],
) -> None:
    eligible_at_start = set(active_ids)
    adjacency: dict[str, list[str]] = {}
    supersession_targets: set[str] = set()
    for edge in relation_edges:
        if edge["relation"] != "supersedes":
            continue
        source_id = edge["source_id"]
        target_id = edge["target_id"]
        if source_id not in eligible_at_start or target_id not in eligible_at_start:
            continue
        if source_id == target_id:
            continue
        adjacency.setdefault(source_id, []).append(target_id)
        supersession_targets.add(target_id)

    roots = [source_id for source_id in adjacency if source_id not in supersession_targets]
    superseded_by: dict[str, str] = {}
    for root_id in roots:
        frontier = [(root_id, 0)]
        visited = {root_id}
        while frontier:
            source_id, depth = frontier.pop(0)
            if depth >= MAX_RELATION_TRAVERSAL_DEPTH:
                continue
            for target_id in adjacency.get(source_id, []):
                if target_id in visited:
                    continue
                visited.add(target_id)
                superseded_by.setdefault(target_id, source_id)
                frontier.append((target_id, depth + 1))

    for target_id, source_id in superseded_by.items():
        if target_id not in active_ids:
            continue
        source_record_type = _record_type(candidates[source_id])
        is_corrective_procedure_change = candidates[target_id].section == "procedure" and source_record_type in {
            "belief",
            "state-change",
        }
        active_ids.remove(target_id)
        suppressed.append(
            _suppressed_payload(
                candidates[target_id],
                reason="superseded",
                by_id=source_id,
                by_title=candidates[source_id].item.get("title"),
                by_record_type=source_record_type if is_corrective_procedure_change else None,
                score=scores.get(target_id),
            )
        )
        if is_corrective_procedure_change and not any(_item_id(item) == source_id for item in corrective_items):
            corrective_items.append(
                _annotate_candidate(
                    candidates[source_id],
                    selected_as="corrective-evidence",
                    score=scores.get(source_id, 0.0),
                )
            )


def _apply_contradictions(
    candidates: dict[str, TaskCandidate],
    active_ids: set[str],
    scores: dict[str, float],
    relation_edges: list[dict[str, str]],
    suppressed: list[dict[str, Any]],
) -> None:
    for edge in relation_edges:
        if edge["relation"] != "contradicts":
            continue
        source_id = edge["source_id"]
        target_id = edge["target_id"]
        if source_id not in active_ids or target_id not in active_ids:
            continue
        winner_id, loser_id = _choose_conflict_winner(
            source_id,
            target_id,
            candidates=candidates,
            scores=scores,
        )
        active_ids.remove(loser_id)
        suppressed.append(
            _suppressed_payload(
                candidates[loser_id],
                reason="contradicted",
                by_id=winner_id,
                by_title=candidates[winner_id].item.get("title"),
                score=scores.get(loser_id),
            )
        )


def _choose_conflict_winner(
    first_id: str,
    second_id: str,
    *,
    candidates: dict[str, TaskCandidate],
    scores: dict[str, float],
) -> tuple[str, str]:
    first_key = _conflict_strength_key(candidates[first_id], scores.get(first_id, 0.0))
    second_key = _conflict_strength_key(candidates[second_id], scores.get(second_id, 0.0))
    if first_key >= second_key:
        return first_id, second_id
    return second_id, first_id


def _conflict_strength_key(candidate: TaskCandidate, score: float) -> tuple[float, int, int, str]:
    direct_strength = 1 if candidate.direct else 0
    bucket_strength = 10 - SECTION_PRIORITY[candidate.section]
    created_at = str(candidate.item.get("created_at") or "")
    return (score, direct_strength, bucket_strength, created_at)


def _select_section(
    candidates: dict[str, TaskCandidate],
    active_ids: set[str],
    scores: dict[str, float],
    *,
    section: str,
    limit: int,
    selected_as: str,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for item_id, candidate in sorted(
        candidates.items(),
        key=lambda pair: _candidate_sort_tuple(pair[1], scores),
    ):
        if item_id not in active_ids or candidate.section != section or not candidate.direct:
            continue
        selected.append(_annotate_candidate(candidate, selected_as=selected_as, score=scores[item_id]))
        if len(selected) >= limit:
            break
    return selected


def _candidate_sort_tuple(candidate: TaskCandidate, scores: dict[str, float]) -> tuple[float, int, str]:
    item_id = _item_id(candidate.item)
    title = str(candidate.item.get("title") or "")
    return (-scores.get(item_id, 0.0), candidate.raw_rank, title.lower())


def _collect_supporting_ids_for_selected(
    *,
    candidates: dict[str, TaskCandidate],
    selected_primary_ids: set[str],
    active_ids: set[str],
    relation_edges: list[dict[str, str]],
) -> list[str]:
    priority = {"depends_on": 0, "supports": 1}
    ranked: list[tuple[int, int, str]] = []
    seen: set[str] = set()
    for index, edge in enumerate(relation_edges):
        if edge["relation"] not in priority:
            continue
        if edge["source_id"] not in selected_primary_ids:
            continue
        target_id = edge["target_id"]
        if target_id not in active_ids or target_id in selected_primary_ids or target_id in seen:
            continue
        if target_id not in candidates:
            continue
        seen.add(target_id)
        ranked.append((priority[edge["relation"]], index, target_id))
    return [item_id for _, _, item_id in sorted(ranked)]


def _annotate_candidate(candidate: TaskCandidate, *, selected_as: str, score: float) -> dict[str, Any]:
    return {
        **candidate.item,
        "task_memory": {
            "selected_as": selected_as,
            "score": round(score, 3),
            "reasons": list(_candidate_reasons(candidate)),
        },
    }


def _suppressed_payload(
    candidate: TaskCandidate,
    *,
    reason: str,
    by_id: str | None = None,
    by_title: str | None = None,
    by_record_type: str | None = None,
    score: float | None = None,
) -> dict[str, Any]:
    return {
        "id": _item_id(candidate.item),
        "title": candidate.item.get("title"),
        "section": candidate.section,
        "reason": reason,
        "by_id": by_id,
        "by_title": by_title,
        "by_record_type": by_record_type,
        "lineage_status": candidate.item.get("lineage_status"),
        "lineage_issues": candidate.item.get("lineage_issues") or [],
        "score": round(score, 3) if score is not None else None,
    }


def _item_id(item: dict[str, Any]) -> str:
    return str(item.get("id") or "").strip()


def _candidate_reasons(candidate: TaskCandidate) -> tuple[str, ...]:
    reasons = list(candidate.reasons)
    if candidate.section == "procedure":
        status = procedure_governance_status(candidate.item)
        if status != "unspecified":
            reasons.append(f"procedure_status:{status}")
    return tuple(reasons)


def _record_type(candidate: TaskCandidate) -> str:
    fields = parse_content_fields(str(candidate.item.get("content") or ""))
    record_type = _normalize_field_value(fields.get("record_type"))
    if record_type:
        return record_type
    if candidate.section == "belief":
        return "belief"
    for tag in candidate.item.get("tags") or []:
        text = str(tag).strip().lower()
        if text.startswith("kind:"):
            return text.removeprefix("kind:")
    return ""


def _collect_ids(items: list[dict[str, Any]]) -> set[str]:
    return {item_id for item in items if (item_id := _item_id(item))}


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


def _normalize_field_value(value: str | None) -> str:
    return "-".join(str(value or "").strip().lower().replace("_", "-").split())


def _parse_as_of(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        normalized = value.strip()
        if not normalized:
            return None
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError as exc:
            raise ValueError("as_of must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
