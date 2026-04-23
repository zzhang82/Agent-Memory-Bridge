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


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CASES_PATH = ROOT / "benchmark" / "task-memory-cases.json"
DEFAULT_REPORT_PATH = ROOT / "benchmark" / "latest-task-memory-report.json"

TaskAssembler = Callable[..., dict[str, Any]]
PACKET_SECTIONS = ("procedure_hits", "concept_hits", "belief_hits", "domain_hits", "supporting_hits")
PRIMARY_SECTIONS = ("procedure_hits", "concept_hits", "belief_hits", "domain_hits")


def run_task_memory_benchmark(
    *,
    cases_path: Path | None = None,
    assembler: TaskAssembler | None = None,
) -> dict[str, Any]:
    """Compare flat task packets with relation-aware task packets on reviewed cases."""
    case_file = cases_path or DEFAULT_CASES_PATH
    cases = load_task_memory_cases(case_file)
    task_assembler = assembler or assemble_task_memory

    results = []
    flat_relation_flag_supported = _supports_relation_aware_flag(task_assembler)
    started = time.perf_counter_ns()
    for case in cases:
        results.append(_run_case(case, assembler=task_assembler))
    elapsed_ms = round((time.perf_counter_ns() - started) / 1_000_000, 3)

    return {
        "summary": build_task_memory_benchmark_summary(results),
        "metadata": {
            "case_count": len(cases),
            "cases_path": _display_path(case_file),
            "elapsed_ms": elapsed_ms,
            "comparison": "flat_current_vs_relation_aware_packet",
            "baseline": "current assemble_task_memory with relation_aware=false",
            "relation_aware_supported": flat_relation_flag_supported,
            "notes": (
                "This slice measures task-memory packet quality, not broad retrieval quality "
                "or graph reasoning."
            ),
        },
        "results": results,
    }


