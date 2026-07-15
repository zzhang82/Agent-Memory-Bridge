from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agent_mem_bridge.release_contract import load_server_tool_names
from agent_mem_bridge.v021_governed_change_proof import (
    DEFAULT_V021_MANIFEST_PATH,
    EXPECTED_MANIFEST_SHA256,
    FIXED_GENERATED_AT,
    V021_GOVERNED_CHANGE_PROOF_SCHEMA,
    _required_retention_checks,
    load_v021_governed_change_manifest,
    run_v021_governed_change_proof,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "run_v021_governed_change_proof.py"
CHECKED_REPORT = ROOT / "benchmark" / "latest-v0.21-governed-change-report.json"
EXPECTED_TOOLS = {
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


@pytest.fixture(scope="module")
def report() -> dict:
    return run_v021_governed_change_proof()


def test_manifest_exact_hash_and_denominators_are_enforced() -> None:
    manifest, digest = load_v021_governed_change_manifest()

    assert digest == EXPECTED_MANIFEST_SHA256
    assert manifest["case_count"] == 20
    assert sum(case["flat_baseline_hazard"]["expected"] for case in manifest["cases"]) == 17
    assert sum(len(case["checkpoints"]) for case in manifest["cases"]) == 40


def test_manifest_hash_mismatch_is_a_hard_failure(tmp_path: Path) -> None:
    changed = tmp_path / "changed-manifest.json"
    changed.write_bytes(DEFAULT_V021_MANIFEST_PATH.read_bytes() + b"\n")

    with pytest.raises(ValueError, match="SHA256 mismatch"):
        load_v021_governed_change_manifest(changed)


def test_governed_change_proof_meets_all_fixed_gates(report: dict) -> None:
    summary = report["summary"]

    assert report["schema"] == V021_GOVERNED_CHANGE_PROOF_SCHEMA
    assert report["release"] == "0.21.0"
    assert report["target_release"] == "0.21.0"
    assert report["generated_at"] == FIXED_GENERATED_AT
    assert summary["gate_passed"] is True
    assert summary["case_count"] == 20
    assert summary["flat_baseline_hazards"] == 17
    assert summary["flat_baseline_expectation_matches"] is True
    assert summary["governed_failures"] == 0
    assert summary["governed_checkpoint_result_count"] == 40
    assert summary["governed_checkpoint_passes"] == 40
    assert summary["useful_current_retention_pass"] is True
    assert summary["suppress_all_can_pass"] is False


def test_every_case_has_two_real_checkpoints_evidence_and_sanitized_write_scope(report: dict) -> None:
    for case in report["cases"]:
        assert len(case["checkpoints"]) == 2
        assert case["baseline_expectation_match"] is True
        assert case["governed_passed"] is True
        assert case["failure_reason"] is None
        assert case["write_scope"]["runtime_root"] == "<temp>"
        assert case["write_scope"]["writes_only_under_temp"] is True
        assert case["write_scope"]["config_write_count"] == 0
        assert case["write_scope"]["durable_live_writeback_count"] == 0
        for checkpoint in case["checkpoints"]:
            assert checkpoint["passed"] is True
            assert checkpoint["checks"]["required_retention_labels_declared"] is True
            assert checkpoint["checks"]["useful_current_retained"] is True
            assert checkpoint["checks"]["suppress_all_structurally_blocked"] is True
            assert (
                checkpoint["evidence"]["required_actionable_labels"]
                or checkpoint["evidence"]["required_corrective_labels"]
            )
            assert checkpoint["checks"]["equal_budget_compared"] is True
            assert checkpoint["checks"]["recall_browse_export_exercised"] is True
            assert checkpoint["failure_reason"] is None


def test_deletion_cases_prove_real_tombstone_lineage_and_index_behavior(report: dict) -> None:
    by_id = {case["id"]: case for case in report["cases"]}
    cleared = by_id["gmuc-del-01-forget-clears-primary-fts-embedding"]["checkpoints"][1]
    redacted = by_id["gmuc-del-02-tombstone-redacts-forgotten-content"]["checkpoints"][1]
    inverse_retirement = by_id["gmuc-del-03-tombstone-prevents-predecessor-resurrection"]
    dependent = by_id["gmuc-del-04-deleted-dependency-demotes-procedure"]["checkpoints"][1]
    rebuilt = by_id["gmuc-del-05-rebuild-cannot-resurrect-forgotten-record"]["checkpoints"][1]

    assert cleared["checks"]["primary_lookup_state_correct"] is True
    assert cleared["checks"]["fts_lookup_state_correct"] is True
    assert cleared["checks"]["embedding_lookup_state_correct"] is True
    assert cleared["checks"]["semantic_recall_state_correct"] is True
    assert redacted["checks"]["tombstone_content_columns_absent"] is True
    assert redacted["checks"]["forgotten_payload_absent_from_tombstone"] is True
    assert redacted["checks"]["forgotten_payload_absent_from_live_export"] is True
    assert redacted["checks"]["flat_forget_response_payload_observed"] is True
    before_forget, after_forget = inverse_retirement["checkpoints"]
    assert before_forget["evidence"]["storage"]["predecessor"]["relations"]["depends_on"] == []
    assert before_forget["evidence"]["storage"]["superseder"]["relations"]["supersedes"] == ["predecessor"]
    assert after_forget["checks"]["predecessor_dependency_shortcut_absent"] is True
    assert after_forget["checks"]["inverse_supersession_retirement_persisted"] is True
    assert after_forget["evidence"]["storage"]["predecessor"]["lineage_status"] == "degraded"
    assert after_forget["evidence"]["storage"]["predecessor"]["lineage_issues"] == [
        {
            "missing_record_label": "superseder",
            "root_forget_label": "superseder",
            "type": "forgotten_superseder",
        }
    ]
    assert dependent["evidence"]["storage"]["dependent-procedure"]["lineage_status"] == "degraded"
    assert rebuilt["evidence"]["storage"]["forgotten"]["primary_count"] == 0
    assert rebuilt["evidence"]["storage"]["forgotten"]["embedding_count"] == 0


def test_category_slices_and_public_surface_remain_bounded(report: dict) -> None:
    assert set(report["category_slices"]) == {
        "deletion_residue",
        "lifecycle_supersession",
        "changed_premise_usefulness",
        "cross_domain_transfer",
    }
    for category in report["category_slices"].values():
        assert category["case_count"] == 5
        assert category["governed_failures"] == 0
        assert category["governed_checkpoint_passes"] == 10
        assert category["governed_checkpoint_count"] == 10
        assert category["useful_current_retention_pass"] is True

    assert report["boundaries"]["public_mcp_tool_count"] == 10
    assert report["boundaries"]["public_mcp_surface_unchanged"] is True
    assert report["boundaries"]["config_write_count"] == 0
    assert report["boundaries"]["durable_live_writeback_count"] == 0
    assert report["boundaries"]["auto_writeback_count"] == 0
    assert report["boundaries"]["private_or_local_cole_data_used"] is False
    assert load_server_tool_names(ROOT / "src" / "agent_mem_bridge" / "server.py") == EXPECTED_TOOLS


def test_useful_current_requires_the_declared_case_labels() -> None:
    unrelated_only = _required_retention_checks(
        required_actionable={"manifest-action"},
        required_corrective=set(),
        actionable_labels={"unrelated-fallback"},
        corrective_labels=set(),
    )
    suppress_all = _required_retention_checks(
        required_actionable={"manifest-action"},
        required_corrective=set(),
        actionable_labels=set(),
        corrective_labels=set(),
    )
    undeclared = _required_retention_checks(
        required_actionable=set(),
        required_corrective=set(),
        actionable_labels={"unrelated-fallback"},
        corrective_labels=set(),
    )
    corrective = _required_retention_checks(
        required_actionable=set(),
        required_corrective={"manifest-correction"},
        actionable_labels=set(),
        corrective_labels={"manifest-correction"},
    )

    assert unrelated_only["useful_current_retained"] is False
    assert suppress_all["useful_current_retained"] is False
    assert undeclared["required_retention_labels_declared"] is False
    assert undeclared["useful_current_retained"] is False
    assert corrective["useful_current_retained"] is True


def test_checked_report_is_exact_deterministic_output(report: dict) -> None:
    assert json.loads(CHECKED_REPORT.read_text(encoding="utf-8")) == report


def test_runner_is_deterministic_and_exits_nonzero_on_manifest_gate_failure(tmp_path: Path) -> None:
    report_path = tmp_path / "report.json"
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    first = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--report-path", str(report_path)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert first.returncode == 0, first.stderr
    first_bytes = report_path.read_bytes()

    second = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--report-path", str(report_path)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert second.returncode == 0, second.stderr
    assert report_path.read_bytes() == first_bytes

    changed = tmp_path / "changed-manifest.json"
    changed.write_bytes(DEFAULT_V021_MANIFEST_PATH.read_bytes() + b"\n")
    failed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--manifest-path",
            str(changed),
            "--report-path",
            str(tmp_path / "should-not-pass.json"),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert failed.returncode != 0
    assert "SHA256 mismatch" in failed.stderr
