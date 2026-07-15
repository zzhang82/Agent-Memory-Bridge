from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

import tomllib


README_NAMES = ("README.md", "README.zh-CN.md")
REQUIRED_BENCHMARK_KEYS = (
    "question_count",
    "memory_expected_top1_accuracy",
    "memory_mrr",
    "file_scan_expected_top1_accuracy",
    "file_scan_mrr",
)
REQUIRED_CALIBRATION_KEYS = (
    "sample_count",
    "classifier_exact_match_rate",
    "fallback_exact_match_rate",
    "classifier_better_count",
    "fallback_better_count",
    "classifier_filtered_low_confidence_count",
)
REQUIRED_PROCEDURE_KEYS = (
    "case_count",
    "flat_case_pass_rate",
    "governed_case_pass_rate",
    "flat_blocked_procedure_leak_rate",
    "governed_blocked_procedure_leak_rate",
    "governed_governance_field_completeness",
)
REQUIRED_SIGNAL_CONTENTION_KEYS = (
    "signal_contention_case_count",
    "signal_contention_case_pass_rate",
    "unique_active_claim_rate",
    "duplicate_active_claim_count",
    "active_reclaim_block_rate",
    "stale_ack_blocked_rate",
    "stale_reclaim_success_rate",
    "pending_under_pressure_claim_rate",
    "initial_hard_expiry_cap_rate",
)
REQUIRED_ADVERSARIAL_KEYS = (
    "adversarial_case_count",
    "adversarial_task_count",
    "adversarial_governed_task_pass_rate",
    "adversarial_governed_blocked_record_leak_rate",
)
REQUIRED_MEMORY_EVOLUTION_KEYS = (
    "memory_evolution_case_count",
    "memory_evolution_task_count",
    "memory_evolution_governed_task_pass_rate",
    "memory_evolution_governed_blocked_record_leak_rate",
    "memory_evolution_governed_disposition_reason_hit_rate",
)
REQUIRED_REVIEW_QUEUE_KEYS = (
    "review_queue_item_count",
    "review_queue_actionable_count",
    "review_queue_hidden_lane_count",
    "review_queue_writeback_plan_count",
    "review_queue_no_auto_mutation",
    "review_queue_public_mcp_surface_change",
    "review_queue_item_type_count",
)
REQUIRED_REVIEW_WORKFLOW_KEYS = (
    "review_workflow_source_queue_item_count",
    "review_workflow_item_count",
    "review_workflow_manual_step_count",
    "review_workflow_requires_human_count",
    "review_workflow_auto_write_count",
    "review_workflow_no_auto_writeback",
    "review_workflow_public_mcp_surface_change",
    "review_workflow_item_type_count",
)
REQUIRED_TASK_BRIEF_KEYS = (
    "task_brief_used_count",
    "task_brief_ignored_count",
    "task_brief_needs_review_count",
    "task_brief_review_queue_item_count",
    "task_brief_active_signal_count",
    "task_brief_no_auto_writeback",
    "task_brief_public_mcp_surface_change",
    "task_brief_needs_review_source_type_count",
)
REQUIRED_V019_ADOPTION_PROOF_KEYS = (
    "v019_case_count",
    "v019_pass_count",
    "v019_pass_rate",
    "v019_retrieval_case_count",
    "v019_retrieval_pass_rate",
    "v019_task_brief_case_count",
    "v019_task_brief_pass_rate",
    "v019_first_run_adoption_case_count",
    "v019_first_run_adoption_pass_rate",
    "v019_public_mcp_tool_count",
    "v019_public_mcp_surface_change",
    "v019_client_config_write_count",
    "v019_durable_writeback_count",
    "v019_amh_required",
    "v019_native_memory_comparison_required",
)
REQUIRED_V020_CLEAN_ROOM_PROOF_KEYS = (
    "v020_case_count",
    "v020_pass_count",
    "v020_pass_rate",
    "v020_import_sanity_pass",
    "v020_stdio_round_trip_pass",
    "v020_first_run_pass",
    "v020_task_brief_pass",
    "v020_public_mcp_tool_count",
    "v020_public_mcp_surface_change",
    "v020_client_config_write_count",
    "v020_explicit_demo_memory_write_count",
    "v020_explicit_demo_signal_write_count",
    "v020_non_demo_durable_writeback_count",
    "v020_amh_required",
    "v020_external_vendor_adoption_claim",
)
REQUIRED_V021_GOVERNED_CHANGE_KEYS = (
    "v021_case_count",
    "v021_category_count",
    "v021_flat_baseline_hazards",
    "v021_governed_case_pass_count",
    "v021_governed_failures",
    "v021_governed_checkpoint_passes",
    "v021_governed_checkpoint_result_count",
    "v021_useful_current_retention_pass",
    "v021_suppress_all_can_pass",
    "v021_public_mcp_tool_count",
    "v021_public_mcp_surface_change",
    "v021_auto_writeback_count",
    "v021_config_write_count",
    "v021_durable_live_writeback_count",
)
V021_RELEASE = "0.21.0"
V021_GOVERNED_CHANGE_REPORT = "latest-v0.21-governed-change-report.json"
V021_PATCH_PATTERN = re.compile(r"0\.21\.\d+")
SEMVER_PATTERN = re.compile(r"(?<![A-Za-z0-9-])v?(\d+\.\d+\.\d+)(?![A-Za-z0-9-])")
KV_PATTERN = re.compile(
    r"(?P<key>[A-Za-z_][A-Za-z0-9_]+)\s*=\s*(?P<value>true|false|\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
PASSED_PATTERN = re.compile(r"(\d+)\s+passed")
PUBLIC_TOOL_COUNT_PATTERN = re.compile(r"`?(\d+)`?\s+public MCP tools", re.IGNORECASE)
TOOL_TOKEN_PATTERN = re.compile(r"`([a-z_][a-z0-9_]*)`")


def run_release_contract_check(
    root: Path,
    *,
    test_count_provider: Callable[[Path], int] | None = None,
) -> dict[str, Any]:
    project_root = root.resolve()
    readme_paths = [project_root / name for name in README_NAMES if (project_root / name).exists()]
    if not readme_paths:
        raise FileNotFoundError("No release README files found.")

    pyproject_version = load_pyproject_version(project_root / "pyproject.toml")
    expected_facts = load_expected_facts(project_root)
    evidence_paths = build_release_evidence_paths(project_root, pyproject_version)
    main_readme_path = project_root / "README.md"
    main_readme_text = main_readme_path.read_text(encoding="utf-8")
    server_tools = load_server_tool_names(project_root / "src" / "agent_mem_bridge" / "server.py")
    test_count = (
        test_count_provider(project_root)
        if test_count_provider is not None
        else collect_test_count(project_root)
    )

    checks: list[dict[str, Any]] = []

    checks.append(
        build_version_check(
            pyproject_version=pyproject_version,
            readme_paths=readme_paths,
        )
    )
    checks.append(
        build_fact_check(
            readme_paths=readme_paths,
            expected_facts=expected_facts,
        )
    )
    checks.append(build_release_proof_check(project_root, pyproject_version))
    checks.append(
        build_test_count_check(
            evidence_paths=evidence_paths,
            expected_test_count=test_count,
        )
    )
    checks.append(
        build_tool_surface_check(
            readme_text=main_readme_text,
            server_tools=server_tools,
        )
    )
    checks.append(
        build_demo_assets_check(
            project_root=project_root,
        )
    )

    return {
        "ok": all(check["ok"] for check in checks),
        "root": str(project_root),
        "pyproject_version": pyproject_version,
        "server_tool_count": len(server_tools),
        "test_count": test_count,
        "test_count_source": "pytest_collect_only",
        "checks": checks,
    }


def build_version_check(pyproject_version: str, readme_paths: list[Path]) -> dict[str, Any]:
    mismatches: list[dict[str, Any]] = []
    readmes: list[dict[str, Any]] = []
    ok = True
    for path in readme_paths:
        versions = sorted(set(SEMVER_PATTERN.findall(path.read_text(encoding="utf-8"))))
        readmes.append({"path": str(path), "versions": versions})
        if not versions or versions != [pyproject_version]:
            ok = False
            mismatches.append(
                {
                    "path": str(path),
                    "expected_version": pyproject_version,
                    "actual_versions": versions,
                }
            )
    return {
        "name": "pyproject_version_matches_readmes",
        "ok": ok,
        "pyproject_version": pyproject_version,
        "readmes": readmes,
        "mismatches": mismatches,
    }


def build_v020_proof_version_check(project_root: Path, pyproject_version: str) -> dict[str, Any]:
    report_path = project_root / "benchmark" / "latest-v0.20-clean-room-proof-report.json"
    if not report_path.exists():
        return {
            "name": "v020_proof_version_matches_pyproject",
            "ok": False,
            "expected_version": pyproject_version,
            "report_path": str(report_path),
            "actual_release": None,
            "actual_package_version": None,
            "mismatches": [{"field": "report", "expected": "present", "actual": "missing"}],
        }

    report = json.loads(report_path.read_text(encoding="utf-8"))
    actual_release = report.get("release")
    environment = report.get("environment") or {}
    actual_package_version = environment.get("package_version")
    entrypoint_cases = [
        case
        for case in (report.get("cases") or [])
        if case.get("id") == "v020-local-entrypoint-import"
    ]
    actual_cli_version = None
    mismatches = []
    for field, actual in (
        ("release", actual_release),
        ("environment.package_version", actual_package_version),
    ):
        if actual != pyproject_version:
            mismatches.append({"field": field, "expected": pyproject_version, "actual": actual})
    if len(entrypoint_cases) != 1:
        mismatches.append(
            {
                "field": "cases[v020-local-entrypoint-import]",
                "expected": "exactly one case",
                "actual": len(entrypoint_cases),
            }
        )
    else:
        actual_cli_version = (entrypoint_cases[0].get("evidence") or {}).get("version")
        if actual_cli_version != pyproject_version:
            mismatches.append(
                {
                    "field": "cases[v020-local-entrypoint-import].evidence.version",
                    "expected": pyproject_version,
                    "actual": actual_cli_version,
                }
            )
    return {
        "name": "v020_proof_version_matches_pyproject",
        "ok": not mismatches,
        "expected_version": pyproject_version,
        "report_path": str(report_path),
        "actual_release": actual_release,
        "actual_package_version": actual_package_version,
        "actual_cli_version": actual_cli_version,
        "mismatches": mismatches,
    }


def build_release_proof_check(project_root: Path, pyproject_version: str) -> dict[str, Any]:
    if V021_PATCH_PATTERN.fullmatch(pyproject_version):
        return build_v021_governed_change_proof_check(project_root, pyproject_version)
    return build_v020_proof_version_check(project_root, pyproject_version)


def build_v021_governed_change_proof_check(
    project_root: Path,
    pyproject_version: str,
) -> dict[str, Any]:
    report_path = project_root / "benchmark" / V021_GOVERNED_CHANGE_REPORT
    if not report_path.exists():
        return {
            "name": "v021_governed_change_proof_matches_release_gate",
            "ok": False,
            "expected_version": V021_RELEASE,
            "report_path": str(report_path),
            "mismatches": [{"field": "report", "expected": "present", "actual": "missing"}],
        }

    report = json.loads(report_path.read_text(encoding="utf-8"))
    summary = report.get("summary") or {}
    boundaries = report.get("boundaries") or {}
    required_values = (
        ("release", V021_RELEASE, report.get("release")),
        ("target_release", V021_RELEASE, report.get("target_release")),
        ("summary.gate_passed", True, summary.get("gate_passed")),
        ("summary.case_count", 20, summary.get("case_count")),
        (
            "summary.governed_checkpoint_result_count",
            40,
            summary.get("governed_checkpoint_result_count"),
        ),
        ("summary.governed_checkpoint_passes", 40, summary.get("governed_checkpoint_passes")),
        (
            "summary.governed_checkpoint_passes_target",
            "40/40",
            summary.get("governed_checkpoint_passes_target"),
        ),
        ("summary.governed_failures", 0, summary.get("governed_failures")),
        ("summary.flat_baseline_hazards", 17, summary.get("flat_baseline_hazards")),
        (
            "summary.flat_baseline_hazards_expected",
            "17/20",
            summary.get("flat_baseline_hazards_expected"),
        ),
        (
            "summary.useful_current_retention_pass",
            True,
            summary.get("useful_current_retention_pass"),
        ),
        (
            "summary.suppress_all_can_pass",
            False,
            summary.get("suppress_all_can_pass"),
        ),
        ("boundaries.public_mcp_tool_count", 10, boundaries.get("public_mcp_tool_count")),
        (
            "boundaries.public_mcp_surface_unchanged",
            True,
            boundaries.get("public_mcp_surface_unchanged"),
        ),
        ("boundaries.config_write_count", 0, boundaries.get("config_write_count")),
        (
            "boundaries.durable_live_writeback_count",
            0,
            boundaries.get("durable_live_writeback_count"),
        ),
        ("boundaries.auto_writeback_count", 0, boundaries.get("auto_writeback_count")),
    )
    mismatches = [
        {"field": field, "expected": expected, "actual": actual}
        for field, expected, actual in required_values
        if type(actual) is not type(expected) or actual != expected
    ]
    if V021_PATCH_PATTERN.fullmatch(pyproject_version) is None:
        mismatches.append(
            {
                "field": "pyproject.version",
                "expected": "0.21.x",
                "actual": pyproject_version,
            }
        )
    return {
        "name": "v021_governed_change_proof_matches_release_gate",
        "ok": not mismatches,
        "expected_version": V021_RELEASE,
        "package_version": pyproject_version,
        "report_path": str(report_path),
        "actual_release": report.get("release"),
        "actual_target_release": report.get("target_release"),
        "mismatches": mismatches,
    }


def build_fact_check(readme_paths: list[Path], expected_facts: dict[str, int | float | bool]) -> dict[str, Any]:
    required_keys = (
        REQUIRED_BENCHMARK_KEYS
        + REQUIRED_CALIBRATION_KEYS
        + REQUIRED_PROCEDURE_KEYS
        + REQUIRED_SIGNAL_CONTENTION_KEYS
        + REQUIRED_ADVERSARIAL_KEYS
        + REQUIRED_MEMORY_EVOLUTION_KEYS
        + REQUIRED_REVIEW_QUEUE_KEYS
        + REQUIRED_REVIEW_WORKFLOW_KEYS
        + REQUIRED_TASK_BRIEF_KEYS
        + REQUIRED_V019_ADOPTION_PROOF_KEYS
        + REQUIRED_V020_CLEAN_ROOM_PROOF_KEYS
        + tuple(
            key
            for key in REQUIRED_V021_GOVERNED_CHANGE_KEYS
            if key in expected_facts
        )
    )
    mismatches: list[dict[str, Any]] = []
    ok = True
    for path in readme_paths:
        facts = extract_key_values(path.read_text(encoding="utf-8"))
        for key in required_keys:
            actual_values = facts.get(key, [])
            expected_value = expected_facts[key]
            if not actual_values or any(value != expected_value for value in actual_values):
                ok = False
                mismatches.append(
                    {
                        "path": str(path),
                        "key": key,
                        "expected": expected_value,
                        "actual": actual_values,
                    }
                )
    return {
        "name": "readme_facts_match_snapshot_reports",
        "ok": ok,
        "expected_facts": expected_facts,
        "mismatches": mismatches,
    }


def build_release_evidence_paths(project_root: Path, pyproject_version: str) -> list[Path]:
    candidates = [
        *(project_root / name for name in README_NAMES),
        project_root / "docs" / "PRODUCTION-STATUS.md",
        project_root / "docs" / f"v{pyproject_version}-announcement.md",
    ]
    return [path for path in candidates if path.exists()]


def build_test_count_check(evidence_paths: list[Path], expected_test_count: int) -> dict[str, Any]:
    mismatches: list[dict[str, Any]] = []
    ok = True
    for path in evidence_paths:
        counts = extract_pass_counts(path.read_text(encoding="utf-8"))
        if not counts or any(count != expected_test_count for count in counts):
            ok = False
            mismatches.append(
                {
                    "path": str(path),
                    "expected": expected_test_count,
                    "actual": counts,
                }
            )
    return {
        "name": "readme_test_count_matches_collected_suite",
        "ok": ok,
        "expected_test_count": expected_test_count,
        "source": "pytest --collect-only -q tests",
        "mismatches": mismatches,
    }


def build_tool_surface_check(readme_text: str, server_tools: set[str]) -> dict[str, Any]:
    public_tool_counts = extract_public_tool_counts(readme_text)
    readme_tool_names = extract_readme_tool_names(readme_text)
    expected_count = len(server_tools)
    ok = True
    mismatches: list[dict[str, Any]] = []

    if not public_tool_counts or any(count != expected_count for count in public_tool_counts):
        ok = False
        mismatches.append(
            {
                "kind": "count",
                "expected": expected_count,
                "actual": public_tool_counts,
            }
        )

    if readme_tool_names != server_tools:
        ok = False
        mismatches.append(
            {
                "kind": "tool_names",
                "expected": sorted(server_tools),
                "actual": sorted(readme_tool_names),
            }
        )

    return {
        "name": "public_mcp_tool_count_matches_server_surface",
        "ok": ok,
        "expected_count": expected_count,
        "server_tools": sorted(server_tools),
        "readme_tools": sorted(readme_tool_names),
        "mismatches": mismatches,
    }


def build_demo_assets_check(project_root: Path) -> dict[str, Any]:
    demo_dir = project_root / "examples" / "demo"
    required_assets = {
        demo_dir / "terminal-demo.cast",
        demo_dir / "terminal-demo.gif",
        project_root / "examples" / "diagrams" / "amb-overview.svg",
    }

    demo_readme = demo_dir / "README.md"
    if demo_readme.exists():
        text = demo_readme.read_text(encoding="utf-8")
        for asset_name in sorted(set(re.findall(r"terminal-demo\.[A-Za-z0-9]+", text))):
            required_assets.add(demo_dir / asset_name)

    missing = sorted(str(path) for path in required_assets if not path.exists())
    return {
        "name": "current_demo_assets_exist",
        "ok": not missing,
        "required_assets": [str(path) for path in sorted(required_assets)],
        "missing_assets": missing,
    }


def load_pyproject_version(path: Path) -> str:
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    return str(data["project"]["version"])


def load_expected_facts(project_root: Path) -> dict[str, int | float | bool]:
    benchmark_report = json.loads((project_root / "benchmark" / "latest-report.json").read_text(encoding="utf-8"))
    calibration_report = json.loads(
        (project_root / "benchmark" / "latest-calibration-report.json").read_text(encoding="utf-8")
    )
    procedure_report = json.loads(
        (project_root / "benchmark" / "latest-procedure-governance-report.json").read_text(encoding="utf-8")
    )
    signal_contention_report = json.loads(
        (project_root / "benchmark" / "latest-signal-contention-report.json").read_text(encoding="utf-8")
    )
    adversarial_report = json.loads(
        (project_root / "benchmark" / "latest-adversarial-memory-report.json").read_text(encoding="utf-8")
    )
    memory_evolution_report = json.loads(
        (project_root / "benchmark" / "latest-memory-evolution-report.json").read_text(encoding="utf-8")
    )
    review_queue_report = json.loads(
        (project_root / "benchmark" / "latest-review-queue-report.json").read_text(encoding="utf-8")
    )
    review_workflow_report = json.loads(
        (project_root / "benchmark" / "latest-review-workflow-report.json").read_text(encoding="utf-8")
    )
    task_brief_report = json.loads(
        (project_root / "benchmark" / "latest-task-brief-report.json").read_text(encoding="utf-8")
    )
    v019_report = json.loads(
        (project_root / "benchmark" / "latest-v0.19-adoption-proof-report.json").read_text(encoding="utf-8")
    )
    v020_report = json.loads(
        (project_root / "benchmark" / "latest-v0.20-clean-room-proof-report.json").read_text(encoding="utf-8")
    )
    v021_report_path = project_root / "benchmark" / V021_GOVERNED_CHANGE_REPORT
    v021_report = (
        json.loads(v021_report_path.read_text(encoding="utf-8"))
        if v021_report_path.exists()
        else None
    )
    benchmark_summary = benchmark_report["summary"]
    calibration_summary = calibration_report["summary"]
    procedure_summary = procedure_report["summary"]
    signal_contention_summary = signal_contention_report["summary"]
    adversarial_summary = adversarial_report["summary"]
    memory_evolution_summary = memory_evolution_report["summary"]
    review_queue_summary = review_queue_report["summary"]
    review_workflow_summary = review_workflow_report["summary"]
    task_brief_summary = task_brief_report["summary"]
    v019_summary = v019_report["summary"]
    v020_summary = v020_report["summary"]
    expected: dict[str, int | float | bool] = {}
    for key in REQUIRED_BENCHMARK_KEYS:
        expected[key] = benchmark_summary[key]
    for key in REQUIRED_CALIBRATION_KEYS:
        expected[key] = calibration_summary[key]
    for key in REQUIRED_PROCEDURE_KEYS:
        expected[key] = procedure_summary[key]
    expected["signal_contention_case_count"] = signal_contention_summary["case_count"]
    expected["signal_contention_case_pass_rate"] = signal_contention_summary["case_pass_rate"]
    for key in REQUIRED_SIGNAL_CONTENTION_KEYS:
        if key not in expected:
            expected[key] = signal_contention_summary[key]
    expected["adversarial_case_count"] = adversarial_summary["case_count"]
    expected["adversarial_task_count"] = adversarial_summary["task_count"]
    expected["adversarial_governed_task_pass_rate"] = adversarial_summary["governed_task_pass_rate"]
    expected["adversarial_governed_blocked_record_leak_rate"] = adversarial_summary[
        "governed_blocked_record_leak_rate"
    ]
    expected["memory_evolution_case_count"] = memory_evolution_summary["case_count"]
    expected["memory_evolution_task_count"] = memory_evolution_summary["task_count"]
    expected["memory_evolution_governed_task_pass_rate"] = memory_evolution_summary["governed_task_pass_rate"]
    expected["memory_evolution_governed_blocked_record_leak_rate"] = memory_evolution_summary[
        "governed_blocked_record_leak_rate"
    ]
    expected["memory_evolution_governed_disposition_reason_hit_rate"] = memory_evolution_summary[
        "governed_disposition_reason_hit_rate"
    ]
    for key in REQUIRED_REVIEW_QUEUE_KEYS:
        expected[key] = review_queue_summary[key]
    for key in REQUIRED_REVIEW_WORKFLOW_KEYS:
        expected[key] = review_workflow_summary[key]
    for key in REQUIRED_TASK_BRIEF_KEYS:
        expected[key] = task_brief_summary[key]
    for key in REQUIRED_V019_ADOPTION_PROOF_KEYS:
        expected[key] = v019_summary[key]
    for key in REQUIRED_V020_CLEAN_ROOM_PROOF_KEYS:
        expected[key] = v020_summary[key]
    if v021_report is not None:
        v021_summary = v021_report["summary"]
        v021_boundaries = v021_report["boundaries"]
        expected.update(
            {
                "v021_case_count": v021_summary["case_count"],
                "v021_category_count": v021_summary["category_count"],
                "v021_flat_baseline_hazards": v021_summary["flat_baseline_hazards"],
                "v021_governed_case_pass_count": v021_summary["governed_case_pass_count"],
                "v021_governed_failures": v021_summary["governed_failures"],
                "v021_governed_checkpoint_passes": v021_summary["governed_checkpoint_passes"],
                "v021_governed_checkpoint_result_count": v021_summary[
                    "governed_checkpoint_result_count"
                ],
                "v021_useful_current_retention_pass": v021_summary[
                    "useful_current_retention_pass"
                ],
                "v021_suppress_all_can_pass": v021_summary["suppress_all_can_pass"],
                "v021_public_mcp_tool_count": v021_boundaries["public_mcp_tool_count"],
                "v021_public_mcp_surface_change": not v021_boundaries[
                    "public_mcp_surface_unchanged"
                ],
                "v021_auto_writeback_count": v021_boundaries["auto_writeback_count"],
                "v021_config_write_count": v021_boundaries["config_write_count"],
                "v021_durable_live_writeback_count": v021_boundaries[
                    "durable_live_writeback_count"
                ],
            }
        )
    return expected


def extract_key_values(text: str) -> dict[str, list[int | float | bool]]:
    values: dict[str, list[int | float | bool]] = {}
    for match in KV_PATTERN.finditer(text):
        key = match.group("key")
        value = coerce_value(match.group("value"))
        values.setdefault(key, []).append(value)
    return values


def extract_pass_counts(text: str) -> list[int]:
    return [int(match) for match in PASSED_PATTERN.findall(text)]


def extract_public_tool_counts(text: str) -> list[int]:
    return [int(match) for match in PUBLIC_TOOL_COUNT_PATTERN.findall(text)]


def extract_readme_tool_names(text: str) -> set[str]:
    section = slice_markdown_section(text, "## MCP Tools")
    tool_names: set[str] = set()
    for line in section.splitlines():
        if not line.lstrip().startswith("-"):
            continue
        for token in TOOL_TOKEN_PATTERN.findall(line):
            if "_" in token or token in {"store", "recall", "browse", "stats", "forget", "promote", "export"}:
                tool_names.add(token)
    return tool_names


def slice_markdown_section(text: str, heading: str) -> str:
    start = text.find(heading)
    if start == -1:
        return ""
    remainder = text[start + len(heading) :]
    next_heading = remainder.find("\n## ")
    if next_heading == -1:
        return remainder
    return remainder[:next_heading]


def load_server_tool_names(path: Path) -> set[str]:
    module = ast.parse(path.read_text(encoding="utf-8"))
    tool_names: set[str] = set()
    for node in module.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        if any(is_mcp_tool_decorator(decorator) for decorator in node.decorator_list):
            tool_names.add(node.name)
    return tool_names


def is_mcp_tool_decorator(node: ast.expr) -> bool:
    target = node.func if isinstance(node, ast.Call) else node
    return isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == "mcp" and target.attr == "tool"


def collect_test_count(project_root: Path) -> int:
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "tests"],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=True,
    )
    match = re.search(r"(\d+)\s+tests collected", completed.stdout)
    if match is None:
        raise ValueError("Could not determine collected test count from pytest output.")
    return int(match.group(1))


def coerce_value(raw: str) -> int | float | bool:
    lowered = raw.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if "." in raw:
        return float(raw)
    return int(raw)
