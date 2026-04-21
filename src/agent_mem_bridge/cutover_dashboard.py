from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .belief_observation import (
    BeliefObservationConfig,
    build_default_belief_observation_config,
    observe_belief_ladder,
)
from .profile_assembly import build_startup_recall_plan
from .profile_bundle import load_profile_bundle, startup_records
from .profile_migration import compare_profile_migration_with_mode
from .rollback_cutover import build_rollback_preflight, find_latest_live_cutover_manifest
from .storage import MemoryStore


@dataclass(frozen=True, slots=True)
class StartupCase:
    case_id: str
    query: str
    project_namespace: str | None = None
    global_namespace: str | None = None
    required_bundle_labels: tuple[str, ...] = ()
    min_new_non_reference_hits: int = 1
    require_project_hit: bool = False
    min_reference_hits: int = 0
    max_reference_hits: int | None = None
    notes: str = ""


@dataclass(frozen=True, slots=True)
class CutoverDashboardConfig:
    source_root: Path
    global_namespace: str
    project_namespace: str | None = None
    startup_cases: tuple[StartupCase, ...] = ()
    bundle_path: Path | None = None
    live_manifest_path: Path | None = None
    cutover_manifest_path: Path | None = None
    startup_limit: int = 5
    max_belief_to_domain_note_ratio: float = 0.25
    belief_config: BeliefObservationConfig | None = None


def load_startup_cases(path: Path) -> tuple[StartupCase, ...]:
    payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    raw_cases: Any
    if isinstance(payload, dict):
        raw_cases = payload.get("cases", [])
    else:
        raw_cases = payload
    if not isinstance(raw_cases, list):
        raise ValueError("Startup case file must be a list or an object with a 'cases' list.")

    cases: list[StartupCase] = []
    for index, entry in enumerate(raw_cases, start=1):
        if not isinstance(entry, dict):
            raise ValueError("Each startup case must be an object.")
        case_id = _clean_str(entry.get("id")) or f"case-{index}"
        query = _require_str(entry, "query")
        required_bundle_labels = tuple(_clean_list(entry.get("required_bundle_labels", [])))
        min_new_non_reference_hits = _coerce_non_negative_int(entry.get("min_new_non_reference_hits", 1))
        min_reference_hits = _coerce_non_negative_int(entry.get("min_reference_hits", 0))
        max_reference_hits_raw = entry.get("max_reference_hits")
        max_reference_hits = None if max_reference_hits_raw is None else _coerce_non_negative_int(max_reference_hits_raw)
        cases.append(
            StartupCase(
                case_id=case_id,
                query=query,
                project_namespace=_clean_str(entry.get("project_namespace")) or None,
                global_namespace=_clean_str(entry.get("global_namespace")) or None,
                required_bundle_labels=required_bundle_labels,
                min_new_non_reference_hits=min_new_non_reference_hits,
                require_project_hit=bool(entry.get("require_project_hit", False)),
                min_reference_hits=min_reference_hits,
                max_reference_hits=max_reference_hits,
                notes=_clean_str(entry.get("notes")),
            )
        )
    return tuple(cases)


def build_cutover_dashboard(store: MemoryStore, config: CutoverDashboardConfig) -> dict[str, Any]:
    structure = _build_structure_section(store, config)
    startup = _build_startup_section(store, config)
    belief = _build_belief_section(store, config)
    rollback = _build_rollback_section(config)

    gate_statuses = {
        "structure": structure["status"],
        "startup": startup["status"],
        "belief": belief["status"],
        "rollback": rollback["status"],
    }
    failing_gates = [name for name, status in gate_statuses.items() if status == "fail"]
    pending_gates = [name for name, status in gate_statuses.items() if status == "pending"]
    overall_status = "go"
    if failing_gates:
        overall_status = "no-go"
    elif pending_gates:
        overall_status = "hold"

    overall_reason_codes = [
        *structure.get("reason_codes", []),
        *startup.get("reason_codes", []),
        *belief.get("reason_codes", []),
        *rollback.get("reason_codes", []),
    ]
    overall_warning_codes = [
        *structure.get("warning_codes", []),
        *startup.get("warning_codes", []),
        *belief.get("warning_codes", []),
        *rollback.get("warning_codes", []),
    ]

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "source_root": str(config.source_root.resolve()),
        "global_namespace": config.global_namespace,
        "project_namespace": config.project_namespace,
        "structure": structure,
        "startup": startup,
        "belief": belief,
        "rollback": rollback,
        "overall": {
            "status": overall_status,
            "failing_gates": failing_gates,
            "pending_gates": pending_gates,
            "reason_codes": overall_reason_codes,
            "warning_codes": overall_warning_codes,
        },
    }


