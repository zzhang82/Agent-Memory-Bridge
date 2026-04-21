from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .belief_observation import BeliefObservationConfig, observe_belief_ladder
from .consolidation import ConsolidationConfig, ConsolidationEngine
from .paths import resolve_consolidation_actor, resolve_profile_namespace
from .storage import MemoryStore


@dataclass(frozen=True, slots=True)
class BeliefReplayConfig:
    source_namespace: str = "global"
    target_namespace: str = "global"
    source_limit: int = 200
    window_size: int = 20
    since_days: int | None = None
    domain_tags: tuple[str, ...] = ()
    project_tags: tuple[str, ...] = ()
    session_ids: tuple[str, ...] = ()
    session_null_only: bool = False
    null_session_uplift_mode: str = "none"
    correlation_ids: tuple[str, ...] = ()
    actor: str = "bridge-consolidation"
    belief_to_domain_note_ratio_red_flag: float = 0.5
    candidate_to_belief_rate_red_flag: float = 0.8
    max_candidate_count_red_flag: int = 25
    stop_on_red_flag: bool = True
    top_n: int = 5
    age_candidates_after_windows: tuple[int, ...] = ()
    age_candidates_by_days: int | None = None


def build_default_belief_replay_config() -> BeliefReplayConfig:
    return BeliefReplayConfig(
        source_namespace=resolve_profile_namespace(),
        target_namespace=resolve_profile_namespace(),
        actor=resolve_consolidation_actor(),
    )


def run_belief_replay(
    *,
    source_store: MemoryStore,
    config: BeliefReplayConfig,
) -> dict[str, Any]:
    _validate_belief_replay_config(config)
    source_rows = _load_source_rows(source_store, config=config)
    runtime_dir = Path(tempfile.mkdtemp(prefix="agent-memory-bridge-belief-replay-"))
    try:
        replay_store = MemoryStore(runtime_dir / "belief-replay.db", log_dir=runtime_dir / "logs")
        engine = ConsolidationEngine(
            store=replay_store,
            config=ConsolidationConfig(
                state_path=runtime_dir / "consolidation-state.json",
                target_namespace=config.target_namespace,
                actor=config.actor,
            ),
        )

        windows: list[dict[str, Any]] = []
        first_candidate_window: int | None = None
        first_belief_window: int | None = None
        first_red_flag_window: int | None = None
        first_out_of_filter_window: int | None = None
        rows_replayed = 0

        for window_index, batch in enumerate(_chunked(source_rows, config.window_size), start=1):
            for row in batch:
                _copy_source_row(
                    replay_store,
                    row=row,
                    namespace=config.target_namespace,
                    session_id=_uplifted_session_id(row=row, config=config),
                )
            rows_replayed += len(batch)
            consolidation_result = engine.run_once()
            observation = observe_belief_ladder(
                replay_store,
                BeliefObservationConfig(
                    namespace=config.target_namespace,
                    actor=config.actor,
                    top_n=config.top_n,
                ),
            )
            red_flags = _detect_red_flags(observation, config=config)
            out_of_filter_domains = _detect_out_of_filter_domains(observation, config=config)
            actions_applied: list[str] = []
            window_report = {
                "window_index": window_index,
                "rows_replayed": rows_replayed,
                "batch_size": len(batch),
                "latest_source_id": batch[-1]["id"] if batch else None,
                "consolidation_processed_count": consolidation_result["processed_count"],
                "summary": observation["summary"],
                "leaderboards": observation["leaderboards"],
                "cohorts": observation["cohorts"],
                "out_of_filter_domains": out_of_filter_domains,
                "actions_applied": actions_applied,
                "red_flags": red_flags,
            }
            windows.append(window_report)

            if first_candidate_window is None and observation["summary"]["belief_candidate_count"] > 0:
                first_candidate_window = window_index
            if first_belief_window is None and observation["summary"]["belief_count"] > 0:
                first_belief_window = window_index
            if first_out_of_filter_window is None and out_of_filter_domains:
                first_out_of_filter_window = window_index
            if first_red_flag_window is None and red_flags:
                first_red_flag_window = window_index
                if config.stop_on_red_flag:
                    break
            if (
                config.age_candidates_after_windows
                and config.age_candidates_by_days is not None
                and window_index in config.age_candidates_after_windows
            ):
                _age_belief_candidates(
                    replay_store,
                    namespace=config.target_namespace,
                    actor=config.actor,
                    age_days=config.age_candidates_by_days,
                )
                actions_applied.append(f"aged-candidates:{config.age_candidates_by_days}d")

        return {
            "summary": {
                "source_row_count": len(source_rows),
                "window_count": len(windows),
                "first_candidate_window": first_candidate_window,
                "first_belief_window": first_belief_window,
                "first_red_flag_window": first_red_flag_window,
                "first_out_of_filter_window": first_out_of_filter_window,
                "stopped_on_red_flag": first_red_flag_window is not None and config.stop_on_red_flag,
            },
            "config": {
                "source_namespace": config.source_namespace,
                "target_namespace": config.target_namespace,
                "source_limit": config.source_limit,
                "window_size": config.window_size,
                "since_days": config.since_days,
                "domain_tags": list(config.domain_tags),
                "project_tags": list(config.project_tags),
                "session_ids": list(config.session_ids),
                "session_null_only": config.session_null_only,
                "null_session_uplift_mode": config.null_session_uplift_mode,
                "correlation_ids": list(config.correlation_ids),
                "belief_to_domain_note_ratio_red_flag": config.belief_to_domain_note_ratio_red_flag,
                "candidate_to_belief_rate_red_flag": config.candidate_to_belief_rate_red_flag,
                "max_candidate_count_red_flag": config.max_candidate_count_red_flag,
                "stop_on_red_flag": config.stop_on_red_flag,
                "top_n": config.top_n,
                "age_candidates_after_windows": list(config.age_candidates_after_windows),
                "age_candidates_by_days": config.age_candidates_by_days,
            },
            "windows": windows,
        }
    finally:
        shutil.rmtree(runtime_dir, ignore_errors=True)