def load_task_memory_cases(path: Path) -> list[dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    cases = raw["cases"] if isinstance(raw, dict) else raw
    if not isinstance(cases, list):
        raise ValueError("task memory cases must be a list or an object with a cases list")
    return cases


def build_task_memory_benchmark_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    flat_scores = [result["flat"]["score"] for result in results]
    relation_scores = [result["relation_aware"]["score"] for result in results]
    return {
        "case_count": len(results),
        "flat_case_pass_rate": _average([1.0 if score["case_passed"] else 0.0 for score in flat_scores]),
        "relation_aware_case_pass_rate": _average(
            [1.0 if score["case_passed"] else 0.0 for score in relation_scores]
        ),
        "flat_required_primary_hit_rate": _requirement_hit_rate(flat_scores, "required_primary"),
        "relation_aware_required_primary_hit_rate": _requirement_hit_rate(
            relation_scores, "required_primary"
        ),
        "flat_required_support_hit_rate": _requirement_hit_rate(flat_scores, "required_support"),
        "relation_aware_required_support_hit_rate": _requirement_hit_rate(
            relation_scores, "required_support"
        ),
        "flat_blocked_item_leak_rate": _blocked_item_leak_rate(flat_scores),
        "relation_aware_blocked_item_leak_rate": _blocked_item_leak_rate(relation_scores),
        "flat_avg_packet_size": _average([float(score["packet_size"]) for score in flat_scores]),
        "relation_aware_avg_packet_size": _average(
            [float(score["packet_size"]) for score in relation_scores]
        ),
    }


def evaluate_task_memory_packet(packet: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
    expectations = case.get("expectations") or {}
    required_primary = _normalize_title_list(expectations.get("required_primary_titles"))
    required_support = _normalize_title_list(expectations.get("required_support_titles"))
    blocked_titles = _normalize_title_list(expectations.get("blocked_titles"))
    expected_top = _normalize_title(expectations.get("expected_top_primary_title"))

    primary_titles = _packet_titles(packet, PRIMARY_SECTIONS)
    all_visible_titles = _packet_titles(packet, PACKET_SECTIONS)
    suppressed_titles = _packet_titles(packet, ("suppressed_items",))
    top_primary = primary_titles[0] if primary_titles else None

    missing_primary = [title for title in required_primary if title not in primary_titles]
    # A relation-aware packet may dedupe support into its primary concept/belief/domain
    # section. The quality question is whether the supporting record survived into the
    # packet, not whether it appears twice.
    missing_support = [title for title in required_support if title not in all_visible_titles]
    leaked_blocked = [title for title in blocked_titles if title in all_visible_titles]
    suppressed_blocked = [title for title in blocked_titles if title in suppressed_titles]
    top_primary_matches = expected_top is None or top_primary == expected_top
    case_passed = not missing_primary and not missing_support and not leaked_blocked and top_primary_matches

    return {
        "case_passed": case_passed,
        "required_primary": {
            "expected": required_primary,
            "hit_count": len(required_primary) - len(missing_primary),
            "total": len(required_primary),
            "missing": missing_primary,
        },
        "required_support": {
            "expected": required_support,
            "hit_count": len(required_support) - len(missing_support),
            "total": len(required_support),
            "missing": missing_support,
        },
        "blocked_items": {
            "expected_absent": blocked_titles,
            "leaked": leaked_blocked,
            "suppressed": suppressed_blocked,
        },
        "expected_top_primary_title": expected_top,
        "top_primary_title": top_primary,
        "top_primary_matches": top_primary_matches,
        "packet_size": len(all_visible_titles),
        "visible_titles": all_visible_titles,
    }


def _run_case(case: dict[str, Any], *, assembler: TaskAssembler) -> dict[str, Any]:
    runtime_dir = Path(tempfile.mkdtemp(prefix=f"amb-task-memory-bench-{case['id']}-"))
    try:
        store = MemoryStore(runtime_dir / "benchmark.db", log_dir=runtime_dir / "logs")
        seed_result = seed_case_store(store, case)
        flat_packet = _assemble_case_packet(store, case, assembler=assembler, relation_aware=False)
        relation_packet = _assemble_case_packet(store, case, assembler=assembler, relation_aware=True)
        return {
            "id": case["id"],
            "query": case["query"],
            "notes": case.get("notes", ""),
            "seeded_records": {
                "count": len(seed_result),
                "local_ids": sorted(seed_result),
            },
            "flat": {
                "score": evaluate_task_memory_packet(flat_packet, case),
                "packet": _summarize_packet(flat_packet),
            },
            "relation_aware": {
                "score": evaluate_task_memory_packet(relation_packet, case),
                "packet": _summarize_packet(relation_packet),
            },
        }
    finally:
        shutil.rmtree(runtime_dir, ignore_errors=True)


def seed_case_store(store: MemoryStore, case: dict[str, Any]) -> dict[str, str]:
    local_to_memory_id: dict[str, str] = {}
    for record in case.get("records") or []:
        local_id = str(record.get("local_id") or "").strip()
        content = _replace_local_id_placeholders(record.get("content") or "", local_to_memory_id)
        result = store.store(
            namespace=record.get("namespace") or case.get("global_namespace") or "global",
            kind=record.get("kind", "memory"),
            title=record.get("title"),
            content=content,
            tags=list(record.get("tags") or []),
        )
        if local_id:
            local_to_memory_id[local_id] = result["id"]
    return local_to_memory_id


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
        packet = assembler(store, relation_aware=relation_aware, **kwargs)
        packet["relation_aware_supported"] = True
        return packet
    packet = assembler(store, **kwargs)
    packet["relation_aware_supported"] = False
    packet["relation_aware_requested"] = relation_aware
    return packet


def _supports_relation_aware_flag(assembler: TaskAssembler) -> bool:
    signature = inspect.signature(assembler)
    if "relation_aware" in signature.parameters:
        return True
    return any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values())


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def _replace_local_id_placeholders(content: str, local_to_memory_id: dict[str, str]) -> str:
    rendered = content
    for local_id, memory_id in local_to_memory_id.items():
        rendered = rendered.replace("{{" + local_id + "}}", memory_id)
    if "{{" in rendered or "}}" in rendered:
        raise ValueError(f"unresolved local relation placeholder in content: {rendered}")
    return rendered


def _summarize_packet(packet: dict[str, Any]) -> dict[str, Any]:
    return {
        "relation_aware_supported": packet.get("relation_aware_supported", True),
        "procedure_titles": _packet_titles(packet, ("procedure_hits",)),
        "concept_titles": _packet_titles(packet, ("concept_hits",)),
        "belief_titles": _packet_titles(packet, ("belief_hits",)),
        "domain_titles": _packet_titles(packet, ("domain_hits",)),
        "supporting_titles": _packet_titles(packet, ("supporting_hits",)),
        "suppressed_titles": _packet_titles(packet, ("suppressed_items",)),
        "summary": packet.get("summary", ""),
    }


def _packet_titles(packet: dict[str, Any], sections: tuple[str, ...]) -> list[str]:
    titles: list[str] = []
    for section in sections:
        for item in packet.get(section) or []:
            title = _normalize_title(item.get("title") or item.get("id"))
            if title is not None:
                titles.append(title)
    return titles


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


def _requirement_hit_rate(scores: list[dict[str, Any]], requirement_key: str) -> float:
    total = sum(int(score[requirement_key]["total"]) for score in scores)
    if total == 0:
        return 1.0
    hit_count = sum(int(score[requirement_key]["hit_count"]) for score in scores)
    return round(hit_count / total, 3)


def _blocked_item_leak_rate(scores: list[dict[str, Any]]) -> float:
    total = sum(len(score["blocked_items"]["expected_absent"]) for score in scores)
    if total == 0:
        return 0.0
    leaked = sum(len(score["blocked_items"]["leaked"]) for score in scores)
    return round(leaked / total, 3)


def _average(values: list[float]) -> float:
    if not values:
        return 0.0
    return round(sum(values) / len(values), 3)