def render_cutover_dashboard_text(report: dict[str, Any]) -> str:
    overall = report["overall"]
    structure = report["structure"]
    startup = report["startup"]
    belief = report["belief"]
    rollback = report["rollback"]

    lines = [
        "Cutover Dashboard",
        "",
        f"overall_status: {overall['status']}",
        f"failing_gates: {', '.join(overall['failing_gates']) or '(none)'}",
        f"pending_gates: {', '.join(overall['pending_gates']) or '(none)'}",
        f"overall_reason_codes: {json.dumps(overall['reason_codes'])}",
        f"overall_warning_codes: {json.dumps(overall['warning_codes'])}",
        "",
        f"Structure [{structure['status']}]",
        f"compare_mode: {structure['compare_mode']}",
        f"missing_count: {structure['comparison']['missing_count']}",
        f"content_mismatch_count: {structure['comparison']['content_mismatch_count']}",
        f"namespace_mismatch_count: {structure['comparison']['namespace_mismatch_count']}",
        f"required_profile_record_counts: {json.dumps(structure['required_profile_record_counts'], sort_keys=True)}",
        f"reason_codes: {json.dumps(structure['reason_codes'])}",
        f"warning_codes: {json.dumps(structure['warning_codes'])}",
    ]
    if structure.get("bundle_file") is not None:
        bundle_file = structure["bundle_file"]
        lines.append(f"bundle_file_matched_startup_records: {bundle_file['matched_startup_record_count']}/{bundle_file['startup_record_count']}")
        if bundle_file["missing_startup_records"]:
            lines.append(f"bundle_file_missing: {json.dumps(bundle_file['missing_startup_records'])}")

    lines.extend(
        [
            "",
            f"Startup [{startup['status']}]",
            f"case_count: {startup['summary']['case_count']}",
            f"pass_count: {startup['summary']['pass_count']}",
            f"fail_count: {startup['summary']['fail_count']}",
            f"bundle_signal_case_count: {startup['summary']['bundle_signal_case_count']}",
            f"query_bundle_signal_case_count: {startup['summary']['query_bundle_signal_case_count']}",
            f"fallback_only_bundle_case_count: {startup['summary']['fallback_only_bundle_case_count']}",
            f"reference_signal_case_count: {startup['summary']['reference_signal_case_count']}",
            f"total_profile_bundle_hits: {startup['summary']['total_profile_bundle_hits']}",
            f"total_startup_loaded_bundle_hits: {startup['summary']['total_startup_loaded_bundle_hits']}",
            f"total_reference_hits: {startup['summary']['total_reference_hits']}",
            f"legacy_reference_signal_case_count: {startup['summary']['legacy_reference_signal_case_count']}",
            f"legacy_total_reference_hits: {startup['summary']['legacy_total_reference_hits']}",
            f"authority_pass_count: {startup['summary']['authority_pass_count']}",
            f"authority_fail_count: {startup['summary']['authority_fail_count']}",
            f"fallback_free_case_count: {startup['summary']['fallback_free_case_count']}",
            f"fallback_needed_case_count: {startup['summary']['fallback_needed_case_count']}",
            f"reason_codes: {json.dumps(startup['reason_codes'])}",
            f"warning_codes: {json.dumps(startup['warning_codes'])}",
        ]
    )
    if startup["cases"]:
        lines.append("startup_cases:")
        for case in startup["cases"]:
            lines.append(
                f"- {case['id']}: {case['status']} "
                f"(query_bundle={case['query_bundle_hit_labels']}, startup_loaded={case['startup_loaded_bundle_labels']}, "
                f"new_non_reference_hits={case['new_non_reference_hit_count']}, "
                f"reference_hits={case['reference_hit_count']}, legacy_reference_hits={case['legacy_reference_hit_count']})"
            )

    lines.extend(
        [
            "",
            f"Belief [{belief['status']}]",
            f"belief_candidate_count: {belief['summary']['belief_candidate_count']}",
            f"belief_count: {belief['summary']['belief_count']}",
            f"domain_note_count: {belief['summary']['domain_note_count']}",
            f"belief_to_domain_note_ratio: {_format_percent(belief['summary']['belief_to_domain_note_ratio'])}",
            f"startup_belief_default_loaded: {belief['summary']['startup_belief_default_loaded']}",
            f"reason_codes: {json.dumps(belief['reason_codes'])}",
            f"warning_codes: {json.dumps(belief['warning_codes'])}",
            "",
            f"Rollback [{rollback['status']}]",
            f"mode: {rollback['mode']}",
            f"reason_codes: {json.dumps(rollback['reason_codes'])}",
            f"warning_codes: {json.dumps(rollback['warning_codes'])}",
        ]
    )
    if rollback["mode"] == "manifest":
        lines.append(f"overwrite_candidate_count: {rollback['preflight']['overwrite_candidate_count']}")
        lines.append(f"newer_live_conflict_count: {rollback['preflight']['newer_live_conflict_count']}")
    else:
        lines.append(f"note: {rollback['note']}")

    return "\n".join(lines)


