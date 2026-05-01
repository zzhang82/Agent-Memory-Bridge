from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CASES_PATH = ROOT / "benchmark" / "adversarial-memory-cases.json"
DEFAULT_REPORT_PATH = ROOT / "benchmark" / "latest-adversarial-memory-report.json"

BLOCKED_VALIDITY_STATUSES = {"expired", "future", "invalid"}
BLOCKED_GOVERNANCE_STATUSES = {"stale", "replaced", "superseded", "unsafe"}
STATUS_STRENGTH = {
    "validated": 4,
    "current": 3,
    "unbounded": 2,
    "unspecified": 2,
    "draft": 1,
    "stale": 0,
    "replaced": 0,
    "superseded": 0,
    "unsafe": 0,
}


def run_adversarial_benchmark(*, cases_path: Path | None = None) -> dict[str, Any]:
    """Run deterministic adversarial memory-governance cases from a fixture file."""
    case_file = cases_path or DEFAULT_CASES_PATH
    suite = load_adversarial_cases(case_file)
    as_of = _parse_timestamp(suite.get("as_of")) or datetime.now(UTC)

    started = time.perf_counter_ns()
    results = [evaluate_adversarial_case(case, as_of=as_of) for case in suite["cases"]]
    elapsed_ms = round((time.perf_counter_ns() - started) / 1_000_000, 3)

    return {
        "summary": build_adversarial_summary(results),
        "metadata": {
            "case_count": len(suite["cases"]),
            "task_count": _task_count(results),
            "cases_path": _display_path(case_file),
            "as_of": as_of.isoformat(),
            "elapsed_ms": elapsed_ms,
            "comparison": "raw_fixture_visibility_vs_governed_fixture_visibility",
            "notes": (
                "This slice exercises memory-governance realism with synthetic fixtures only. "
                "It does not query live bridge state, tune ranking, or claim broad retrieval quality."
            ),
        },
        "results": results,
    }


