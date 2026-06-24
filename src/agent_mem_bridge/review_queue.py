from __future__ import annotations

import gc
import json
import tempfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .learning_candidates import build_review_receipt_hash
from .learning_policy import evaluate_learning_candidate
from .promotion import parse_structured_record
from .promotion_governance import (
    candidate_from_learning_candidate_item,
    review_learning_candidate,
)
from .repository import MEMORY_ROW_SELECT, MemoryRow
from .storage import MemoryStore


REVIEW_QUEUE_SCHEMA = "memory.review_queue.v1"
REVIEW_QUEUE_BENCHMARK_SCHEMA = "memory.review_queue_benchmark.v1"
DEFAULT_REVIEW_QUEUE_REPORT_PATH = Path(__file__).resolve().parents[2] / "benchmark" / "latest-review-queue-report.json"

BLOCKED_GOVERNANCE_STATUSES = {"deleted", "replaced", "revoked", "stale", "superseded", "unsafe"}
QUARANTINE_STATUSES = {"quarantined", "suspicious"}
UNTRUSTED_SOURCE_TRUST = {"poisoned", "untrusted"}
OPEN_CANDIDATE_STATUSES = {"pending", "needs_review"}


def build_review_queue_report(
    store: MemoryStore,
    *,
    namespace: str,
    limit: int = 100,
    include_closed: bool = False,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build an operator-facing review report without mutating AMB state."""

    cleaned_namespace = namespace.strip()
    if not cleaned_namespace:
        raise ValueError("namespace must not be empty")
    scan_limit = max(1, min(limit, 5000))
    rows = _load_candidate_rows(store, namespace=cleaned_namespace, limit=scan_limit)
    items = [
        item
        for row in rows
        for item in _review_items_for_row(store, row)
        if include_closed or item["status"] != "closed"
    ]
    items.sort(key=_review_sort_key)
    if len(items) > scan_limit:
        items = items[:scan_limit]
    return {
        "schema": REVIEW_QUEUE_SCHEMA,
        "namespace": cleaned_namespace,
        "generated_at": generated_at or datetime.now(UTC).isoformat(),
        "mutation_boundary": "read_only_report_no_auto_writeback",
        "public_mcp_surface_change": False,
        "summary": _summary(items),
        "items": items,
    }


def render_review_queue_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# AMB Review Queue",
        "",
        f"- schema: `{report['schema']}`",
        f"- namespace: `{report['namespace']}`",
        f"- mutation_boundary: `{report['mutation_boundary']}`",
        f"- total_items: `{summary['total_items']}`",
        f"- actionable_items: `{summary['actionable_items']}`",
        f"- hidden_lane_items: `{summary['hidden_lane_items']}`",
        f"- writeback_plan_items: `{summary['writeback_plan_items']}`",
        "",
        "## Action Counts",
        "",
    ]
    for action, count in summary["action_counts"].items():
        lines.append(f"- `{action}`: `{count}`")
    if not summary["action_counts"]:
        lines.append("- none")
    lines.extend(["", "## Items", ""])
    for item in report["items"]:
        lines.extend(
            [
                f"### {item['id']}",
                "",
                f"- type: `{item['item_type']}`",
                f"- priority: `{item['priority']}`",
                f"- status: `{item['status']}`",
                f"- recommended_action: `{item['recommended_action']}`",
                f"- source_record_id: `{item['source_record_id']}`",
                f"- title: {item['title'] or 'n/a'}",
                f"- reason_codes: `{', '.join(item['reason_codes']) or 'none'}`",
                f"- writeback_boundary: `{item['writeback_plan']['boundary']}`",
                f"- writeback_steps: `{', '.join(item['writeback_plan']['steps']) or 'none'}`",
                "",
            ]
        )
    if not report["items"]:
        lines.append("No review items found.")
    return "\n".join(lines)


def run_review_queue_benchmark() -> dict[str, Any]:
    """Run a deterministic fixture proof for the review queue workflow."""

    report = build_review_queue_fixture_report()
    summary = report["summary"]
    return {
        "schema": REVIEW_QUEUE_BENCHMARK_SCHEMA,
        "summary": {
            "review_queue_item_count": summary["total_items"],
            "review_queue_actionable_count": summary["actionable_items"],
            "review_queue_hidden_lane_count": summary["hidden_lane_items"],
            "review_queue_writeback_plan_count": summary["writeback_plan_items"],
            "review_queue_no_auto_mutation": report["mutation_boundary"] == "read_only_report_no_auto_writeback",
            "review_queue_public_mcp_surface_change": bool(report["public_mcp_surface_change"]),
            "review_queue_item_type_count": len(summary["item_type_counts"]),
        },
    }


def build_review_queue_fixture_report() -> dict[str, Any]:
    """Build the stable fixture report shared by review-queue proof layers."""

    with tempfile.TemporaryDirectory() as temp_dir:
        store = MemoryStore(Path(temp_dir) / "bridge.db", log_dir=Path(temp_dir) / "logs")
        _seed_review_queue_fixture(store)
        report = build_review_queue_report(
            store,
            namespace="project:review-queue-fixture",
            limit=50,
            generated_at="2026-06-24T00:00:00+00:00",
        )
        del store
        gc.collect()
    return report


def _load_candidate_rows(store: MemoryStore, *, namespace: str, limit: int) -> list[MemoryRow]:
    with store._connect() as conn:
        rows = conn.execute(
            f"""
            SELECT
                {MEMORY_ROW_SELECT}
            FROM memories
            WHERE namespace = ?
              AND kind = 'memory'
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (namespace, limit),
        ).fetchall()
    return [MemoryRow.from_sqlite(row) for row in rows]