def _build_structure_section(store: MemoryStore, config: CutoverDashboardConfig) -> dict[str, Any]:
    compare_mode = "live"
    try:
        comparison = compare_profile_migration_with_mode(
            store,
            config.source_root,
            mode="live",
            live_manifest_path=config.live_manifest_path,
        )
    except FileNotFoundError:
        compare_mode = "full-fallback"
        comparison = compare_profile_migration_with_mode(
            store,
            config.source_root,
            mode="full",
        )

    required_profile_record_counts = {
        record_type: _count_tagged_rows(store, config.global_namespace, f"record:{record_type}")
        for record_type in ("core-policy", "persona", "soul")
    }
    required_profile_records_present = all(count > 0 for count in required_profile_record_counts.values())

    bundle_file_report = None
    bundle_file_ok = True
    if config.bundle_path is not None:
        bundle_file_report = _compare_bundle_file(store, config.bundle_path)
        bundle_file_ok = bundle_file_report["matched_startup_record_count"] == bundle_file_report["startup_record_count"]

    checks = {
        "compare_clean": (
            comparison["missing_count"] == 0
            and comparison["content_mismatch_count"] == 0
            and comparison["namespace_mismatch_count"] == 0
        ),
        "required_profile_records_present": required_profile_records_present,
        "bundle_file_matches": bundle_file_ok,
        "old_structure_preserved": comparison["expected_count"] > 0,
    }
    status = "pass" if all(checks.values()) else "fail"
    reason_codes: list[str] = []
    if checks["compare_clean"]:
        reason_codes.append(f"structure:{compare_mode}-compare-clean")
    else:
        reason_codes.append("structure:compare-mismatch")
    if checks["required_profile_records_present"]:
        reason_codes.append("structure:profile-records-present")
    else:
        reason_codes.append("structure:missing-profile-records")
    if checks["old_structure_preserved"]:
        reason_codes.append("structure:old-structure-preserved")
    else:
        reason_codes.append("structure:missing-source-structure")
    if config.bundle_path is not None:
        reason_codes.append("structure:bundle-file-matched" if bundle_file_ok else "structure:bundle-file-mismatch")

    return {
        "status": status,
        "compare_mode": compare_mode,
        "comparison": comparison,
        "required_profile_record_counts": required_profile_record_counts,
        "checks": checks,
        "reason_codes": reason_codes,
        "warning_codes": [],
        "bundle_file": bundle_file_report,
    }