def diff_belief_replay_reports(
    *,
    baseline_report: dict[str, Any],
    variant_report: dict[str, Any],
) -> dict[str, Any]:
    max_window_count = max(
        len(baseline_report["windows"]),
        len(variant_report["windows"]),
    )
    metric_keys = (
        "belief_candidate_count",
        "belief_count",
        "domain_note_count",
        "blocked_by_contradiction",
        "blocked_by_staleness",
        "blocked_by_low_support",
        "blocked_by_stability",
    )
    windows: list[dict[str, Any]] = []
    for window_index in range(1, max_window_count + 1):
        baseline_window = _window_or_none(baseline_report, window_index)
        variant_window = _window_or_none(variant_report, window_index)
        baseline_summary = baseline_window["summary"] if baseline_window else {}
        variant_summary = variant_window["summary"] if variant_window else {}
        delta: dict[str, Any] = {}
        for key in metric_keys:
            baseline_value = baseline_summary.get(key)
            variant_value = variant_summary.get(key)
            if isinstance(baseline_value, (int, float)) and isinstance(variant_value, (int, float)):
                delta[key] = variant_value - baseline_value
            else:
                delta[key] = None
        delta["contradiction_reason_counts"] = _diff_count_maps(
            baseline_summary.get("contradiction_reason_counts"),
            variant_summary.get("contradiction_reason_counts"),
        )
        windows.append(
            {
                "window_index": window_index,
                "baseline": _render_window_diff_side(baseline_window),
                "variant": _render_window_diff_side(variant_window),
                "delta": delta,
            }
        )

    return {
        "summary": {
            "baseline_first_candidate_window": baseline_report["summary"]["first_candidate_window"],
            "variant_first_candidate_window": variant_report["summary"]["first_candidate_window"],
            "baseline_first_belief_window": baseline_report["summary"]["first_belief_window"],
            "variant_first_belief_window": variant_report["summary"]["first_belief_window"],
            "baseline_first_red_flag_window": baseline_report["summary"]["first_red_flag_window"],
            "variant_first_red_flag_window": variant_report["summary"]["first_red_flag_window"],
            "baseline_first_out_of_filter_window": baseline_report["summary"]["first_out_of_filter_window"],
            "variant_first_out_of_filter_window": variant_report["summary"]["first_out_of_filter_window"],
            "belief_emergence": _compare_window_transition(
                baseline_report["summary"]["first_belief_window"],
                variant_report["summary"]["first_belief_window"],
            ),
            "candidate_emergence": _compare_window_transition(
                baseline_report["summary"]["first_candidate_window"],
                variant_report["summary"]["first_candidate_window"],
            ),
            "red_flag_emergence": _compare_window_transition(
                baseline_report["summary"]["first_red_flag_window"],
                variant_report["summary"]["first_red_flag_window"],
            ),
            "out_of_filter_emergence": _compare_window_transition(
                baseline_report["summary"]["first_out_of_filter_window"],
                variant_report["summary"]["first_out_of_filter_window"],
            ),
        },
        "windows": windows,
    }


