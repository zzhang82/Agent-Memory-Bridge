from __future__ import annotations

import ast
import inspect
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import tools.evidence.v019_adoption_proof as v019_adoption_proof
from agent_mem_bridge.onboarding import TOOL_NAMES
from agent_mem_bridge.release_contract import load_server_tool_names
from tools.evidence.v019_adoption_proof import (
    DEFAULT_V019_MANIFEST_PATH,
    EXPECTED_PUBLIC_TOOL_COUNT,
    V019_ADOPTION_PROOF_SCHEMA,
    run_v019_adoption_proof,
)

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "run_v019_adoption_proof.py"
EXPECTED_PUBLIC_TOOLS = TOOL_NAMES


def test_v019_manifest_denominator_is_fixed_before_implementation() -> None:
    manifest = json.loads(DEFAULT_V019_MANIFEST_PATH.read_text(encoding="utf-8"))

    assert manifest["schema"] == "amb.v0.19.fixture_manifest.v1"
    assert manifest["status"] == "planning_manifest_not_benchmark"
    assert manifest["case_count"] == 12
    categories = {}
    for case in manifest["cases"]:
        categories[case["category"]] = categories.get(case["category"], 0) + 1
        assert set(case) >= {
            "id",
            "category",
            "purpose",
            "query_or_command",
            "expected_behavior",
            "failure_reason",
            "non_goal_guard",
        }
    assert categories == {"retrieval": 4, "task_brief": 4, "first_run_adoption": 4}


def test_v019_adoption_proof_runs_fixed_12_case_pack_without_surface_expansion() -> None:
    report = run_v019_adoption_proof()
    summary = report["summary"]

    assert report["schema"] == V019_ADOPTION_PROOF_SCHEMA
    assert report["release"] == "0.19.0"
    assert summary["v019_case_count"] == 12
    assert summary["v019_pass_count"] == 12
    assert summary["v019_pass_rate"] == 1.0
    assert summary["v019_retrieval_case_count"] == 4
    assert summary["v019_retrieval_pass_rate"] == 1.0
    assert summary["v019_task_brief_case_count"] == 4
    assert summary["v019_task_brief_pass_rate"] == 1.0
    assert summary["v019_first_run_adoption_case_count"] == 4
    assert summary["v019_first_run_adoption_pass_rate"] == 1.0
    assert summary["v019_public_mcp_tool_count"] == EXPECTED_PUBLIC_TOOL_COUNT
    assert summary["v019_public_mcp_surface_change"] is False
    assert summary["v019_client_config_write_count"] == 0
    assert summary["v019_durable_writeback_count"] == 0
    assert summary["v019_amh_required"] is False
    assert summary["v019_native_memory_comparison_required"] is True
    assert summary["v019_current_public_surface_contract_pass"] is True

    assert {case["id"] for case in report["cases"]} == {
        case["id"] for case in json.loads(DEFAULT_V019_MANIFEST_PATH.read_text(encoding="utf-8"))["cases"]
    }


def test_v019_first_run_cases_use_current_renderer_and_surface_evidence() -> None:
    report = run_v019_adoption_proof()
    first_run_cases = [case for case in report["cases"] if case["category"] == "first_run_adoption"]
    current_tools = load_server_tool_names(ROOT / "src" / "agent_mem_bridge" / "server.py")

    assert len(current_tools) == 17
    assert current_tools == EXPECTED_PUBLIC_TOOLS
    assert len(first_run_cases) == 4
    for case in first_run_cases:
        checks = case["checks"]
        assert checks["renderer_returns_fragment_without_memory_write"] is True
        assert checks["no_mutation"] is True
        assert checks["no_private_user_path"] is True
        assert checks["placeholder_safe"] is True
        assert checks["current_public_surface_contract_pass"] is (current_tools == EXPECTED_PUBLIC_TOOLS)
        assert "manual_copy_only" not in checks
        assert "amh_not_required" not in checks
        assert case["observations"]["retired_historical_assertions"] == [
            "manual_copy_only",
            "amh_not_required",
        ]


def test_v019_first_run_pass_criteria_are_not_literal_true_constants() -> None:
    source = textwrap.dedent(inspect.getsource(v019_adoption_proof._first_run_case))
    function = ast.parse(source).body[0]
    checks_assignment = next(
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "checks" for target in node.targets)
    )

    assert isinstance(checks_assignment.value, ast.Dict)
    assert all(
        not (isinstance(value, ast.Constant) and value.value is True) for value in checks_assignment.value.values
    )


def test_v019_current_surface_check_fails_when_server_tools_do_not_match_contract(monkeypatch) -> None:
    monkeypatch.setattr(v019_adoption_proof, "load_server_tool_names", lambda _: set())

    assert v019_adoption_proof._current_public_surface_matches_contract() is False


def test_v019_stays_out_of_public_mcp_surface() -> None:
    tool_names = load_server_tool_names(ROOT / "src" / "agent_mem_bridge" / "server.py")

    assert tool_names == EXPECTED_PUBLIC_TOOLS
    assert "v019_adoption_proof" not in tool_names
    assert "first_run" not in tool_names
    assert "task_brief" not in tool_names


def test_v019_adoption_proof_runner_writes_stable_report(tmp_path: Path) -> None:
    report_path = tmp_path / "v019-report.json"

    completed = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--report-path", str(report_path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["summary"]["v019_case_count"] == 12
    assert report["summary"]["v019_pass_rate"] == 1.0
    before = report_path.read_bytes()

    subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--report-path", str(report_path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )

    assert report_path.read_bytes() == before
