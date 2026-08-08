from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Any

RUN_PROJECTION_VERSION = 1
MEMORY_UTILITY_SHADOW_PROJECTION_VERSION = 1

RUN_STATE_FIELDS = (
    "run_id",
    "status",
    "last_sequence",
    "unresolved_blocker_count",
    "active_work_item_count",
    "outcome_id",
    "ended_at",
    "termination_reason",
)
WORK_ITEM_STATE_FIELDS = (
    "work_item_id",
    "run_id",
    "status",
    "last_sequence",
    "started_at",
    "ended_at",
    "last_summary",
)
MEMORY_UTILITY_SHADOW_FIELDS = (
    "memory_id",
    "exact_content_version",
    "helpful_count",
    "misleading_count",
    "outdated_count",
    "not_used_count",
    "supporting_run_count",
    "contradicting_run_count",
    "shadow_score",
)


def initialize_run_projections(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    root_work_item_id: str,
    created_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO run_state_projection (
            run_id, status, last_sequence, unresolved_blocker_count,
            active_work_item_count, projection_version, rebuilt_at
        ) VALUES (?, 'active', 0, 0, 1, ?, ?)
        """,
        (run_id, RUN_PROJECTION_VERSION, created_at),
    )
    initialize_work_item_projection(
        conn,
        run_id=run_id,
        work_item_id=root_work_item_id,
        created_at=created_at,
        active=True,
    )


def initialize_work_item_projection(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    work_item_id: str,
    created_at: str,
    active: bool = False,
) -> None:
    conn.execute(
        """
        INSERT INTO run_work_item_state_projection (
            work_item_id, run_id, status, last_sequence, started_at,
            projection_version, rebuilt_at
        ) VALUES (?, ?, ?, 0, ?, ?, ?)
        """,
        (
            work_item_id,
            run_id,
            "active" if active else "pending",
            created_at if active else None,
            RUN_PROJECTION_VERSION,
            created_at,
        ),
    )


def apply_run_event_projection(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    work_item_id: str,
    sequence: int,
    event_type: str,
    summary: str,
    created_at: str,
) -> None:
    status: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    if event_type == "work_item_started":
        status = "active"
        started_at = created_at
    elif event_type == "blocker":
        status = "blocked"
    elif event_type == "work_item_completed":
        status = "completed"
        ended_at = created_at
    elif event_type == "work_item_failed":
        status = "failed"
        ended_at = created_at
    elif event_type == "work_item_abandoned":
        status = "abandoned"
        ended_at = created_at

    if status == "active":
        cursor = conn.execute(
            """
            UPDATE run_work_item_state_projection
            SET status = ?, last_sequence = ?, started_at = COALESCE(started_at, ?),
                ended_at = NULL, last_summary = ?, rebuilt_at = ?
            WHERE run_id = ? AND work_item_id = ?
            """,
            (status, sequence, started_at, summary, created_at, run_id, work_item_id),
        )
    elif status is not None:
        cursor = conn.execute(
            """
            UPDATE run_work_item_state_projection
            SET status = ?, last_sequence = ?, ended_at = COALESCE(?, ended_at),
                last_summary = ?, rebuilt_at = ?
            WHERE run_id = ? AND work_item_id = ?
            """,
            (status, sequence, ended_at, summary, created_at, run_id, work_item_id),
        )
    else:
        cursor = conn.execute(
            """
            UPDATE run_work_item_state_projection
            SET last_sequence = ?, last_summary = ?, rebuilt_at = ?
            WHERE run_id = ? AND work_item_id = ?
            """,
            (sequence, summary, created_at, run_id, work_item_id),
        )
    if cursor.rowcount != 1:
        raise RuntimeError("run work-item projection is missing")
    cursor = conn.execute(
        """
        UPDATE run_state_projection
        SET last_sequence = ?,
            unresolved_blocker_count = (
                SELECT COUNT(*)
                FROM run_work_item_state_projection
                WHERE run_id = ? AND status = 'blocked'
            ),
            active_work_item_count = (
                SELECT COUNT(*)
                FROM run_work_item_state_projection
                WHERE run_id = ? AND status = 'active'
            ),
            rebuilt_at = ?
        WHERE run_id = ?
        """,
        (sequence, run_id, run_id, created_at, run_id),
    )
    if cursor.rowcount != 1:
        raise RuntimeError("run projection is missing")


def apply_run_outcome_projection(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    outcome_id: str,
    outcome_type: str,
    termination_reason: str | None,
    created_at: str,
) -> None:
    status = "failed" if outcome_type == "failed" else ("abandoned" if outcome_type == "abandoned" else "completed")
    cursor = conn.execute(
        """
        UPDATE run_state_projection
        SET status = ?, outcome_id = ?, ended_at = ?, termination_reason = ?,
            rebuilt_at = ?
        WHERE run_id = ?
        """,
        (status, outcome_id, created_at, termination_reason, created_at, run_id),
    )
    if cursor.rowcount != 1:
        raise RuntimeError("run projection is missing")


def rebuild_run_projections(conn: sqlite3.Connection, *, rebuilt_at: str | None = None) -> dict[str, int]:
    """Rebuild materialized run/work-item state from append-only authority."""

    timestamp = rebuilt_at or datetime.now(UTC).isoformat()
    run_rows, work_item_rows = _expected_projection_rows(conn)
    conn.execute("DELETE FROM run_work_item_state_projection")
    conn.execute("DELETE FROM run_state_projection")
    conn.executemany(
        """
        INSERT INTO run_state_projection (
            run_id, status, last_sequence, unresolved_blocker_count,
            active_work_item_count, outcome_id, ended_at, termination_reason,
            projection_version, rebuilt_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                row["run_id"],
                row["status"],
                row["last_sequence"],
                row["unresolved_blocker_count"],
                row["active_work_item_count"],
                row["outcome_id"],
                row["ended_at"],
                row["termination_reason"],
                RUN_PROJECTION_VERSION,
                timestamp,
            )
            for row in run_rows.values()
        ],
    )
    conn.executemany(
        """
        INSERT INTO run_work_item_state_projection (
            work_item_id, run_id, status, last_sequence, started_at, ended_at,
            last_summary, projection_version, rebuilt_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                row["work_item_id"],
                row["run_id"],
                row["status"],
                row["last_sequence"],
                row["started_at"],
                row["ended_at"],
                row["last_summary"],
                RUN_PROJECTION_VERSION,
                timestamp,
            )
            for row in work_item_rows.values()
        ],
    )
    return {"run_count": len(run_rows), "work_item_count": len(work_item_rows)}


def rebuild_memory_utility_shadow(
    conn: sqlite3.Connection,
    *,
    computed_at: str | None = None,
) -> dict[str, int]:
    """Rebuild shadow-only memory utility counters from current feedback and outcomes."""

    timestamp = computed_at or datetime.now(UTC).isoformat()
    rows = _expected_memory_utility_shadow_rows(conn)
    conn.execute("DELETE FROM memory_utility_shadow")
    conn.executemany(
        """
        INSERT INTO memory_utility_shadow (
            memory_id, exact_content_version, helpful_count, misleading_count,
            outdated_count, not_used_count, supporting_run_count,
            contradicting_run_count, shadow_score, projection_version, computed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0.0, ?, ?)
        """,
        [
            (
                row["memory_id"],
                row["exact_content_version"],
                row["helpful_count"],
                row["misleading_count"],
                row["outdated_count"],
                row["not_used_count"],
                row["supporting_run_count"],
                row["contradicting_run_count"],
                MEMORY_UTILITY_SHADOW_PROJECTION_VERSION,
                timestamp,
            )
            for row in rows.values()
        ],
    )
    return {"memory_version_count": len(rows)}


def inspect_memory_utility_shadow(conn: sqlite3.Connection) -> dict[str, Any]:
    """Compare shadow utility rows with current append-only evidence."""

    counts = {
        "missing_memory_utility_shadow_count": 0,
        "stale_memory_utility_shadow_count": 0,
        "orphan_memory_utility_shadow_count": 0,
    }
    samples: dict[str, list[str]] = {key.removesuffix("_count"): [] for key in counts}
    tables = {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()}
    required = {"run_memory_links", "retrieval_feedback", "run_outcomes", "memory_utility_shadow"}
    if not required.issubset(tables):
        missing_tables = sorted(required - tables)
        counts["missing_memory_utility_shadow_count"] = 1
        samples["missing_memory_utility_shadow"].append(f"required-tables-missing:{','.join(missing_tables)}")
        return {"ok": False, "counts": counts, "samples": samples}

    expected = _expected_memory_utility_shadow_rows(conn)
    actual = {
        _memory_utility_shadow_key(str(row["memory_id"]), str(row["exact_content_version"])): _row_values(
            row, MEMORY_UTILITY_SHADOW_FIELDS
        )
        for row in conn.execute(
            """
            SELECT memory_id, exact_content_version, helpful_count, misleading_count,
                   outdated_count, not_used_count, supporting_run_count,
                   contradicting_run_count, shadow_score
            FROM memory_utility_shadow
            """
        ).fetchall()
    }
    _compare_projection_maps(
        expected,
        actual,
        missing_key="missing_memory_utility_shadow",
        stale_key="stale_memory_utility_shadow",
        orphan_key="orphan_memory_utility_shadow",
        counts=counts,
        samples=samples,
    )
    return {"ok": sum(counts.values()) == 0, "counts": counts, "samples": samples}


def inspect_run_projections(conn: sqlite3.Connection) -> dict[str, Any]:
    """Compare materialized run state with independently computed authority state."""

    counts = {
        "missing_run_state_projection_count": 0,
        "stale_run_state_projection_count": 0,
        "orphan_run_state_projection_count": 0,
        "missing_work_item_state_projection_count": 0,
        "stale_work_item_state_projection_count": 0,
        "orphan_work_item_state_projection_count": 0,
    }
    samples: dict[str, list[str]] = {key.removesuffix("_count"): [] for key in counts}
    tables = {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()}
    required = {
        "agent_runs",
        "run_work_items",
        "run_events",
        "run_outcomes",
        "run_state_projection",
        "run_work_item_state_projection",
    }
    if not required.issubset(tables):
        missing_tables = sorted(required - tables)
        if "run_state_projection" not in tables or {"agent_runs", "run_events", "run_outcomes"} - tables:
            counts["missing_run_state_projection_count"] = 1
            samples["missing_run_state_projection"].append(f"required-tables-missing:{','.join(missing_tables)}")
        if "run_work_item_state_projection" not in tables or "run_work_items" not in tables:
            counts["missing_work_item_state_projection_count"] = 1
            samples["missing_work_item_state_projection"].append(f"required-tables-missing:{','.join(missing_tables)}")
        return {"ok": False, "counts": counts, "samples": samples}

    expected_runs, expected_work_items = _expected_projection_rows(conn)
    actual_runs = {
        str(row["run_id"]): _row_values(row, RUN_STATE_FIELDS)
        for row in conn.execute(
            """
            SELECT run_id, status, last_sequence, unresolved_blocker_count,
                   active_work_item_count, outcome_id, ended_at, termination_reason
            FROM run_state_projection
            """
        ).fetchall()
    }
    actual_work_items = {
        str(row["work_item_id"]): _row_values(row, WORK_ITEM_STATE_FIELDS)
        for row in conn.execute(
            """
            SELECT work_item_id, run_id, status, last_sequence, started_at,
                   ended_at, last_summary
            FROM run_work_item_state_projection
            """
        ).fetchall()
    }
    _compare_projection_maps(
        expected_runs,
        actual_runs,
        missing_key="missing_run_state_projection",
        stale_key="stale_run_state_projection",
        orphan_key="orphan_run_state_projection",
        counts=counts,
        samples=samples,
    )
    _compare_projection_maps(
        expected_work_items,
        actual_work_items,
        missing_key="missing_work_item_state_projection",
        stale_key="stale_work_item_state_projection",
        orphan_key="orphan_work_item_state_projection",
        counts=counts,
        samples=samples,
    )
    return {"ok": sum(counts.values()) == 0, "counts": counts, "samples": samples}


def _expected_projection_rows(
    conn: sqlite3.Connection,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    run_rows: dict[str, dict[str, Any]] = {}
    for row in conn.execute("SELECT run_id FROM agent_runs ORDER BY created_at, run_id").fetchall():
        run_id = str(row["run_id"])
        run_rows[run_id] = {
            "run_id": run_id,
            "status": "active",
            "last_sequence": 0,
            "unresolved_blocker_count": 0,
            "active_work_item_count": 0,
            "outcome_id": None,
            "ended_at": None,
            "termination_reason": None,
        }

    work_item_rows: dict[str, dict[str, Any]] = {}
    for row in conn.execute(
        """
        SELECT work_item_id, run_id, parent_work_item_id, created_at
        FROM run_work_items
        ORDER BY created_at, work_item_id
        """
    ).fetchall():
        work_item_id = str(row["work_item_id"])
        is_root = row["parent_work_item_id"] is None
        work_item_rows[work_item_id] = {
            "work_item_id": work_item_id,
            "run_id": str(row["run_id"]),
            "status": "active" if is_root else "pending",
            "last_sequence": 0,
            "started_at": str(row["created_at"]) if is_root else None,
            "ended_at": None,
            "last_summary": None,
        }

    for row in conn.execute(
        """
        SELECT run_id, work_item_id, sequence, event_type, summary, created_at
        FROM run_events
        ORDER BY run_id, sequence
        """
    ).fetchall():
        run_id = str(row["run_id"])
        work_item_id = str(row["work_item_id"])
        sequence = int(row["sequence"])
        event_type = str(row["event_type"])
        created_at = str(row["created_at"])
        run_state = run_rows[run_id]
        work_state = work_item_rows[work_item_id]
        run_state["last_sequence"] = sequence
        work_state["last_sequence"] = sequence
        work_state["last_summary"] = str(row["summary"])
        if event_type == "work_item_started":
            work_state["status"] = "active"
            work_state["started_at"] = work_state["started_at"] or created_at
            work_state["ended_at"] = None
        elif event_type == "blocker":
            work_state["status"] = "blocked"
        elif event_type == "work_item_completed":
            work_state["status"] = "completed"
            work_state["ended_at"] = created_at
        elif event_type == "work_item_failed":
            work_state["status"] = "failed"
            work_state["ended_at"] = created_at
        elif event_type == "work_item_abandoned":
            work_state["status"] = "abandoned"
            work_state["ended_at"] = created_at

    outcome_heads = conn.execute(
        """
        SELECT outcome.outcome_id, outcome.run_id, outcome.outcome_type,
               outcome.termination_reason, outcome.created_at
        FROM run_outcomes outcome
        LEFT JOIN run_outcomes child
          ON child.supersedes_outcome_id = outcome.outcome_id
        WHERE child.outcome_id IS NULL
        ORDER BY outcome.created_at, outcome.outcome_id
        """
    ).fetchall()
    for row in outcome_heads:
        run_state = run_rows[str(row["run_id"])]
        outcome_type = str(row["outcome_type"])
        run_state["status"] = (
            "failed" if outcome_type == "failed" else ("abandoned" if outcome_type == "abandoned" else "completed")
        )
        run_state["outcome_id"] = str(row["outcome_id"])
        run_state["ended_at"] = str(row["created_at"])
        run_state["termination_reason"] = (
            str(row["termination_reason"]) if row["termination_reason"] is not None else None
        )

    for run_id, run_state in run_rows.items():
        states = [row["status"] for row in work_item_rows.values() if row["run_id"] == run_id]
        run_state["unresolved_blocker_count"] = sum(status == "blocked" for status in states)
        run_state["active_work_item_count"] = sum(status == "active" for status in states)
    return run_rows, work_item_rows


def _expected_memory_utility_shadow_rows(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        """
        WITH eligible AS (
            SELECT DISTINCT
                link.memory_id,
                link.exact_content_version,
                link.run_id,
                link.feedback_id,
                feedback.outcome AS feedback_outcome,
                outcome.outcome_type AS run_outcome
            FROM run_memory_links link
            JOIN retrieval_feedback feedback ON feedback.feedback_id = link.feedback_id
            JOIN run_outcomes outcome ON outcome.run_id = link.run_id
            LEFT JOIN retrieval_feedback feedback_child
              ON feedback_child.supersedes_feedback_id = feedback.feedback_id
            LEFT JOIN run_outcomes outcome_child
              ON outcome_child.supersedes_outcome_id = outcome.outcome_id
            WHERE link.feedback_id IS NOT NULL
              AND link.review_required = 0
              AND feedback_child.feedback_id IS NULL
              AND feedback.feedback_type != 'retraction'
              AND outcome_child.outcome_id IS NULL
              AND feedback.outcome IN ('helpful', 'misleading', 'outdated', 'not_used')
        )
        SELECT
            memory_id,
            exact_content_version,
            SUM(CASE WHEN feedback_outcome = 'helpful' THEN 1 ELSE 0 END) AS helpful_count,
            SUM(CASE WHEN feedback_outcome = 'misleading' THEN 1 ELSE 0 END) AS misleading_count,
            SUM(CASE WHEN feedback_outcome = 'outdated' THEN 1 ELSE 0 END) AS outdated_count,
            SUM(CASE WHEN feedback_outcome = 'not_used' THEN 1 ELSE 0 END) AS not_used_count,
            COUNT(DISTINCT CASE
                WHEN feedback_outcome = 'helpful' AND run_outcome = 'verified_success' THEN run_id
            END) AS supporting_run_count,
            COUNT(DISTINCT CASE
                WHEN feedback_outcome IN ('misleading', 'outdated')
                 AND run_outcome IN ('failed', 'user_corrected', 'regression') THEN run_id
            END) AS contradicting_run_count
        FROM eligible
        GROUP BY memory_id, exact_content_version
        """
    ).fetchall()
    return {
        _memory_utility_shadow_key(str(row["memory_id"]), str(row["exact_content_version"])): {
            "memory_id": str(row["memory_id"]),
            "exact_content_version": str(row["exact_content_version"]),
            "helpful_count": int(row["helpful_count"]),
            "misleading_count": int(row["misleading_count"]),
            "outdated_count": int(row["outdated_count"]),
            "not_used_count": int(row["not_used_count"]),
            "supporting_run_count": int(row["supporting_run_count"]),
            "contradicting_run_count": int(row["contradicting_run_count"]),
            "shadow_score": 0.0,
        }
        for row in rows
    }


def _memory_utility_shadow_key(memory_id: str, exact_content_version: str) -> str:
    return f"{memory_id}:{exact_content_version}"


def _row_values(row: sqlite3.Row, fields: tuple[str, ...]) -> dict[str, Any]:
    return {field: row[field] for field in fields}


def _compare_projection_maps(
    expected: dict[str, dict[str, Any]],
    actual: dict[str, dict[str, Any]],
    *,
    missing_key: str,
    stale_key: str,
    orphan_key: str,
    counts: dict[str, int],
    samples: dict[str, list[str]],
) -> None:
    for identifier, expected_row in expected.items():
        actual_row = actual.get(identifier)
        if actual_row is None:
            counts[f"{missing_key}_count"] += 1
            _append_sample(samples[missing_key], identifier)
        elif actual_row != expected_row:
            counts[f"{stale_key}_count"] += 1
            _append_sample(samples[stale_key], identifier)
    for identifier in actual.keys() - expected.keys():
        counts[f"{orphan_key}_count"] += 1
        _append_sample(samples[orphan_key], identifier)


def _append_sample(samples: list[str], identifier: str) -> None:
    if len(samples) < 20:
        samples.append(identifier)
