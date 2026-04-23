from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .relation_metadata import parse_content_fields
from .repository import MemoryRow, fetch_row_by_id
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
        "suppressed_items": [],
        "unresolved_relation_targets": [],
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

    candidates = _build_direct_candidates(
        {
            "procedure": raw_procedure_hits,
            "concept": raw_concept_hits,
            "belief": raw_belief_hits,
            "domain": raw_domain_hits,
        },
        project_namespace=config.project_namespace,
    )
    relation_edges = _collect_relation_edges(candidates)
    target_ids = _ordered_relation_targets(relation_edges)
    fetched_items, unresolved_targets = _fetch_items_by_id_with_unresolved(store, target_ids)
    _add_support_candidates(candidates, fetched_items)

    suppressed: list[dict[str, Any]] = []
    active_ids = set(candidates)
    _apply_validity_suppression(candidates, active_ids, suppressed)
    scores = _score_candidates(candidates, relation_edges, active_ids)
    _apply_supersession(candidates, active_ids, scores, relation_edges, suppressed)
    scores = _score_candidates(candidates, relation_edges, active_ids)
    _apply_contradictions(candidates, active_ids, scores, relation_edges, suppressed)
    scores = _score_candidates(candidates, relation_edges, active_ids)

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

    enriched_procedures = [_enrich_procedure_hit(item) for item in procedure_hits]
    report = {
        "query": query.strip(),
        "project_namespace": config.project_namespace,
        "global_namespace": config.global_namespace,
        "procedure_hits": enriched_procedures,
        "concept_hits": concept_hits,
        "belief_hits": belief_hits,
        "domain_hits": domain_hits,
        "supporting_hits": supporting_hits,
        "suppressed_items": suppressed,
        "unresolved_relation_targets": unresolved_targets,
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


def _enrich_procedure_hit(item: dict[str, Any]) -> dict[str, Any]:
    fields = parse_content_fields(item.get("content") or "")
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
            if existing is None or _candidate_sort_tuple(candidate, {item_id: _base_score(candidate)}) < _candidate_sort_tuple(
                existing,
                {item_id: _base_score(existing)},
            ):
                candidates[item_id] = candidate
    return candidates


def _add_support_candidates(candidates: dict[str, TaskCandidate], fetched_items: dict[str, dict[str, Any]]) -> None:
    for item_id, item in fetched_items.items():
        if item_id in candidates:
            continue
        candidates[item_id] = TaskCandidate(
            item=item,
            section="support",
            raw_rank=999,
            direct=False,
            namespace_role="project" if str(item.get("namespace") or "").startswith("project:") else "global",
            reasons=("relation-target",),
        )


def _collect_relation_edges(candidates: dict[str, TaskCandidate]) -> list[dict[str, str]]:
    edges: list[dict[str, str]] = []
    for source_id, candidate in candidates.items():
        relations = candidate.item.get("relations") or {}
        for relation_name in ("depends_on", "supports", "contradicts", "supersedes"):
            for target in relations.get(relation_name, []) or []:
                target_id = str(target).strip()
                if not target_id:
                    continue
                edges.append({"source_id": source_id, "relation": relation_name, "target_id": target_id})
    return edges


def _ordered_relation_targets(relation_edges: list[dict[str, str]]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for edge in relation_edges:
        target_id = edge["target_id"]
        if target_id in seen:
            continue
        seen.add(target_id)
        ordered.append(target_id)
    return ordered


def _fetch_items_by_id_with_unresolved(
    store: MemoryStore,
    ids: list[str],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, str]]]:
    items: dict[str, dict[str, Any]] = {}
    unresolved: list[dict[str, str]] = []
    if not ids:
        return items, unresolved
    with store._connect() as conn:
        for memory_id in ids:
            row = fetch_row_by_id(conn, memory_id)
            if row is None:
                unresolved.append({"target_id": memory_id, "reason": "not-found"})
                continue
            item = MemoryRow.from_sqlite(row).as_dict()
            items[item["id"]] = item
    return items, unresolved


def _apply_validity_suppression(
    candidates: dict[str, TaskCandidate],
    active_ids: set[str],
    suppressed: list[dict[str, Any]],
) -> None:
    for item_id, candidate in candidates.items():
        validity = str(candidate.item.get("validity_status") or "unbounded")
        if validity not in INELIGIBLE_VALIDITY_STATUSES:
            continue
        if item_id in active_ids:
            active_ids.remove(item_id)
            suppressed.append(_suppressed_payload(candidate, reason=f"validity:{validity}"))


def _score_candidates(
    candidates: dict[str, TaskCandidate],
    relation_edges: list[dict[str, str]],
    active_ids: set[str],
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
            inbound.get(item_id, {}).get("depends_on", 0) * 18.0
            + inbound.get(item_id, {}).get("supports", 0) * 10.0
        )
        scores[item_id] = round(_base_score(candidate) + relation_bonus, 3)
    return scores


def _base_score(candidate: TaskCandidate) -> float:
    rank_penalty = 5.0 if candidate.section in {"procedure", "concept", "belief"} else 4.0
    namespace_bonus = 4.0 if candidate.namespace_role == "project" else 0.0
    direct_bonus = 5.0 if candidate.direct else 0.0
    return (
        SECTION_BASE_SCORES[candidate.section]
        - (rank_penalty * max(candidate.raw_rank - 1, 0))
        + namespace_bonus
        + direct_bonus
    )


def _apply_supersession(
    candidates: dict[str, TaskCandidate],
    active_ids: set[str],
    scores: dict[str, float],
    relation_edges: list[dict[str, str]],
    suppressed: list[dict[str, Any]],
) -> None:
    for edge in relation_edges:
        if edge["relation"] != "supersedes":
            continue
        source_id = edge["source_id"]
        target_id = edge["target_id"]
        if source_id not in active_ids or target_id not in active_ids:
            continue
        if source_id == target_id:
            continue
        active_ids.remove(target_id)
        suppressed.append(
            _suppressed_payload(
                candidates[target_id],
                reason="superseded",
                by_id=source_id,
                by_title=candidates[source_id].item.get("title"),
                score=scores.get(target_id),
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
            "reasons": list(candidate.reasons),
        },
    }


def _suppressed_payload(
    candidate: TaskCandidate,
    *,
    reason: str,
    by_id: str | None = None,
    by_title: str | None = None,
    score: float | None = None,
) -> dict[str, Any]:
    return {
        "id": _item_id(candidate.item),
        "title": candidate.item.get("title"),
        "section": candidate.section,
        "reason": reason,
        "by_id": by_id,
        "by_title": by_title,
        "score": round(score, 3) if score is not None else None,
    }


def _item_id(item: dict[str, Any]) -> str:
    return str(item.get("id") or "").strip()


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
