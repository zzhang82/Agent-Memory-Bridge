from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from agent_mem_bridge.learning_policy import evaluate_learning_candidate
from agent_mem_bridge.review_queue import build_review_queue_report, run_review_queue_benchmark
from agent_mem_bridge.storage import MemoryStore

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "run_review_queue_benchmark.py"


def _candidate(**overrides):
    candidate = {
        "schema": "memory.candidate.v1",
        "namespace": "project:review-queue-test",
        "authority_class": "context_hint",
        "claim": "Review queue candidates must stay hidden until an operator reviews them.",
        "evidence_refs": ["pytest: tests/test_review_queue.py"],
        "source_runtime": "pytest",
        "source_session_id": "session-1",
        "source_task_id": "task-1",
        "confidence": 0.82,
        "domain_tags": ["domain:memory-governance"],
        "sensitivity": "safe",
    }
    candidate.update(overrides)
    return candidate


def test_review_queue_benchmark_covers_operator_workflow() -> None:
    report = run_review_queue_benchmark()
    summary = report["summary"]

    assert report["schema"] == "memory.review_queue_benchmark.v1"
    assert summary["review_queue_item_count"] == 6
    assert summary["review_queue_actionable_count"] == 6
    assert summary["review_queue_hidden_lane_count"] == 2
    assert summary["review_queue_writeback_plan_count"] == 6
    assert summary["review_queue_item_type_count"] == 6
    assert summary["review_queue_no_auto_mutation"] is True
    assert summary["review_queue_public_mcp_surface_change"] is False
    assert "report" not in report


def test_review_queue_reports_hidden_candidate_without_making_it_recallable(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    candidate = _candidate()
    store.store_learning_candidate(candidate, evaluate_learning_candidate(candidate))

    before_recall = store.recall(namespace="project:review-queue-test", query="review queue candidates", limit=10)
    before_stats = store.stats("project:review-queue-test")

    report = build_review_queue_report(store, namespace="project:review-queue-test", generated_at="fixed")

    after_recall = store.recall(namespace="project:review-queue-test", query="review queue candidates", limit=10)
    after_stats = store.stats("project:review-queue-test")

    assert before_recall["count"] == 0
    assert after_recall["count"] == 0
    assert before_stats["total_count"] == 0
    assert after_stats["total_count"] == 0
    assert report["mutation_boundary"] == "read_only_report_no_auto_writeback"
    assert report["public_mcp_surface_change"] is False
    assert report["summary"]["total_items"] == 1
    item = report["items"][0]
    assert item["item_type"] == "learning_candidate"
    assert item["hidden_from_normal_recall"] is True
    assert item["writeback_plan"]["boundary"] == "proposal_only_no_auto_mutation"


def test_review_queue_marks_review_receipt_and_disposition_items(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    candidate = _candidate()
    decision = evaluate_learning_candidate(candidate)
    stored_candidate = store.store_learning_candidate(candidate, decision)
    store.store_learning_review(
        {
            "namespace": "project:review-queue-test",
            "candidate_ref": decision["candidate_ref"],
            "source_candidate_id": stored_candidate["id"],
            "review_decision": "approved",
            "reviewed_by": "reviewer-a",
            "review_reason": "Approved, but no durable target was created yet.",
            "target_record_type": "learn",
            "recommended_action": "learn",
            "reason_codes": [],
            "evidence_refs": candidate["evidence_refs"],
        }
    )
    store.store(
        namespace="project:review-queue-test",
        kind="memory",
        title="Deleted memory tombstone target",
        content="record_type: learn\nclaim: Deleted record is no longer authority.\ngovernance_status: deleted",
        tags=["domain:test"],
    )

    report = build_review_queue_report(store, namespace="project:review-queue-test", generated_at="fixed")
    by_type = {item["item_type"]: item for item in report["items"]}

    assert by_type["learning_review"]["recommended_action"] == "complete_review_writeback_or_keep_staged"
    assert by_type["learning_review"]["status"] == "open"
    assert by_type["governance_disposition"]["recommended_action"] == "confirm_not_authority"
    assert by_type["governance_disposition"]["reason_codes"] == ["governance:deleted"]


def test_review_queue_cli_renders_json_from_env_store(tmp_path: Path) -> None:
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
            "review-queue",
            "--namespace",
            "project:review-queue-test",
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
    assert payload["schema"] == "memory.review_queue.v1"
    assert payload["summary"]["total_items"] == 1
    assert payload["items"][0]["item_type"] == "learning_candidate"


def test_review_queue_benchmark_runner_writes_stable_summary(tmp_path: Path) -> None:
    report_path = tmp_path / "review-queue-report.json"

    completed = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--report-path", str(report_path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["summary"]["review_queue_item_count"] == 6
    before = report_path.read_bytes()

    subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--report-path", str(report_path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )

    assert report_path.read_bytes() == before