def build_source_store(db_path: Path | None = None) -> MemoryStore:
    if db_path is None:
        return MemoryStore.from_env()
    return MemoryStore(db_path=db_path, log_dir=db_path.parent / "logs")


def _load_source_rows(source_store: MemoryStore, *, config: BeliefReplayConfig) -> list[sqlite3.Row]:
    params: list[Any] = [config.source_namespace, '%"kind:learn"%', '%"kind:gotcha"%']
    sql = """
        SELECT
            id,
            namespace,
            title,
            content,
            tags_json,
            session_id,
            actor,
            correlation_id,
            source_app,
            source_client,
            source_model,
            client_session_id,
            client_workspace,
            client_transport,
            created_at
        FROM memories
        WHERE namespace = ?
          AND (tags_json LIKE ? OR tags_json LIKE ?)
    """
    if config.since_days is not None:
        cutoff = (datetime.now(UTC) - timedelta(days=config.since_days)).isoformat()
        sql += " AND created_at >= ?"
        params.append(cutoff)
    for domain_tag in config.domain_tags:
        sql += " AND tags_json LIKE ?"
        params.append(f'%"{domain_tag}"%')
    for project_tag in config.project_tags:
        sql += " AND tags_json LIKE ?"
        params.append(f'%"{project_tag}"%')
    if config.session_null_only:
        sql += " AND session_id IS NULL"
    if config.session_ids:
        placeholders = ", ".join("?" for _ in config.session_ids)
        sql += f" AND session_id IN ({placeholders})"
        params.extend(config.session_ids)
    if config.correlation_ids:
        placeholders = ", ".join("?" for _ in config.correlation_ids)
        sql += f" AND correlation_id IN ({placeholders})"
        params.extend(config.correlation_ids)
    sql += " ORDER BY created_at ASC LIMIT ?"
    params.append(config.source_limit)
    with source_store._connect() as conn:
        return conn.execute(sql, params).fetchall()


def _copy_source_row(
    store: MemoryStore,
    *,
    row: sqlite3.Row,
    namespace: str,
    session_id: str | None,
) -> None:
    store.store(
        namespace=namespace,
        kind="memory",
        title=row["title"],
        content=row["content"],
        tags=json.loads(row["tags_json"] or "[]"),
        session_id=session_id,
        actor=row["actor"],
        correlation_id=row["correlation_id"],
        source_app=row["source_app"],
        source_client=row["source_client"],
        source_model=row["source_model"],
        client_session_id=row["client_session_id"],
        client_workspace=row["client_workspace"],
        client_transport=row["client_transport"],
    )


def _validate_belief_replay_config(config: BeliefReplayConfig) -> None:
    if config.session_null_only and config.session_ids:
        raise ValueError("session_null_only cannot be combined with explicit session_ids")
    allowed_modes = {"none", "by_day", "by_row", "by_correlation", "by_correlation_or_day"}
    if config.null_session_uplift_mode not in allowed_modes:
        raise ValueError(f"null_session_uplift_mode must be one of {sorted(allowed_modes)}")


def _detect_red_flags(report: dict[str, Any], *, config: BeliefReplayConfig) -> list[str]:
    summary = report["summary"]
    red_flags: list[str] = []
    if summary["belief_candidate_count"] > config.max_candidate_count_red_flag:
        red_flags.append("candidate-count")
    if summary["belief_to_domain_note_ratio"] >= config.belief_to_domain_note_ratio_red_flag:
        red_flags.append("belief-to-domain-note-ratio")
    if summary["belief_count"] > 0 and summary["candidate_to_belief_rate"] >= config.candidate_to_belief_rate_red_flag:
        red_flags.append("candidate-to-belief-rate")
    return red_flags


