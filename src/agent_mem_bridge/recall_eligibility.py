from __future__ import annotations

import sqlite3
from collections import Counter
from typing import Any

from .procedure_governance import INELIGIBLE_PROCEDURE_STATUSES, parse_procedure_artifact

_MAX_SQL_VARIABLES = 900
_PROCEDURE_TAG = "kind:procedure"

DEFAULT_RECALL_ELIGIBILITY = "default"
PROCEDURE_GOVERNANCE_RECALL_ELIGIBILITY = "procedure-governance"
HISTORICAL_RECALL_ELIGIBILITY = "historical"
_RECALL_ELIGIBILITY_MODES = frozenset(
    {
        DEFAULT_RECALL_ELIGIBILITY,
        PROCEDURE_GOVERNANCE_RECALL_ELIGIBILITY,
        HISTORICAL_RECALL_ELIGIBILITY,
    }
)


def normalize_recall_eligibility(value: str | None) -> str:
    normalized = "-".join(str(value or "").strip().lower().replace("_", "-").split())
    if normalized not in _RECALL_ELIGIBILITY_MODES:
        raise ValueError(f"eligibility must be one of {sorted(_RECALL_ELIGIBILITY_MODES)}")
    return normalized


def recall_eligibility_suppresses_procedures(eligibility: str) -> bool:
    """Only the governed procedure path may inspect procedure-status-ineligible records."""
    return eligibility != PROCEDURE_GOVERNANCE_RECALL_ELIGIBILITY


def direct_lookup_ineligibility_reasons(
    store: Any,
    items: list[dict[str, Any]],
    *,
    connection: sqlite3.Connection | None = None,
) -> dict[str, str]:
    """Per-item ineligibility reasons for records read directly by id.

    Task memory reads relation targets and supporting records straight from
    the database instead of going through ranked recall, so the same lifecycle
    rules must be applied to those reads. Reasons use the task-memory
    suppression vocabulary ("superseded_revision", "procedure_status:<status>").
    """
    superseded_ids = _superseded_ids(
        store,
        [str(item.get("id") or "").strip() for item in items],
        connection=connection,
    )
    reasons: dict[str, str] = {}
    for item in items:
        if item.get("kind") != "memory":
            continue
        item_id = str(item.get("id") or "").strip()
        if not item_id or item_id in reasons:
            continue
        if item_id in superseded_ids:
            reasons[item_id] = "superseded_revision"
            continue
        status = _ineligible_procedure_status(item)
        if status:
            reasons[item_id] = f"procedure_status:{status}"
    return reasons


def filter_default_recall_candidates(
    store: Any,
    items: list[dict[str, Any]],
    *,
    connection: sqlite3.Connection | None = None,
    suppress_ineligible_procedures: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Remove known non-actionable records from ordinary recall candidates.

    Revision predecessors are always removed. Structured procedures with an
    ineligible status are removed unless the governed task-memory path asks to
    inspect them so its own governance layer can report suppression decisions.
    """

    candidate_ids = [str(item.get("id") or "").strip() for item in items]
    superseded_ids = _superseded_ids(store, candidate_ids, connection=connection)
    eligible: list[dict[str, Any]] = []
    suppression_reason_counts: Counter[str] = Counter()
    seen_ids: set[str] = set()

    for item in items:
        item_id = str(item.get("id") or "").strip()
        if item_id and item_id in seen_ids:
            continue
        if item_id:
            seen_ids.add(item_id)
        if item.get("kind") != "memory":
            eligible.append(item)
            continue

        reason = (
            "superseded_revision"
            if item_id in superseded_ids
            else _procedure_suppression_reason(item)
            if suppress_ineligible_procedures
            else None
        )
        if reason is not None:
            suppression_reason_counts[reason] += 1
            continue
        eligible.append(item)

    return eligible, dict(sorted(suppression_reason_counts.items()))


def _superseded_ids(
    store: Any,
    candidate_ids: list[str],
    *,
    connection: sqlite3.Connection | None,
) -> set[str]:
    unique_ids = list(dict.fromkeys(candidate_id for candidate_id in candidate_ids if candidate_id))
    if not unique_ids:
        return set()

    def _read(conn: sqlite3.Connection) -> set[str]:
        superseded: set[str] = set()
        for start in range(0, len(unique_ids), _MAX_SQL_VARIABLES):
            batch = unique_ids[start : start + _MAX_SQL_VARIABLES]
            placeholders = ", ".join("?" for _ in batch)
            rows = conn.execute(
                f"""
                SELECT predecessor_id
                FROM memory_revisions
                WHERE predecessor_id IN ({placeholders})
                """,
                batch,
            ).fetchall()
            superseded.update(str(row["predecessor_id"]) for row in rows)
        return superseded

    if connection is not None:
        return _read(connection)
    with store._connect() as conn:
        return _read(conn)


def _procedure_suppression_reason(item: dict[str, Any]) -> str | None:
    status = _ineligible_procedure_status(item)
    return f"procedure_{status}" if status else None


def _ineligible_procedure_status(item: dict[str, Any]) -> str | None:
    tags = {str(tag).strip() for tag in item.get("tags") or []}
    if _PROCEDURE_TAG not in tags:
        return None

    governance = parse_procedure_artifact(
        str(item.get("content") or ""),
        tags=item.get("tags") or [],
    )["governance"]
    status = str(governance.get("status") or "")
    if status not in INELIGIBLE_PROCEDURE_STATUSES:
        return None
    return status
