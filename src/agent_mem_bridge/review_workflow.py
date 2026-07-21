from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .review_queue import build_review_queue_fixture_report, build_review_queue_report
from .storage import MemoryStore

REVIEW_WORKFLOW_SCHEMA = "memory.review_workflow.v1"
REVIEW_WORKFLOW_BENCHMARK_SCHEMA = "memory.review_workflow_benchmark.v1"
DEFAULT_REVIEW_WORKFLOW_REPORT_PATH = (
    Path(__file__).resolve().parents[2] / "benchmark" / "latest-review-workflow-report.json"
)

WORKFLOW_BOUNDARY = "proposal_only_no_auto_writeback"


def build_review_workflow_report(
    store: MemoryStore,
    *,
    namespace: str,
    limit: int = 100,
    include_closed: bool = False,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Compile a human review workflow from the review queue without mutating memory."""

    queue_report = build_review_queue_report(
        store,
        namespace=namespace,
        limit=limit,
        include_closed=include_closed,
        generated_at=generated_at,
    )
    return build_review_workflow_plan(queue_report, generated_at=generated_at)


def build_review_workflow_plan(
    queue_report: dict[str, Any],
    *,
    max_items: int | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Turn review queue items into explicit human decision steps."""

    raw_items = list(queue_report.get("items") or [])
    if max_items is not None:
        raw_items = raw_items[: max(0, max_items)]
    workflow_items = [_workflow_item(item) for item in raw_items]
    return {
        "schema": REVIEW_WORKFLOW_SCHEMA,
        "source_schema": queue_report.get("schema"),
        "namespace": queue_report.get("namespace"),
        "generated_at": generated_at or datetime.now(UTC).isoformat(),
        "mutation_boundary": WORKFLOW_BOUNDARY,
        "public_mcp_surface_change": False,
        "summary": _summary(workflow_items, source_item_count=len(queue_report.get("items") or [])),
        "items": workflow_items,
    }


def render_review_workflow_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# AMB Human Review Workflow",
        "",
        f"- schema: `{report['schema']}`",
        f"- source_schema: `{report['source_schema']}`",
        f"- namespace: `{report['namespace']}`",
        f"- mutation_boundary: `{report['mutation_boundary']}`",
        f"- workflow_item_count: `{summary['workflow_item_count']}`",
        f"- workflow_requires_human_count: `{summary['workflow_requires_human_count']}`",
        f"- workflow_auto_write_count: `{summary['workflow_auto_write_count']}`",
        f"- workflow_public_mcp_surface_change: `{summary['workflow_public_mcp_surface_change']}`",
        "",
        "## Manual Workflow Items",
        "",
    ]
    for item in report["items"]:
        lines.extend(
            [
                f"### {item['id']}",
                "",
                f"- source_queue_item_id: `{item['source_queue_item_id']}`",
                f"- source_record_id: `{item['source_record_id']}`",
                f"- item_type: `{item['item_type']}`",
                f"- priority: `{item['priority']}`",
                f"- recommended_action: `{item['recommended_action']}`",
                f"- decision_prompt: {item['decision_prompt']}",
                f"- blocked_until: `{item['blocked_until']}`",
                f"- auto_writeback_allowed: `{str(item['auto_writeback_allowed']).lower()}`",
                f"- allowed_outcomes: `{', '.join(item['allowed_outcomes'])}`",
                "- manual_steps:",
            ]
        )
        for step in item["manual_steps"]:
            lines.append(f"  - `{step}`")
        lines.append("")
    if not report["items"]:
        lines.append("No workflow items found.")
    return "\n".join(lines)


def run_review_workflow_benchmark() -> dict[str, Any]:
    """Run a deterministic fixture proof for the human review workflow."""

    queue_report = build_review_queue_fixture_report()
    workflow_report = build_review_workflow_plan(
        queue_report,
        generated_at="2026-06-24T00:00:00+00:00",
    )
    summary = workflow_report["summary"]
    return {
        "schema": REVIEW_WORKFLOW_BENCHMARK_SCHEMA,
        "summary": {
            "review_workflow_source_queue_item_count": summary["source_queue_item_count"],
            "review_workflow_item_count": summary["workflow_item_count"],
            "review_workflow_manual_step_count": summary["workflow_manual_step_count"],
            "review_workflow_requires_human_count": summary["workflow_requires_human_count"],
            "review_workflow_auto_write_count": summary["workflow_auto_write_count"],
            "review_workflow_no_auto_writeback": summary["workflow_auto_write_count"] == 0,
            "review_workflow_public_mcp_surface_change": summary["workflow_public_mcp_surface_change"],
            "review_workflow_item_type_count": len(summary["item_type_counts"]),
        },
    }


