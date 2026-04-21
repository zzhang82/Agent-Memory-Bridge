from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .profile_assembly import StartupRecallLayer, build_startup_recall_plan
from .storage import MemoryStore
from .task_memory import assemble_task_memory


ISSUE_MARKERS = (
    "error",
    "bug",
    "fix",
    "broken",
    "broke",
    "regression",
    "drift",
    "wrong db",
    "sqlite",
    "fts",
    "timeout",
    "handoff",
    "recall",
    "duplicate",
    "overwrite",
    "overwritten",
    "gotcha",
    "summary",
    "token",
    "human readable",
    "machine-readable",
)

ISSUE_TAG_HINTS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (
        ("wrong db", "database", "bridge.db", "recall misses"),
        ("problem:split-store", "symptom:wrong-db", "fix:canonical-runtime-path", "topic:runtime-path"),
    ),
    (
        ("drift", "overwrite", "overwritten", "contract"),
        ("problem:contract-drift", "symptom:conflicting-edits", "fix:single-ownership", "topic:subagents"),
    ),
    (
        ("fts", "values.yaml", "punctuation", "semantic"),
        ("problem:fts-query-shape", "symptom:punctuation-query-failure", "fix:safe-fts-fallback", "topic:fts"),
    ),
    (
        ("summary", "noisy", "chat-shaped", "token", "human readable"),
        ("problem:summary-noise", "problem:narrative-memory", "fix:layered-promotion", "fix:structured-records"),
    ),
)


@dataclass(frozen=True, slots=True)
class RecallPlan:
    query: str
    project_namespace: str
    should_search_local: bool
    tag_hints: tuple[str, ...]


def plan_recall(query: str, project_namespace: str) -> RecallPlan:
    normalized = " ".join(query.lower().split())
    tag_hints = _infer_tag_hints(normalized)
    should_search_local = any(marker in normalized for marker in ISSUE_MARKERS) or bool(tag_hints)
    return RecallPlan(
        query=query.strip(),
        project_namespace=project_namespace.strip(),
        should_search_local=should_search_local,
        tag_hints=tag_hints,
    )


def recall_first(
    store: MemoryStore,
    query: str,
    project_namespace: str,
    limit: int = 5,
    global_namespace: str | None = None,
) -> dict[str, Any]:
    plan = plan_recall(query=query, project_namespace=project_namespace)
    target_global_namespace = (global_namespace or "global").strip()
    profile_bundle_hits = _recall_profile_bundle_hits(
        store=store,
        global_namespace=target_global_namespace,
        project_namespace=plan.project_namespace,
        query=plan.query,
        limit=min(max(limit, 1), 2),
    )
    if not plan.should_search_local:
        return {
            "query": plan.query,
            "project_namespace": plan.project_namespace,
            "global_namespace": target_global_namespace,
            "should_search_local": False,
            "tag_hints": list(plan.tag_hints),
            "profile_bundle_hits": profile_bundle_hits,
            "project_hits": [],
            "learn_hits": [],
            "gotcha_hits": [],
            "domain_hits": [],
            "reference_hits": [],
            "recommended_action": "No local-first trigger fired.",
        }

    project_hits = store.recall(
        namespace=plan.project_namespace,
        query=plan.query,
        limit=limit,
    )["items"]

    global_query_hits = store.recall(
        namespace=target_global_namespace,
        query=plan.query,
        limit=max(limit * 3, 15),
    )["items"]
    learn_hits = _filter_by_tag(global_query_hits, "kind:learn")[:limit]
    gotcha_hits = _filter_by_tag(global_query_hits, "kind:gotcha")[:limit]
    domain_hits = _filter_by_tag(global_query_hits, "kind:domain-note")[:limit]

    if plan.tag_hints:
        tag_hint_hits = store.recall(
            namespace=target_global_namespace,
            tags_any=list(plan.tag_hints),
            limit=max(limit * 3, 10),
        )["items"]
        learn_hits = _merge_hits(learn_hits, _filter_by_tag(tag_hint_hits, "kind:learn"), limit)
        gotcha_hits = _merge_hits(gotcha_hits, _filter_by_tag(tag_hint_hits, "kind:gotcha"), limit)
        domain_hits = _merge_hits(domain_hits, _filter_by_tag(tag_hint_hits, "kind:domain-note"), limit)

    task_memory = assemble_task_memory(
        store,
        query=plan.query,
        project_namespace=plan.project_namespace,
        global_namespace=target_global_namespace,
        procedure_limit=max(1, min(limit, 3)),
        concept_limit=max(1, min(limit, 3)),
        belief_limit=max(1, min(limit, 3)),
        domain_limit=max(1, min(limit, 2)),
        support_limit=max(2, min(limit * 2, 6)),
    )

    reference_hits: list[dict[str, Any]] = []
    if not _has_profile_bundle_signal(profile_bundle_hits) and not learn_hits and not gotcha_hits and not domain_hits:
        reference_hits = _filter_reference_hits(
            global_query_hits,
            excluded_ids=_collect_ids(project_hits),
            limit=limit,
        )

    recommended_action = "Search local memory first."
    if task_memory["procedure_hits"]:
        recommended_action = "Search local memory first, starting with applicable procedures and supporting concepts."
    if reference_hits and not project_hits:
        recommended_action = "Profile bundle missed; fallback reference memory may help before external search."
    if (
        not _has_profile_bundle_signal(profile_bundle_hits)
        and not project_hits
        and not learn_hits
        and not gotcha_hits
        and not domain_hits
        and not reference_hits
    ):
        recommended_action = "Local memory had no strong hits; external search may be needed."

    return {
        "query": plan.query,
        "project_namespace": plan.project_namespace,
        "global_namespace": target_global_namespace,
        "should_search_local": True,
        "tag_hints": list(plan.tag_hints),
        "profile_bundle_hits": profile_bundle_hits,
        "project_hits": project_hits,
        "learn_hits": learn_hits,
        "gotcha_hits": gotcha_hits,
        "domain_hits": domain_hits,
        "procedure_hits": task_memory["procedure_hits"],
        "concept_hits": task_memory["concept_hits"],
        "belief_hits": task_memory["belief_hits"],
        "supporting_hits": task_memory["supporting_hits"],
        "task_memory_summary": task_memory["summary"],
        "reference_hits": reference_hits,
        "recommended_action": recommended_action,
    }