def _review_items_for_row(store: MemoryStore, row: MemoryRow) -> list[dict[str, Any]]:
    item = row.as_dict()
    fields = parse_structured_record(row.content)
    tags = set(row.tags)
    record_type = fields.get("record_type", "")
    items: list[dict[str, Any]] = []

    if "kind:learning-candidate" in tags or record_type == "learning-candidate":
        items.append(_learning_candidate_item(store, item, fields))
    if "kind:learning-review" in tags or record_type == "learning-review":
        items.append(_learning_review_item(item, fields))
    if record_type == "forgetting-audit":
        items.append(
            _base_item(
                item,
                fields,
                item_type="forgetting_audit",
                priority="low",
                status="open",
                recommended_action="verify_tombstone",
                reason_codes=["forgetting-audit-present"],
                writeback_steps=["confirm_deleted_content_absent", "keep_audit_record"],
            )
        )

    governance_status = _field_or_tag(fields, tags, "governance_status", "governance_status:")
    if governance_status in BLOCKED_GOVERNANCE_STATUSES and record_type != "forgetting-audit":
        items.append(
            _base_item(
                item,
                fields,
                item_type="governance_disposition",
                priority="high" if governance_status in {"unsafe", "deleted", "revoked"} else "medium",
                status="open",
                recommended_action="confirm_not_authority",
                reason_codes=[f"governance:{governance_status}"],
                writeback_steps=["verify_hidden_from_normal_recall", "add_or_confirm_supersession_or_tombstone"],
            )
        )

    quarantine_status = _field_or_tag(fields, tags, "quarantine_status", "quarantine:")
    source_trust = _field_or_tag(fields, tags, "source_trust", "source_trust:")
    if quarantine_status in QUARANTINE_STATUSES or source_trust in UNTRUSTED_SOURCE_TRUST:
        reason = f"quarantine:{quarantine_status}" if quarantine_status else f"source_trust:{source_trust}"
        items.append(
            _base_item(
                item,
                fields,
                item_type="quarantine_review",
                priority="high",
                status="open",
                recommended_action="reject_or_keep_quarantined",
                reason_codes=[reason],
                writeback_steps=["inspect_source_evidence", "reject_or_store_review_receipt"],
            )
        )

    validity_status = str(item.get("validity_status") or "")
    if validity_status in {"expired", "future", "invalid"}:
        items.append(
            _base_item(
                item,
                fields,
                item_type="validity_review",
                priority="medium",
                status="open",
                recommended_action="refresh_or_confirm_expiry",
                reason_codes=[f"validity:{validity_status}"],
                writeback_steps=["verify_current_replacement", "store_supersession_review_if_needed"],
            )
        )

    return _dedupe_review_items(items)


