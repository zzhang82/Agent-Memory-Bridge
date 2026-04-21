from __future__ import annotations

import json
import shutil
import tempfile
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .belief_observation import BeliefObservationConfig, observe_belief_ladder
from .consolidation import ConsolidationConfig, ConsolidationEngine
from .storage import MemoryStore


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REVIEWED_SAMPLES_PATH = ROOT / "benchmark" / "belief-reviewed-samples.json"


def run_belief_review(
    *,
    reviewed_samples_path: Path | None = None,
    slices: tuple[str, ...] = (),
) -> dict[str, Any]:
    samples_path = reviewed_samples_path or DEFAULT_REVIEWED_SAMPLES_PATH
    samples = json.loads(samples_path.read_text(encoding="utf-8"))
    normalized_slices = tuple(dict.fromkeys(str(slice_name).strip() for slice_name in slices if str(slice_name).strip()))
    if normalized_slices:
        allowed = {slice_name.lower() for slice_name in normalized_slices}
        samples = [
            sample
            for sample in samples
            if str(sample.get("slice") or "unspecified").strip().lower() in allowed
        ]

    results: list[dict[str, Any]] = []
    by_slice: dict[str, list[dict[str, Any]]] = defaultdict(list)
    exact_match_count = 0
    for sample in samples:
        result = run_belief_review_case(sample)
        results.append(result)
        by_slice[result["slice"]].append(result)
        if result["match"]:
            exact_match_count += 1

    sample_count = len(results)
    blocking_reason_counts = Counter(
        result["actual"]["first_blocking_reason"]
        for result in results
        if result["actual"]["first_blocking_reason"]
    )
    belief_count = sum(1 for result in results if result["actual"]["belief"])
    candidate_only_count = sum(
        1
        for result in results
        if result["actual"]["belief_candidate"] and not result["actual"]["belief"]
    )
    return {
        "filters": {
            "slices": list(normalized_slices),
        },
        "summary": {
            "sample_count": sample_count,
            "exact_match_count": exact_match_count,
            "exact_match_rate": _rate(exact_match_count, sample_count),
            "belief_count": belief_count,
            "candidate_only_count": candidate_only_count,
            "blocking_reason_counts": dict(sorted(blocking_reason_counts.items())),
        },
        "slice_summaries": {
            slice_name: _summarize_slice(items)
            for slice_name, items in sorted(by_slice.items(), key=lambda item: item[0])
        },
        "results": results,
    }


def run_belief_review_case(sample: dict[str, Any]) -> dict[str, Any]:
    runtime_dir = Path(tempfile.mkdtemp(prefix="agent-memory-bridge-belief-review-"))
    try:
        store = MemoryStore(runtime_dir / "belief-review.db", log_dir=runtime_dir / "logs")
        target_domain = str(sample["target_domain"])
        target_claim = str(sample["target_claim"])
        config = _build_consolidation_config(runtime_dir, sample.get("config_overrides") or {})
        engine = ConsolidationEngine(store=store, config=config)
        phase_results: list[dict[str, Any]] = []

        for phase in sample.get("phases", []):
            for row in phase.get("source_rows", []):
                _store_reflex_row(store, row)
            phase_results.append(engine.run_once())
            for action in phase.get("actions_after_run", []):
                _apply_action(store, action=action, target_domain=target_domain, target_claim=target_claim)

        report = observe_belief_ladder(
            store=store,
            config=BeliefObservationConfig(
                namespace=config.target_namespace,
                actor=config.actor,
                belief_min_support=config.belief_min_support,
                belief_min_distinct_sessions=config.belief_min_distinct_sessions,
                belief_max_contradictions=config.belief_max_contradictions,
                belief_required_stable_candidates=config.belief_required_stable_candidates,
                belief_freshness_days=config.belief_freshness_days,
                top_n=1000,
            ),
        )

        actual = _extract_actual_outcome(store, report=report, target_domain=target_domain, target_claim=target_claim)
        expected = {
            "domain_note": bool(sample["expected"]["domain_note"]),
            "belief_candidate": bool(sample["expected"]["belief_candidate"]),
            "belief": bool(sample["expected"]["belief"]),
            "first_blocking_reason": sample["expected"].get("first_blocking_reason"),
        }
        return {
            "id": sample["id"],
            "slice": str(sample.get("slice") or "unspecified"),
            "description": str(sample.get("description") or "").strip(),
            "target_domain": target_domain,
            "target_claim": target_claim,
            "expected": expected,
            "actual": actual,
            "phase_results": phase_results,
            "match": actual == expected,
        }
    finally:
        shutil.rmtree(runtime_dir, ignore_errors=True)


def _build_consolidation_config(runtime_dir: Path, overrides: dict[str, Any]) -> ConsolidationConfig:
    config = ConsolidationConfig(state_path=runtime_dir / "consolidation-state.json")
    for key, value in overrides.items():
        setattr(config, key, value)
    return config


