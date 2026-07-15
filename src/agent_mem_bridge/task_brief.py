from __future__ import annotations

import gc
from collections import Counter
from datetime import UTC, datetime, timedelta
import tempfile
from pathlib import Path
from typing import Any

from .learning_policy import evaluate_learning_candidate
from .relation_metadata import parse_content_fields
from .review_queue import REVIEW_QUEUE_SCHEMA, build_review_queue_report
from .storage import MemoryStore
from .task_memory import assemble_task_memory


TASK_BRIEF_SCHEMA = "memory.task_brief.v1"
TASK_BRIEF_BENCHMARK_SCHEMA = "memory.task_brief_benchmark.v1"
TASK_BRIEF_BOUNDARY = "read_only_report_no_auto_writeback"
DEFAULT_TASK_BRIEF_REPORT_PATH = Path(__file__).resolve().parents[2] / "benchmark" / "latest-task-brief-report.json"

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


def build_task_brief_report(
    store: MemoryStore,
    *,
    query: str,
    namespace: str,
    global_namespace: str = "global",
    review_limit: int = 100,
    signal_limit: int = 20,
    generated_at: str | None = None,
    as_of: datetime | str | None = None,
    task_domain: str | None = None,
) -> dict[str, Any]:
    """Render an operator Task Brief from existing AMB reports without durable writes."""

    cleaned_query = query.strip()
    cleaned_namespace = namespace.strip()
    cleaned_global_namespace = global_namespace.strip() or "global"
    if not cleaned_query:
        raise ValueError("query must not be empty")
    if not cleaned_namespace:
        raise ValueError("namespace must not be empty")

    task_memory = assemble_task_memory(
        store,
        query=cleaned_query,
        project_namespace=cleaned_namespace,
        global_namespace=cleaned_global_namespace,
        as_of=as_of,
        task_domain=task_domain,
    )
    review_queue = build_review_queue_report(
        store,
        namespace=cleaned_namespace,
        limit=review_limit,
        include_closed=False,
        generated_at=generated_at,
    )
    active_signals = _load_active_signals(store, namespace=cleaned_namespace, limit=signal_limit)

    used = _used_items(task_memory)
    ignored, task_review_items = _task_decision_items(task_memory)
    needs_review = [
        *task_review_items,
        *[_review_queue_item(item) for item in review_queue["items"]],
        *[_signal_item(item) for item in active_signals],
    ]

    sections = {
        "used": used,
        "ignored": ignored,
        "needs_review": needs_review,
    }
    return {
        "schema": TASK_BRIEF_SCHEMA,
        "query": cleaned_query,
        "namespace": cleaned_namespace,
        "global_namespace": cleaned_global_namespace,
        "as_of": task_memory.get("as_of"),
        "task_domain": task_memory.get("task_domain"),
        "generated_at": generated_at or datetime.now(UTC).isoformat(),
        "mutation_boundary": TASK_BRIEF_BOUNDARY,
        "writeback_boundary": "proposal_only_no_auto_writeback",
        "public_mcp_surface_change": False,
        "source_schemas": {
            "review_queue": REVIEW_QUEUE_SCHEMA,
            "task_memory": "memory.task_memory.derived",
        },
        "task_memory_assembly_mode": task_memory.get("assembly_mode"),
        "summary": _summary(sections, review_queue_item_count=len(review_queue["items"]), active_signal_count=len(active_signals)),
        "sections": sections,
    }


def render_task_brief_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    sections = report["sections"]
    lines = [
        "# AMB Task Brief",
        "",
        f"- schema: `{report['schema']}`",
        f"- namespace: `{report['namespace']}`",
        f"- global_namespace: `{report['global_namespace']}`",
        f"- query: {report['query']}",
        f"- mutation_boundary: `{report['mutation_boundary']}`",
        f"- writeback_boundary: `{report['writeback_boundary']}`",
        f"- public_mcp_surface_change: `{str(report['public_mcp_surface_change']).lower()}`",
        f"- used_count: `{summary['used_count']}`",
        f"- ignored_count: `{summary['ignored_count']}`",
        f"- needs_review_count: `{summary['needs_review_count']}`",
        "",
    ]
    lines.extend(_render_section("Used", sections["used"]))
    lines.extend(_render_section("Ignored", sections["ignored"]))
    lines.extend(_render_section("Needs Review", sections["needs_review"]))
    return "\n".join(lines)


