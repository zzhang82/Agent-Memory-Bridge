from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

from agent_mem_bridge.release_contract import run_release_contract_check


def test_run_release_contract_check_passes_for_aligned_fixture(tmp_path: Path) -> None:
    root = create_release_fixture(tmp_path)

    # 146 is the fixture-internal count declared in create_release_fixture below.
    # It is intentionally not the live test count. The fixture is a synthetic
    # tree where the README, report JSON, and test_count_provider are all set to the
    # same fixed value so the contract check passes deterministically without running
    # pytest on the real suite.
    report = run_release_contract_check(root, test_count_provider=lambda _: 146)

    assert report["ok"] is True
    assert report["pyproject_version"] == "0.9.0"
    assert report["server_tool_count"] == 10
    assert report["test_count_source"] == "pytest_collect_only"
    assert all(check["ok"] for check in report["checks"])

    v021_root = create_v021_release_fixture(tmp_path / "v021")
    v021_report = run_release_contract_check(v021_root, test_count_provider=lambda _: 146)
    checks = {check["name"]: check for check in v021_report["checks"]}
    assert v021_report["ok"] is True
    assert "v020_proof_version_matches_pyproject" not in checks
    assert checks["v021_governed_change_proof_matches_release_gate"]["ok"] is True
    historical_v020 = json.loads(
        (v021_root / "benchmark" / "latest-v0.20-clean-room-proof-report.json").read_text(
            encoding="utf-8"
        )
    )
    assert historical_v020["release"] == "0.9.0"


def test_run_release_contract_check_reports_specific_mismatches(tmp_path: Path) -> None:
    root = create_release_fixture(tmp_path)

    pyproject = root / "pyproject.toml"
    pyproject.write_text(pyproject.read_text(encoding="utf-8").replace('version = "0.9.0"', 'version = "0.9.1"'), encoding="utf-8")

    readme = root / "README.md"
    readme.write_text(
        readme.read_text(encoding="utf-8")
        .replace("`146 passed`", "`140 passed`")
        .replace("`classifier_exact_match_rate = 0.875`", "`classifier_exact_match_rate = 0.9`")
        .replace("`10` public MCP tools", "`9` public MCP tools"),
        encoding="utf-8",
    )
    production_status = root / "docs" / "PRODUCTION-STATUS.md"
    production_status.write_text(
        production_status.read_text(encoding="utf-8").replace("`146 passed`", "`140 passed`"),
        encoding="utf-8",
    )

    (root / "examples" / "demo" / "terminal-demo.gif").unlink()

    report = run_release_contract_check(root, test_count_provider=lambda _: 146)

    assert report["ok"] is False
    check_names = {check["name"]: check for check in report["checks"]}
    assert check_names["pyproject_version_matches_readmes"]["ok"] is False
    assert check_names["v020_proof_version_matches_pyproject"]["ok"] is False
    assert check_names["readme_facts_match_snapshot_reports"]["ok"] is False
    assert check_names["readme_test_count_matches_collected_suite"]["ok"] is False
    assert check_names["public_mcp_tool_count_matches_server_surface"]["ok"] is False
    assert check_names["current_demo_assets_exist"]["ok"] is False

    gate_mismatches = [
        (None, "release", "0.20.0", "release"),
        (None, "target_release", "0.22.0", "target_release"),
        ("summary", "gate_passed", False, "summary.gate_passed"),
        ("summary", "gate_passed", 1, "summary.gate_passed"),
        ("summary", "case_count", 19, "summary.case_count"),
        (
            "summary",
            "governed_checkpoint_result_count",
            39,
            "summary.governed_checkpoint_result_count",
        ),
        ("summary", "governed_checkpoint_passes", 39, "summary.governed_checkpoint_passes"),
        (
            "summary",
            "governed_checkpoint_passes_target",
            "39/40",
            "summary.governed_checkpoint_passes_target",
        ),
        ("summary", "governed_failures", 1, "summary.governed_failures"),
        ("summary", "flat_baseline_hazards", 16, "summary.flat_baseline_hazards"),
        (
            "summary",
            "flat_baseline_hazards_expected",
            "16/20",
            "summary.flat_baseline_hazards_expected",
        ),
        (
            "summary",
            "useful_current_retention_pass",
            False,
            "summary.useful_current_retention_pass",
        ),
        (
            "summary",
            "suppress_all_can_pass",
            True,
            "summary.suppress_all_can_pass",
        ),
        ("boundaries", "public_mcp_tool_count", 11, "boundaries.public_mcp_tool_count"),
        (
            "boundaries",
            "public_mcp_surface_unchanged",
            False,
            "boundaries.public_mcp_surface_unchanged",
        ),
        ("boundaries", "config_write_count", 1, "boundaries.config_write_count"),
        (
            "boundaries",
            "durable_live_writeback_count",
            1,
            "boundaries.durable_live_writeback_count",
        ),
        ("boundaries", "auto_writeback_count", 1, "boundaries.auto_writeback_count"),
    ]
    for index, (section, field, invalid_value, mismatch_field) in enumerate(gate_mismatches):
        v021_root = create_v021_release_fixture(tmp_path / f"v021-mismatch-{index}")
        report_path = v021_root / "benchmark" / "latest-v0.21-governed-change-report.json"
        proof = json.loads(report_path.read_text(encoding="utf-8"))
        target = proof if section is None else proof[section]
        target[field] = invalid_value
        report_path.write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")

        mismatch_report = run_release_contract_check(
            v021_root,
            test_count_provider=lambda _: 146,
        )
        proof_check = next(
            item
            for item in mismatch_report["checks"]
            if item["name"] == "v021_governed_change_proof_matches_release_gate"
        )
        assert proof_check["ok"] is False
        assert {mismatch["field"] for mismatch in proof_check["mismatches"]} == {
            mismatch_field
        }


