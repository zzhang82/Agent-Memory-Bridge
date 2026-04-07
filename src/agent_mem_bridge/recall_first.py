from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .storage import MemoryStore


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
    if not plan.should_search_local:
        return {
            "query": plan.query,
            "project_namespace": plan.project_namespace,
            "global_namespace": target_global_namespace,
            "should_search_local": False,
            "tag_hints": list(plan.tag_hints),
            "project_hits": [],
            "learn_hits": [],
            "gotcha_hits": [],
            "domain_hits": [],
            "recommended_action": "No local-first trigger fired.",
        }

    project_hits = store.recall(
        namespace=plan.project_namespace,
        query=plan.query,
        limit=limit,
    )["items"]

    learn_hits = _filter_by_tag(
        store.recall(
            namespace=target_global_namespace,
            query=plan.query,
            limit=limit,
        )["items"],
        "kind:learn",
    )
    gotcha_hits = _filter_by_tag(
        store.recall(
            namespace=target_global_namespace,
            query=plan.query,
            limit=limit,
        )["items"],
        "kind:gotcha",
    )
    domain_hits = _filter_by_tag(
        store.recall(
            namespace=target_global_namespace,
            query=plan.query,
            limit=limit,
        )["items"],
        "kind:domain-note",
    )

    if plan.tag_hints:
        tag_hint_hits = store.recall(
            namespace=target_global_namespace,
            tags_any=list(plan.tag_hints),
            limit=max(limit * 3, 10),
        )["items"]
        learn_hits = _merge_hits(learn_hits, _filter_by_tag(tag_hint_hits, "kind:learn"), limit)
        gotcha_hits = _merge_hits(gotcha_hits, _filter_by_tag(tag_hint_hits, "kind:gotcha"), limit)
        domain_hits = _merge_hits(domain_hits, _filter_by_tag(tag_hint_hits, "kind:domain-note"), limit)

    recommended_action = "Search local memory first."
    if not project_hits and not learn_hits and not gotcha_hits and not domain_hits:
        recommended_action = "Local memory had no strong hits; external search may be needed."

    return {
        "query": plan.query,
        "project_namespace": plan.project_namespace,
        "global_namespace": target_global_namespace,
        "should_search_local": True,
        "tag_hints": list(plan.tag_hints),
        "project_hits": project_hits,
        "learn_hits": learn_hits,
        "gotcha_hits": gotcha_hits,
        "domain_hits": domain_hits,
        "recommended_action": recommended_action,
    }


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
