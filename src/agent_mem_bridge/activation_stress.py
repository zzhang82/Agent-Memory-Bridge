from __future__ import annotations

import json
import shutil
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .belief_replay import BeliefReplayConfig, run_belief_replay
from .belief_review import DEFAULT_REVIEWED_SAMPLES_PATH, run_belief_review_case
from .storage import MemoryStore


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ACTIVATION_STRESS_PACK_PATH = ROOT / "benchmark" / "belief-activation-stress-pack.json"


def run_activation_stress_pack(
    *,
    pack_path: Path | None = None,
    buckets: tuple[str, ...] = (),
) -> dict[str, Any]:
    manifest_path = pack_path or DEFAULT_ACTIVATION_STRESS_PACK_PATH
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    normalized_buckets = tuple(
        dict.fromkeys(str(bucket).strip() for bucket in buckets if str(bucket).strip())
    )
    allowed_buckets = {bucket.lower() for bucket in normalized_buckets}
    reviewed_samples_path = _resolve_relative_path(
        manifest_path,
        manifest.get("reviewed_samples_path"),
        fallback=DEFAULT_REVIEWED_SAMPLES_PATH,
    )
    reviewed_samples = {
        str(sample["id"]): sample
        for sample in json.loads(reviewed_samples_path.read_text(encoding="utf-8"))
    }

    results: list[dict[str, Any]] = []
    for case in manifest.get("reviewed_cases", []):
        bucket = str(case["bucket"]).strip()
        if allowed_buckets and bucket.lower() not in allowed_buckets:
            continue
        sample_id = str(case["sample_id"])
        sample = reviewed_samples.get(sample_id)
        if sample is None:
            raise ValueError(f"unknown reviewed sample id in activation stress pack: {sample_id}")
        review_result = run_belief_review_case(sample)
        results.append(
            {
                "id": sample_id,
                "kind": "reviewed",
                "bucket": bucket,
                "durability": str(case.get("durability") or "durable-regression"),
                "description": str(sample.get("description") or ""),
                "expected": review_result["expected"],
                "actual": review_result["actual"],
                "match": bool(review_result["match"]),
                "failure_reasons": [],
            }
        )

    for scenario in manifest.get("replay_scenarios", []):
        bucket = str(scenario["bucket"]).strip()
        if allowed_buckets and bucket.lower() not in allowed_buckets:
            continue
        results.append(_run_replay_scenario(scenario))

    pass_count = sum(1 for result in results if result["match"])
    case_count = len(results)
    by_bucket: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_durability: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for result in results:
        by_bucket[result["bucket"]].append(result)
        by_durability[result["durability"]].append(result)

    return {
        "filters": {
            "buckets": list(normalized_buckets),
            "pack_path": str(manifest_path),
            "reviewed_samples_path": str(reviewed_samples_path),
        },
        "summary": {
            "case_count": case_count,
            "pass_count": pass_count,
            "pass_rate": _rate(pass_count, case_count),
            "reviewed_case_count": sum(1 for result in results if result["kind"] == "reviewed"),
            "replay_scenario_count": sum(1 for result in results if result["kind"] == "replay"),
        },
        "bucket_summaries": {
            bucket: _summarize_group(items)
            for bucket, items in sorted(by_bucket.items(), key=lambda item: item[0])
        },
        "durability_summaries": {
            durability: _summarize_group(items)
            for durability, items in sorted(by_durability.items(), key=lambda item: item[0])
        },
        "cleanup_posture": {
            "touches_live_data": False,
            "runtime_cleanup": "automatic-temp-store-removal",
            "durable_regression_ids": [
                result["id"]
                for result in results
                if result["durability"] == "durable-regression"
            ],
            "cleanup_guidance": list(manifest.get("cleanup_guidance") or []),
        },
        "results": sorted(
            results,
            key=lambda result: (
                0 if result["kind"] == "reviewed" else 1,
                result["bucket"],
                result["id"],
            ),
        ),
    }