def _build_startup_section(store: MemoryStore, config: CutoverDashboardConfig) -> dict[str, Any]:
    if not config.startup_cases:
        return {
            "status": "pending",
            "summary": {
                "case_count": 0,
                "pass_count": 0,
                "fail_count": 0,
                "bundle_signal_case_count": 0,
                "query_bundle_signal_case_count": 0,
                "fallback_only_bundle_case_count": 0,
                "reference_signal_case_count": 0,
                "total_profile_bundle_hits": 0,
                "total_startup_loaded_bundle_hits": 0,
                "total_reference_hits": 0,
                "legacy_reference_signal_case_count": 0,
                "legacy_total_reference_hits": 0,
                "authority_pass_count": 0,
                "authority_fail_count": 0,
                "fallback_free_case_count": 0,
                "fallback_needed_case_count": 0,
            },
            "cases": [],
            "note": "No curated startup case file was provided.",
            "reason_codes": ["startup:pending-cases"],
            "warning_codes": [],
        }

    evaluated_cases: list[dict[str, Any]] = []
    pass_count = 0
    bundle_signal_case_count = 0
    query_bundle_signal_case_count = 0
    fallback_only_bundle_case_count = 0
    reference_signal_case_count = 0
    total_profile_bundle_hits = 0
    total_startup_loaded_bundle_hits = 0
    total_reference_hits = 0
    legacy_reference_signal_case_count = 0
    legacy_total_reference_hits = 0
    authority_pass_count = 0

    for case in config.startup_cases:
        result = _evaluate_startup_case(
            store=store,
            case=case,
            default_global_namespace=config.global_namespace,
            default_project_namespace=config.project_namespace,
            limit=config.startup_limit,
        )
        evaluated_cases.append(result)
        if result["status"] == "pass":
            pass_count += 1
        if result["startup_loaded_bundle_labels"]:
            bundle_signal_case_count += 1
        if result["query_bundle_hit_labels"]:
            query_bundle_signal_case_count += 1
        if result["fallback_only_bundle"]:
            fallback_only_bundle_case_count += 1
        if result["reference_hit_count"] > 0:
            reference_signal_case_count += 1
        total_profile_bundle_hits += result["profile_bundle_hit_count"]
        total_startup_loaded_bundle_hits += result["startup_loaded_bundle_hit_count"]
        total_reference_hits += result["reference_hit_count"]
        if result["legacy_reference_hit_count"] > 0:
            legacy_reference_signal_case_count += 1
        legacy_total_reference_hits += result["legacy_reference_hit_count"]
        if result["authority_status"] == "pass":
            authority_pass_count += 1

    fail_count = len(evaluated_cases) - pass_count
    status = "pass" if fail_count == 0 else "fail"
    case_count = len(evaluated_cases)
    authority_fail_count = case_count - authority_pass_count
    fallback_needed_case_count = reference_signal_case_count
    fallback_free_case_count = case_count - fallback_needed_case_count
    reason_codes = [
        "startup:all-cases-pass" if status == "pass" else "startup:case-failure",
        "startup:authority-strong" if authority_fail_count == 0 else "startup:authority-gap",
        "startup:fallback-free" if fallback_needed_case_count == 0 else "startup:fallback-still-needed",
    ]
    warning_codes: list[str] = []
    reference_dependency_case_ratio = 0.0
    if case_count > 0:
        reference_dependency_case_ratio = max(
            reference_signal_case_count / case_count,
            legacy_reference_signal_case_count / case_count,
        )
    if case_count > 0 and reference_dependency_case_ratio >= 0.5:
        warning_codes.append("startup:reference-dependency-high")
    reference_hit_ratio = 0.0
    bundle_hit_denominator = total_profile_bundle_hits or total_startup_loaded_bundle_hits
    if bundle_hit_denominator > 0:
        reference_hit_ratio = max(
            (total_reference_hits / bundle_hit_denominator) if total_reference_hits > 0 else 0.0,
            (legacy_total_reference_hits / bundle_hit_denominator) if legacy_total_reference_hits > 0 else 0.0,
        )
    if bundle_hit_denominator > 0 and reference_hit_ratio >= 0.75:
        warning_codes.append("startup:reference-hit-ratio-high")
    fallback_only_bundle_ratio = 0.0
    if case_count > 0:
        fallback_only_bundle_ratio = fallback_only_bundle_case_count / case_count
    if fallback_only_bundle_ratio >= 0.5:
        warning_codes.append("startup:bundle-fallback-only-high")
    return {
        "status": status,
        "summary": {
            "case_count": case_count,
            "pass_count": pass_count,
            "fail_count": fail_count,
            "bundle_signal_case_count": bundle_signal_case_count,
            "query_bundle_signal_case_count": query_bundle_signal_case_count,
            "fallback_only_bundle_case_count": fallback_only_bundle_case_count,
            "reference_signal_case_count": reference_signal_case_count,
            "total_profile_bundle_hits": total_profile_bundle_hits,
            "total_startup_loaded_bundle_hits": total_startup_loaded_bundle_hits,
            "total_reference_hits": total_reference_hits,
            "legacy_reference_signal_case_count": legacy_reference_signal_case_count,
            "legacy_total_reference_hits": legacy_total_reference_hits,
            "authority_pass_count": authority_pass_count,
            "authority_fail_count": authority_fail_count,
            "fallback_free_case_count": fallback_free_case_count,
            "fallback_needed_case_count": fallback_needed_case_count,
        },
        "cases": evaluated_cases,
        "reason_codes": reason_codes,
        "warning_codes": warning_codes,
    }


