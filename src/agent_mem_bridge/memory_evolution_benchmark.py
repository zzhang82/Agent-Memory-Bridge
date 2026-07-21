from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CASES_PATH = ROOT / "benchmark" / "memory-evolution-cases.json"
DEFAULT_REPORT_PATH = ROOT / "benchmark" / "latest-memory-evolution-report.json"

BLOCKED_VALIDITY_STATUSES = {"expired", "future", "invalid"}
BLOCKED_GOVERNANCE_STATUSES = {"deleted", "replaced", "revoked", "stale", "superseded", "unsafe"}
REVIEW_RECORD_TYPES = {"learning-candidate", "learning-review", "memory-revision-candidate"}
QUARANTINE_STATUSES = {"quarantined", "suspicious"}
UNTRUSTED_SOURCE_TRUST = {"poisoned", "untrusted"}


def run_memory_evolution_benchmark(*, cases_path: Path | None = None) -> dict[str, Any]:
    """Run deterministic reviewed-memory-evolution cases from a fixture file."""

    case_file = cases_path or DEFAULT_CASES_PATH
    suite = load_memory_evolution_cases(case_file)
    as_of = _parse_timestamp(suite.get("as_of")) or datetime.now(UTC)

    results = [evaluate_memory_evolution_case(case, as_of=as_of) for case in suite["cases"]]

    return {
        "summary": build_memory_evolution_summary(results),
        "metadata": {
            "case_count": len(suite["cases"]),
            "task_count": _task_count(results),
            "cases_path": _display_path(case_file),
            "as_of": as_of.isoformat(),
            "comparison": "raw_fixture_visibility_vs_reviewed_evolution_governance",
            "notes": (
                "This is a deterministic fixture benchmark for reviewed memory revision, "
                "forgetting/tombstone audit, quarantine, principal scope, bitemporal validity, "
                "and hidden review lanes. It does not query live bridge state, enforce ACLs, "
                "certify poisoning resistance, or perform durable mutations."
            ),
        },
        "results": results,
    }