def _learning_candidate_item(store: MemoryStore, item: dict[str, Any], fields: dict[str, str]) -> dict[str, Any]:
    candidate = candidate_from_learning_candidate_item(item)
    review = review_learning_candidate(store, candidate)
    status = fields.get("candidate_status", "")
    priority = "high" if review["recommended_action"] in {"reject", "merge"} else "medium"
    return _base_item(
        item,
        fields,
        item_type="learning_candidate",
        priority=priority,
        status="open" if status in OPEN_CANDIDATE_STATUSES or not status else "closed",
        recommended_action=str(review.get("recommended_action") or "keep_staged"),
        reason_codes=[str(reason) for reason in (review.get("reason_codes") or [])],
        writeback_steps=_candidate_writeback_steps(review),
        extras={
            "candidate_ref": review.get("candidate_ref"),
            "target_record_type": review.get("target_record_type"),
            "durable_write_allowed": bool(review.get("durable_write_allowed")),
            "checks": review.get("checks") or {},
        },
    )


def _learning_review_item(item: dict[str, Any], fields: dict[str, str]) -> dict[str, Any]:
    decision = fields.get("review_decision", "")
    has_target = bool(fields.get("target_record_id"))
    receipt_hash = fields.get("review_receipt_hash") or build_review_receipt_hash(fields, candidate_status=fields.get("candidate_status", ""))
    if decision in {"approved", "merged"} and not has_target:
        status = "open"
        action = "complete_review_writeback_or_keep_staged"
        priority = "medium"
        reasons = ["approved_without_target_record"]
    else:
        status = "closed"
        action = "inspect_review_receipt"
        priority = "low"
        reasons = [f"review_decision:{decision or 'unknown'}"]
    return _base_item(
        item,
        fields,
        item_type="learning_review",
        priority=priority,
        status=status,
        recommended_action=action,
        reason_codes=reasons,
        writeback_steps=["do_not_auto_mutate", "use_receipt_as_audit_evidence"],
        extras={"review_receipt_hash": receipt_hash, "target_record_id": fields.get("target_record_id", "")},
    )


def _base_item(
    item: dict[str, Any],
    fields: dict[str, str],
    *,
    item_type: str,
    priority: str,
    status: str,
    recommended_action: str,
    reason_codes: list[str],
    writeback_steps: list[str],
    extras: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": f"{item_type}:{item['id']}",
        "item_type": item_type,
        "source_record_id": item["id"],
        "namespace": item["namespace"],
        "title": item.get("title"),
        "record_type": fields.get("record_type", ""),
        "priority": priority,
        "status": status,
        "recommended_action": recommended_action,
        "reason_codes": _dedupe(reason_codes),
        "created_at": item.get("created_at"),
        "hidden_from_normal_recall": bool(item.get("is_learning_candidate")),
        "tags": item.get("tags") or [],
        "writeback_plan": {
            "boundary": "proposal_only_no_auto_mutation",
            "steps": _dedupe(writeback_steps),
        },
        **(extras or {}),
    }


def _candidate_writeback_steps(review: dict[str, Any]) -> list[str]:
    action = str(review.get("recommended_action") or "keep_staged")
    if action == "learn":
        return ["human_review_required", "store_durable_record_explicitly", "store_learning_review_receipt"]
    if action == "merge":
        return ["human_review_required", "store_superseding_record_or_receipt", "do_not_promote_candidate_directly"]
    if action == "reject":
        return ["store_learning_review_receipt", "keep_candidate_hidden"]
    return ["keep_candidate_staged", "collect_more_evidence"]


