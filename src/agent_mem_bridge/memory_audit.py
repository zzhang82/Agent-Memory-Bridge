from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .learning_policy import evaluate_learning_candidate
from .memory_cli import resolve_default_namespace
from .query import _confirmation_commits, _dependency_states
from .recheck import dependency_recheck_status
from .repository import MEMORY_ROW_SELECT, MemoryRow
from .storage import MemoryStore

MEMORY_AUDIT_SCHEMA = "memory.offline_audit.v1"


def build_memory_audit_report(
    store: MemoryStore,
    *,
    namespace: str | None = None,
    repository_root: Path | None = None,
    limit: int = 20,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Audit durable memories against live repository state and dependencies.

    Scans active memories in the target namespace, finds those reporting
    `recheck_status == "needs_recheck"` (drift from recorded git commit or broken
    dependencies), and constructs candidate records proposing revision with
    `candidate_status=needs_review`.
    """
    target_namespace = resolve_default_namespace(namespace)
    bounded_limit = max(1, min(limit, 100))
    git_root = (repository_root or Path.cwd()).resolve()

    with store._connect() as conn:
        rows = conn.execute(
            f"""
            SELECT {MEMORY_ROW_SELECT}
            FROM memories
            WHERE namespace = ?
              AND kind = 'memory'
              AND COALESCE(is_learning_candidate, 0) = 0
            ORDER BY created_at DESC
            LIMIT 500
            """,
            (target_namespace,),
        ).fetchall()

        states: dict[str, dict[str, Any]] = {}
        all_target_ids: set[str] = set()
        parsed_rows = [MemoryRow.from_sqlite(row) for row in rows]
        for memory in parsed_rows:
            for edge in memory.metadata_edges:
                if edge.get("relation") == "depends_on" and edge.get("target_id"):
                    all_target_ids.add(str(edge["target_id"]))

        if all_target_ids:
            states = _dependency_states(conn, list(all_target_ids))

    scanned_count = len(parsed_rows)
    needs_recheck_items: list[dict[str, Any]] = []

    for memory in parsed_rows:
        if len(needs_recheck_items) >= bounded_limit:
            break

        recheck = dependency_recheck_status(
            memory.content,
            records_by_id=states,
            repository_root=git_root,
            confirmation_commits=_confirmation_commits(memory),
        )
        if recheck.get("recheck_status") != "needs_recheck":
            continue

        recheck_reasons = list(recheck.get("recheck_reasons") or [])
        repo_divergences = list(recheck.get("repo_divergence") or [])

        domain_tags = [t for t in memory.tags if t.startswith("domain:")] or ["domain:audit"]
        topic_tags = [t for t in memory.tags if t.startswith("topic:")]

        if repo_divergences:
            for div in repo_divergences:
                if len(needs_recheck_items) >= bounded_limit:
                    break
                stored_claim = div.get("stored_claim") or memory.title or memory.id
                curr_c = div.get("current_commit", "")
                rec_c = div.get("recorded_commit", "")
                path = div.get("path", "")
                claim_title = f"Revise '{stored_claim}' for {path}@{curr_c[:7]}"
                candidate = {
                    "schema": "memory.candidate.v1",
                    "namespace": memory.namespace,
                    "authority_class": "decision",
                    "claim": claim_title,
                    "evidence_refs": [f"git:{curr_c[:7]}", f"repo:{path}"],
                    "domain_tags": domain_tags,
                    "topic_tags": topic_tags,
                    "supersedes_record_ids": [memory.id],
                    "supersession_plan": (
                        f"Update recorded commit from {rec_c[:7]} to {curr_c[:7]} "
                        f"based on repository changes in {path} ({div.get('diff_summary', '')})"
                    ),
                    "source_runtime": "amb-memory-audit",
                    "source_session_id": f"audit-{curr_c[:7]}",
                    "source_task_id": memory.id,
                    "candidate_subject_id": memory.id,
                    "evidence_revision_id": curr_c,
                    "confidence": "candidate",
                    "recommended_action": "revise",
                }
                decision = evaluate_learning_candidate(candidate)
                needs_recheck_items.append(
                    {
                        "predecessor_id": memory.id,
                        "recheck_reasons": recheck_reasons,
                        "repo_divergence": [div],
                        "candidate": candidate,
                        "decision": decision,
                    }
                )
        else:
            reasons_summary = ", ".join(recheck_reasons)
            candidate = {
                "schema": "memory.candidate.v1",
                "namespace": memory.namespace,
                "authority_class": "decision",
                "claim": f"Revise memory {memory.id} to resolve {reasons_summary}",
                "evidence_refs": [f"memory:{memory.id}"],
                "domain_tags": domain_tags,
                "topic_tags": topic_tags,
                "supersedes_record_ids": [memory.id],
                "supersession_plan": f"Resolve invalid dependencies: {reasons_summary}",
                "source_runtime": "amb-memory-audit",
                "source_session_id": "audit-dependency",
                "source_task_id": memory.id,
                "candidate_subject_id": memory.id,
                "evidence_revision_id": "",
                "confidence": "candidate",
                "recommended_action": "revise",
            }
            decision = evaluate_learning_candidate(candidate)
            needs_recheck_items.append(
                {
                    "predecessor_id": memory.id,
                    "recheck_reasons": recheck_reasons,
                    "repo_divergence": [],
                    "candidate": candidate,
                    "decision": decision,
                }
            )

    return {
        "schema": MEMORY_AUDIT_SCHEMA,
        "namespace": target_namespace,
        "repository_root": str(git_root),
        "generated_at": generated_at or datetime.now(UTC).isoformat(),
        "mutation_boundary": "offline_audit_stage_only_no_recall_writeback",
        "scanned_count": scanned_count,
        "needs_recheck_count": len(needs_recheck_items),
        "candidate_count": len(needs_recheck_items),
        "staged_count": 0,
        "stage": False,
        "items": needs_recheck_items,
    }


def stage_memory_audit_report(store: MemoryStore, report: dict[str, Any]) -> dict[str, Any]:
    """Stage eligible audit candidates into the hidden review queue."""
    staged_count = 0
    stage_results: list[dict[str, Any]] = []
    for item in report.get("items", []):
        candidate = item["candidate"]
        decision = item["decision"]
        try:
            stored = store.store_learning_candidate(candidate, decision, candidate_status="needs_review")
            disposition = "stored" if stored.get("stored") else "duplicate"
            if disposition == "stored":
                staged_count += 1
            stage_results.append(
                {
                    "candidate_ref": decision.get("candidate_ref"),
                    "disposition": disposition,
                    "id": stored.get("id"),
                }
            )
        except Exception as exc:
            stage_results.append(
                {
                    "candidate_ref": decision.get("candidate_ref"),
                    "disposition": "error",
                    "error": str(exc),
                }
            )
    report["stage"] = True
    report["staged_count"] = staged_count
    report["stage_results"] = stage_results
    return report


def render_memory_audit_markdown(report: dict[str, Any]) -> str:
    """Render the offline audit report in human-readable Markdown."""
    lines = [
        "# AMB Offline Memory Audit",
        "",
        f"- namespace: `{report['namespace']}`",
        f"- repository root: `{report.get('repository_root', '.')}`",
        f"- scanned memories: `{report['scanned_count']}`",
        f"- memories needing recheck: `{report['needs_recheck_count']}`",
        f"- candidate count: `{report['candidate_count']}`",
    ]
    if report.get("stage"):
        lines.append(f"- staged candidates: `{report['staged_count']}` (reviewable in review queue)")
    else:
        lines.append(
            "- mode: `shadow / dry-run` (pass `--stage` to stage candidates with `candidate_status=needs_review`)"
        )
    lines.append("")
    items = report.get("items", [])
    if not items:
        lines.append("No memory drift or invalid dependencies detected.")
        return "\n".join(lines)

    lines.append("## Staged Candidates" if report.get("stage") else "## Audit Candidates")
    lines.append("")
    for idx, item in enumerate(items, 1):
        cand = item["candidate"]
        reasons = item.get("recheck_reasons", [])
        lines.append(f"### {idx}. {cand['claim']}")
        lines.append(f"- predecessor id: `{item['predecessor_id']}`")
        lines.append(f"- recheck reasons: `{', '.join(reasons)}`")
        if item.get("repo_divergence"):
            for div in item["repo_divergence"]:
                lines.append(f"- live diff ({div['path']}): `{div['diff_summary']}`")
                if div.get("live_content_preview"):
                    first_line = div["live_content_preview"].splitlines()[0]
                    lines.append(f"- live preview: `{first_line}`")
        lines.append(f"- action: `{cand['recommended_action']}` (status: `needs_review`)")
        lines.append(f"- plan: {cand['supersession_plan']}")
        lines.append("")
    return "\n".join(lines)