def run_task_brief_benchmark() -> dict[str, Any]:
    """Run a deterministic fixture proof for the Task Brief report."""

    report = build_task_brief_fixture_report()
    summary = report["summary"]
    return {
        "schema": TASK_BRIEF_BENCHMARK_SCHEMA,
        "summary": {
            "task_brief_used_count": summary["used_count"],
            "task_brief_ignored_count": summary["ignored_count"],
            "task_brief_needs_review_count": summary["needs_review_count"],
            "task_brief_review_queue_item_count": summary["review_queue_item_count"],
            "task_brief_active_signal_count": summary["active_signal_count"],
            "task_brief_no_auto_writeback": summary["task_brief_no_auto_writeback"],
            "task_brief_public_mcp_surface_change": summary["task_brief_public_mcp_surface_change"],
            "task_brief_needs_review_source_type_count": len(summary["needs_review_source_counts"]),
        },
    }


def build_task_brief_fixture_report() -> dict[str, Any]:
    """Build the stable fixture report shared by Task Brief proof layers."""

    with tempfile.TemporaryDirectory() as temp_dir:
        store = MemoryStore(Path(temp_dir) / "bridge.db", log_dir=Path(temp_dir) / "logs")
        _seed_task_brief_fixture(store)
        report = build_task_brief_report(
            store,
            namespace="project:task-brief-fixture",
            query="release handoff",
            generated_at="2026-07-01T00:00:00+00:00",
        )
        del store
        gc.collect()
    return report