def load_memory_evolution_cases(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(raw, list):
        raw = {"cases": raw}
    cases = raw.get("cases")
    if not isinstance(cases, list):
        raise ValueError("memory evolution cases must be a list or an object with a cases list")
    for case in cases:
        if not isinstance(case.get("records"), list):
            raise ValueError(f"memory evolution case {case.get('id')} must include records")
        if not isinstance(case.get("tasks"), list):
            raise ValueError(f"memory evolution case {case.get('id')} must include tasks")
    return {"as_of": raw.get("as_of"), "cases": cases}


def evaluate_memory_evolution_case(case: dict[str, Any], *, as_of: datetime) -> dict[str, Any]:
    records = list(case.get("records") or [])
    task_results = [
        _evaluate_task(case=case, task=task, records=records, default_as_of=as_of) for task in case.get("tasks") or []
    ]
    return {
        "id": case["id"],
        "scenario": case.get("scenario", case["id"]),
        "query": case.get("query", ""),
        "notes": case.get("notes", ""),
        "record_count": len(records),
        "task_results": task_results,
    }


def build_memory_evolution_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    governed_scores = [task_result["governed"]["score"] for result in results for task_result in result["task_results"]]
    raw_scores = [task_result["raw"]["score"] for result in results for task_result in result["task_results"]]
    blocked_reasons = _blocked_reason_counts(results)
    warning_counts = _warning_counts(results)
    return {
        "case_count": len(results),
        "task_count": _task_count(results),
        "raw_task_pass_rate": _case_pass_rate(raw_scores),
        "governed_task_pass_rate": _case_pass_rate(governed_scores),
        "governed_required_visible_hit_rate": _requirement_hit_rate(governed_scores, "required_visible"),
        "raw_blocked_record_leak_rate": _blocked_leak_rate(raw_scores),
        "governed_blocked_record_leak_rate": _blocked_leak_rate(governed_scores),
        "governed_required_warning_hit_rate": _requirement_hit_rate(governed_scores, "required_warnings"),
        "governed_preferred_match_rate": _boolean_rate(governed_scores, "preferred_matches"),
        "governed_disposition_reason_hit_rate": _requirement_hit_rate(governed_scores, "required_block_reasons"),
        "scenario_pass_rates": _scenario_pass_rates(results),
        "blocked_reason_counts": blocked_reasons,
        "warning_counts": warning_counts,
    }


def _evaluate_task(
    *,
    case: dict[str, Any],
    task: dict[str, Any],
    records: list[dict[str, Any]],
    default_as_of: datetime,
) -> dict[str, Any]:
    expectations = task.get("expectations") or {}
    raw_visible_ids = [_record_id(record) for record in records]
    task_as_of = _parse_timestamp(task.get("as_of")) or default_as_of
    governed = _govern_records(records=records, task=task, as_of=task_as_of)
    warnings = _case_warnings(case=case, records=records, governed=governed, task=task)
    return {
        "id": task["id"],
        "task_intent": task.get("task_intent", ""),
        "as_of": task_as_of.isoformat(),
        "raw": {
            "visible_ids": raw_visible_ids,
            "score": _score_visibility(raw_visible_ids, expectations, warnings=[], blocked=[]),
        },
        "governed": {
            "visible_ids": governed["visible_ids"],
            "blocked": governed["blocked"],
            "warnings": warnings,
            "score": _score_visibility(
                governed["visible_ids"],
                expectations,
                warnings=warnings,
                blocked=governed["blocked"],
            ),
        },
    }


def _govern_records(
    *,
    records: list[dict[str, Any]],
    task: dict[str, Any],
    as_of: datetime,
) -> dict[str, Any]:
    visible_ids: list[str] = []
    blocked: dict[str, str] = {}
    for record in records:
        record_id = _record_id(record)
        reason = _initial_block_reason(record, task=task, as_of=as_of)
        if reason:
            blocked[record_id] = reason
            continue
        visible_ids.append(record_id)

    for source_id in list(visible_ids):
        source = _record_by_id(records, source_id)
        for target_id in _relation_targets(source, "supersedes"):
            if target_id in visible_ids:
                visible_ids.remove(target_id)
                blocked[target_id] = f"superseded-by:{source_id}"

    return {
        "visible_ids": visible_ids,
        "blocked": [{"id": record_id, "reason": reason} for record_id, reason in blocked.items()],
    }


def _initial_block_reason(record: dict[str, Any], *, task: dict[str, Any], as_of: datetime) -> str | None:
    principal = _normalize_text(task.get("principal"))
    applies_to_principals = _normalize_list(record.get("applies_to_principals"))
    if applies_to_principals:
        if not principal:
            return "principal-scope-required"
        if principal not in set(applies_to_principals):
            return "principal-scope-mismatch"

    task_intent = _normalize_text(task.get("task_intent"))
    applies_to_intents = _normalize_list(record.get("applies_to_intents"))
    if applies_to_intents and task_intent not in set(applies_to_intents):
        return "intent-mismatch"

    if _is_review_lane(record) and not bool(task.get("review_mode")):
        return "review-lane-hidden"

    validity_status = _validity_status(record, as_of=as_of)
    if validity_status in BLOCKED_VALIDITY_STATUSES:
        return f"validity:{validity_status}"

    governance_status = _governance_status(record)
    if governance_status in BLOCKED_GOVERNANCE_STATUSES:
        return f"governance:{governance_status}"

    quarantine_status = _normalize_text(record.get("quarantine_status"))
    if quarantine_status in QUARANTINE_STATUSES:
        return f"quarantine:{quarantine_status}"

    source_trust = _normalize_text(record.get("source_trust"))
    if source_trust in UNTRUSTED_SOURCE_TRUST:
        return f"source_trust:{source_trust}"

    return None


def _case_warnings(
    *,
    case: dict[str, Any],
    records: list[dict[str, Any]],
    governed: dict[str, Any],
    task: dict[str, Any],
) -> list[str]:
    warnings: list[str] = []
    visible_ids = set(governed["visible_ids"])
    blocked_reasons = {item["id"]: item["reason"] for item in governed["blocked"]}

    if any(reason == "principal-scope-mismatch" for reason in blocked_reasons.values()):
        warnings.append("principal-scope-filtered")
    if any(
        reason.startswith("quarantine:") or reason.startswith("source_trust:") for reason in blocked_reasons.values()
    ):
        warnings.append("quarantine-filtered")
    if any(reason.startswith("governance:deleted") for reason in blocked_reasons.values()):
        warnings.append("forgetting-delete-filtered")
    if any(
        reason.startswith("superseded-by:") or reason.startswith("governance:superseded")
        for reason in blocked_reasons.values()
    ):
        warnings.append("revision-lineage-applied")
    records_by_id = {_record_id(record): record for record in records}
    for source_id in visible_ids:
        source = records_by_id[source_id]
        if any(target_id in blocked_reasons for target_id in _relation_targets(source, "supersedes")):
            warnings.append("revision-lineage-applied")
            break
    if any(reason == "review-lane-hidden" for reason in blocked_reasons.values()):
        warnings.append("review-lane-hidden")

    has_tombstone = any(
        _record_id(record) in visible_ids and _normalize_text(record.get("record_type")) == "forgetting-audit"
        for record in records
    )
    if has_tombstone:
        warnings.append("forgetting-audit-present")

    if bool(task.get("review_mode")) and any(
        _record_id(record) in visible_ids and _is_review_lane(record) for record in records
    ):
        warnings.append("review-lane-visible-by-explicit-query")

    return _dedupe(warnings + _normalize_list(case.get("warnings")))


def _score_visibility(
    visible_ids: list[str],
    expectations: dict[str, Any],
    *,
    warnings: list[str],
    blocked: list[dict[str, str]],
) -> dict[str, Any]:
    required_visible = _normalize_list(expectations.get("required_visible_ids"))
    blocked_ids = _normalize_list(expectations.get("blocked_ids"))
    required_warnings = _normalize_list(expectations.get("required_warnings"))
    required_block_reasons = {
        str(item.get("id")): str(item.get("reason"))
        for item in expectations.get("required_block_reasons", [])
        if isinstance(item, dict) and item.get("id") and item.get("reason")
    }
    expected_preferred = _normalize_optional(expectations.get("expected_preferred_id"))
    visible_set = set(visible_ids)
    warning_set = set(warnings)
    blocked_reasons = {item["id"]: item["reason"] for item in blocked}

    missing_visible = [record_id for record_id in required_visible if record_id not in visible_set]
    leaked_blocked = [record_id for record_id in blocked_ids if record_id in visible_set]
    missing_warnings = [warning for warning in required_warnings if warning not in warning_set]
    missing_block_reasons = [
        {"id": record_id, "expected_reason": reason, "actual_reason": blocked_reasons.get(record_id)}
        for record_id, reason in required_block_reasons.items()
        if blocked_reasons.get(record_id) != reason
    ]
    preferred_id = visible_ids[0] if visible_ids else None
    preferred_matches = expected_preferred is None or preferred_id == expected_preferred
    case_passed = (
        not missing_visible
        and not leaked_blocked
        and not missing_warnings
        and not missing_block_reasons
        and preferred_matches
    )

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
        "required_block_reasons": {
            "expected": required_block_reasons,
            "hit_count": len(required_block_reasons) - len(missing_block_reasons),
            "total": len(required_block_reasons),
            "missing": missing_block_reasons,
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


def _is_review_lane(record: dict[str, Any]) -> bool:
    record_type = _normalize_text(record.get("record_type"))
    tags = set(_normalize_list(record.get("tags")))
    return record_type in REVIEW_RECORD_TYPES or bool(
        tags.intersection({"kind:learning-candidate", "kind:learning-review"})
    )


def _relation_targets(record: dict[str, Any], relation: str) -> list[str]:
    relations = record.get("relations") or {}
    return _normalize_list(relations.get(relation))


def _record_by_id(records: list[dict[str, Any]], record_id: str) -> dict[str, Any]:
    for record in records:
        if _record_id(record) == record_id:
            return record
    return {}


def _scenario_pass_rates(results: list[dict[str, Any]]) -> dict[str, float]:
    rates: dict[str, float] = {}
    for result in results:
        scores = [task["governed"]["score"] for task in result["task_results"]]
        rates[result["scenario"]] = _case_pass_rate(scores)
    return rates


def _blocked_reason_counts(results: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for result in results:
        for task in result["task_results"]:
            for blocked in task["governed"]["blocked"]:
                reason = str(blocked["reason"])
                counts[reason] = counts.get(reason, 0) + 1
    return dict(sorted(counts.items()))


def _warning_counts(results: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for result in results:
        for task in result["task_results"]:
            for warning in task["governed"]["warnings"]:
                counts[str(warning)] = counts.get(str(warning), 0) + 1
    return dict(sorted(counts.items()))


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
        raise ValueError("memory evolution records must include non-empty id")
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