def render_activation_stress_text(report: dict[str, Any]) -> str:
    summary = report["summary"]
    cleanup_posture = report["cleanup_posture"]
    lines = [
        "Activation Stress Pack",
        "",
        "Summary",
        f"case_count: {summary['case_count']}",
        f"pass_count: {summary['pass_count']}",
        f"pass_rate: {_format_percent(summary['pass_rate'])}",
        f"reviewed_case_count: {summary['reviewed_case_count']}",
        f"replay_scenario_count: {summary['replay_scenario_count']}",
        "",
        "Buckets",
    ]
    for bucket, bucket_summary in report["bucket_summaries"].items():
        lines.append(
            f"{bucket}: {bucket_summary['pass_count']}/{bucket_summary['case_count']} "
            f"({_format_percent(bucket_summary['pass_rate'])})"
        )
    lines.extend(["", "Durability"])
    for durability, durability_summary in report["durability_summaries"].items():
        lines.append(
            f"{durability}: {durability_summary['pass_count']}/{durability_summary['case_count']} "
            f"({_format_percent(durability_summary['pass_rate'])})"
        )
    lines.extend(
        [
            "",
            "Cleanup",
            f"touches_live_data: {cleanup_posture['touches_live_data']}",
            f"runtime_cleanup: {cleanup_posture['runtime_cleanup']}",
            "durable_regression_ids: " + ", ".join(cleanup_posture["durable_regression_ids"]),
        ]
    )
    for guidance in cleanup_posture["cleanup_guidance"]:
        lines.append(f"- {guidance}")

    failing = [result for result in report["results"] if not result["match"]]
    if failing:
        lines.extend(["", "Failures"])
        for result in failing:
            reasons = ", ".join(result["failure_reasons"]) or "mismatch"
            lines.append(f"{result['id']} ({result['bucket']}): {reasons}")
    else:
        lines.extend(["", "Failures", "(none)"])
    return "\n".join(lines)


def _run_replay_scenario(scenario: dict[str, Any]) -> dict[str, Any]:
    runtime_dir = Path(tempfile.mkdtemp(prefix="agent-memory-bridge-activation-stress-"))
    try:
        source_store = MemoryStore(runtime_dir / "source.db", log_dir=runtime_dir / "logs")
        for row in scenario.get("source_rows", []):
            _store_source_row(source_store, row=row)

        replay_report = run_belief_replay(
            source_store=source_store,
            config=_build_replay_config(
                source_row_count=len(scenario.get("source_rows", [])),
                config_data=scenario.get("config") or {},
            ),
        )
        final_window = replay_report["windows"][-1] if replay_report["windows"] else None
        actual = {
            "first_candidate_window": replay_report["summary"]["first_candidate_window"],
            "first_belief_window": replay_report["summary"]["first_belief_window"],
            "first_red_flag_window": replay_report["summary"]["first_red_flag_window"],
            "first_out_of_filter_window": replay_report["summary"]["first_out_of_filter_window"],
            "stopped_on_red_flag": replay_report["summary"]["stopped_on_red_flag"],
            "final_belief_count": final_window["summary"]["belief_count"] if final_window else 0,
            "final_candidate_count": final_window["summary"]["belief_candidate_count"] if final_window else 0,
            "final_red_flags": list(final_window.get("red_flags", [])) if final_window else [],
            "final_candidate_statuses": sorted(
                {
                    str(row["status"])
                    for row in (final_window["leaderboards"]["candidates"] if final_window else [])
                }
            ),
            "all_actions_applied": sorted(
                {
                    action
                    for window in replay_report["windows"]
                    for action in window.get("actions_applied", [])
                }
            ),
            "out_of_filter_domains": sorted(
                {
                    domain
                    for window in replay_report["windows"]
                    for domain in window.get("out_of_filter_domains", [])
                }
            ),
        }
        match, failure_reasons = _evaluate_replay_expectations(
            actual=actual,
            expected=scenario.get("expected") or {},
        )
        return {
            "id": str(scenario["id"]),
            "kind": "replay",
            "bucket": str(scenario["bucket"]),
            "durability": str(scenario.get("durability") or "durable-regression"),
            "description": str(scenario.get("description") or ""),
            "expected": scenario.get("expected") or {},
            "actual": actual,
            "match": match,
            "failure_reasons": failure_reasons,
        }
    finally:
        shutil.rmtree(runtime_dir, ignore_errors=True)