def _build_belief_section(store: MemoryStore, config: CutoverDashboardConfig) -> dict[str, Any]:
    belief_config = config.belief_config or build_default_belief_observation_config()
    report = observe_belief_ladder(store, belief_config)
    summary = report["summary"]
    checks = {
        "startup_belief_default_unloaded": summary["startup_belief_default_loaded"] is False,
        "belief_ratio_ok": summary["belief_to_domain_note_ratio"] <= config.max_belief_to_domain_note_ratio,
    }
    status = "pass" if all(checks.values()) else "fail"
    reason_codes = [
        "belief:startup-unloaded" if checks["startup_belief_default_unloaded"] else "belief:startup-loaded",
        "belief:ratio-ok" if checks["belief_ratio_ok"] else "belief:ratio-too-high",
    ]
    return {
        "status": status,
        "summary": summary,
        "checks": checks,
        "reason_codes": reason_codes,
        "warning_codes": [],
        "report": report,
    }


def _build_rollback_section(config: CutoverDashboardConfig) -> dict[str, Any]:
    manifest_path = config.cutover_manifest_path
    if manifest_path is None:
        manifest_path = find_latest_live_cutover_manifest(config.source_root)
    if manifest_path is None:
        return {
            "status": "pass",
            "mode": "pre-cutover",
            "note": "No live cutover manifest found; old source files remain available for fallback.",
            "reason_codes": ["rollback:pre-cutover"],
            "warning_codes": [],
        }

    preflight = build_rollback_preflight(manifest_path)
    checks = {
        "retired_files_present": preflight["missing_retired_count"] == 0,
        "no_newer_live_conflicts": preflight["newer_live_conflict_count"] == 0,
    }
    status = "pass" if all(checks.values()) else "fail"
    reason_codes = [
        "rollback:retired-files-present" if checks["retired_files_present"] else "rollback:missing-retired-files",
        "rollback:no-newer-live-conflicts" if checks["no_newer_live_conflicts"] else "rollback:newer-live-conflicts",
    ]
    return {
        "status": status,
        "mode": "manifest",
        "manifest_path": str(Path(manifest_path).resolve()),
        "preflight": preflight,
        "checks": checks,
        "reason_codes": reason_codes,
        "warning_codes": [],
    }