def _summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    action_counts = Counter(str(item["recommended_action"]) for item in items)
    reason_counts = Counter(reason for item in items for reason in item["reason_codes"])
    item_type_counts = Counter(str(item["item_type"]) for item in items)
    priority_counts = Counter(str(item["priority"]) for item in items)
    return {
        "total_items": len(items),
        "actionable_items": sum(1 for item in items if item["status"] == "open"),
        "closed_items": sum(1 for item in items if item["status"] == "closed"),
        "hidden_lane_items": sum(1 for item in items if item["hidden_from_normal_recall"]),
        "writeback_plan_items": sum(1 for item in items if item["writeback_plan"]["steps"]),
        "item_type_counts": dict(sorted(item_type_counts.items())),
        "action_counts": dict(sorted(action_counts.items())),
        "reason_counts": dict(sorted(reason_counts.items())),
        "priority_counts": dict(sorted(priority_counts.items())),
    }


def _review_sort_key(item: dict[str, Any]) -> tuple[int, int, str]:
    priority_rank = {"high": 0, "medium": 1, "low": 2}.get(str(item["priority"]), 3)
    status_rank = {"open": 0, "closed": 1}.get(str(item["status"]), 2)
    return (status_rank, priority_rank, str(item.get("created_at") or ""))


def _field_or_tag(fields: dict[str, str], tags: set[str], field: str, tag_prefix: str) -> str:
    if fields.get(field):
        return str(fields[field]).strip()
    for tag in sorted(tags):
        if tag.startswith(tag_prefix):
            return tag.split(":", 1)[1].strip()
    return ""


def _dedupe_review_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        if item["id"] in seen:
            continue
        seen.add(item["id"])
        deduped.append(item)
    return deduped


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


def _seed_review_queue_fixture(store: MemoryStore) -> None:
    namespace = "project:review-queue-fixture"
    candidate = {
        "schema": "memory.candidate.v1",
        "namespace": namespace,
        "authority_class": "context_hint",
        "claim": "Review queue candidates should remain hidden until an operator reviews them.",
        "evidence_refs": ["fixture:review-queue"],
        "source_runtime": "fixture-runtime",
        "source_session_id": "session-1",
        "source_task_id": "task-1",
        "confidence": 0.82,
        "domain_tags": ["domain:memory-governance"],
        "sensitivity": "safe",
    }
    decision = evaluate_learning_candidate(candidate)
    stored_candidate = store.store_learning_candidate(candidate, decision)
    store.store_learning_review(
        {
            "namespace": namespace,
            "candidate_ref": decision["candidate_ref"],
            "source_candidate_id": stored_candidate["id"],
            "review_decision": "approved",
            "reviewed_by": "reviewer-a",
            "review_reason": "Fixture approval without durable writeback yet.",
            "target_record_type": "learn",
            "recommended_action": "learn",
            "reason_codes": [],
            "evidence_refs": candidate["evidence_refs"],
        }
    )
    store.store(
        namespace=namespace,
        kind="memory",
        title="Deleted runtime-sensitive note",
        content="\n".join(
            [
                "record_type: learn",
                "claim: Deleted runtime-sensitive note should not be authority.",
                "governance_status: deleted",
            ]
        ),
        tags=["domain:runtime"],
    )
    store.store(
        namespace=namespace,
        kind="memory",
        title="Runtime-sensitive note tombstone",
        content="\n".join(
            [
                "record_type: forgetting-audit",
                "claim: A memory was deleted; original content is intentionally not retained.",
                "governance_status: current",
            ]
        ),
        tags=["domain:runtime", "topic:forgetting"],
    )
    store.store(
        namespace=namespace,
        kind="memory",
        title="Quarantined external claim",
        content="\n".join(
            [
                "record_type: learn",
                "claim: External untrusted claim should remain quarantined.",
                "source_trust: untrusted",
                "quarantine_status: quarantined",
            ]
        ),
        tags=["domain:security"],
    )
    store.store(
        namespace=namespace,
        kind="memory",
        title="Expired runtime rule",
        content="\n".join(
            [
                "record_type: learn",
                "claim: Expired runtime rule should be refreshed before use.",
                "valid_until: 2020-01-01T00:00:00+00:00",
            ]
        ),
        tags=["domain:runtime"],
    )