def _store_reflex_row(store: MemoryStore, row: dict[str, Any]) -> None:
    record_type = str(row["record_type"])
    lines = [
        f"record_type: {record_type}",
        f"claim: {row['claim']}",
    ]
    if row.get("trigger"):
        lines.append(f"trigger: {row['trigger']}")
    if row.get("symptom"):
        lines.append(f"symptom: {row['symptom']}")
    if row.get("fix"):
        lines.append(f"fix: {row['fix']}")
    lines.extend(["scope: global", f"confidence: {row.get('confidence', 'observed')}"])
    store.store(
        namespace="global",
        kind="memory",
        title=str(row["title"]),
        content="\n".join(lines),
        tags=[f"kind:{record_type}", str(row["domain"]), str(row["topic"]), "project:mem-store"],
        session_id=str(row["session_id"]),
        actor="bridge-reflex",
        correlation_id=str(row["correlation_id"]),
        source_app="agent-memory-bridge-reflex",
    )


def _apply_action(store: MemoryStore, *, action: dict[str, Any], target_domain: str, target_claim: str) -> None:
    action_type = str(action.get("type") or "").strip()
    if action_type != "age_latest_candidate_days":
        raise ValueError(f"unsupported belief review action: {action_type}")
    days = int(action.get("days") or 0)
    memory_id = _latest_candidate_id(store, target_domain=target_domain, target_claim=target_claim)
    if memory_id is None:
        raise ValueError(f"no belief candidate found to age for {target_domain}: {target_claim}")
    created_at = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    with store._connect() as conn:
        conn.execute("UPDATE memories SET created_at = ? WHERE id = ?", (created_at, memory_id))
        conn.commit()


def _extract_actual_outcome(
    store: MemoryStore,
    *,
    report: dict[str, Any],
    target_domain: str,
    target_claim: str,
) -> dict[str, Any]:
    candidate_row = next(
        (
            row
            for row in report["leaderboards"]["candidates"]
            if row["domain"] == target_domain and row["claim"] == target_claim
        ),
        None,
    )
    belief_row = next(
        (
            row
            for row in report["leaderboards"]["beliefs"]
            if row["domain"] == target_domain and row["claim"] == target_claim
        ),
        None,
    )
    return {
        "domain_note": _domain_note_exists(store, target_domain=target_domain),
        "belief_candidate": candidate_row is not None,
        "belief": belief_row is not None,
        "first_blocking_reason": None if belief_row is not None else (candidate_row["status"] if candidate_row else None),
    }


def _domain_note_exists(store: MemoryStore, *, target_domain: str) -> bool:
    with store._connect() as conn:
        row = conn.execute(
            """
            SELECT id
            FROM memories
            WHERE namespace = 'global'
              AND actor = 'bridge-consolidation'
              AND tags_json LIKE '%"kind:domain-note"%'
              AND tags_json LIKE ?
            LIMIT 1
            """,
            (f'%"{target_domain}"%',),
        ).fetchone()
    return row is not None


def _latest_candidate_id(store: MemoryStore, *, target_domain: str, target_claim: str) -> str | None:
    with store._connect() as conn:
        rows = conn.execute(
            """
            SELECT id, content, created_at
            FROM memories
            WHERE namespace = 'global'
              AND actor = 'bridge-consolidation'
              AND tags_json LIKE '%"kind:belief-candidate"%'
              AND tags_json LIKE ?
            ORDER BY created_at DESC
            """,
            (f'%"{target_domain}"%',),
        ).fetchall()
    for row in rows:
        content = _parse_fields(str(row["content"]))
        if content.get("claim") == target_claim:
            return str(row["id"])
    return None


def _parse_fields(content: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in content.splitlines():
        label, separator, remainder = line.partition(":")
        if not separator:
            continue
        key = label.strip().lower()
        value = " ".join(remainder.split()).strip()
        if not key or not value:
            continue
        fields.setdefault(key, value)
    return fields


def _summarize_slice(items: list[dict[str, Any]]) -> dict[str, Any]:
    sample_count = len(items)
    exact_match_count = sum(1 for item in items if item["match"])
    belief_count = sum(1 for item in items if item["actual"]["belief"])
    candidate_only_count = sum(
        1 for item in items if item["actual"]["belief_candidate"] and not item["actual"]["belief"]
    )
    blocking_reason_counts = Counter(
        item["actual"]["first_blocking_reason"]
        for item in items
        if item["actual"]["first_blocking_reason"]
    )
    return {
        "sample_count": sample_count,
        "exact_match_count": exact_match_count,
        "exact_match_rate": _rate(exact_match_count, sample_count),
        "belief_count": belief_count,
        "candidate_only_count": candidate_only_count,
        "blocking_reason_counts": dict(sorted(blocking_reason_counts.items())),
    }


def _rate(count: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round(count / total, 3)