def _workflow_item(item: dict[str, Any]) -> dict[str, Any]:
    item_type = str(item.get("item_type") or "unknown")
    return {
        "id": f"workflow:{item.get('id')}",
        "source_queue_item_id": item.get("id"),
        "source_record_id": item.get("source_record_id"),
        "item_type": item_type,
        "priority": item.get("priority"),
        "status": item.get("status"),
        "recommended_action": item.get("recommended_action"),
        "decision_prompt": _decision_prompt(item),
        "manual_steps": _manual_steps(item_type, item),
        "allowed_outcomes": _allowed_outcomes(item_type, item),
        "blocked_until": _blocked_until(item_type, item),
        "requires_human_review": True,
        "auto_writeback_allowed": False,
        "writeback_boundary": WORKFLOW_BOUNDARY,
        "source_reason_codes": item.get("reason_codes") or [],
        "source_writeback_plan": item.get("writeback_plan") or {},
    }


def _decision_prompt(item: dict[str, Any]) -> str:
    item_type = str(item.get("item_type") or "unknown")
    title = str(item.get("title") or item.get("source_record_id") or "this record")
    action = str(item.get("recommended_action") or "review")
    prompts = {
        "learning_candidate": f"Should the staged candidate `{title}` become durable memory, merge into an existing record, or stay hidden?",
        "learning_review": f"Does the review receipt for `{title}` have a durable target, or should it stay staged?",
        "governance_disposition": f"Is `{title}` still non-authoritative, and is the supersession/tombstone evidence complete?",
        "forgetting_audit": f"Does `{title}` prove the forgotten content is absent without preserving sensitive content?",
        "quarantine_review": f"Should `{title}` be rejected, kept quarantined, or manually rewritten from trusted evidence?",
        "validity_review": f"Should `{title}` be refreshed, superseded, or confirmed expired before recall can trust it?",
    }
    return prompts.get(item_type, f"Review `{title}` and decide whether to apply `{action}` manually.")


def _manual_steps(item_type: str, item: dict[str, Any]) -> list[str]:
    base = ["inspect_source_record", "verify_reason_codes"]
    mapping = {
        "learning_candidate": [
            *base,
            "recall_related_records_for_dedup_or_conflict",
            "choose_learn_merge_reject_or_keep_staged",
            "if_accepted_write_durable_record_explicitly_with_store",
            "store_learning_review_receipt_after_manual_decision",
        ],
        "learning_review": [
            *base,
            "verify_target_record_or_missing_writeback",
            "keep_receipt_as_audit_evidence",
        ],
        "governance_disposition": [
            *base,
            "confirm_record_is_not_authority",
            "confirm_supersession_or_tombstone_evidence",
        ],
        "forgetting_audit": [
            *base,
            "confirm_deleted_content_absent",
            "keep_audit_record_without_restoring_content",
        ],
        "quarantine_review": [
            *base,
            "inspect_source_trust",
            "reject_or_keep_quarantined",
            "only_rewrite_from_trusted_evidence",
        ],
        "validity_review": [
            *base,
            "check_current_replacement_or_fresh_evidence",
            "confirm_expiry_or_store_supersession_receipt",
        ],
    }
    return mapping.get(item_type, [*base, "make_explicit_manual_decision"])


def _allowed_outcomes(item_type: str, item: dict[str, Any]) -> list[str]:
    mapping = {
        "learning_candidate": ["learn", "merge", "reject", "keep_staged"],
        "learning_review": ["complete_writeback", "keep_staged", "close_as_audit_only"],
        "governance_disposition": ["confirm_non_authority", "add_supersession_receipt", "escalate"],
        "forgetting_audit": ["confirm_tombstone", "escalate_if_content_reappears"],
        "quarantine_review": ["reject", "keep_quarantined", "rewrite_from_trusted_source"],
        "validity_review": ["refresh", "supersede", "confirm_expired"],
    }
    return mapping.get(item_type, [str(item.get("recommended_action") or "review"), "escalate"])


def _blocked_until(item_type: str, item: dict[str, Any]) -> str:
    if item_type == "learning_candidate":
        return "human_decision_recorded"
    if item_type == "learning_review":
        return "target_record_verified_or_receipt_closed"
    if item_type == "governance_disposition":
        return "non_authority_evidence_confirmed"
    if item_type == "quarantine_review":
        return "trusted_source_review_completed"
    if item_type == "validity_review":
        return "freshness_or_expiry_confirmed"
    return "manual_review_completed"


def _summary(items: list[dict[str, Any]], *, source_item_count: int) -> dict[str, Any]:
    item_type_counts = Counter(str(item["item_type"]) for item in items)
    priority_counts = Counter(str(item["priority"]) for item in items)
    outcome_counts = Counter(outcome for item in items for outcome in item["allowed_outcomes"])
    return {
        "source_queue_item_count": source_item_count,
        "workflow_item_count": len(items),
        "workflow_manual_step_count": sum(len(item["manual_steps"]) for item in items),
        "workflow_requires_human_count": sum(1 for item in items if item["requires_human_review"]),
        "workflow_auto_write_count": sum(1 for item in items if item["auto_writeback_allowed"]),
        "workflow_public_mcp_surface_change": False,
        "item_type_counts": dict(sorted(item_type_counts.items())),
        "priority_counts": dict(sorted(priority_counts.items())),
        "outcome_counts": dict(sorted(outcome_counts.items())),
    }


def dumps_report(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, sort_keys=True)
