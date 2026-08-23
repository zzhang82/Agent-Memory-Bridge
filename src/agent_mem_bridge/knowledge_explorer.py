from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .paths import resolve_bridge_db_path
from .relation_metadata import parse_content_fields, parse_relation_metadata
from .repository_snapshot_store import RepositorySnapshotStore

RELATION_EDGE_NAMES = frozenset({"supports", "contradicts", "supersedes", "depends_on"})
GOVERNED_MEMORY_KINDS = frozenset({"memory"})


def build_explorer_projection(
    *,
    namespace: str,
    snapshot_root: Any,
    memory_store: Any | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Build a bounded, deterministic, read-only graph projection.

    The projection reads the existing bound repository snapshot and existing
    governed memory browse surface. It never writes either authority.
    """
    cleaned_namespace = namespace.strip()
    if not cleaned_namespace:
        raise ValueError("namespace must not be empty")
    bounded_limit = max(1, min(int(limit), 500))
    snapshot = RepositorySnapshotStore(snapshot_root).load_bound_snapshot(cleaned_namespace)
    if snapshot is None:
        repo = {"binding_state": "unbound", "selected": [], "excluded_count": 0}
    elif snapshot.get("binding_state") != "current":
        repo = {
            "binding_state": snapshot.get("binding_state"),
            "stale_reason": snapshot.get("stale_reason"),
            "commit": snapshot.get("commit"),
            "current_commit": snapshot.get("current_commit"),
            "selected": [],
            "excluded_count": 0,
        }
    else:
        raw_facts = [fact for fact in snapshot.get("facts", []) if isinstance(fact, dict)]
        selected = [
            fact
            for fact in raw_facts
            if str(fact.get("key") or "") not in {"repository_root", "commit_sha", "source_digest"}
        ][:bounded_limit]
        excluded = raw_facts[len(selected) :]
        repo = {
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
            "excluded_count": len(excluded),
        }
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []

    def add_node(node_id: str, **payload: Any) -> None:
        if node_id not in nodes:
            nodes[node_id] = {"id": node_id, **payload}

    repository_id = str(
        (snapshot or {}).get("local_repository_source_id") or (snapshot or {}).get("repository_id") or ""
    )
    if repo.get("binding_state") == "current" and repository_id:
        project_id = f"repository:{repository_id}"
        add_node(
            project_id,
            type="project",
            label=str((snapshot or {}).get("repository") or repository_id),
            authority="derived_repository",
            source_ref={"kind": "repository_snapshot", "repository_id": repository_id, "commit": repo.get("commit")},
        )
        raw_selected = repo.get("selected")
        repository_facts = (
            [fact for fact in raw_selected if isinstance(fact, dict)] if isinstance(raw_selected, list) else []
        )
        for fact in sorted(repository_facts, key=_fact_sort_key):
            key = str(fact.get("key") or "fact")
            value = fact.get("value")
            label = _display_value(value)
            node_id = f"repository-fact:{key}:{label}"
            add_node(
                node_id,
                type="repository_fact",
                label=label,
                authority="derived_repository",
                source_ref={"source": fact.get("source"), "commit": fact.get("commit"), "key": key},
            )
            relation = _repository_relation(key)
            edges.append(
                {
                    "source": project_id,
                    "relation": relation,
                    "target": node_id,
                    "evidence": {
                        "source": fact.get("source"),
                        "commit": fact.get("commit"),
                        "authority": "derived_repository",
                    },
                }
            )

    memory_items: list[dict[str, Any]] = _read_governed_memories(
        memory_store=memory_store,
        db_path=resolve_bridge_db_path(),
        namespace=cleaned_namespace,
        limit=bounded_limit,
    )
    for item in sorted(memory_items, key=lambda row: str(row.get("id") or "")):
        kind = str(item.get("kind") or "")
        if kind not in GOVERNED_MEMORY_KINDS:
            continue
        fields = parse_content_fields(str(item.get("content") or ""))
        record_type = fields.get("record_type")
        if record_type not in {"decision", "constraint"}:
            continue
        memory_id = str(item.get("id") or "")
        if not memory_id:
            continue
        node_id = f"memory:{memory_id}"
        add_node(
            node_id,
            type="durable_memory",
            label=str(item.get("title") or item.get("content") or memory_id),
            authority="governed_durable_memory",
            source_ref={"memory_id": memory_id, "kind": kind, "namespace": cleaned_namespace},
        )
        project_id = f"memory-project:{cleaned_namespace}"
        add_node(
            project_id,
            type="project",
            label=cleaned_namespace,
            authority="governed_durable_memory",
            source_ref={"namespace": cleaned_namespace},
        )
        relation = "has_decision" if record_type == "decision" else "has_constraint"
        edges.append(
            {
                "source": project_id,
                "relation": relation,
                "target": node_id,
                "evidence": {"memory_id": memory_id, "authority": "governed_durable_memory"},
            }
        )
        metadata = parse_relation_metadata(str(item.get("content") or ""))
        for relation_name in sorted(RELATION_EDGE_NAMES):
            for target in metadata["relations"].get(relation_name, []):
                target_id = f"memory:{target}"
                add_node(
                    target_id,
                    type="durable_memory_reference",
                    label=target,
                    authority="governed_durable_memory",
                    source_ref={"memory_id": memory_id, "relation": relation_name},
                )
                edges.append(
                    {
                        "source": node_id,
                        "relation": relation_name,
                        "target": target_id,
                        "evidence": {"memory_id": memory_id, "authority": "governed_durable_memory"},
                    }
                )

    edges.sort(key=lambda edge: (edge["source"], edge["relation"], edge["target"]))
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
    ]
    if projection["repository"].get("commit"):
        lines.append(f"Repository commit: `{projection['repository']['commit']}`")
    lines.extend(["", "## Relationships", ""])
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
    return "\n".join(lines) + "\n"


def _read_governed_memories(
    *, memory_store: Any | None, db_path: Path, namespace: str, limit: int
) -> list[dict[str, Any]]:
    if memory_store is not None:
        payload = memory_store.browse(namespace, limit=limit)
        return [item for item in payload.get("items") or [] if isinstance(item, dict)]
    if not db_path.is_file():
        return []
    connection = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        rows = connection.execute(
            """
            SELECT id, kind, title, content
            FROM memories
            WHERE namespace = ? AND COALESCE(is_learning_candidate, 0) = 0
            ORDER BY created_at ASC, id ASC
            LIMIT ?
            """,
            (namespace, max(1, min(limit, 500))),
        ).fetchall()
        return [dict(row) for row in rows]
    except sqlite3.OperationalError:
        return []
    finally:
        connection.close()


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
