from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .paths import resolve_bridge_db_path
from .recall_eligibility import direct_lookup_ineligibility_reasons
from .relation_metadata import parse_content_fields, parse_relation_metadata
from .repository_snapshot_store import RepositorySnapshotStore

RELATION_EDGE_NAMES = frozenset({"supports", "contradicts", "supersedes", "depends_on"})
HUMAN_RELATION_EDGE_NAMES = ("supersedes", "depends_on", "contradicts")
GOVERNED_MEMORY_KINDS = frozenset({"memory"})
INELIGIBLE_VALIDITY = frozenset({"expired", "future", "invalid"})
PRIMARY_SCAN_CEILING = 500
RELATION_TARGET_CEILING = 100
HUMAN_DISPLAY_LIMIT = 280
HUMAN_WHAT_ROW_LIMIT = 8
HUMAN_WHAT_VALUE_LIMIT = 3
HUMAN_DECISION_LIMIT = 3
HUMAN_CONSTRAINT_LIMIT = 3
HUMAN_RELATIONSHIP_LIMIT = 3
HUMAN_SECTION_RULE = "────────────────────────"
EMPTY_WHY_GUIDANCE = (
    "No project decisions or constraints have been explicitly stored yet.\n"
    "\n"
    "Teach one naturally:\n"
    "\n"
    '"Remember that we decided X because Y."'
)
REASON_NOT_RECORDED = "Reason not explicitly recorded."


@dataclass(frozen=True, slots=True)
class _ExplorerBuild:
    projection: dict[str, Any]
    repository_view: dict[str, Any]
    primary_items: tuple[dict[str, Any], ...]
    eligible_by_id: dict[str, dict[str, Any]]


@dataclass(frozen=True, slots=True)
class _WhyDisplay:
    memory_id: str
    record_type: str
    claim: str
    reason: str


@dataclass(frozen=True, slots=True)
class _WhatRow:
    label: str
    values: tuple[str, ...]
    overflow: int


@dataclass(frozen=True, slots=True)
class _RelationDisplay:
    source_id: str
    target_id: str
    relation: str
    source_label: str
    target_label: str


@dataclass(frozen=True, slots=True)
class _ExplorerPresentation:
    project_name: str
    what_rows: tuple[_WhatRow, ...]
    what_message: str | None
    decisions: tuple[_WhyDisplay, ...]
    constraints: tuple[_WhyDisplay, ...]
    decision_overflow: int
    constraint_overflow: int
    why_ids: tuple[str, ...]
    relationships: tuple[_RelationDisplay, ...]
    relationship_overflow: int
    status_lines: tuple[str, ...]


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
    return _build_explorer(
        namespace=namespace,
        snapshot_root=snapshot_root,
        memory_store=memory_store,
        limit=limit,
    ).projection