def _used_items(task_memory: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    corrective_ids = {
        str(item.get("id") or "")
        for item in task_memory.get("corrective_items") or []
    }
    for section_name in USED_TASK_SECTIONS:
        for item in task_memory.get(section_name) or []:
            decision = item.get("task_memory") or {}
            extras = {
                "selected_as": decision.get("selected_as"),
                "score": decision.get("score"),
            }
            if str(item.get("id") or "") in corrective_ids:
                extras.update(
                    {
                        "corrective_evidence": _corrective_evidence(item),
                        "recommended_action": "review_current_evidence_before_replacing_procedure",
                    }
                )
            items.append(
                _base_brief_item(
                    source="task_memory",
                    source_section=section_name,
                    source_record_id=item.get("id"),
                    title=item.get("title"),
                    namespace=item.get("namespace"),
                    kind=item.get("kind"),
                    reason_codes=[*decision.get("reasons", []), f"selected_as:{decision.get('selected_as', 'unknown')}"],
                    extras=extras,
                )
            )
    return items


def _task_decision_items(task_memory: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    ignored: list[dict[str, Any]] = []
    needs_review: list[dict[str, Any]] = []
    for item in task_memory.get("suppressed_items") or []:
        reason = str(item.get("reason") or "suppressed")
        lineage_issue_ids = _lineage_issue_ids(item.get("lineage_issues"))
        corrective_supersession = (
            reason == "superseded"
            and item.get("section") == "procedure"
            and item.get("by_record_type") in {"belief", "state-change"}
        )
        target = needs_review if reason in REVIEW_SUPPRESSION_REASONS or corrective_supersession else ignored
        target.append(
            _base_brief_item(
                source="task_memory",
                source_section="suppressed_items",
                source_record_id=item.get("id"),
                title=item.get("title"),
                namespace=None,
                kind=None,
                reason_codes=[reason],
                extras={
                    "blocked_by_id": item.get("by_id"),
                    "blocked_by_title": item.get("by_title"),
                    "blocked_by_record_type": item.get("by_record_type"),
                    "source_task_section": item.get("section"),
                    "score": item.get("score"),
                    "lineage_issue_count": len(item.get("lineage_issues") or []) or None,
                    "missing_lineage_record_ids": lineage_issue_ids or None,
                    "recommended_action": (
                        "review_current_evidence_before_replacing_procedure"
                        if corrective_supersession
                        else None
                    ),
                },
            )
        )
    for item in task_memory.get("unresolved_relation_targets") or []:
        needs_review.append(
            _base_brief_item(
                source="task_memory",
                source_section="unresolved_relation_targets",
                source_record_id=item.get("target_id"),
                title=None,
                namespace=None,
                kind=None,
                reason_codes=["unresolved_relation_target", str(item.get("reason") or "unknown")],
                extras={
                    "blocked_until": "relation_target_resolved_or_removed",
                    "tombstone_namespace": item.get("tombstone_namespace"),
                    "tombstone_kind": item.get("tombstone_kind"),
                    "tombstone_deleted_at": item.get("tombstone_deleted_at"),
                    "tombstone_root_forget_id": item.get("tombstone_root_forget_id"),
                    "tombstone_cause": item.get("tombstone_cause"),
                },
            )
        )
    for item in task_memory.get("descriptive_dependencies") or []:
        ignored.append(
            _base_brief_item(
                source="task_memory",
                source_section="descriptive_dependencies",
                source_record_id=item.get("source_id"),
                title=item.get("source_title"),
                namespace=None,
                kind=None,
                reason_codes=["descriptive-dependency"],
                extras={
                    "dependency_value": item.get("value"),
                    "classification_reason": item.get("reason"),
                },
            )
        )
    used_ids = {
        str(item.get("id") or "")
        for section_name in USED_TASK_SECTIONS
        for item in task_memory.get(section_name) or []
    }
    for item in task_memory.get("corrective_items") or []:
        if str(item.get("id") or "") in used_ids:
            continue
        needs_review.append(_corrective_item(item))
    return ignored, needs_review


def _corrective_item(item: dict[str, Any]) -> dict[str, Any]:
    decision = item.get("task_memory") or {}
    return _base_brief_item(
        source="task_memory",
        source_section="corrective_items",
        source_record_id=item.get("id"),
        title=item.get("title"),
        namespace=item.get("namespace"),
        kind=item.get("kind"),
        reason_codes=[*decision.get("reasons", []), "corrective-evidence"],
        extras={
            "selected_as": "corrective-evidence",
            "corrective_evidence": _corrective_evidence(item),
            "recommended_action": "review_current_evidence_before_replacing_procedure",
        },
    )


def _corrective_evidence(item: dict[str, Any]) -> str | None:
    fields = parse_content_fields(str(item.get("content") or ""))
    return next(
        (fields[name] for name in ("claim", "current_state", "change", "summary") if fields.get(name)),
        None,
    )


def _lineage_issue_ids(raw_issues: object) -> list[str]:
    if not isinstance(raw_issues, list):
        return []
    return _dedupe(
        [
            str(issue.get("missing_record_id") or "")
            for issue in raw_issues
            if isinstance(issue, dict)
        ]
    )


def _review_queue_item(item: dict[str, Any]) -> dict[str, Any]:
    return _base_brief_item(
        source="review_queue",
        source_section="items",
        source_record_id=item.get("source_record_id"),
        title=item.get("title"),
        namespace=item.get("namespace"),
        kind="memory",
        reason_codes=item.get("reason_codes") or [],
        extras={
            "source_queue_item_id": item.get("id"),
            "item_type": item.get("item_type"),
            "priority": item.get("priority"),
            "status": item.get("status"),
            "recommended_action": item.get("recommended_action"),
            "writeback_boundary": (item.get("writeback_plan") or {}).get("boundary"),
        },
    )


def _signal_item(item: dict[str, Any]) -> dict[str, Any]:
    return _base_brief_item(
        source="signal",
        source_section="active_signals",
        source_record_id=item.get("id"),
        title=item.get("title"),
        namespace=item.get("namespace"),
        kind="signal",
        reason_codes=[f"signal_status:{item.get('signal_status') or 'pending'}"],
        extras={
            "signal_status": item.get("signal_status"),
            "claimed_by": item.get("claimed_by"),
            "lease_expires_at": item.get("lease_expires_at"),
            "blocked_until": "signal_acked_or_expired",
        },
    )


def _base_brief_item(
    *,
    source: str,
    source_section: str,
    source_record_id: object,
    title: object,
    namespace: object,
    kind: object,
    reason_codes: list[str],
    extras: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "source": source,
        "source_section": source_section,
        "source_record_id": str(source_record_id or ""),
        "title": str(title).strip() if title else None,
        "namespace": str(namespace).strip() if namespace else None,
        "kind": str(kind).strip() if kind else None,
        "reason_codes": _dedupe(reason_codes),
        **(extras or {}),
    }


def _load_active_signals(store: MemoryStore, *, namespace: str, limit: int) -> list[dict[str, Any]]:
    signal_limit = max(0, min(limit, 100))
    if signal_limit == 0:
        return []

    active: list[dict[str, Any]] = []
    seen: set[str] = set()
    for status in ("pending", "claimed"):
        payload = store.recall(
            namespace=namespace,
            kind="signal",
            signal_status=status,
            limit=signal_limit,
        )
        for item in payload["items"]:
            item_id = str(item.get("id") or "")
            if not item_id or item_id in seen:
                continue
            seen.add(item_id)
            active.append(item)
            if len(active) >= signal_limit:
                return active
    return active


def _summary(
    sections: dict[str, list[dict[str, Any]]],
    *,
    review_queue_item_count: int,
    active_signal_count: int,
) -> dict[str, Any]:
    needs_review_sources = Counter(item["source"] for item in sections["needs_review"])
    return {
        "used_count": len(sections["used"]),
        "ignored_count": len(sections["ignored"]),
        "needs_review_count": len(sections["needs_review"]),
        "review_queue_item_count": review_queue_item_count,
        "active_signal_count": active_signal_count,
        "needs_review_source_counts": dict(sorted(needs_review_sources.items())),
        "task_brief_no_auto_writeback": True,
        "task_brief_public_mcp_surface_change": False,
    }


def _render_section(title: str, items: list[dict[str, Any]]) -> list[str]:
    lines = [f"## {title}", ""]
    if not items:
        lines.extend([f"No {title.lower()} items.", ""])
        return lines
    for item in items:
        heading = item.get("title") or item.get("source_record_id") or item["source"]
        lines.extend(
            [
                f"### {heading}",
                "",
                f"- source: `{item['source']}`",
                f"- source_section: `{item['source_section']}`",
                f"- source_record_id: `{item['source_record_id'] or 'n/a'}`",
                f"- reason_codes: `{', '.join(item['reason_codes']) or 'none'}`",
            ]
        )
        for key in (
            "selected_as",
            "item_type",
            "priority",
            "status",
            "recommended_action",
            "blocked_until",
            "blocked_by_id",
            "blocked_by_title",
            "corrective_evidence",
            "dependency_value",
            "classification_reason",
            "lineage_issue_count",
            "missing_lineage_record_ids",
            "tombstone_namespace",
            "tombstone_kind",
            "tombstone_deleted_at",
            "tombstone_root_forget_id",
            "tombstone_cause",
        ):
            value = item.get(key)
            if value is not None:
                if isinstance(value, list):
                    value = ", ".join(str(part) for part in value)
                lines.append(f"- {key}: `{value}`")
        lines.append("")
    return lines


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _seed_task_brief_fixture(store: MemoryStore) -> None:
    namespace = "project:task-brief-fixture"
    expired_until = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    old_belief = store.store(
        namespace="global",
        kind="memory",
        title="[[Belief]] optional release handoff owner",
        content="record_type: belief\nclaim: Release handoff owner assignment is optional.\n",
        tags=["kind:belief", "domain:release", "topic:handoff"],
    )
    current_belief = store.store(
        namespace="global",
        kind="memory",
        title="[[Belief]] explicit release handoff owner",
        content=(
            "record_type: belief\n"
            "claim: Release handoff owner must be explicit before execution.\n"
            f"contradicts: {old_belief['id']}\n"
        ),
        tags=["kind:belief", "domain:release", "topic:handoff"],
    )
    store.store(
        namespace=namespace,
        kind="memory",
        title="[[Procedure]] expired release handoff path",
        content=(
            "record_type: procedure\n"
            "goal: Run the expired release handoff path.\n"
            "steps: skip owner | merge release\n"
            f"valid_until: {expired_until}\n"
        ),
        tags=["kind:procedure", "domain:release", "topic:handoff"],
    )
    store.store(
        namespace=namespace,
        kind="memory",
        title="[[Procedure]] current release handoff path",
        content=(
            "record_type: procedure\n"
            "goal: Run the current release handoff path.\n"
            "steps: assign owner | confirm queue | tag release\n"
            f"depends_on: {current_belief['id']}\n"
        ),
        tags=["kind:procedure", "domain:release", "topic:handoff"],
    )
    candidate = {
        "schema": "memory.candidate.v1",
        "namespace": namespace,
        "authority_class": "context_hint",
        "claim": "Task Brief review items should stay proposal-only until an operator reviews them.",
        "evidence_refs": ["benchmark:task-brief-fixture"],
        "source_runtime": "benchmark",
        "source_session_id": "session-1",
        "source_task_id": "task-1",
        "confidence": 0.82,
        "domain_tags": ["domain:memory-governance"],
        "sensitivity": "safe",
    }
    store.store_learning_candidate(candidate, evaluate_learning_candidate(candidate))
    store.store(
        namespace=namespace,
        kind="signal",
        title="Release note review ready",
        content="Task Brief signal: release note review ready.",
        tags=["domain:release"],
    )
