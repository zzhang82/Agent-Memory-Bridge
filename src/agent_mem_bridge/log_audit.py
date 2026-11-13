from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .learning_policy import evaluate_learning_candidate
from .memory_cli import resolve_default_namespace
from .storage import MemoryStore

LOG_AUDIT_SCHEMA = "memory.log_audit.v1"
MAX_LOG_CANDIDATES = 50

# Pattern matchers for gotchas, decisions, and verified bug resolutions in logs
_CLAIM_PATTERNS = (
    re.compile(r"\b(?:gotcha|lesson|bug-fix|fix_or_decision|decision)\s*[:：]\s*(.+)", re.IGNORECASE),
    re.compile(r"\bclaim\s*[:：]\s*(.+)", re.IGNORECASE),
    re.compile(r"\*{0,2}(?:gotcha|fix|decision|lesson)\*{0,2}\s*[:：]\s*(.+)", re.IGNORECASE),
)


def extract_candidates_from_log_text(
    text: str,
    *,
    source_name: str,
    namespace: str,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Extract candidate lessons and gotchas from raw transcript text without storing transcripts.

    Transforms unstructured logs into candidate claims with audit provenance
    and evaluates each candidate against writeback policy.
    """
    candidates: list[dict[str, Any]] = []
    seen_claims: set[str] = set()
    bounded_limit = max(1, min(limit, MAX_LOG_CANDIDATES))

    lines = text.splitlines()
    for line_idx, line in enumerate(lines, 1):
        if len(candidates) >= bounded_limit:
            break
        stripped = line.strip()
        if not stripped:
            continue

        raw_claim = ""
        # 1. Try structured JSON / embedded JSON in log line
        first_brace = stripped.find("{")
        last_brace = stripped.rfind("}")
        if first_brace != -1 and last_brace > first_brace:
            try:
                data = json.loads(stripped[first_brace : last_brace + 1])
                if isinstance(data, dict):
                    for key in ("claim", "gotcha", "decision", "lesson", "fix"):
                        val = data.get(key)
                        if isinstance(val, str) and val.strip():
                            raw_claim = val.strip()
                            break
                    if not raw_claim:
                        content = data.get("content") or data.get("message") or ""
                        if isinstance(content, str):
                            for pattern in _CLAIM_PATTERNS:
                                match = pattern.search(content)
                                if match:
                                    raw_claim = match.group(1).strip()
                                    break
            except json.JSONDecodeError:
                pass

        # 2. Try regex pattern match on plain text
        if not raw_claim:
            for pattern in _CLAIM_PATTERNS:
                match = pattern.search(stripped)
                if match:
                    raw_claim = match.group(1).strip()
                    break

        if not raw_claim or len(raw_claim) < 10:
            continue

        # Clean and bound claim text
        clean_claim = " ".join(raw_claim.split())
        if len(clean_claim) > 160:
            clean_claim = clean_claim[:157] + "..."

        normalized_key = clean_claim.lower()
        if normalized_key in seen_claims:
            continue
        seen_claims.add(normalized_key)

        evidence_ref = f"log:{source_name}:{line_idx}"
        candidate = {
            "schema": "memory.candidate.v1",
            "namespace": namespace,
            "authority_class": "decision",
            "claim": clean_claim,
            "evidence_refs": [evidence_ref],
            "domain_tags": ["domain:audit", "source:log-audit"],
            "topic_tags": ["topic:hindsight"],
            "source_runtime": "amb-log-audit",
            "source_session_id": source_name[:32],
            "source_task_id": f"line-{line_idx}",
            "candidate_subject_id": f"log-cand-{line_idx}",
            "evidence_revision_id": "",
            "confidence": "candidate",
            "recommended_action": "learn",
        }
        decision = evaluate_learning_candidate(candidate)
        candidates.append(
            {
                "line_number": line_idx,
                "evidence_ref": evidence_ref,
                "candidate": candidate,
                "decision": decision,
            }
        )

    return candidates


def build_log_audit_report(
    log_path: Path,
    store: MemoryStore,
    *,
    namespace: str | None = None,
    limit: int = 10,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Audit an operator-provided session log and stage candidate lessons."""
    target_namespace = resolve_default_namespace(namespace)
    path = Path(log_path)
    if not path.is_file():
        raise FileNotFoundError(f"log file not found: {path}")

    text = path.read_text(encoding="utf-8", errors="replace")
    items = extract_candidates_from_log_text(
        text,
        source_name=path.name,
        namespace=target_namespace,
        limit=limit,
    )

    return {
        "schema": LOG_AUDIT_SCHEMA,
        "log_path": str(path.resolve()),
        "namespace": target_namespace,
        "generated_at": generated_at or datetime.now(UTC).isoformat(),
        "total_lines_scanned": len(text.splitlines()),
        "candidate_count": len(items),
        "staged_count": 0,
        "stage": False,
        "items": items,
    }


def stage_log_audit_report(store: MemoryStore, report: dict[str, Any]) -> dict[str, Any]:
    """Stage extracted log audit candidates into the review queue."""
    staged_count = 0
    stage_results: list[dict[str, Any]] = []

    for item in report.get("items", []):
        cand = item["candidate"]
        decision = item["decision"]
        try:
            stored = store.store_learning_candidate(cand, decision, candidate_status="needs_review")
            disposition = "stored" if stored.get("stored") else "duplicate"
            if disposition == "stored":
                staged_count += 1
            stage_results.append(
                {
                    "line_number": item["line_number"],
                    "candidate_ref": decision.get("candidate_ref"),
                    "disposition": disposition,
                    "id": stored.get("id"),
                }
            )
        except Exception as exc:
            stage_results.append(
                {
                    "line_number": item["line_number"],
                    "candidate_ref": decision.get("candidate_ref"),
                    "disposition": "error",
                    "error": str(exc),
                }
            )

    report["stage"] = True
    report["staged_count"] = staged_count
    report["stage_results"] = stage_results
    return report


def render_log_audit_markdown(report: dict[str, Any]) -> str:
    """Format log audit report as human-readable Markdown."""
    lines = [
        "# AMB Session Log Audit",
        "",
        f"- log file: `{report['log_path']}`",
        f"- namespace: `{report['namespace']}`",
        f"- scanned lines: `{report['total_lines_scanned']}`",
        f"- extracted candidates: `{report['candidate_count']}`",
    ]
    if report.get("stage"):
        lines.append(f"- staged count: `{report['staged_count']}` (reviewable via `review-queue`)")
    else:
        lines.append("- mode: `shadow / preview` (pass `--stage` to push candidates to review queue)")
    lines.append("")

    items = report.get("items", [])
    if not items:
        lines.append("No candidates extracted from log file.")
        return "\n".join(lines)

    lines.append("## Extracted Candidates")
    lines.append("")
    for idx, item in enumerate(items, 1):
        cand = item["candidate"]
        lines.append(f"### {idx}. {cand['claim']}")
        lines.append(f"- source line: `{item['line_number']}` ({item['evidence_ref']})")
        lines.append(f"- authority class: `{cand['authority_class']}`")
        lines.append(f"- policy decision: `{item['decision'].get('decision')}` (status: `needs_review`)")
        lines.append("")
    return "\n".join(lines)