def _build_explorer(
    *,
    namespace: str,
    snapshot_root: Any,
    memory_store: Any | None = None,
    limit: int = 100,
) -> _ExplorerBuild:
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
    target_items, target_suppressed, found_target_ids = _read_relation_targets(
        memory_store=memory_store,
        db_path=resolve_bridge_db_path(),
        namespace=cleaned_namespace,
        target_ids=sorted(bounded_relation_target_ids),
    )
    diagnostics.extend(target_suppressed)
    eligible_by_id.update({str(item["id"]): item for item in target_items if str(item.get("id") or "")})

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
                            "reason": "ineligible_target" if target in found_target_ids else "missing_target",
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
    projection = {
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
    return _ExplorerBuild(
        projection=projection,
        repository_view=repo,
        primary_items=tuple(primary_items),
        eligible_by_id=eligible_by_id,
    )


def render_explorer_markdown(projection: dict[str, Any]) -> str:
    return render_explorer_technical_markdown(projection)


def render_explorer_technical_markdown(projection: dict[str, Any]) -> str:
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


def render_explorer_human_markdown(build: _ExplorerBuild) -> str:
    presentation = _presentation_from_build(build)
    lines = [
        f"PROJECT: {presentation.project_name}",
        "",
        "CODE / WHAT",
        HUMAN_SECTION_RULE,
        "",
    ]
    if presentation.what_message:
        lines.extend([presentation.what_message, ""])
    elif presentation.what_rows:
        for row in presentation.what_rows:
            joined = ", ".join(row.values)
            suffix = f" (+{row.overflow} more)" if row.overflow else ""
            lines.append(f"{row.label}: {joined}{suffix}")
        lines.append("")
    else:
        lines.extend(["No current repository facts are available.", ""])
    lines.extend(["CONVERSATION / WHY", HUMAN_SECTION_RULE, ""])
    if not presentation.decisions and not presentation.constraints:
        lines.extend([EMPTY_WHY_GUIDANCE, ""])
    else:
        for item in presentation.decisions:
            lines.extend(["Decision", item.claim, "", f"Reason: {item.reason}", ""])
        if presentation.decision_overflow:
            lines.extend([f"+{presentation.decision_overflow} more", ""])
        for item in presentation.constraints:
            lines.extend(["Constraint", item.claim, "", f"Reason: {item.reason}", ""])
        if presentation.constraint_overflow:
            lines.extend([f"+{presentation.constraint_overflow} more", ""])
    if presentation.relationships:
        lines.extend(["Relationships", HUMAN_SECTION_RULE, ""])
        for relation in presentation.relationships:
            lines.append(f"{relation.source_label} {relation.relation} {relation.target_label}")
        if presentation.relationship_overflow:
            lines.append(f"+{presentation.relationship_overflow} more")
        lines.append("")
    lines.extend(["STATUS", HUMAN_SECTION_RULE, ""])
    lines.extend(presentation.status_lines)
    lines.append("")
    return "\n".join(lines) + "\n"


def _presentation_from_build(build: _ExplorerBuild) -> _ExplorerPresentation:
    projection = build.projection
    namespace = str(projection["namespace"])
    project_name = _human_project_name(namespace, build.repository_view)
    why_items = _primary_why_displays(build.primary_items)
    decisions = tuple(item for item in why_items if item.record_type == "decision")
    constraints = tuple(item for item in why_items if item.record_type == "constraint")
    what_rows, what_message = _human_what_rows(build.repository_view)
    relationships = _human_relationships(build)
    status_lines = _human_status_lines(
        repository_view=build.repository_view,
        decision_count=len(decisions),
        constraint_count=len(constraints),
        fact_count=_visible_fact_count(build.repository_view),
    )
    return _ExplorerPresentation(
        project_name=project_name,
        what_rows=what_rows[:HUMAN_WHAT_ROW_LIMIT],
        what_message=what_message,
        decisions=decisions[:HUMAN_DECISION_LIMIT],
        constraints=constraints[:HUMAN_CONSTRAINT_LIMIT],
        decision_overflow=max(0, len(decisions) - HUMAN_DECISION_LIMIT),
        constraint_overflow=max(0, len(constraints) - HUMAN_CONSTRAINT_LIMIT),
        why_ids=_primary_why_ids(build.primary_items),
        relationships=relationships[:HUMAN_RELATIONSHIP_LIMIT],
        relationship_overflow=max(0, len(relationships) - HUMAN_RELATIONSHIP_LIMIT),
        status_lines=status_lines,
    )


def _primary_why_displays(primary_items: tuple[dict[str, Any], ...]) -> tuple[_WhyDisplay, ...]:
    displays: list[_WhyDisplay] = []
    for item in primary_items:
        memory_id = str(item.get("id") or "")
        if not memory_id:
            continue
        fields = parse_content_fields(str(item.get("content") or ""))
        record_type = str(item.get("record_type") or fields.get("record_type") or "")
        if str(item.get("kind") or "") not in GOVERNED_MEMORY_KINDS or record_type not in {"decision", "constraint"}:
            continue
        claim = (
            _bounded_display_text(fields.get("claim"))
            or _bounded_display_text(str(item.get("title") or ""))
            or f"Untitled {record_type}"
        )
        reason_field = fields.get("reason")
        reason = _bounded_display_text(reason_field) if reason_field else REASON_NOT_RECORDED
        displays.append(_WhyDisplay(memory_id=memory_id, record_type=record_type, claim=claim, reason=reason))
    return tuple(displays)


def _primary_why_ids(primary_items: tuple[dict[str, Any], ...]) -> tuple[str, ...]:
    ids: list[str] = []
    for item in primary_items:
        memory_id = str(item.get("id") or "")
        if not memory_id:
            continue
        fields = parse_content_fields(str(item.get("content") or ""))
        record_type = str(item.get("record_type") or fields.get("record_type") or "")
        if str(item.get("kind") or "") not in GOVERNED_MEMORY_KINDS or record_type not in {"decision", "constraint"}:
            continue
        ids.append(memory_id)
    return tuple(ids)


def _human_relationships(build: _ExplorerBuild) -> tuple[_RelationDisplay, ...]:
    labels = {str(item.get("id") or ""): _why_label(item) for item in build.primary_items if str(item.get("id") or "")}
    labels.update(
        {memory_id: _why_label(item) for memory_id, item in build.eligible_by_id.items() if memory_id not in labels}
    )
    primary_ids = {str(item.get("id") or "") for item in build.primary_items if str(item.get("id") or "")}
    relations: list[_RelationDisplay] = []
    seen: set[tuple[str, str, str]] = set()
    for item in build.primary_items:
        source_id = str(item.get("id") or "")
        if not source_id:
            continue
        metadata = parse_relation_metadata(str(item.get("content") or ""))
        for relation_name in HUMAN_RELATION_EDGE_NAMES:
            for target in metadata["relations"].get(relation_name, []):
                if target not in primary_ids and target not in build.eligible_by_id:
                    continue
                key = (source_id, relation_name, target)
                if key in seen:
                    continue
                seen.add(key)
                target_item = build.eligible_by_id.get(target)
                relations.append(
                    _RelationDisplay(
                        source_id=source_id,
                        target_id=target,
                        relation=relation_name,
                        source_label=labels.get(source_id) or _why_label(item),
                        target_label=labels.get(target)
                        or (_why_label(target_item) if target_item is not None else "Untitled decision"),
                    )
                )
    relations.sort(key=lambda item: (HUMAN_RELATION_EDGE_NAMES.index(item.relation), item.source_id, item.target_id))
    return tuple(relations)


def _why_label(item: dict[str, Any]) -> str:
    fields = parse_content_fields(str(item.get("content") or ""))
    record_type = str(item.get("record_type") or fields.get("record_type") or "")
    fallback = f"Untitled {record_type}" if record_type in {"decision", "constraint"} else "Untitled decision"
    return _bounded_display_text(fields.get("claim")) or _bounded_display_text(str(item.get("title") or "")) or fallback


def _human_what_rows(repository_view: dict[str, Any]) -> tuple[tuple[_WhatRow, ...], str | None]:
    binding_state = str(repository_view.get("binding_state") or "unbound")
    stale_reason = str(repository_view.get("stale_reason") or "")
    message = _repository_status_message(binding_state, stale_reason)
    if message is not None:
        return (), message
    grouped: dict[str, list[tuple[str, str, str, str]]] = {}
    raw_selected = repository_view.get("selected")
    facts = [fact for fact in raw_selected if isinstance(fact, dict)] if isinstance(raw_selected, list) else []
    for fact in facts:
        key = str(fact.get("key") or "")
        for label, value in _human_what_values(key, fact.get("value"), facts):
            grouped.setdefault(label, []).append(
                (
                    value.casefold(),
                    value,
                    str(fact.get("source") or ""),
                    str(fact.get("commit") or ""),
                )
            )
    rows: list[_WhatRow] = []
    for label in _WHAT_LABEL_ORDER:
        values = grouped.get(label)
        if not values:
            continue
        unique: list[str] = []
        seen: set[str] = set()
        for _casefold, raw, _source, _commit in sorted(values):
            bounded = _bounded_display_text(raw)
            if not bounded or bounded.casefold() in seen:
                continue
            seen.add(bounded.casefold())
            unique.append(bounded)
        rows.append(
            _WhatRow(
                label=label,
                values=tuple(unique[:HUMAN_WHAT_VALUE_LIMIT]),
                overflow=max(0, len(unique) - HUMAN_WHAT_VALUE_LIMIT),
            )
        )
        if len(rows) >= HUMAN_WHAT_ROW_LIMIT:
            break
    return tuple(rows), None


_WHAT_LABEL_ORDER = ("Runtime", "Package", "Package manager", "CI", "Build / container", "Project guidance")


def _human_what_values(key: str, value: Any, facts: list[dict[str, Any]]) -> list[tuple[str, str]]:
    has_repository_name = any(str(fact.get("key") or "") == "repository_name" for fact in facts)
    if key == "python_requires":
        return [("Runtime", _bounded_display_text(f"Python {value}"))]
    if key == "repository_name":
        return [("Package", _bounded_display_text(value))]
    if key == "python_package":
        if has_repository_name:
            return []
        return [("Package", _bounded_display_text("Python package"))]
    if key == "node_package":
        if has_repository_name:
            return []
        return [("Package", _bounded_display_text("Node package"))]
    if key == "package_manager":
        return [("Package manager", _bounded_display_text(value))]
    if key == "github_actions_workflows":
        names = value if isinstance(value, list) else [value]
        unique = sorted({str(name) for name in names if str(name)}, key=str.casefold)
        return [("CI", _bounded_display_text(name)) for name in unique]
    if key == "container_config":
        return [("Build / container", _bounded_display_text(value))]
    if key == "project_document":
        names = value if isinstance(value, list) else [value]
        unique = sorted({str(name) for name in names if str(name)}, key=str.casefold)
        return [("Project guidance", _bounded_display_text(name)) for name in unique]
    return []


def _repository_status_message(binding_state: str, stale_reason: str) -> str | None:
    if binding_state == "current":
        return None
    if binding_state == "unbound":
        return "No repository binding exists. Bind an explicit namespace and run `bootstrap-repo`."
    if binding_state == "missing_snapshot":
        return "No repository snapshot is available. Run explicit `bootstrap-repo` to create one."
    if binding_state == "stale" and stale_reason == "dirty_worktree":
        return (
            "Repository WHAT is withheld until the worktree is clean and explicitly re-bootstrapped. "
            "Dirty content is not attributed to HEAD."
        )
    if binding_state == "stale" and stale_reason == "worktree_status_unavailable":
        return "Repository status could not be verified, so WHAT cannot be shown as current."
    if binding_state == "stale":
        return (
            "Repository snapshot is stale. Explicit `bootstrap-repo` is required before WHAT can be shown as current."
        )
    if binding_state == "ineligible":
        return "No current repository WHAT is available from the existing source data."
    return "No current repository WHAT is available from the existing source data."


def _human_status_lines(
    *,
    repository_view: dict[str, Any],
    decision_count: int,
    constraint_count: int,
    fact_count: int,
) -> tuple[str, ...]:
    binding_state = str(repository_view.get("binding_state") or "unbound")
    stale_reason = str(repository_view.get("stale_reason") or "")
    if binding_state == "current":
        status = "Repository snapshot is current."
        commit = repository_view.get("commit") or repository_view.get("current_commit")
        if commit:
            status = f"Repository snapshot is current at `{commit}`."
    elif binding_state == "unbound":
        status = "No repository binding exists."
    elif binding_state == "missing_snapshot":
        status = "Repository snapshot is missing."
    elif binding_state == "stale" and stale_reason == "dirty_worktree":
        status = "Worktree is dirty; repository WHAT is withheld."
    elif binding_state == "stale" and stale_reason == "worktree_status_unavailable":
        status = "Repository status could not be verified."
    elif binding_state == "stale":
        status = "Repository snapshot is stale."
    else:
        status = "Repository WHAT is not currently available."
    counts = f"Decisions: {decision_count}. Constraints: {constraint_count}."
    if binding_state == "current":
        counts = f"Repository facts: {fact_count}. {counts}"
    excluded = int(repository_view.get("excluded_count") or 0)
    extra: list[str] = []
    if excluded:
        extra.append(f"+{excluded} more repository facts beyond the current view.")
    return (status, counts, *extra)


def _visible_fact_count(repository_view: dict[str, Any]) -> int:
    raw_selected = repository_view.get("selected")
    if not isinstance(raw_selected, list):
        return 0
    return len([fact for fact in raw_selected if isinstance(fact, dict)])


def _human_project_name(namespace: str, repository_view: dict[str, Any]) -> str:
    raw_selected = repository_view.get("selected")
    facts = [fact for fact in raw_selected if isinstance(fact, dict)] if isinstance(raw_selected, list) else []
    for fact in facts:
        if str(fact.get("key") or "") == "repository_name" and fact.get("value"):
            named = _bounded_display_text(fact.get("value"))
            if named:
                return named
    cleaned = namespace.strip()
    if cleaned.startswith("project:"):
        cleaned = cleaned.split(":", 1)[1]
    return _bounded_display_text(cleaned or namespace) or "project"


def _bounded_display_text(value: Any) -> str:
    text = " ".join(str(value or "").split()).strip()
    if not text:
        return ""
    if len(text) <= HUMAN_DISPLAY_LIMIT:
        return text
    return text[: HUMAN_DISPLAY_LIMIT - 1] + "…"


def _read_governed_memories(
    *, memory_store: Any | None, db_path: Path, namespace: str, limit: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if memory_store is not None:
        payload = memory_store.browse(namespace, limit=PRIMARY_SCAN_CEILING)
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


def _read_relation_targets(
    *, memory_store: Any | None, db_path: Path, namespace: str, target_ids: list[str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], set[str]]:
    bounded_ids = sorted(dict.fromkeys(target_ids))[:RELATION_TARGET_CEILING]
    if not bounded_ids:
        return [], [], set()
    if memory_store is not None:
        payload = memory_store.browse(namespace, limit=PRIMARY_SCAN_CEILING)
        raw_items = [item for item in payload.get("items") or [] if isinstance(item, dict)]
        found = {str(item.get("id") or "") for item in raw_items if str(item.get("id") or "") in bounded_ids}
        selected = [item for item in raw_items if str(item.get("id") or "") in bounded_ids]
        eligible, suppressed = _apply_memory_governance(selected, memory_store=memory_store, connection=None)
        return eligible, suppressed, found
    if not db_path.is_file():
        return [], [], set()
    connection = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        placeholders = ", ".join("?" for _ in bounded_ids)
        rows = connection.execute(
            f"""
            SELECT m.id, m.kind, m.title, m.content, m.tags_json, m.created_at,
                   m.lineage_status, mm.record_type, mm.valid_from, mm.valid_until, mm.validation_issues_json
            FROM memories AS m
            LEFT JOIN memory_metadata AS mm ON mm.memory_id = m.id
            WHERE m.namespace = ? AND m.id IN ({placeholders})
              AND COALESCE(m.is_learning_candidate, 0) = 0
            ORDER BY m.id ASC
            """,
            [namespace, *bounded_ids],
        ).fetchall()
        found = {str(row["id"]) for row in rows}
        items: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["tags"] = json.loads(item.pop("tags_json") or "[]")
            items.append(item)
        eligible, suppressed = _apply_memory_governance(items, memory_store=None, connection=connection)
        return eligible, suppressed, found
    except sqlite3.OperationalError:
        return [], [], set()
    finally:
        connection.close()


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


__all__ = [
    "build_explorer_projection",
    "render_explorer_human_markdown",
    "render_explorer_markdown",
    "render_explorer_technical_markdown",
]