def _evaluate_startup_case(
    *,
    store: MemoryStore,
    case: StartupCase,
    default_global_namespace: str,
    default_project_namespace: str | None,
    limit: int,
) -> dict[str, Any]:
    global_namespace = case.global_namespace or default_global_namespace
    project_namespace = case.project_namespace or default_project_namespace or ""
    legacy_result = _legacy_reference_baseline(
        store=store,
        query=case.query,
        project_namespace=project_namespace,
        global_namespace=global_namespace,
        limit=limit,
    )
    startup_result = _startup_bundle_recall(
        store=store,
        query=case.query,
        project_namespace=project_namespace,
        global_namespace=global_namespace,
        limit=limit,
    )

    query_bundle_hit_labels = [
        layer["label"]
        for layer in startup_result["profile_bundle_hits"]
        if layer.get("query_items")
    ]
    startup_loaded_bundle_labels = [
        layer["label"]
        for layer in startup_result["profile_bundle_hits"]
        if layer.get("startup_items")
    ]
    profile_bundle_hit_count = sum(len(layer.get("query_items", [])) for layer in startup_result["profile_bundle_hits"])
    startup_loaded_bundle_hit_count = sum(
        len(layer.get("startup_items", [])) for layer in startup_result["profile_bundle_hits"]
    )
    new_non_reference_hit_count = profile_bundle_hit_count + len(startup_result["project_hits"])
    reference_hit_count = len(startup_result["reference_hits"])
    missing_bundle_labels = [label for label in case.required_bundle_labels if label not in startup_loaded_bundle_labels]

    checks = {
        "required_bundle_labels": len(missing_bundle_labels) == 0,
        "new_non_reference_hits": new_non_reference_hit_count >= case.min_new_non_reference_hits,
        "project_hit": (not case.require_project_hit) or len(startup_result["project_hits"]) > 0,
        "min_reference_hits": reference_hit_count >= case.min_reference_hits,
        "max_reference_hits": case.max_reference_hits is None or reference_hit_count <= case.max_reference_hits,
    }
    authority_checks = {
        "required_bundle_labels": checks["required_bundle_labels"],
        "new_non_reference_hits": checks["new_non_reference_hits"],
        "project_hit": checks["project_hit"],
    }
    authority_status = "pass" if all(authority_checks.values()) else "fail"
    status = "pass" if all(checks.values()) else "fail"

    return {
        "id": case.case_id,
        "query": case.query,
        "status": status,
        "authority_status": authority_status,
        "project_namespace": project_namespace,
        "global_namespace": global_namespace,
        "query_bundle_hit_labels": query_bundle_hit_labels,
        "startup_loaded_bundle_labels": startup_loaded_bundle_labels,
        "missing_bundle_labels": missing_bundle_labels,
        "profile_bundle_hit_count": profile_bundle_hit_count,
        "startup_loaded_bundle_hit_count": startup_loaded_bundle_hit_count,
        "fallback_only_bundle": bool(startup_loaded_bundle_labels) and not bool(query_bundle_hit_labels),
        "new_non_reference_hit_count": new_non_reference_hit_count,
        "reference_hit_count": reference_hit_count,
        "legacy_reference_hit_count": legacy_result["reference_hit_count"],
        "legacy_total_hit_count": legacy_result["total_hit_count"],
        "recommended_action": startup_result["recommended_action"],
        "checks": checks,
        "authority_checks": authority_checks,
        "notes": case.notes,
    }


def _startup_bundle_recall(
    *,
    store: MemoryStore,
    query: str,
    project_namespace: str,
    global_namespace: str,
    limit: int,
) -> dict[str, Any]:
    plan = build_startup_recall_plan(
        global_namespace=global_namespace,
        project_namespace=project_namespace or None,
        issue_mode=False,
    )
    profile_layers = []
    for layer in plan[:3]:
        query_items = store.recall(
            namespace=layer.namespace,
            query=query,
            tags_any=list(layer.tags_any),
            limit=limit,
        )["items"]
        startup_items = query_items
        match_mode = "query"
        if not startup_items:
            startup_items = store.recall(
                namespace=layer.namespace,
                tags_any=list(layer.tags_any),
                limit=limit,
            )["items"]
            match_mode = "fallback" if startup_items else "none"
        profile_layers.append(
            {
                "label": layer.label,
                "namespace": layer.namespace,
                "query_items": query_items,
                "startup_items": startup_items,
                "match_mode": match_mode,
            }
        )

    project_hits: list[dict[str, Any]] = []
    if project_namespace:
        project_hits = store.recall(namespace=project_namespace, query=query, limit=limit)["items"]

    reference_hits: list[dict[str, Any]] = []
    if not any(layer["startup_items"] for layer in profile_layers) and not project_hits:
        reference_hits = _legacy_reference_items(
            store=store,
            query=query,
            global_namespace=global_namespace,
            limit=limit,
        )

    recommended_action = "Use bundle-first startup recall."
    if reference_hits:
        recommended_action = "Bundle-first startup missed; fallback reference memory is carrying this case."

    return {
        "profile_bundle_hits": profile_layers,
        "project_hits": project_hits,
        "reference_hits": reference_hits,
        "recommended_action": recommended_action,
    }