def _detect_out_of_filter_domains(report: dict[str, Any], *, config: BeliefReplayConfig) -> list[str]:
    if not config.domain_tags:
        return []
    allowed_domains = set(config.domain_tags)
    observed_domains = {
        str(row["domain"])
        for row in report["cohorts"]["by_domain"]
        if (
            row.get("domain_note_count", 0) > 0
            or row.get("candidate_count", 0) > 0
            or row.get("belief_count", 0) > 0
        )
    }
    return sorted(domain for domain in observed_domains if domain not in allowed_domains)


def _age_belief_candidates(store: MemoryStore, *, namespace: str, actor: str, age_days: int) -> None:
    aged_created_at = (datetime.now(UTC) - timedelta(days=age_days)).isoformat()
    with store._connect() as conn:
        conn.execute(
            """
            UPDATE memories
            SET created_at = ?
            WHERE namespace = ?
              AND actor = ?
              AND tags_json LIKE ?
            """,
            (
                aged_created_at,
                namespace,
                actor,
                '%"kind:belief-candidate"%',
            ),
        )
        conn.commit()


def _window_or_none(report: dict[str, Any], window_index: int) -> dict[str, Any] | None:
    if window_index <= 0 or window_index > len(report["windows"]):
        return None
    return report["windows"][window_index - 1]


def _render_window_diff_side(window: dict[str, Any] | None) -> dict[str, Any] | None:
    if window is None:
        return None
    return {
        "summary": window["summary"],
        "red_flags": window["red_flags"],
        "actions_applied": window["actions_applied"],
        "out_of_filter_domains": window["out_of_filter_domains"],
    }


def _compare_window_transition(baseline_window: int | None, variant_window: int | None) -> str:
    if baseline_window is None and variant_window is None:
        return "absent-both"
    if baseline_window is None and variant_window is not None:
        return "new-in-variant"
    if baseline_window is not None and variant_window is None:
        return "suppressed-in-variant"
    assert baseline_window is not None and variant_window is not None
    if variant_window > baseline_window:
        return "delayed-in-variant"
    if variant_window < baseline_window:
        return "earlier-in-variant"
    return "same-window"


def _diff_count_maps(
    baseline_counts: dict[str, Any] | None,
    variant_counts: dict[str, Any] | None,
) -> dict[str, int]:
    baseline = {
        str(key): int(value)
        for key, value in (baseline_counts or {}).items()
        if isinstance(value, (int, float))
    }
    variant = {
        str(key): int(value)
        for key, value in (variant_counts or {}).items()
        if isinstance(value, (int, float))
    }
    keys = sorted(set(baseline) | set(variant))
    return {
        key: variant.get(key, 0) - baseline.get(key, 0)
        for key in keys
        if variant.get(key, 0) - baseline.get(key, 0) != 0
    }


def _uplifted_session_id(*, row: sqlite3.Row, config: BeliefReplayConfig) -> str | None:
    original = row["session_id"]
    if original:
        return str(original)
    mode = config.null_session_uplift_mode
    if mode == "none":
        return None
    row_id = str(row["id"])
    if mode == "by_row":
        return f"uplift:row:{row_id}"
    created_at = str(row["created_at"])
    if mode == "by_day":
        return f"uplift:day:{created_at[:10]}"
    correlation_id = str(row["correlation_id"] or "").strip()
    if mode == "by_correlation" and correlation_id:
        return f"uplift:correlation:{correlation_id}"
    if mode == "by_correlation_or_day":
        if correlation_id:
            return f"uplift:correlation:{correlation_id}"
        return f"uplift:day:{created_at[:10]}"
    return None


def _chunked(rows: list[sqlite3.Row], size: int) -> list[list[sqlite3.Row]]:
    if size <= 0:
        raise ValueError("window size must be greater than 0")
    return [rows[index : index + size] for index in range(0, len(rows), size)]
