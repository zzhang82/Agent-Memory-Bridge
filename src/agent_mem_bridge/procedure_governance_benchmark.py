from __future__ import annotations

import inspect
import json
import shutil
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .storage import MemoryStore
from .task_memory import assemble_task_memory
from .task_memory_benchmark import seed_case_store


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CASES_PATH = ROOT / "benchmark" / "procedure-governance-cases.json"
DEFAULT_REPORT_PATH = ROOT / "benchmark" / "latest-procedure-governance-report.json"

TaskAssembler = Callable[..., dict[str, Any]]


def run_procedure_governance_benchmark(
    *,
    cases_path: Path | None = None,
    assembler: TaskAssembler | None = None,
) -> dict[str, Any]:
    """Compare flat packets with governed procedure packets on reviewed cases."""
    case_file = cases_path or DEFAULT_CASES_PATH
    cases = load_procedure_governance_cases(case_file)
    task_assembler = assembler or assemble_task_memory

    started = time.perf_counter_ns()
    results = [_run_case(case, assembler=task_assembler) for case in cases]
    elapsed_ms = round((time.perf_counter_ns() - started) / 1_000_000, 3)

    return {
        "summary": build_procedure_governance_summary(results),
        "metadata": {
            "case_count": len(cases),
            "cases_path": _display_path(case_file),
            "elapsed_ms": elapsed_ms,
            "comparison": "flat_packet_vs_governed_procedure_packet",
            "baseline": "assemble_task_memory with relation_aware=false",
            "governed_packet": "assemble_task_memory with relation_aware=true and procedure governance",
            "notes": (
                "This slice measures procedure packet quality and governance. "
                "It does not claim productivity gains, procedure execution, or automatic learning."
            ),
        },
        "results": results,
    }