def test_release_contract_rejects_stale_v020_proof_version(tmp_path: Path) -> None:
    root = create_release_fixture(tmp_path)
    report_path = root / "benchmark" / "latest-v0.20-clean-room-proof-report.json"
    proof = json.loads(report_path.read_text(encoding="utf-8"))
    proof["release"] = "0.8.0"
    proof["environment"]["package_version"] = "0.8.0"
    report_path.write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")

    report = run_release_contract_check(root, test_count_provider=lambda _: 146)

    check = next(item for item in report["checks"] if item["name"] == "v020_proof_version_matches_pyproject")
    assert check["ok"] is False
    assert {item["field"] for item in check["mismatches"]} == {
        "release",
        "environment.package_version",
    }


def test_release_contract_rejects_stale_v020_cli_version_evidence(tmp_path: Path) -> None:
    root = create_release_fixture(tmp_path)
    report_path = root / "benchmark" / "latest-v0.20-clean-room-proof-report.json"
    proof = json.loads(report_path.read_text(encoding="utf-8"))
    proof["cases"][0]["evidence"]["version"] = "0.8.0"
    report_path.write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")

    report = run_release_contract_check(root, test_count_provider=lambda _: 146)

    check = next(item for item in report["checks"] if item["name"] == "v020_proof_version_matches_pyproject")
    assert check["ok"] is False
    assert check["mismatches"] == [
        {
            "field": "cases[v020-local-entrypoint-import].evidence.version",
            "expected": "0.9.0",
            "actual": "0.8.0",
        }
    ]


def test_release_contract_rejects_v021_readme_fact_drift(tmp_path: Path) -> None:
    root = create_v021_release_fixture(tmp_path)
    readme = root / "README.md"
    readme.write_text(
        readme.read_text(encoding="utf-8").replace(
            "v021_governed_failures = 0",
            "v021_governed_failures = 1",
        ),
        encoding="utf-8",
    )

    report = run_release_contract_check(root, test_count_provider=lambda _: 146)

    checks = {check["name"]: check for check in report["checks"]}
    assert checks["v021_governed_change_proof_matches_release_gate"]["ok"] is True
    fact_check = checks["readme_facts_match_snapshot_reports"]
    assert fact_check["ok"] is False
    assert any(
        mismatch["key"] == "v021_governed_failures"
        and mismatch["expected"] == 0
        and mismatch["actual"] == [1]
        for mismatch in fact_check["mismatches"]
    )