def _recall_profile_bundle_hits(
    store: MemoryStore,
    *,
    global_namespace: str,
    project_namespace: str,
    query: str,
    limit: int,
) -> list[dict[str, Any]]:
    startup_plan = build_startup_recall_plan(
        global_namespace=global_namespace,
        project_namespace=project_namespace,
        issue_mode=False,
    )
    layers: list[dict[str, Any]] = []
    for layer in startup_plan[:3]:
        layers.append(
            {
                "label": layer.label,
                "namespace": layer.namespace,
                "tags_any": list(layer.tags_any),
                "items": _recall_layer_hits(store, layer=layer, query=query, limit=limit),
            }
        )
    return layers


def _recall_layer_hits(
    store: MemoryStore,
    *,
    layer: StartupRecallLayer,
    query: str,
    limit: int,
) -> list[dict[str, Any]]:
    query_hits = store.recall(
        namespace=layer.namespace,
        query=query,
        tags_any=list(layer.tags_any) if layer.tags_any else None,
        limit=limit,
    )["items"]
    if query_hits:
        return query_hits
    return store.recall(
        namespace=layer.namespace,
        tags_any=list(layer.tags_any) if layer.tags_any else None,
        limit=limit,
    )["items"]


def _infer_tag_hints(normalized_query: str) -> tuple[str, ...]:
    hints: list[str] = []
    for markers, tags in ISSUE_TAG_HINTS:
        if any(marker in normalized_query for marker in markers):
            hints.extend(tags)
    return tuple(_unique_strings(hints))


def _filter_by_tag(items: list[dict[str, Any]], tag: str) -> list[dict[str, Any]]:
    return [item for item in items if tag in item.get("tags", [])]


def _merge_hits(
    primary: list[dict[str, Any]],
    secondary: list[dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for item in [*primary, *secondary]:
        item_id = str(item.get("id") or "")
        if item_id and item_id in seen_ids:
            continue
        if item_id:
            seen_ids.add(item_id)
        merged.append(item)
        if len(merged) >= limit:
            break
    return merged


def _has_profile_bundle_signal(profile_bundle_hits: list[dict[str, Any]]) -> bool:
    return any(layer.get("items") for layer in profile_bundle_hits)


def _collect_ids(items: list[dict[str, Any]]) -> set[str]:
    return {
        item_id
        for item in items
        if (item_id := str(item.get("id") or "").strip())
    }


def _filter_reference_hits(
    items: list[dict[str, Any]],
    *,
    excluded_ids: set[str],
    limit: int,
) -> list[dict[str, Any]]:
    reference_hits: list[dict[str, Any]] = []
    for item in items:
        item_id = str(item.get("id") or "").strip()
        if item_id and item_id in excluded_ids:
            continue
        tags = item.get("tags", [])
        if any(tag in tags for tag in ("kind:learn", "kind:gotcha", "kind:domain-note")):
            continue
        reference_hits.append(item)
        if len(reference_hits) >= limit:
            break
    return reference_hits


def _unique_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        cleaned = value.strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        unique.append(cleaned)
    return unique