def load_adversarial_cases(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(raw, list):
        raw = {"cases": raw}
    cases = raw.get("cases")
    if not isinstance(cases, list):
        raise ValueError("adversarial cases must be a list or an object with a cases list")
    for case in cases:
        if not isinstance(case.get("records"), list):
            raise ValueError(f"adversarial case {case.get('id')} must include records")
        if not isinstance(case.get("tasks"), list):
            raise ValueError(f"adversarial case {case.get('id')} must include tasks")
    return {"as_of": raw.get("as_of"), "cases": cases}


def evaluate_adversarial_case(case: dict[str, Any], *, as_of: datetime) -> dict[str, Any]:
    records = list(case.get("records") or [])
    task_results = [
        _evaluate_task(case=case, task=task, records=records, as_of=as_of)
        for task in case.get("tasks") or []
    ]
    return {
        "id": case["id"],
        "scenario": case.get("scenario", case["id"]),
        "query": case.get("query", ""),
        "notes": case.get("notes", ""),
        "record_count": len(records),
        "task_results": task_results,
    }


def build_adversarial_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    task_scores = [
        task_result["governed"]["score"]
        for result in results
        for task_result in result["task_results"]
    ]
    raw_scores = [
        task_result["raw"]["score"]
        for result in results
        for task_result in result["task_results"]
    ]
    return {
        "case_count": len(results),
        "task_count": _task_count(results),
        "raw_task_pass_rate": _case_pass_rate(raw_scores),
        "governed_task_pass_rate": _case_pass_rate(task_scores),
        "governed_required_visible_hit_rate": _requirement_hit_rate(task_scores, "required_visible"),
        "raw_blocked_record_leak_rate": _blocked_leak_rate(raw_scores),
        "governed_blocked_record_leak_rate": _blocked_leak_rate(task_scores),
        "governed_required_warning_hit_rate": _requirement_hit_rate(task_scores, "required_warnings"),
        "governed_preferred_match_rate": _boolean_rate(task_scores, "preferred_matches"),
        "scenario_pass_rates": _scenario_pass_rates(results),
    }


def _evaluate_task(
    *,
    case: dict[str, Any],
    task: dict[str, Any],
    records: list[dict[str, Any]],
    as_of: datetime,
) -> dict[str, Any]:
    expectations = task.get("expectations") or {}
    raw_visible_ids = [_record_id(record) for record in records]
    governed = _govern_records(records=records, task=task, as_of=as_of)
    warnings = _case_warnings(case=case, records=records, visible_ids=governed["visible_ids"])

    return {
        "id": task["id"],
        "task_intent": task.get("task_intent", ""),
        "raw": {
            "visible_ids": raw_visible_ids,
            "score": _score_visibility(raw_visible_ids, expectations, warnings=[]),
        },
        "governed": {
            "visible_ids": governed["visible_ids"],
            "blocked": governed["blocked"],
            "warnings": warnings,
            "score": _score_visibility(governed["visible_ids"], expectations, warnings=warnings),
        },
    }


def _govern_records(
    *,
    records: list[dict[str, Any]],
    task: dict[str, Any],
    as_of: datetime,
) -> dict[str, Any]:
    records_by_id = {_record_id(record): record for record in records}
    visible_ids: list[str] = []
    blocked: dict[str, str] = {}
    task_intent = str(task.get("task_intent") or "").strip()

    for record in records:
        record_id = _record_id(record)
        reason = _initial_block_reason(record, task_intent=task_intent, as_of=as_of)
        if reason:
            blocked[record_id] = reason
        else:
            visible_ids.append(record_id)

    changed = True
    while changed:
        changed = False
        visible_set = set(visible_ids)
        for source_id in list(visible_ids):
            source = records_by_id[source_id]
            for target_id in _relation_targets(source, "contradicts"):
                if target_id not in visible_set:
                    continue
                winner_id, loser_id = _contradiction_winner(source_id, target_id, records_by_id)
                if loser_id not in visible_set:
                    continue
                visible_ids.remove(loser_id)
                blocked[loser_id] = f"contradicted-by:{winner_id}"
                changed = True
                break
            if changed:
                break

    return {
        "visible_ids": visible_ids,
        "blocked": [
            {"id": record_id, "reason": reason}
            for record_id, reason in blocked.items()
        ],
    }


def _initial_block_reason(record: dict[str, Any], *, task_intent: str, as_of: datetime) -> str | None:
    applies_to_intents = record.get("applies_to_intents")
    if applies_to_intents and task_intent not in {str(intent) for intent in applies_to_intents}:
        return "intent-mismatch"

    validity_status = _validity_status(record, as_of=as_of)
    if validity_status in BLOCKED_VALIDITY_STATUSES:
        return f"validity:{validity_status}"

    governance_status = _governance_status(record)
    if governance_status in BLOCKED_GOVERNANCE_STATUSES:
        return f"governance:{governance_status}"

    if record.get("record_type") == "session-summary" and record.get("noise") in {True, "high"}:
        return "noisy-session-summary"

    return None


def _case_warnings(
    *,
    case: dict[str, Any],
    records: list[dict[str, Any]],
    visible_ids: list[str],
) -> list[str]:
    warnings: list[str] = []
    visible_set = set(visible_ids)
    provenance_groups: dict[tuple[str, str, str], set[str]] = {}
    for record in records:
        if _record_id(record) not in visible_set:
            continue
        key = (
            _normalize_text(record.get("title")),
            _normalize_text(record.get("client_workspace")),
            _normalize_text(record.get("client_session_id")),
        )
        if not key[0] or not key[1] or not key[2]:
            continue
        provenance_groups.setdefault(key, set()).add(_normalize_text(record.get("source_client")))
    if any(len(clients) > 1 for clients in provenance_groups.values()):
        warnings.append("provenance-collision")

    records_by_id = {_record_id(record): record for record in records}
    visible_set = set(visible_ids)
    for source_id in visible_ids:
        for target_id in _relation_targets(records_by_id[source_id], "contradicts"):
            if target_id in visible_set:
                warnings.append("unresolved-contradiction")
                break

    return _dedupe(warnings)


def _score_visibility(
    visible_ids: list[str],
    expectations: dict[str, Any],
    *,
    warnings: list[str],
) -> dict[str, Any]:
    required_visible = _normalize_list(expectations.get("required_visible_ids"))
    blocked_ids = _normalize_list(expectations.get("blocked_ids"))
    required_warnings = _normalize_list(expectations.get("required_warnings"))
    expected_preferred = _normalize_optional(expectations.get("expected_preferred_id"))
    visible_set = set(visible_ids)
    warning_set = set(warnings)

    missing_visible = [record_id for record_id in required_visible if record_id not in visible_set]
    leaked_blocked = [record_id for record_id in blocked_ids if record_id in visible_set]
    missing_warnings = [warning for warning in required_warnings if warning not in warning_set]
    preferred_id = visible_ids[0] if visible_ids else None
    preferred_matches = expected_preferred is None or preferred_id == expected_preferred
    case_passed = not missing_visible and not leaked_blocked and not missing_warnings and preferred_matches

    return {
        "case_passed": case_passed,
        "preferred_id": preferred_id,
        "expected_preferred_id": expected_preferred,
        "preferred_matches": preferred_matches,
        "required_visible": {
            "expected": required_visible,
            "hit_count": len(required_visible) - len(missing_visible),
            "total": len(required_visible),
            "missing": missing_visible,
        },
        "blocked_records": {
            "expected_absent": blocked_ids,
            "leaked": leaked_blocked,
        },
        "required_warnings": {
            "expected": required_warnings,
            "hit_count": len(required_warnings) - len(missing_warnings),
            "total": len(required_warnings),
            "missing": missing_warnings,
        },
    }


def _validity_status(record: dict[str, Any], *, as_of: datetime) -> str:
    explicit_status = _normalize_text(record.get("validity_status"))
    if explicit_status:
        return explicit_status
    valid_from = _parse_timestamp(record.get("valid_from"))
    valid_until = _parse_timestamp(record.get("valid_until"))
    if record.get("valid_from") and valid_from is None:
        return "invalid"
    if record.get("valid_until") and valid_until is None:
        return "invalid"
    if valid_from is not None and as_of < valid_from:
        return "future"
    if valid_until is not None and as_of > valid_until:
        return "expired"
    if valid_from is not None or valid_until is not None:
        return "current"
    return "unbounded"


def _governance_status(record: dict[str, Any]) -> str:
    return _normalize_text(record.get("governance_status")) or "unspecified"


def _contradiction_winner(
    source_id: str,
    target_id: str,
    records_by_id: dict[str, dict[str, Any]],
) -> tuple[str, str]:
    source_strength = STATUS_STRENGTH.get(_governance_status(records_by_id[source_id]), 0)
    target_strength = STATUS_STRENGTH.get(_governance_status(records_by_id[target_id]), 0)
    if source_strength > target_strength:
        return source_id, target_id
    if target_strength > source_strength:
        return target_id, source_id
    return max(source_id, target_id), min(source_id, target_id)


def _relation_targets(record: dict[str, Any], relation: str) -> list[str]:
    relations = record.get("relations") or {}
    return _normalize_list(relations.get(relation))


def _scenario_pass_rates(results: list[dict[str, Any]]) -> dict[str, float]:
    rates: dict[str, float] = {}
    for result in results:
        scores = [task["governed"]["score"] for task in result["task_results"]]
        rates[result["scenario"]] = _case_pass_rate(scores)
    return rates


def _task_count(results: list[dict[str, Any]]) -> int:
    return sum(len(result["task_results"]) for result in results)


def _case_pass_rate(scores: list[dict[str, Any]]) -> float:
    return _average([1.0 if score["case_passed"] else 0.0 for score in scores])


def _boolean_rate(scores: list[dict[str, Any]], key: str) -> float:
    return _average([1.0 if score[key] else 0.0 for score in scores])


def _requirement_hit_rate(scores: list[dict[str, Any]], requirement_key: str) -> float:
    total = sum(int(score[requirement_key]["total"]) for score in scores)
    if total == 0:
        return 1.0
    hit_count = sum(int(score[requirement_key]["hit_count"]) for score in scores)
    return round(hit_count / total, 3)


def _blocked_leak_rate(scores: list[dict[str, Any]]) -> float:
    total = sum(len(score["blocked_records"]["expected_absent"]) for score in scores)
    if total == 0:
        return 0.0
    leaked = sum(len(score["blocked_records"]["leaked"]) for score in scores)
    return round(leaked / total, 3)


def _average(values: list[float]) -> float:
    if not values:
        return 0.0
    return round(sum(values) / len(values), 3)


def _parse_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _normalize_list(raw_values: Any) -> list[str]:
    if raw_values is None:
        return []
    if not isinstance(raw_values, list):
        raw_values = [raw_values]
    return _dedupe([value for value in (_normalize_optional(raw) for raw in raw_values) if value])


def _normalize_optional(value: Any) -> str | None:
    text = _normalize_text(value)
    return text or None


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _record_id(record: dict[str, Any]) -> str:
    record_id = _normalize_text(record.get("id"))
    if not record_id:
        raise ValueError("adversarial records must include non-empty id")
    return record_id


def _dedupe(values: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()