def _legacy_reference_baseline(
    *,
    store: MemoryStore,
    query: str,
    project_namespace: str,
    global_namespace: str,
    limit: int,
) -> dict[str, int]:
    project_hits = []
    if project_namespace:
        project_hits = store.recall(namespace=project_namespace, query=query, limit=limit)["items"]
    reference_hits = _legacy_reference_items(
        store=store,
        query=query,
        global_namespace=global_namespace,
        limit=limit,
    )

    return {
        "project_hit_count": len(project_hits),
        "reference_hit_count": len(reference_hits),
        "total_hit_count": len(project_hits) + len(reference_hits),
    }


def _legacy_reference_items(
    *,
    store: MemoryStore,
    query: str,
    global_namespace: str,
    limit: int,
) -> list[dict[str, Any]]:
    global_hits = store.recall(namespace=global_namespace, query=query, limit=max(limit * 3, 10))["items"]
    reference_hits: list[dict[str, Any]] = []
    for item in global_hits:
        tags = item.get("tags", [])
        if any(tag in tags for tag in ("record:core-policy", "record:persona", "record:soul")):
            continue
        if any(tag in tags for tag in ("kind:learn", "kind:gotcha", "kind:domain-note")):
            continue
        reference_hits.append(item)
        if len(reference_hits) >= limit:
            break
    return reference_hits


def _recall_layer_hits(
    *,
    store: MemoryStore,
    namespace: str,
    query: str,
    tags_any: list[str] | None,
    limit: int,
) -> list[dict[str, Any]]:
    query_hits = store.recall(
        namespace=namespace,
        query=query,
        tags_any=tags_any,
        limit=limit,
    )["items"]
    if query_hits:
        return query_hits
    return store.recall(
        namespace=namespace,
        tags_any=tags_any,
        limit=limit,
    )["items"]


def _compare_bundle_file(store: MemoryStore, bundle_path: Path) -> dict[str, Any]:
    bundle = load_profile_bundle(bundle_path)
    startup = startup_records(bundle)
    missing_startup_records: list[dict[str, str]] = []
    matched_startup_record_count = 0

    for record in startup:
        tag = f"record:{record.record_type}"
        if _stored_record_exists(store, bundle.namespace, record.title, tag):
            matched_startup_record_count += 1
            continue
        missing_startup_records.append({"title": record.title, "record_type": record.record_type})

    return {
        "bundle_name": bundle.name,
        "bundle_namespace": bundle.namespace,
        "startup_record_count": len(startup),
        "matched_startup_record_count": matched_startup_record_count,
        "missing_startup_records": missing_startup_records,
    }


def _count_tagged_rows(store: MemoryStore, namespace: str, tag: str) -> int:
    with store._connect() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM memories
            WHERE namespace = ?
              AND tags_json LIKE ?
            """,
            (namespace, f'%"{tag}"%'),
        ).fetchone()
    return int(row["count"]) if row is not None else 0


def _stored_record_exists(store: MemoryStore, namespace: str, title: str, tag: str) -> bool:
    with store._connect() as conn:
        row = conn.execute(
            """
            SELECT 1
            FROM memories
            WHERE namespace = ?
              AND title = ?
              AND tags_json LIKE ?
            LIMIT 1
            """,
            (namespace, title, f'%"{tag}"%'),
        ).fetchone()
    return row is not None


def _clean_str(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def _clean_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise ValueError("Expected a list of strings.")
    items: list[str] = []
    for item in value:
        cleaned = _clean_str(item)
        if cleaned:
            items.append(cleaned)
    return items


def _require_str(mapping: dict[str, Any], key: str) -> str:
    value = _clean_str(mapping.get(key))
    if not value:
        raise ValueError(f"Missing required string value for {key}.")
    return value


def _coerce_non_negative_int(value: Any) -> int:
    number = int(value)
    if number < 0:
        raise ValueError("Expected a non-negative integer.")
    return number


def _format_percent(value: float) -> str:
    return f"{value * 100:.1f}%"
