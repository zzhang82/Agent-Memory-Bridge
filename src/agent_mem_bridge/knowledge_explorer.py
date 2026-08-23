from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .paths import resolve_bridge_db_path
from .recall_eligibility import direct_lookup_ineligibility_reasons
from .relation_metadata import parse_content_fields, parse_relation_metadata
from .repository_snapshot_store import RepositorySnapshotStore

RELATION_EDGE_NAMES = frozenset({"supports", "contradicts", "supersedes", "depends_on"})
GOVERNED_MEMORY_KINDS = frozenset({"memory"})
INELIGIBLE_VALIDITY = frozenset({"expired", "future", "invalid"})
PRIMARY_SCAN_CEILING = 500
RELATION_TARGET_CEILING = 100


def build_explorer_projection(
    *,
    namespace: str,
    snapshot_root: Any,
    memory_store: Any | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Build a bounded, deterministic, read-only graph projection.

    Explorer uses the existing bound repository snapshot and applies the
    existing direct-lookup supersession rules plus the shared validity parser
    before durable records can become active projection nodes.
    """
    cleaned_namespace = namespace.strip()
    if not cleaned_namespace:
        raise ValueError("namespace must not be empty")
    bounded_limit = max(1, min(int(limit), 500))
    snapshot = RepositorySnapshotStore(snapshot_root).load_bound_snapshot(cleaned_namespace)
    repo = _repository_view(snapshot, bounded_limit)
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []

    def add_node(node_id: str, **payload: Any) -> None:
        if node_id not in nodes:
            nodes[node_id] = {"id": node_id, **payload}

    project_id = f"project:{cleaned_namespace}"
    add_node(
        project_id,
        type="project",
        label=cleaned_namespace,
        authority="derived_projection",
        source_ref={"kind": "namespace_binding", "namespace": cleaned_namespace},
    )

    repository_id = str(
        (snapshot or {}).get("local_repository_source_id") or (snapshot or {}).get("repository_id") or ""
    )
    if repo["binding_state"] == "current" and repository_id:
        repository_node = f"repository:{repository_id}"
        add_node(
            repository_node,
            type="repository",
            label=str((snapshot or {}).get("repository") or repository_id),
            authority="derived_repository",
            source_ref={"kind": "repository_snapshot", "repository_id": repository_id, "commit": repo.get("commit")},
        )
        edges.append(
            {
                "source": project_id,
                "relation": "bound_to",
                "target": repository_node,
                "evidence": {
                    "namespace": cleaned_namespace,
                    "repository_id": repository_id,
                    "authority": "derived_repository",
                },
            }
        )
        raw_selected = repo.get("selected")
        repository_facts = (
            [fact for fact in raw_selected if isinstance(fact, dict)] if isinstance(raw_selected, list) else []
        )
        for fact in sorted(repository_facts, key=_fact_sort_key):
            key = str(fact.get("key") or "fact")
            value = fact.get("value")
            node_id = f"repository-fact:{key}:{_display_value(value)}"
            add_node(
                node_id,
                type="repository_fact",
                label=_display_value(value),
                authority="derived_repository",
                source_ref={"source": fact.get("source"), "commit": fact.get("commit"), "key": key},
            )
            edges.append(
                {
                    "source": repository_node,
                    "relation": _repository_relation(key),
                    "target": node_id,
                    "evidence": {
                        "source": fact.get("source"),
                        "commit": fact.get("commit"),
                        "authority": "derived_repository",
                    },
                }
            )

    eligible_items, suppressed = _read_governed_memories(
        memory_store=memory_store,
        db_path=resolve_bridge_db_path(),
        namespace=cleaned_namespace,
        limit=bounded_limit,
    )
    diagnostics.extend(suppressed)
    eligible_by_id = {str(item["id"]): item for item in eligible_items if str(item.get("id") or "")}
    primary_items = eligible_items[:bounded_limit]
    relation_targets: list[str] = []
    for item in primary_items:
        metadata = parse_relation_metadata(str(item.get("content") or ""))
        relation_targets.extend(
            target
            for relation_name in sorted(RELATION_EDGE_NAMES)
            for target in metadata["relations"].get(relation_name, [])
        )
    relation_target_ids = set(dict.fromkeys(sorted(relation_targets)))
    bounded_relation_target_ids = set(sorted(relation_target_ids)[:RELATION_TARGET_CEILING])

    for item in primary_items:
        memory_id = str(item["id"])
        fields = parse_content_fields(str(item.get("content") or ""))
        record_type = str(item.get("record_type") or fields.get("record_type") or "")
        if str(item.get("kind") or "") not in GOVERNED_MEMORY_KINDS or record_type not in {"decision", "constraint"}:
            continue
        node_id = f"memory:{memory_id}"
        add_node(
            node_id,
            type="durable_memory",
            label=str(item.get("title") or item.get("content") or memory_id),
            authority="governed_durable_memory",
            source_ref={
                "memory_id": memory_id,
                "kind": item.get("kind"),
                "record_type": record_type,
                "namespace": cleaned_namespace,
            },
        )
        edges.append(
            {
                "source": project_id,
                "relation": "has_decision" if record_type == "decision" else "has_constraint",
                "target": node_id,
                "evidence": {
                    "memory_id": memory_id,
                    "authority": "governed_durable_memory",
                    "eligibility": "existing_governance",
                },
            }
        )
        metadata = parse_relation_metadata(str(item.get("content") or ""))
        for relation_name in sorted(RELATION_EDGE_NAMES):
            for target in metadata["relations"].get(relation_name, []):
                if target not in bounded_relation_target_ids:
                    diagnostics.append(
                        {
                            "kind": "unresolved_relation",
                            "source_memory_id": memory_id,
                            "relation": relation_name,
                            "target_memory_id": target,
                            "reason": "relation_resolution_budget_exhausted",
                        }
                    )
                    continue
                target_item = eligible_by_id.get(target)
                if target_item is None:
                    diagnostics.append(
                        {
                            "kind": "unresolved_relation",
                            "source_memory_id": memory_id,
                            "relation": relation_name,
                            "target_memory_id": target,
                            "reason": "missing_or_ineligible_target",
                        }
                    )
                    continue
                target_node = f"memory:{target}"
                target_fields = parse_content_fields(str(target_item.get("content") or ""))
                target_record_type = str(target_item.get("record_type") or target_fields.get("record_type") or "")
                add_node(
                    target_node,
                    type="durable_memory",
                    label=str(target_item.get("title") or target_item.get("content") or target),
                    authority="governed_durable_memory",
                    source_ref={
                        "memory_id": target,
                        "kind": target_item.get("kind"),
                        "record_type": target_record_type,
                        "namespace": cleaned_namespace,
                    },
                )
                edges.append(
                    {
                        "source": node_id,
                        "relation": relation_name,
                        "target": target_node,
                        "evidence": {
                            "memory_id": memory_id,
                            "target_memory_id": target,
                            "authority": "governed_durable_memory",
                            "eligibility": "existing_governance",
                        },
                    }
                )

    edges.sort(key=lambda edge: (edge["source"], edge["relation"], edge["target"]))
    diagnostics.sort(key=lambda item: json.dumps(item, sort_keys=True))
    return {
        "schema": "knowledge-explorer-v1",
        "namespace": cleaned_namespace,
        "read_only": True,
        "rebuildable": True,
        "repository": {
            "binding_state": repo.get("binding_state", "unbound"),
            "commit": repo.get("commit"),
            "current_commit": repo.get("current_commit"),
            "authority": "derived_repository",
            "excluded_count": repo.get("excluded_count", 0),
        },
        "nodes": sorted(nodes.values(), key=lambda node: str(node["id"])),
        "edges": edges,
        "diagnostics": diagnostics,
    }


def render_explorer_markdown(projection: dict[str, Any]) -> str:
    lines = [
        "# AMB Knowledge Explorer",
        "",
        f"Namespace: `{projection['namespace']}`",
        "",
        "> The graph is a read-only, rebuildable projection. It is not a source of truth.",
        "",
        f"Repository binding: `{projection['repository']['binding_state']}`",
        "",
        "## Relationships",
        "",
    ]
    if projection["repository"].get("commit"):
        lines.insert(7, f"Repository commit: `{projection['repository']['commit']}`")
    if not projection["edges"]:
        lines.append("No eligible relationships are available.")
    else:
        by_id = {node["id"]: node for node in projection["nodes"]}
        for edge in projection["edges"]:
            source = by_id[edge["source"]]
            target = by_id[edge["target"]]
            lines.extend(
                [
                    f"- **{source['label']}** — `{edge['relation']}` → **{target['label']}**",
                    f"  - authority: `{target['authority']}`",
                    f"  - source: `{json.dumps(edge['evidence'], sort_keys=True)}`",
                ]
            )
    if projection.get("diagnostics"):
        lines.extend(["", "## Unresolved or suppressed relationships", ""])
        for diagnostic in projection["diagnostics"]:
            lines.append(f"- `{json.dumps(diagnostic, sort_keys=True)}`")
    return "\n".join(lines) + "\n"


def _read_governed_memories(
    *, memory_store: Any | None, db_path: Path, namespace: str, limit: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if memory_store is not None:
        payload = memory_store.browse(namespace, limit=max(1, min(limit, 500)))
        browse_items: list[dict[str, Any]] = [item for item in payload.get("items") or [] if isinstance(item, dict)]
        eligible, suppressed = _apply_memory_governance(browse_items, memory_store=memory_store, connection=None)
        return eligible, suppressed
    if not db_path.is_file():
        return [], []
    connection = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        rows = connection.execute(
            """
            SELECT m.id, m.kind, m.title, m.content, m.tags_json, m.created_at,
                   m.lineage_status, mm.record_type, mm.valid_from, mm.valid_until, mm.validation_issues_json
            FROM memories AS m
            LEFT JOIN memory_metadata AS mm ON mm.memory_id = m.id
            WHERE m.namespace = ? AND COALESCE(m.is_learning_candidate, 0) = 0
            ORDER BY m.created_at ASC, m.id ASC
            LIMIT ?
            """,
            (namespace, PRIMARY_SCAN_CEILING),
        ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["tags"] = json.loads(item.pop("tags_json") or "[]")
            items.append(item)
        return _apply_memory_governance(items, memory_store=None, connection=connection)
    except sqlite3.OperationalError:
        return [], []
    finally:
        connection.close()


def _apply_memory_governance(
    items: list[dict[str, Any]], *, memory_store: Any | None, connection: sqlite3.Connection | None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    suppressed: list[dict[str, Any]] = []
    candidates = [item for item in items if str(item.get("kind") or "") in GOVERNED_MEMORY_KINDS]
    reasons: dict[str, str] = (
        direct_lookup_ineligibility_reasons(memory_store, candidates, connection=connection)
        if connection is not None or (memory_store is not None and hasattr(memory_store, "_connect"))
        else {}
    )
    filtered: list[dict[str, Any]] = []
    for item in candidates:
        memory_id = str(item.get("id") or "")
        reason: str | None = reasons.get(memory_id)
        metadata_issues = item.get("validation_issues_json") or item.get("validation_issues") or []
        if isinstance(metadata_issues, str):
            metadata_issues = _json_list(metadata_issues)
        validity = str(
            item.get("validity_status") or parse_relation_metadata(str(item.get("content") or ""))["validity_status"]
        )
        fields = parse_content_fields(str(item.get("content") or ""))
        persisted_lineage = str(item.get("lineage_status") or "intact").strip().lower()
        declared_lineage = str(fields.get("lineage_status") or "").strip().lower()
        if reason is None and metadata_issues:
            reason = "invalid_structured_metadata"
        if reason is None and (persisted_lineage == "degraded" or declared_lineage == "degraded"):
            reason = "lineage_status:degraded"
        if reason is None and validity in INELIGIBLE_VALIDITY:
            reason = f"validity:{validity}"
        if reason is None:
            filtered.append(item)
        else:
            suppressed.append({"kind": "suppressed_memory", "memory_id": memory_id, "reason": reason})
    candidates = filtered
    candidates = [
        item
        for item in candidates
        if str(item.get("record_type") or parse_content_fields(str(item.get("content") or "")).get("record_type") or "")
        in {"decision", "constraint"}
    ]
    return candidates, suppressed


def _repository_view(snapshot: dict[str, Any] | None, limit: int) -> dict[str, Any]:
    if snapshot is None:
        return {"binding_state": "unbound", "selected": [], "excluded_count": 0}
    if snapshot.get("binding_state") != "current":
        return {
            "binding_state": snapshot.get("binding_state"),
            "stale_reason": snapshot.get("stale_reason"),
            "commit": snapshot.get("commit"),
            "current_commit": snapshot.get("current_commit"),
            "selected": [],
            "excluded_count": 0,
        }
    raw_facts = [fact for fact in snapshot.get("facts", []) if isinstance(fact, dict)]
    visible = [
        fact
        for fact in raw_facts
        if str(fact.get("key") or "") not in {"repository_root", "commit_sha", "source_digest"}
    ]
    selected = visible[:limit]
    return {
        "binding_state": "current",
        "commit": snapshot.get("commit"),
        "current_commit": snapshot.get("current_commit"),
        "selected": [
            {
                "key": fact.get("key"),
                "value": fact.get("value"),
                "source": fact.get("source"),
                "commit": fact.get("commit"),
                "authority": "derived_repository",
            }
            for fact in selected
        ],
        "excluded_count": len(visible[len(selected) :]),
    }


def _json_list(value: Any) -> list[Any]:
    if not value:
        return []
    try:
        parsed = json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return [value]
    return parsed if isinstance(parsed, list) else [parsed]


def _repository_relation(key: str) -> str:
    return {
        "python_requires": "uses",
        "test_runner": "tests_with",
        "ci_system": "uses_ci",
        "instruction_file": "governed_by",
        "top_level_structure": "contains",
        "storage_system": "uses_storage",
    }.get(key, "has_fact")


def _fact_sort_key(fact: dict[str, Any]) -> tuple[str, str, str]:
    return (str(fact.get("key") or ""), _display_value(fact.get("value")), str(fact.get("source") or ""))


def _display_value(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value)


__all__ = ["build_explorer_projection", "render_explorer_markdown"]