def test_check_release_contract_script_exits_zero_for_aligned_fixture(tmp_path: Path) -> None:
    root = create_release_fixture(tmp_path)
    script = Path(__file__).resolve().parents[1] / "scripts" / "check_release_contract.py"

    completed = subprocess.run(
        [sys.executable, str(script), "--root", str(root)],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True


# The fixture creates a fully synthetic release tree with fixed counts.
# All numeric facts (test count, benchmark values, calibration values) are
# pinned to the same values in the README, report JSON, and test_count_provider
# so the contract check passes without touching the live codebase.
# This count (146) is NOT required to match the live test count.
def create_release_fixture(root: Path) -> Path:
    write_file(
        root / "pyproject.toml",
        """
        [project]
        name = "agent-memory-bridge"
        version = "0.9.0"
        """,
    )
    readme_text = """
        # Agent Memory Bridge

        `0.9.0` makes memory more structured and more applicable.

        - `10` public MCP tools, with most sophistication staying behind the bridge

        ## Evidence

        - `pytest` currently passes with `146 passed`
        - `question_count = 11`
        - `memory_expected_top1_accuracy = 1.0`
        - `memory_mrr = 1.0`
        - `file_scan_expected_top1_accuracy = 0.636`
        - `file_scan_mrr = 0.909`
        - `sample_count = 16`
        - `classifier_exact_match_rate = 0.875`
        - `fallback_exact_match_rate = 0.062`
        - `classifier_better_count = 13`
        - `fallback_better_count = 2`
        - `classifier_filtered_low_confidence_count = 2`
        - `case_count = 7`
        - `flat_case_pass_rate = 0.429`
        - `governed_case_pass_rate = 1.0`
        - `flat_blocked_procedure_leak_rate = 1.0`
        - `governed_blocked_procedure_leak_rate = 0.0`
        - `governed_governance_field_completeness = 1.0`
        - `signal_contention_case_count = 5`
        - `signal_contention_case_pass_rate = 1.0`
        - `unique_active_claim_rate = 1.0`
        - `duplicate_active_claim_count = 0`
        - `active_reclaim_block_rate = 1.0`
        - `stale_ack_blocked_rate = 1.0`
        - `stale_reclaim_success_rate = 1.0`
        - `pending_under_pressure_claim_rate = 1.0`
        - `initial_hard_expiry_cap_rate = 1.0`
        - `adversarial_case_count = 6`
        - `adversarial_task_count = 7`
        - `adversarial_governed_task_pass_rate = 1.0`
        - `adversarial_governed_blocked_record_leak_rate = 0.0`
        - `memory_evolution_case_count = 6`
        - `memory_evolution_task_count = 7`
        - `memory_evolution_governed_task_pass_rate = 1.0`
        - `memory_evolution_governed_blocked_record_leak_rate = 0.0`
        - `memory_evolution_governed_disposition_reason_hit_rate = 1.0`
        - `review_queue_item_count = 6`
        - `review_queue_actionable_count = 6`
        - `review_queue_hidden_lane_count = 2`
        - `review_queue_writeback_plan_count = 6`
        - `review_queue_no_auto_mutation = true`
        - `review_queue_public_mcp_surface_change = false`
        - `review_queue_item_type_count = 6`
        - `review_workflow_source_queue_item_count = 6`
        - `review_workflow_item_count = 6`
        - `review_workflow_manual_step_count = 27`
        - `review_workflow_requires_human_count = 6`
        - `review_workflow_auto_write_count = 0`
        - `review_workflow_no_auto_writeback = true`
        - `review_workflow_public_mcp_surface_change = false`
        - `review_workflow_item_type_count = 6`
        - `task_brief_used_count = 2`
        - `task_brief_ignored_count = 1`
        - `task_brief_needs_review_count = 4`
        - `task_brief_review_queue_item_count = 2`
        - `task_brief_active_signal_count = 1`
        - `task_brief_no_auto_writeback = true`
        - `task_brief_public_mcp_surface_change = false`
        - `task_brief_needs_review_source_type_count = 3`
        - `v019_case_count = 12`
        - `v019_pass_count = 12`
        - `v019_pass_rate = 1.0`
        - `v019_retrieval_case_count = 4`
        - `v019_retrieval_pass_rate = 1.0`
        - `v019_task_brief_case_count = 4`
        - `v019_task_brief_pass_rate = 1.0`
        - `v019_first_run_adoption_case_count = 4`
        - `v019_first_run_adoption_pass_rate = 1.0`
        - `v019_public_mcp_tool_count = 10`
        - `v019_public_mcp_surface_change = false`
        - `v019_client_config_write_count = 0`
        - `v019_durable_writeback_count = 0`
        - `v019_amh_required = false`
        - `v019_native_memory_comparison_required = true`
        - `v020_case_count = 6`
        - `v020_pass_count = 6`
        - `v020_pass_rate = 1.0`
        - `v020_import_sanity_pass = true`
        - `v020_stdio_round_trip_pass = true`
        - `v020_first_run_pass = true`
        - `v020_task_brief_pass = true`
        - `v020_public_mcp_tool_count = 10`
        - `v020_public_mcp_surface_change = false`
        - `v020_client_config_write_count = 0`
        - `v020_explicit_demo_memory_write_count = 1`
        - `v020_explicit_demo_signal_write_count = 0`
        - `v020_non_demo_durable_writeback_count = 0`
        - `v020_amh_required = false`
        - `v020_external_vendor_adoption_claim = false`

        ## MCP Tools

        - `store` and `recall`
        - `browse` and `stats`
        - `forget` and `promote`
        - `claim_signal`, `extend_signal_lease`, and `ack_signal`
        - `export`

        ![Agent Memory Bridge terminal demo](examples/demo/terminal-demo.gif)
        """
    write_file(root / "README.md", readme_text)
    write_file(root / "README.zh-CN.md", readme_text)
    write_file(root / "docs" / "PRODUCTION-STATUS.md", "`pytest`: `146 passed`\n")
    write_file(root / "docs" / "v0.9.0-announcement.md", "`pytest`: `146 passed`\n")
    write_file(
        root / "benchmark" / "latest-report.json",
        json.dumps(
            {
                "summary": {
                    "question_count": 11,
                    "memory_expected_top1_accuracy": 1.0,
                    "memory_mrr": 1.0,
                    "file_scan_expected_top1_accuracy": 0.636,
                    "file_scan_mrr": 0.909,
                }
            },
            indent=2,
        ),
    )
    write_file(
        root / "benchmark" / "latest-calibration-report.json",
        json.dumps(
            {
                "summary": {
                    "sample_count": 16,
                    "classifier_exact_match_rate": 0.875,
                    "fallback_exact_match_rate": 0.062,
                    "classifier_better_count": 13,
                    "fallback_better_count": 2,
                    "classifier_filtered_low_confidence_count": 2,
                }
            },
            indent=2,
        ),
    )
    write_file(
        root / "benchmark" / "latest-procedure-governance-report.json",
        json.dumps(
            {
                "summary": {
                    "case_count": 7,
                    "flat_case_pass_rate": 0.429,
                    "governed_case_pass_rate": 1.0,
                    "flat_blocked_procedure_leak_rate": 1.0,
                    "governed_blocked_procedure_leak_rate": 0.0,
                    "governed_governance_field_completeness": 1.0,
                }
            },
            indent=2,
        ),
    )
    write_file(
        root / "benchmark" / "latest-signal-contention-report.json",
        json.dumps(
            {
                "summary": {
                    "case_count": 5,
                    "case_pass_rate": 1.0,
                    "unique_active_claim_rate": 1.0,
                    "duplicate_active_claim_count": 0,
                    "active_reclaim_block_rate": 1.0,
                    "stale_ack_blocked_rate": 1.0,
                    "stale_reclaim_success_rate": 1.0,
                    "pending_under_pressure_claim_rate": 1.0,
                    "initial_hard_expiry_cap_rate": 1.0,
                }
            },
            indent=2,
        ),
    )
    write_file(
        root / "benchmark" / "latest-adversarial-memory-report.json",
        json.dumps(
            {
                "summary": {
                    "case_count": 6,
                    "task_count": 7,
                    "governed_task_pass_rate": 1.0,
                    "governed_blocked_record_leak_rate": 0.0,
                }
            },
            indent=2,
        ),
    )
    write_file(
        root / "benchmark" / "latest-memory-evolution-report.json",
        json.dumps(
            {
                "summary": {
                    "case_count": 6,
                    "task_count": 7,
                    "governed_task_pass_rate": 1.0,
                    "governed_blocked_record_leak_rate": 0.0,
                    "governed_disposition_reason_hit_rate": 1.0,
                }
            },
            indent=2,
        ),
    )
    write_file(
        root / "benchmark" / "latest-review-queue-report.json",
        json.dumps(
            {
                "summary": {
                    "review_queue_item_count": 6,
                    "review_queue_actionable_count": 6,
                    "review_queue_hidden_lane_count": 2,
                    "review_queue_writeback_plan_count": 6,
                    "review_queue_no_auto_mutation": True,
                    "review_queue_public_mcp_surface_change": False,
                    "review_queue_item_type_count": 6,
                }
            },
            indent=2,
        ),
    )
    write_file(
        root / "benchmark" / "latest-review-workflow-report.json",
        json.dumps(
            {
                "summary": {
                    "review_workflow_source_queue_item_count": 6,
                    "review_workflow_item_count": 6,
                    "review_workflow_manual_step_count": 27,
                    "review_workflow_requires_human_count": 6,
                    "review_workflow_auto_write_count": 0,
                    "review_workflow_no_auto_writeback": True,
                    "review_workflow_public_mcp_surface_change": False,
                    "review_workflow_item_type_count": 6,
                }
            },
            indent=2,
        ),
    )
    write_file(
        root / "benchmark" / "latest-task-brief-report.json",
        json.dumps(
            {
                "summary": {
                    "task_brief_used_count": 2,
                    "task_brief_ignored_count": 1,
                    "task_brief_needs_review_count": 4,
                    "task_brief_review_queue_item_count": 2,
                    "task_brief_active_signal_count": 1,
                    "task_brief_no_auto_writeback": True,
                    "task_brief_public_mcp_surface_change": False,
                    "task_brief_needs_review_source_type_count": 3,
                }
            },
            indent=2,
        ),
    )
    write_file(
        root / "benchmark" / "latest-v0.19-adoption-proof-report.json",
        json.dumps(
            {
                "summary": {
                    "v019_case_count": 12,
                    "v019_pass_count": 12,
                    "v019_pass_rate": 1.0,
                    "v019_retrieval_case_count": 4,
                    "v019_retrieval_pass_rate": 1.0,
                    "v019_task_brief_case_count": 4,
                    "v019_task_brief_pass_rate": 1.0,
                    "v019_first_run_adoption_case_count": 4,
                    "v019_first_run_adoption_pass_rate": 1.0,
                    "v019_public_mcp_tool_count": 10,
                    "v019_public_mcp_surface_change": False,
                    "v019_client_config_write_count": 0,
                    "v019_durable_writeback_count": 0,
                    "v019_amh_required": False,
                    "v019_native_memory_comparison_required": True,
                }
            },
            indent=2,
        ),
    )
    write_file(
        root / "benchmark" / "latest-v0.20-clean-room-proof-report.json",
        json.dumps(
            {
                "release": "0.9.0",
                "environment": {"package_version": "0.9.0"},
                "cases": [
                    {
                        "id": "v020-local-entrypoint-import",
                        "evidence": {"version": "0.9.0"},
                    }
                ],
                "summary": {
                    "v020_case_count": 6,
                    "v020_pass_count": 6,
                    "v020_pass_rate": 1.0,
                    "v020_import_sanity_pass": True,
                    "v020_stdio_round_trip_pass": True,
                    "v020_first_run_pass": True,
                    "v020_task_brief_pass": True,
                    "v020_public_mcp_tool_count": 10,
                    "v020_public_mcp_surface_change": False,
                    "v020_client_config_write_count": 0,
                    "v020_explicit_demo_memory_write_count": 1,
                    "v020_explicit_demo_signal_write_count": 0,
                    "v020_non_demo_durable_writeback_count": 0,
                    "v020_amh_required": False,
                    "v020_external_vendor_adoption_claim": False,
                }
            },
            indent=2,
        ),
    )
    write_file(
        root / "src" / "agent_mem_bridge" / "server.py",
        """
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("agent-memory-bridge")

        @mcp.tool()
        def store():
            return None

        @mcp.tool()
        def recall():
            return None

        @mcp.tool()
        def browse():
            return None

        @mcp.tool()
        def stats():
            return None

        @mcp.tool()
        def forget():
            return None

        @mcp.tool()
        def claim_signal():
            return None

        @mcp.tool()
        def extend_signal_lease():
            return None

        @mcp.tool()
        def ack_signal():
            return None

        @mcp.tool()
        def promote():
            return None

        @mcp.tool()
        def export():
            return None
        """,
    )
    write_file(
        root / "examples" / "demo" / "README.md",
        """
        # Demo

        - `terminal-demo.cast`
        - `terminal-demo.gif`
        - `terminal-demo.tape`
        """,
    )
    write_file(root / "examples" / "demo" / "terminal-demo.cast", "cast\n")
    write_file(root / "examples" / "demo" / "terminal-demo.gif", "gif\n")
    write_file(root / "examples" / "demo" / "terminal-demo.tape", "tape\n")
    write_file(root / "examples" / "diagrams" / "amb-overview.png", "png\n")
    sample_tests = "\n".join(
        f"def test_release_contract_sample_{index:03d}() -> None:\n    assert True\n"
        for index in range(146)
    )
    write_file(root / "tests" / "test_sample.py", sample_tests)
    return root


def create_v021_release_fixture(root: Path) -> Path:
    create_release_fixture(root)
    pyproject = root / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text(encoding="utf-8").replace('version = "0.9.0"', 'version = "0.21.0"'),
        encoding="utf-8",
    )
    for readme_name in ("README.md", "README.zh-CN.md"):
        readme = root / readme_name
        v021_facts = """

v021_case_count = 20
v021_category_count = 4
v021_flat_baseline_hazards = 17
v021_governed_case_pass_count = 20
v021_governed_failures = 0
v021_governed_checkpoint_passes = 40
v021_governed_checkpoint_result_count = 40
v021_useful_current_retention_pass = true
v021_suppress_all_can_pass = false
v021_public_mcp_tool_count = 10
v021_public_mcp_surface_change = false
v021_auto_writeback_count = 0
v021_config_write_count = 0
v021_durable_live_writeback_count = 0
"""
        readme.write_text(
            readme.read_text(encoding="utf-8").replace("`0.9.0`", "`0.21.0`")
            + v021_facts,
            encoding="utf-8",
        )
    write_file(root / "docs" / "v0.21.0-announcement.md", "`pytest`: `146 passed`\n")
    write_file(
        root / "benchmark" / "latest-v0.21-governed-change-report.json",
        json.dumps(
            {
                "release": "0.21.0",
                "target_release": "0.21.0",
                "summary": {
                    "gate_passed": True,
                    "case_count": 20,
                    "category_count": 4,
                    "governed_case_pass_count": 20,
                    "governed_checkpoint_result_count": 40,
                    "governed_checkpoint_passes": 40,
                    "governed_checkpoint_passes_target": "40/40",
                    "governed_failures": 0,
                    "flat_baseline_hazards": 17,
                    "flat_baseline_hazards_expected": "17/20",
                    "useful_current_retention_pass": True,
                    "suppress_all_can_pass": False,
                },
                "boundaries": {
                    "public_mcp_tool_count": 10,
                    "public_mcp_surface_unchanged": True,
                    "config_write_count": 0,
                    "durable_live_writeback_count": 0,
                    "auto_writeback_count": 0,
                },
            },
            indent=2,
        ),
    )
    return root


def write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).strip() + "\n", encoding="utf-8")
