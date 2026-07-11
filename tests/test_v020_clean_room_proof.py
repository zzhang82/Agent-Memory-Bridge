from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from agent_mem_bridge.release_contract import load_pyproject_version, load_server_tool_names
from agent_mem_bridge.v020_clean_room_proof import (
    EXPECTED_PUBLIC_TOOL_COUNT,
    PROOF_KIND,
    V020_CLEAN_ROOM_PROOF_SCHEMA,
    V020_CASE_MANIFEST,
    _contains_private_path,
    run_v020_clean_room_proof,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "run_v020_clean_room_proof.py"
EXPECTED_PUBLIC_TOOLS = {
    "ack_signal",
    "browse",
    "claim_signal",
    "extend_signal_lease",
    "export",
    "forget",
    "promote",
    "recall",
    "stats",
    "store",
}


def test_v020_clean_room_proof_runs_against_fresh_temp_store_and_stdio(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"

    report = run_v020_clean_room_proof(
        project_root=ROOT,
        runtime_dir=runtime_dir,
        generated_at="2026-07-07T00:00:00+00:00",
    )
    summary = report["summary"]
    project_version = load_pyproject_version(ROOT / "pyproject.toml")

    assert report["schema"] == V020_CLEAN_ROOM_PROOF_SCHEMA
    assert report["release"] == project_version
    assert report["proof_kind"] == PROOF_KIND
    assert summary["v020_ok"] is True
    assert summary["v020_case_count"] == len(V020_CASE_MANIFEST)
    assert summary["v020_pass_count"] == len(V020_CASE_MANIFEST)
    assert summary["v020_pass_rate"] == 1.0
    assert summary["v020_import_sanity_pass"] is True
    assert summary["v020_stdio_round_trip_pass"] is True
    assert summary["v020_explicit_demo_memory_write_count"] == 1
    assert summary["v020_explicit_demo_signal_write_count"] == 0
    assert summary["v020_non_demo_durable_writeback_count"] == 0
    assert summary["v020_public_mcp_tool_count"] == EXPECTED_PUBLIC_TOOL_COUNT
    assert summary["v020_public_mcp_surface_change"] is False
    assert summary["v020_client_config_write_count"] == 0
    assert summary["v020_amh_required"] is False
    assert summary["v020_external_vendor_adoption_claim"] is False
    assert report["environment"]["project_root"] == "<repo>"
    assert report["environment"]["package_version"] == project_version
    assert report["environment"]["package_version_source"] == "pyproject.toml"
    assert report["runtime"]["db_path"] == "<temp>/home/bridge.db"
    assert report["runtime"]["config_path"] == "<temp>/home/config.toml"
    assert report["runtime"]["config_path_exists_after"] is False

    assert report["stdio"]["entrypoint"]["command"] == "python -m agent_mem_bridge"
    assert set(report["stdio"]["tool_names"]) == EXPECTED_PUBLIC_TOOLS
    assert report["stdio"]["store_stored"] is True
    assert report["stdio"]["stored_id_recalled"] is True
    assert "stdio" in report["stdio"]["recalled_client_transports"]

    case_ids = [case["id"] for case in report["cases"]]
    assert case_ids == [case["id"] for case in V020_CASE_MANIFEST]
    entrypoint_case = next(case for case in report["cases"] if case["id"] == "v020-local-entrypoint-import")
    assert entrypoint_case["evidence"]["version"] == project_version
    assert all(case["passed"] is True for case in report["cases"])
    assert all(case["failure_reason"] == "" for case in report["cases"])


def test_v020_first_run_and_task_brief_cli_outputs_are_rendered_without_mutation(tmp_path: Path) -> None:
    report = run_v020_clean_room_proof(
        project_root=ROOT,
        runtime_dir=tmp_path / "runtime",
        generated_at="2026-07-07T00:00:00+00:00",
    )
    first_run = report["cli_reports"]["first_run"]
    task_brief = report["cli_reports"]["task_brief"]

    assert first_run["schema"] == "memory.first_run.v1"
    assert first_run["client_config_write_mode"] == "manual_copy_only"
    assert first_run["amh_required"] is False
    assert first_run["contains_private_path"] is False

    assert task_brief["schema"] == "memory.task_brief.v1"
    assert task_brief["used_count"] >= 1
    assert task_brief["public_mcp_surface_change"] is False
    assert task_brief["no_auto_writeback"] is True
    assert task_brief["contains_private_path"] is False
    assert report["runtime"]["table_count_delta_from_cli_reports"] == 0


def test_v020_runner_writes_json_report_and_markdown_transcript(tmp_path: Path) -> None:
    report_path = tmp_path / "v020-report.json"
    transcript_path = tmp_path / "v020-transcript.md"

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--runtime-dir",
            str(tmp_path / "runtime"),
            "--report-path",
            str(report_path),
            "--transcript-path",
            str(transcript_path),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(report_path.read_text(encoding="utf-8"))
    transcript = transcript_path.read_text(encoding="utf-8")
    project_version = load_pyproject_version(ROOT / "pyproject.toml")
    assert report["summary"]["v020_ok"] is True
    assert report["release"] == project_version
    assert report["environment"]["package_version"] == project_version
    assert report["summary"]["v020_client_config_write_count"] == 0
    assert report["summary"]["v020_external_vendor_adoption_claim"] is False
    assert "# v0.20 Clean-Room Adoption Proof" in transcript
    assert f"- release: `{project_version}`" in transcript
    assert "## Stdio MCP Evidence" in transcript
    assert "## CLI Report Evidence" in transcript
    assert "## Boundary" in transcript
    assert str(tmp_path) not in transcript


def test_v020_proof_rejects_nonempty_runtime_dir(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    (runtime_dir / "existing.txt").write_text("not clean\n", encoding="utf-8")

    with pytest.raises(ValueError, match="runtime_dir must be absent or empty"):
        run_v020_clean_room_proof(project_root=ROOT, runtime_dir=runtime_dir)


def test_v020_stays_out_of_public_mcp_surface() -> None:
    tool_names = load_server_tool_names(ROOT / "src" / "agent_mem_bridge" / "server.py")

    assert tool_names == EXPECTED_PUBLIC_TOOLS
    assert "v020_clean_room_proof" not in tool_names
    assert "clean_room_proof" not in tool_names


@pytest.mark.parametrize(
    "value",
    [
        r"C:\Users\example\project",
        r"D:\workspace\project",
        r"\\wsl.localhost\Distribution\home\example",
        "/home/example/project",
        "/mnt/d/workspace/project",
        json.dumps({"path": r"C:\Users\example\project"}),
    ],
)
def test_v020_private_path_detection_is_generic(value: str) -> None:
    assert _contains_private_path(value) is True