def load_procedure_governance_cases(path: Path) -> list[dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    cases = raw["cases"] if isinstance(raw, dict) else raw
    if not isinstance(cases, list):
        raise ValueError("procedure governance cases must be a list or an object with a cases list")
    return cases


def build_procedure_governance_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    flat_scores = [result["flat"]["score"] for result in results]
    governed_scores = [result["governed"]["score"] for result in results]
    return {
        "case_count": len(results),
        "flat_case_pass_rate": _case_pass_rate(flat_scores),
        "governed_case_pass_rate": _case_pass_rate(governed_scores),
        "flat_validated_procedure_hit_rate": _requirement_hit_rate(flat_scores, "validated_procedures"),
        "governed_validated_procedure_hit_rate": _requirement_hit_rate(
            governed_scores, "validated_procedures"
        ),
        "flat_top_procedure_match_rate": _boolean_rate(flat_scores, "top_procedure_matches"),
        "governed_top_procedure_match_rate": _boolean_rate(governed_scores, "top_procedure_matches"),
        "flat_required_procedure_hit_rate": _requirement_hit_rate(flat_scores, "required_procedures"),
        "governed_required_procedure_hit_rate": _requirement_hit_rate(governed_scores, "required_procedures"),
        "flat_blocked_procedure_leak_rate": _blocked_leak_rate(flat_scores),
        "governed_blocked_procedure_leak_rate": _blocked_leak_rate(governed_scores),
        "flat_blocked_stale_unsafe_leak_rate": _blocked_stale_unsafe_leak_rate(flat_scores),
        "governed_blocked_stale_unsafe_leak_rate": _blocked_stale_unsafe_leak_rate(governed_scores),
        "flat_governance_status_match_rate": _requirement_hit_rate(flat_scores, "governance_statuses"),
        "governed_governance_status_match_rate": _requirement_hit_rate(governed_scores, "governance_statuses"),
        "flat_governance_field_completeness": _requirement_hit_rate(flat_scores, "required_fields"),
        "governed_governance_field_completeness": _requirement_hit_rate(
            governed_scores, "required_fields"
        ),
        "flat_required_field_hit_rate": _requirement_hit_rate(flat_scores, "required_fields"),
        "governed_required_field_hit_rate": _requirement_hit_rate(governed_scores, "required_fields"),
        "flat_required_warning_hit_rate": _requirement_hit_rate(flat_scores, "required_warnings"),
        "governed_required_warning_hit_rate": _requirement_hit_rate(governed_scores, "required_warnings"),
        "flat_avg_visible_procedures": _average([float(score["visible_procedure_count"]) for score in flat_scores]),
        "governed_avg_visible_procedures": _average(
            [float(score["visible_procedure_count"]) for score in governed_scores]
        ),
    }


def evaluate_procedure_governance_packet(packet: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
    expectations = case.get("expectations") or {}
    required_titles = _normalize_title_list(expectations.get("required_procedure_titles"))
    blocked_titles = _normalize_title_list(expectations.get("blocked_procedure_titles"))
    blocked_stale_unsafe_titles = _normalize_title_list(expectations.get("blocked_stale_unsafe_titles"))
    expected_top = _normalize_title(expectations.get("expected_top_procedure_title"))
    expected_statuses = _normalize_title_mapping(expectations.get("expected_governance_status_by_title"))
    required_fields = _normalize_title_mapping_list(expectations.get("required_fields_by_title"))
    required_warnings = _normalize_title_mapping_list(expectations.get("required_warnings_by_title"))
    expected_validated_titles = [
        title for title, status in expected_statuses.items() if status == "validated"
    ]

    procedures = _procedure_items(packet)
    visible_titles = [_normalize_title(item.get("title") or item.get("id")) for item in procedures]
    visible_titles = [title for title in visible_titles if title is not None]
    suppressed_titles = _packet_titles(packet, ("suppressed_items",))
    top_title = visible_titles[0] if visible_titles else None
    procedure_by_title = {
        title: item
        for item in procedures
        if (title := _normalize_title(item.get("title") or item.get("id"))) is not None
    }

    missing_required = [title for title in required_titles if title not in visible_titles]
    leaked_blocked = [title for title in blocked_titles if title in visible_titles]
    suppressed_blocked = [title for title in blocked_titles if title in suppressed_titles]
    leaked_stale_unsafe = [title for title in blocked_stale_unsafe_titles if title in visible_titles]
    suppressed_stale_unsafe = [title for title in blocked_stale_unsafe_titles if title in suppressed_titles]
    status_mismatches = _status_mismatches(procedure_by_title, expected_statuses)
    missing_fields = _missing_required_fields(procedure_by_title, required_fields)
    missing_warnings = _missing_required_warnings(procedure_by_title, required_warnings)
    missing_validated = _missing_validated_procedures(procedure_by_title, expected_validated_titles)
    top_matches = expected_top is None or top_title == expected_top

    case_passed = (
        not missing_required
        and not leaked_blocked
        and not leaked_stale_unsafe
        and not status_mismatches
        and not missing_fields
        and not missing_warnings
        and not missing_validated
        and top_matches
    )
    return {
        "case_passed": case_passed,
        "required_procedures": {
            "expected": required_titles,
            "hit_count": len(required_titles) - len(missing_required),
            "total": len(required_titles),
            "missing": missing_required,
        },
        "blocked_procedures": {
            "expected_absent": blocked_titles,
            "leaked": leaked_blocked,
            "suppressed": suppressed_blocked,
        },
        "blocked_stale_unsafe_procedures": {
            "expected_absent": blocked_stale_unsafe_titles,
            "leaked": leaked_stale_unsafe,
            "suppressed": suppressed_stale_unsafe,
        },
        "expected_top_procedure_title": expected_top,
        "top_procedure_title": top_title,
        "top_procedure_matches": top_matches,
        "validated_procedures": {
            "expected": expected_validated_titles,
            "hit_count": len(expected_validated_titles) - len(missing_validated),
            "total": len(expected_validated_titles),
            "missing": missing_validated,
        },
        "governance_statuses": _requirement_payload(status_mismatches, expected_statuses),
        "required_fields": _requirement_payload(missing_fields, required_fields),
        "required_warnings": _requirement_payload(missing_warnings, required_warnings),
        "visible_procedure_count": len(visible_titles),
        "visible_procedure_titles": visible_titles,
    }


def _run_case(case: dict[str, Any], *, assembler: TaskAssembler) -> dict[str, Any]:
    runtime_dir = Path(tempfile.mkdtemp(prefix=f"amb-procedure-governance-bench-{case['id']}-"))
    try:
        store = MemoryStore(runtime_dir / "benchmark.db", log_dir=runtime_dir / "logs")
        seed_result = seed_case_store(store, case)
        flat_packet = _assemble_case_packet(store, case, assembler=assembler, relation_aware=False)
        governed_packet = _assemble_case_packet(store, case, assembler=assembler, relation_aware=True)
        return {
            "id": case["id"],
            "query": case["query"],
            "notes": case.get("notes", ""),
            "seeded_records": {
                "count": len(seed_result),
                "local_ids": sorted(seed_result),
            },
            "flat": {
                "score": evaluate_procedure_governance_packet(flat_packet, case),
                "packet": _summarize_procedure_packet(flat_packet),
            },
            "governed": {
                "score": evaluate_procedure_governance_packet(governed_packet, case),
                "packet": _summarize_procedure_packet(governed_packet),
            },
        }
    finally:
        shutil.rmtree(runtime_dir, ignore_errors=True)


def _assemble_case_packet(
    store: MemoryStore,
    case: dict[str, Any],
    *,
    assembler: TaskAssembler,
    relation_aware: bool,
) -> dict[str, Any]:
    limits = case.get("limits") or {}
    kwargs = {
        "query": case["query"],
        "project_namespace": case.get("project_namespace") or "project:demo",
        "global_namespace": case.get("global_namespace") or "global",
        "procedure_limit": int(limits.get("procedure_limit", 3)),
        "concept_limit": int(limits.get("concept_limit", 3)),
        "belief_limit": int(limits.get("belief_limit", 3)),
        "domain_limit": int(limits.get("domain_limit", 2)),
        "support_limit": int(limits.get("support_limit", 6)),
    }
    if _supports_relation_aware_flag(assembler):
        return assembler(store, relation_aware=relation_aware, **kwargs)
    packet = assembler(store, **kwargs)
    packet["relation_aware_requested"] = relation_aware
    return packet


def _supports_relation_aware_flag(assembler: TaskAssembler) -> bool:
    signature = inspect.signature(assembler)
    if "relation_aware" in signature.parameters:
        return True
    return any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values())


def _summarize_procedure_packet(packet: dict[str, Any]) -> dict[str, Any]:
    return {
        "procedure_titles": _packet_titles(packet, ("procedure_hits",)),
        "suppressed_titles": _packet_titles(packet, ("suppressed_items",)),
        "procedures": [
            {
                "title": item.get("title"),
                "status": ((item.get("procedure") or {}).get("governance") or {}).get("status"),
                "warnings": ((item.get("procedure") or {}).get("governance") or {}).get("warnings", []),
                "fields_present": _present_procedure_fields(item),
            }
            for item in _procedure_items(packet)
        ],
    }


def _procedure_items(packet: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in packet.get("procedure_hits") or [] if isinstance(item, dict)]


def _present_procedure_fields(item: dict[str, Any]) -> list[str]:
    procedure = item.get("procedure") or {}
    present: list[str] = []
    for field_name in (
        "goal",
        "when_to_use",
        "when_not_to_use",
        "prerequisites",
        "steps",
        "failure_mode",
        "rollback_path",
    ):
        value = procedure.get(field_name)
        if isinstance(value, list) and value:
            present.append(field_name)
        elif isinstance(value, str) and value.strip():
            present.append(field_name)
    return present


def _status_mismatches(
    procedure_by_title: dict[str, dict[str, Any]],
    expected_statuses: dict[str, str],
) -> dict[str, Any]:
    mismatches: dict[str, Any] = {}
    for title, expected_status in expected_statuses.items():
        item = procedure_by_title.get(title)
        actual_status = ((item or {}).get("procedure") or {}).get("governance", {}).get("status")
        if actual_status != expected_status:
            mismatches[title] = {"expected": expected_status, "actual": actual_status}
    return mismatches


def _missing_required_fields(
    procedure_by_title: dict[str, dict[str, Any]],
    required_fields: dict[str, list[str]],
) -> dict[str, list[str]]:
    missing: dict[str, list[str]] = {}
    for title, fields in required_fields.items():
        present = set(_present_procedure_fields(procedure_by_title.get(title, {})))
        missing_fields = [field for field in fields if field not in present]
        if missing_fields:
            missing[title] = missing_fields
    return missing


def _missing_required_warnings(
    procedure_by_title: dict[str, dict[str, Any]],
    required_warnings: dict[str, list[str]],
) -> dict[str, list[str]]:
    missing: dict[str, list[str]] = {}
    for title, warnings in required_warnings.items():
        procedure = (procedure_by_title.get(title) or {}).get("procedure") or {}
        present = set((procedure.get("governance") or {}).get("warnings") or [])
        missing_warnings = [warning for warning in warnings if warning not in present]
        if missing_warnings:
            missing[title] = missing_warnings
    return missing


def _missing_validated_procedures(
    procedure_by_title: dict[str, dict[str, Any]],
    expected_titles: list[str],
) -> list[str]:
    missing: list[str] = []
    for title in expected_titles:
        item = procedure_by_title.get(title)
        status = ((item or {}).get("procedure") or {}).get("governance", {}).get("status")
        if status != "validated":
            missing.append(title)
    return missing


def _requirement_payload(missing: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
    total = sum(len(value) if isinstance(value, list) else 1 for value in expected.values())
    missing_total = sum(len(value) if isinstance(value, list) else 1 for value in missing.values())
    return {
        "expected": expected,
        "hit_count": total - missing_total,
        "total": total,
        "missing": missing,
    }


def _packet_titles(packet: dict[str, Any], sections: tuple[str, ...]) -> list[str]:
    titles: list[str] = []
    for section in sections:
        for item in packet.get(section) or []:
            title = _normalize_title(item.get("title") or item.get("id"))
            if title is not None:
                titles.append(title)
    return titles


def _normalize_title_mapping(raw_mapping: Any) -> dict[str, str]:
    if not isinstance(raw_mapping, dict):
        return {}
    normalized: dict[str, str] = {}
    for raw_title, raw_value in raw_mapping.items():
        title = _normalize_title(raw_title)
        value = " ".join(str(raw_value or "").split()).strip()
        if title and value:
            normalized[title] = value
    return normalized


def _normalize_title_mapping_list(raw_mapping: Any) -> dict[str, list[str]]:
    if not isinstance(raw_mapping, dict):
        return {}
    normalized: dict[str, list[str]] = {}
    for raw_title, raw_values in raw_mapping.items():
        title = _normalize_title(raw_title)
        values = _normalize_title_list(raw_values)
        if title and values:
            normalized[title] = values
    return normalized


def _normalize_title_list(raw_titles: Any) -> list[str]:
    if raw_titles is None:
        return []
    if not isinstance(raw_titles, list):
        raw_titles = [raw_titles]
    titles: list[str] = []
    seen: set[str] = set()
    for raw_title in raw_titles:
        title = _normalize_title(raw_title)
        if title is None or title in seen:
            continue
        seen.add(title)
        titles.append(title)
    return titles


def _normalize_title(raw_title: Any) -> str | None:
    if raw_title is None:
        return None
    title = " ".join(str(raw_title).split()).strip()
    return title or None


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
    total = sum(len(score["blocked_procedures"]["expected_absent"]) for score in scores)
    if total == 0:
        return 0.0
    leaked = sum(len(score["blocked_procedures"]["leaked"]) for score in scores)
    return round(leaked / total, 3)


def _blocked_stale_unsafe_leak_rate(scores: list[dict[str, Any]]) -> float:
    total = sum(len(score["blocked_stale_unsafe_procedures"]["expected_absent"]) for score in scores)
    if total == 0:
        return 0.0
    leaked = sum(len(score["blocked_stale_unsafe_procedures"]["leaked"]) for score in scores)
    return round(leaked / total, 3)


def _average(values: list[float]) -> float:
    if not values:
        return 0.0
    return round(sum(values) / len(values), 3)


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)
