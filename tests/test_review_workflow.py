from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from agent_mem_bridge.learning_policy import evaluate_learning_candidate
from agent_mem_bridge.review_workflow import build_review_workflow_report, run_review_workflow_benchmark
from agent_mem_bridge.storage import MemoryStore

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "run_review_workflow_benchmark.py"


def _candidate(**overrides):
    candidate = {
        "schema": "memory.candidate.v1",
        "namespace": "project:review-workflow-test",
        "authority_class": "context_hint",
        "claim": "Review workflow should keep hidden candidates behind human approval.",
        "evidence_refs": ["pytest: tests/test_review_workflow.py"],
        "source_runtime": "pytest",
        "source_session_id": "session-1",
        "source_task_id": "task-1",
        "confidence": 0.82,
        "domain_tags": ["domain:memory-governance"],
        "sensitivity": "safe",
    }
    candidate.update(overrides)
    return candidate


def test_review_workflow_benchmark_covers_human_steps_without_auto_writeback() -> None:
    report = run_review_workflow_benchmark()
    summary = report["summary"]

    assert report["schema"] == "memory.review_workflow_benchmark.v1"
    assert summary["review_workflow_source_queue_item_count"] == 6
    assert summary["review_workflow_item_count"] == 6
    assert summary["review_workflow_manual_step_count"] == 27
    assert summary["review_workflow_requires_human_count"] == 6
    assert summary["review_workflow_auto_write_count"] == 0
    assert summary["review_workflow_no_auto_writeback"] is True
    assert summary["review_workflow_public_mcp_surface_change"] is False
    assert summary["review_workflow_item_type_count"] == 6
    assert "items" not in report


def test_review_workflow_turns_candidate_into_manual_decision_plan(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    candidate = _candidate()
    store.store_learning_candidate(candidate, evaluate_learning_candidate(candidate))

    report = build_review_workflow_report(store, namespace="project:review-workflow-test", generated_at="fixed")

    assert report["schema"] == "memory.review_workflow.v1"
    assert report["mutation_boundary"] == "proposal_only_no_auto_writeback"
    assert report["public_mcp_surface_change"] is False
    assert report["summary"]["workflow_item_count"] == 1
    assert report["summary"]["workflow_auto_write_count"] == 0

    item = report["items"][0]
    assert item["item_type"] == "learning_candidate"
    assert item["requires_human_review"] is True
    assert item["auto_writeback_allowed"] is False
    assert item["blocked_until"] == "human_decision_recorded"
    assert "recall_related_records_for_dedup_or_conflict" in item["manual_steps"]
    assert item["allowed_outcomes"] == ["learn", "merge", "reject", "keep_staged"]


def test_review_workflow_cli_renders_json_from_env_store(tmp_path: Path) -> None:
    db_path = tmp_path / "bridge.db"
    log_dir = tmp_path / "logs"
    store = MemoryStore(db_path, log_dir=log_dir)
    candidate = _candidate()
    store.store_learning_candidate(candidate, evaluate_learning_candidate(candidate))

    env = os.environ.copy()
    env["AGENT_MEMORY_BRIDGE_DB_PATH"] = str(db_path)
    env["AGENT_MEMORY_BRIDGE_LOG_DIR"] = str(log_dir)

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "agent_mem_bridge",
            "review-workflow",
            "--namespace",
            "project:review-workflow-test",
            "--format",
            "json",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["schema"] == "memory.review_workflow.v1"
    assert payload["summary"]["workflow_item_count"] == 1
    assert payload["items"][0]["writeback_boundary"] == "proposal_only_no_auto_writeback"


def test_review_workflow_benchmark_runner_writes_stable_summary(tmp_path: Path) -> None:
    report_path = tmp_path / "review-workflow-report.json"

    completed = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--report-path", str(report_path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["summary"]["review_workflow_item_count"] == 6
    before = report_path.read_bytes()

    subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--report-path", str(report_path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )

    assert report_path.read_bytes() == before
