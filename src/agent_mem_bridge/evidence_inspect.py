from __future__ import annotations

import json
from typing import Any

from .repository_snapshot_store import select_repository_facts
from .review_queue import REVIEW_QUEUE_SCHEMA, build_review_queue_report
from .storage import MemoryStore
from .task_memory import assemble_task_memory

MEMORY_INSPECT_SCHEMA = "memory.inspect.v1"
MEMORY_INSPECT_BOUNDARY = "read_only_with_respect_to_user_memory_state_and_configuration"
USED_TASK_SECTIONS = (
    "procedure_hits",
    "concept_hits",
    "belief_hits",
    "domain_hits",
    "supporting_hits",
)
REVIEW_SUPPRESSION_REASONS = {
    "contradicted",
    "depends_on:ineligible",
    "depends_on:unresolved",
    "lineage_status:degraded",
    "procedure_status:unsafe",
}


def build_memory_inspect_report(
    store: MemoryStore,
    *,
    namespace: str,
    query: str,
    global_namespace: str = "global",
    technical: bool = False,
    repository_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Project existing governed task-memory decisions without durable user-authority writes."""

    cleaned_namespace = namespace.strip()
    cleaned_query = query.strip()
    cleaned_global_namespace = global_namespace.strip() or "global"
    if not cleaned_namespace:
        raise ValueError("namespace must not be empty")
    if not cleaned_query:
        raise ValueError("query must not be empty")

    task_memory = assemble_task_memory(
        store,
        query=cleaned_query,
        project_namespace=cleaned_namespace,
        global_namespace=cleaned_global_namespace,
    )
    selected = _selected_items(task_memory, include_technical=technical)
    selected_ids = {item["memory_id"] for item in selected if item["memory_id"]}
    excluded, suppression_review = _excluded_items(task_memory, include_technical=technical)
    corrective_review = _unselected_corrective_items(
        task_memory,
        selected_ids=selected_ids,
        include_technical=technical,
    )
    candidate_ids = set(selected_ids)
    candidate_ids.update(item["memory_id"] for item in excluded if item["memory_id"])
    needs_review = [
        *suppression_review,
        *corrective_review,
        *_relevant_review_queue_items(store, cleaned_namespace, candidate_ids, technical),
    ]
    repository_selected, repository_excluded = _repository_items(repository_snapshot, cleaned_query)

    return {
        "schema": MEMORY_INSPECT_SCHEMA,
        "namespace": cleaned_namespace,
        "query": cleaned_query,
        "mutation_boundary": MEMORY_INSPECT_BOUNDARY,
        "selected": selected,
        "excluded": excluded,
        "repository_knowledge": {
            "selected": repository_selected,
            "excluded": repository_excluded,
            "snapshot": _repository_snapshot_metadata(repository_snapshot),
        },
        "needs_review": _dedupe_items(needs_review),
        "explanation": {
            "selected_means": "These records surfaced in the governed task-memory result.",
            "excluded_means": "These records were candidates that existing governance deliberately left out.",
            "not_considered_boundary": "Records not retrieved or not present in governed exclusions are not described as left out.",
            "causal_boundary": "Surfaced memory is not evidence that it was applied or caused an outcome.",
        },
        "technical_details": {
            "enabled": technical,
            "task_memory_assembly_mode": task_memory.get("assembly_mode"),
            "source_schemas": {
                "task_memory": "memory.task_memory.derived",
                "review_queue": REVIEW_QUEUE_SCHEMA,
            },
            "selected_count": len(selected),
            "excluded_count": len(excluded),
            "needs_review_count": len(_dedupe_items(needs_review)),
            "unresolved_relation_target_count": len(task_memory.get("unresolved_relation_targets") or []),
        },
    }


def render_memory_inspect_markdown(report: dict[str, Any]) -> str:
    repository = report.get("repository_knowledge") or {}
    lines = ["# AMB Inspect", "", "## Question", "", report["query"], "", "## Repository knowledge (WHAT)", ""]
    repository_snapshot = repository.get("snapshot") or {}
    if repository_snapshot.get("binding_state") == "current":
        lines.extend(
            _render_repository_items(repository.get("selected") or [], empty="No relevant repository facts surfaced.")
        )
    elif repository_snapshot:
        lines.append(
            f"Repository knowledge is unavailable for current truth: {repository_snapshot.get('stale_reason') or repository_snapshot.get('binding_state') or 'ineligible'}."
        )
    else:
        lines.append("No repository is bound to this project namespace.")
    lines.extend(["", "## What AMB remembered", "", "### Durable project memory (WHY)", ""])
    lines.extend(_render_items(report["selected"], empty="No governed memories surfaced for this question."))
    lines.extend(["## Why this appeared", ""])
    if report["selected"]:
        for item in report["selected"]:
            title = item["title"] or item["memory_id"]
            lines.append(f"- **{title}** — {' '.join(item['why'])}")
    else:
        lines.append("No selected memory has an explanation because no governed memory surfaced.")
    lines.extend(["", "## What AMB left out", ""])
    lines.extend(_render_items(report["excluded"], empty="No relevant governed exclusions were recorded."))
    lines.extend(["## Needs review", ""])
    lines.extend(_render_items(report["needs_review"], empty="No relevant evidence currently requires human review."))
    if report["technical_details"]["enabled"]:
        lines.extend(["## Technical details", ""])
        for item in [*report["selected"], *report["excluded"], *report["needs_review"]]:
            technical = item.get("technical") or {}
            if not technical:
                continue
            heading = item["title"] or item["memory_id"]
            lines.append(f"- **{heading}**: `{technical}`")
        lines.append("")
        lines.append(
            "This report shows selection and governance evidence only; it does not prove memory application or causal success."
        )
    return "\n".join(lines).rstrip() + "\n"


def _repository_items(snapshot: dict[str, Any] | None, query: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not snapshot or snapshot.get("binding_state") != "current":
        return [], []
    selected, excluded = select_repository_facts(snapshot, query)

    def project(fact: dict[str, Any], status: str) -> dict[str, Any]:
        return {
            "fact_kind": str(fact.get("key") or "repository_fact"),
            "value": fact.get("value"),
            "source": fact.get("source"),
            "commit": fact.get("commit"),
            "authority": "derived_repository",
            "status": status,
        }

    return [project(fact, "current") for fact in selected], [project(fact, "not_selected") for fact in excluded]


def _repository_snapshot_metadata(snapshot: dict[str, Any] | None) -> dict[str, Any] | None:
    if not snapshot:
        return None
    return {
        "repository_id": snapshot.get("repository_id"),
        "root": snapshot.get("root"),
        "commit": snapshot.get("commit"),
        "current_commit": snapshot.get("current_commit"),
        "binding": snapshot.get("binding"),
        "binding_state": snapshot.get("binding_state"),
        "stale_reason": snapshot.get("stale_reason"),
        "authority": "derived_repository",
    }


def _render_repository_items(items: list[dict[str, Any]], *, empty: str) -> list[str]:
    if not items:
        return [empty]
    lines: list[str] = []
    for item in items:
        lines.append(f"- **{item['fact_kind']}**: {json.dumps(item['value'], ensure_ascii=False, sort_keys=True)}")
        lines.append(
            f"  source: `{item.get('source')}`; commit: `{item.get('commit') or 'unavailable'}`; authority: `derived_repository`"
        )
    return lines


def _selected_items(task_memory: dict[str, Any], *, include_technical: bool) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for section in USED_TASK_SECTIONS:
        for item in task_memory.get(section) or []:
            decision = item.get("task_memory") or {}
            selected.append(
                _item(
                    memory_id=str(item.get("id") or ""),
                    title=item.get("title"),
                    namespace=item.get("namespace"),
                    summary=_bounded_summary(item.get("content")),
                    status="current" if section != "procedure_hits" else _procedure_status(item),
                    why=_selected_explanations(decision.get("reasons") or []),
                    reason_codes=list(decision.get("reasons") or []),
                    include_technical=include_technical,
                    technical={
                        "selected_as": decision.get("selected_as"),
                        "score": decision.get("score"),
                        "section": section,
                    },
                )
            )
    return selected


def _excluded_items(
    task_memory: dict[str, Any], *, include_technical: bool
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    excluded: list[dict[str, Any]] = []
    needs_review: list[dict[str, Any]] = []
    for item in task_memory.get("suppressed_items") or []:
        reason = str(item.get("reason") or "suppressed")
        projected = _item(
            memory_id=str(item.get("id") or ""),
            title=item.get("title"),
            namespace=None,
            summary=None,
            status="left_out",
            why=[_suppression_explanation(reason)],
            reason_codes=[reason],
            include_technical=include_technical,
            technical={
                "section": item.get("section"),
                "blocked_by_id": item.get("by_id"),
                "blocked_by_title": item.get("by_title"),
                "score": item.get("score"),
                "blocked_by_record_type": item.get("by_record_type"),
            },
        )
        excluded.append(projected)
        if reason in REVIEW_SUPPRESSION_REASONS or _is_corrective_supersession(item, reason):
            needs_review.append(
                {
                    **projected,
                    "status": "needs_review",
                    "why": [
                        _corrective_supersession_explanation()
                        if _is_corrective_supersession(item, reason)
                        else _review_explanation(reason)
                    ],
                }
            )
    for item in task_memory.get("unresolved_relation_targets") or []:
        reason = str(item.get("reason") or "unresolved")
        needs_review.append(
            _item(
                memory_id=str(item.get("target_id") or ""),
                title=None,
                namespace=None,
                summary=None,
                status="needs_review",
                why=["A related record could not be resolved, so a person should review the dependency."],
                reason_codes=["unresolved_relation_target", reason],
                include_technical=include_technical,
                technical={
                    "tombstone_kind": item.get("tombstone_kind"),
                    "tombstone_deleted_at": item.get("tombstone_deleted_at"),
                },
            )
        )
    return excluded, needs_review


def _unselected_corrective_items(
    task_memory: dict[str, Any], *, selected_ids: set[str], include_technical: bool
) -> list[dict[str, Any]]:
    needs_review: list[dict[str, Any]] = []
    for item in task_memory.get("corrective_items") or []:
        memory_id = str(item.get("id") or "")
        if not memory_id or memory_id in selected_ids:
            continue
        decision = item.get("task_memory") or {}
        needs_review.append(
            _item(
                memory_id=memory_id,
                title=item.get("title"),
                namespace=item.get("namespace"),
                summary=_bounded_summary(item.get("content")),
                status="needs_review",
                why=[_corrective_supersession_explanation()],
                reason_codes=[*list(decision.get("reasons") or []), "corrective-evidence"],
                include_technical=include_technical,
                technical={
                    "selected_as": "corrective-evidence",
                    "source_section": "corrective_items",
                },
            )
        )
    return needs_review


def _is_corrective_supersession(item: dict[str, Any], reason: str) -> bool:
    return (
        reason == "superseded"
        and item.get("section") == "procedure"
        and item.get("by_record_type") in {"belief", "state-change"}
    )


def _corrective_supersession_explanation() -> str:
    return "Current corrective evidence may affect an older procedure and should be reviewed before replacing guidance."


def _relevant_review_queue_items(
    store: MemoryStore, namespace: str, candidate_ids: set[str], technical: bool
) -> list[dict[str, Any]]:
    if not candidate_ids:
        return []
    review_queue = build_review_queue_report(store, namespace=namespace, limit=100, include_closed=False)
    items: list[dict[str, Any]] = []
    for item in review_queue.get("items") or []:
        source_id = str(item.get("source_record_id") or "")
        if source_id not in candidate_ids:
            continue
        items.append(
            _item(
                memory_id=source_id,
                title=item.get("title"),
                namespace=item.get("namespace"),
                summary=None,
                status="needs_review",
                why=["Existing review evidence says this record needs human review before any action."],
                reason_codes=list(item.get("reason_codes") or []),
                include_technical=technical,
                technical={
                    "review_item_type": item.get("item_type"),
                    "priority": item.get("priority"),
                    "recommended_action": item.get("recommended_action"),
                },
            )
        )
    return items


def _item(
    *,
    memory_id: str,
    title: object,
    namespace: object,
    summary: str | None,
    status: str,
    why: list[str],
    reason_codes: list[str],
    include_technical: bool,
    technical: dict[str, Any],
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "memory_id": memory_id,
        "title": str(title).strip() if title else None,
        "namespace": str(namespace).strip() if namespace else None,
        "summary": summary,
        "status": status,
        "why": why,
    }
    if include_technical:
        result["technical"] = {key: value for key, value in technical.items() if value is not None}
        result["reason_codes"] = [code for code in reason_codes if code]
    return result


def _selected_explanations(reason_codes: list[object]) -> list[str]:
    mapping = {
        "direct:procedure": "It directly matches this task as a procedure.",
        "direct:concept": "It directly matches this task as project guidance.",
        "direct:belief": "It directly matches this task as a current belief.",
        "direct:domain": "It directly matches this task as domain context.",
        "direct:support": "It supports another selected memory.",
        "relation-target": "It supports a selected memory through an existing relation.",
        "namespace:project": "It belongs to this project namespace.",
        "namespace:global": "It is shared guidance available to this project.",
        "procedure_status:current": "Its procedure status is current.",
    }
    explanations = [mapping[str(code)] for code in reason_codes if str(code) in mapping]
    return explanations or ["It is part of the governed task-memory result for this question."]


def _suppression_explanation(reason: str) -> str:
    if reason == "superseded" or reason == "superseded_revision":
        return "Superseded by a newer memory."
    if reason == "validity:expired":
        return "Out of date for this task."
    if reason == "validity:future":
        return "Not yet valid for this task."
    if reason == "validity:invalid":
        return "Its validity window is invalid, so governance left it out."
    if reason == "procedure_status:unsafe":
        return "Marked unsafe to use."
    if reason == "procedure_status:stale":
        return "Marked stale and not used automatically."
    if reason == "procedure_status:replaced":
        return "Replaced by current guidance."
    if reason == "depends_on:ineligible":
        return "Depends on evidence that is not currently eligible."
    if reason == "depends_on:unresolved":
        return "Depends on evidence that could not be resolved."
    if reason == "contradicted":
        return "Conflicts with stronger current guidance."
    if reason == "lineage_status:degraded":
        return "Has incomplete lineage evidence."
    return "Existing governance kept this candidate out of the task-memory result."


def _review_explanation(reason: str) -> str:
    if reason == "procedure_status:unsafe":
        return "This procedure is marked unsafe and needs human review."
    if reason.startswith("depends_on:"):
        return "This guidance depends on unresolved or ineligible evidence and needs review."
    if reason == "contradicted":
        return "This guidance conflicts with another candidate and needs review."
    return "Existing governance indicates this candidate needs human review."


def _procedure_status(item: dict[str, Any]) -> str:
    status = ((item.get("procedure") or {}).get("governance") or {}).get("status")
    return str(status) if status else "current"


def _bounded_summary(content: object, limit: int = 180) -> str | None:
    text = " ".join(str(content or "").split())
    if not text:
        return None
    return text[: limit - 1] + "…" if len(text) > limit else text


def _render_items(items: list[dict[str, Any]], *, empty: str) -> list[str]:
    if not items:
        return [empty, ""]
    lines: list[str] = []
    for item in items:
        heading = item["title"] or item["memory_id"] or "Unnamed evidence"
        lines.append(f"- **{heading}**")
        if item.get("summary"):
            lines.append(f"  - {item['summary']}")
        for reason in item["why"]:
            lines.append(f"  - {reason}")
    lines.append("")
    return lines


def _dedupe_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for item in items:
        key = (str(item.get("memory_id") or ""), tuple(item.get("why") or []))
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result