def _build_replay_config(*, source_row_count: int, config_data: dict[str, Any]) -> BeliefReplayConfig:
    return BeliefReplayConfig(
        source_namespace=str(config_data.get("source_namespace") or "global"),
        target_namespace=str(config_data.get("target_namespace") or "global"),
        source_limit=int(config_data.get("source_limit") or max(source_row_count, 1)),
        window_size=int(config_data.get("window_size") or 5),
        since_days=config_data.get("since_days"),
        domain_tags=tuple(config_data.get("domain_tags") or ()),
        project_tags=tuple(config_data.get("project_tags") or ()),
        session_ids=tuple(config_data.get("session_ids") or ()),
        session_null_only=bool(config_data.get("session_null_only") or False),
        null_session_uplift_mode=str(config_data.get("null_session_uplift_mode") or "none"),
        correlation_ids=tuple(config_data.get("correlation_ids") or ()),
        actor=str(config_data.get("actor") or "bridge-consolidation"),
        belief_to_domain_note_ratio_red_flag=float(
            config_data.get("belief_to_domain_note_ratio_red_flag") or 0.5
        ),
        candidate_to_belief_rate_red_flag=float(
            config_data.get("candidate_to_belief_rate_red_flag") or 0.8
        ),
        max_candidate_count_red_flag=int(config_data.get("max_candidate_count_red_flag") or 25),
        stop_on_red_flag=bool(config_data.get("stop_on_red_flag", True)),
        top_n=int(config_data.get("top_n") or 5),
        age_candidates_after_windows=tuple(config_data.get("age_candidates_after_windows") or ()),
        age_candidates_by_days=config_data.get("age_candidates_by_days"),
    )


def _store_source_row(store: MemoryStore, *, row: dict[str, Any]) -> None:
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
    result = store.store(
        namespace="global",
        kind="memory",
        title=str(row["title"]),
        content="\n".join(lines),
        tags=[
            f"kind:{record_type}",
            str(row["domain"]),
            str(row["topic"]),
            "project:mem-store",
            *(row.get("extra_tags") or []),
        ],
        session_id=None if row.get("session_id") is None else str(row["session_id"]),
        actor="bridge-reflex",
        correlation_id=None if row.get("correlation_id") is None else str(row["correlation_id"]),
        source_app="agent-memory-bridge-reflex",
    )
    if row.get("created_at"):
        with store._connect() as conn:
            conn.execute(
                "UPDATE memories SET created_at = ? WHERE id = ?",
                (str(row["created_at"]), result["id"]),
            )
            conn.commit()


def _evaluate_replay_expectations(*, actual: dict[str, Any], expected: dict[str, Any]) -> tuple[bool, list[str]]:
    failures: list[str] = []
    exact_keys = (
        "first_candidate_window",
        "first_belief_window",
        "first_red_flag_window",
        "first_out_of_filter_window",
        "stopped_on_red_flag",
        "final_belief_count",
        "final_candidate_count",
    )
    for key in exact_keys:
        if key in expected and actual.get(key) != expected.get(key):
            failures.append(f"{key}: expected {expected.get(key)!r}, got {actual.get(key)!r}")
    if "min_final_candidate_count" in expected:
        minimum = int(expected["min_final_candidate_count"])
        if int(actual.get("final_candidate_count") or 0) < minimum:
            failures.append(
                f"min_final_candidate_count: expected >= {minimum}, got {actual.get('final_candidate_count')!r}"
            )
    for key, actual_key in (
        ("required_red_flags", "final_red_flags"),
        ("required_candidate_statuses", "final_candidate_statuses"),
        ("required_actions_applied", "all_actions_applied"),
        ("required_out_of_filter_domains", "out_of_filter_domains"),
    ):
        for value in expected.get(key) or []:
            if value not in actual.get(actual_key, []):
                failures.append(f"{actual_key}: missing {value!r}")
    return not failures, failures


def _resolve_relative_path(manifest_path: Path, value: Any, *, fallback: Path) -> Path:
    if value is None:
        return fallback
    candidate = Path(str(value))
    if candidate.is_absolute():
        return candidate
    return manifest_path.parent / candidate


def _summarize_group(results: list[dict[str, Any]]) -> dict[str, Any]:
    case_count = len(results)
    pass_count = sum(1 for result in results if result["match"])
    kind_counts = Counter(result["kind"] for result in results)
    return {
        "case_count": case_count,
        "pass_count": pass_count,
        "pass_rate": _rate(pass_count, case_count),
        "kind_counts": dict(sorted(kind_counts.items())),
    }


def _rate(count: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round(count / total, 3)


def _format_percent(value: float) -> str:
    return f"{value * 100:.1f}%"
