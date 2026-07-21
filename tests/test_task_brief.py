from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agent_mem_bridge.learning_policy import evaluate_learning_candidate
from agent_mem_bridge.onboarding import TOOL_NAMES
from agent_mem_bridge.release_contract import load_server_tool_names
from agent_mem_bridge.storage import MemoryStore
from agent_mem_bridge.task_brief import build_task_brief_report, render_task_brief_markdown, run_task_brief_benchmark

ROOT = Path(__file__).resolve().parents[1]
NAMESPACE = "project:task-brief-test"
SCRIPT_PATH = ROOT / "scripts" / "run_task_brief_benchmark.py"
EXPECTED_PUBLIC_TOOLS = TOOL_NAMES


def _candidate(**overrides):
    candidate = {
        "schema": "memory.candidate.v1",
        "namespace": NAMESPACE,
        "authority_class": "context_hint",
        "claim": "Task Brief review items should stay proposal-only until an operator reviews them.",
        "evidence_refs": ["pytest: tests/test_task_brief.py"],
        "source_runtime": "pytest",
        "source_session_id": "session-1",
        "source_task_id": "task-1",
        "confidence": 0.82,
        "domain_tags": ["domain:memory-governance"],
        "sensitivity": "safe",
    }
    candidate.update(overrides)
    return candidate


def test_task_brief_groups_used_ignored_and_needs_review_without_memory_mutation(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    expired_until = (datetime.now(UTC) - timedelta(days=1)).isoformat()

    old_belief = store.store(
        namespace="global",
        kind="memory",
        title="[[Belief]] optional release handoff owner",
        content="record_type: belief\nclaim: Release handoff owner assignment is optional.\n",
        tags=["kind:belief", "domain:release", "topic:handoff"],
    )
    current_belief = store.store(
        namespace="global",
        kind="memory",
        title="[[Belief]] explicit release handoff owner",
        content=(
            "record_type: belief\n"
            "claim: Release handoff owner must be explicit before execution.\n"
            f"contradicts: {old_belief['id']}\n"
        ),
        tags=["kind:belief", "domain:release", "topic:handoff"],
    )
    store.store(
        namespace=NAMESPACE,
        kind="memory",
        title="[[Procedure]] expired release handoff path",
        content=(
            "record_type: procedure\n"
            "goal: Run the expired release handoff path.\n"
            "steps: skip owner | merge release\n"
            f"valid_until: {expired_until}\n"
        ),
        tags=["kind:procedure", "domain:release", "topic:handoff"],
    )
    store.store(
        namespace=NAMESPACE,
        kind="memory",
        title="[[Procedure]] current release handoff path",
        content=(
            "record_type: procedure\n"
            "goal: Run the current release handoff path.\n"
            "steps: assign owner | confirm queue | tag release\n"
            f"depends_on: {current_belief['id']}\n"
        ),
        tags=["kind:procedure", "domain:release", "topic:handoff"],
    )
    candidate = _candidate()
    store.store_learning_candidate(candidate, evaluate_learning_candidate(candidate))
    store.store(
        namespace=NAMESPACE,
        kind="signal",
        title="Release note review ready",
        content="Task Brief signal: release note review ready.",
        tags=["domain:release"],
    )

    before_stats = store.stats(NAMESPACE)
    before_table_counts = _table_counts(db_path=store.db_path)
    report = build_task_brief_report(store, query="release handoff", namespace=NAMESPACE, generated_at="fixed")
    after_stats = store.stats(NAMESPACE)
    after_table_counts = _table_counts(db_path=store.db_path)

    assert before_stats == after_stats
    assert before_table_counts == after_table_counts
    assert report["schema"] == "memory.task_brief.v1"
    assert report["mutation_boundary"] == "read_only_report_no_auto_writeback"
    assert report["writeback_boundary"] == "proposal_only_no_auto_writeback"
    assert report["public_mcp_surface_change"] is False
    assert report["summary"]["task_brief_no_auto_writeback"] is True

    used_titles = {item["title"] for item in report["sections"]["used"]}
    assert "[[Procedure]] current release handoff path" in used_titles
    assert "[[Belief]] explicit release handoff owner" in used_titles

    ignored_reasons = {reason for item in report["sections"]["ignored"] for reason in item["reason_codes"]}
    assert "validity:expired" in ignored_reasons

    needs_review = report["sections"]["needs_review"]
    needs_review_sources = {item["source"] for item in needs_review}
    assert {"review_queue", "signal", "task_memory"} <= needs_review_sources
    assert any("contradicted" in item["reason_codes"] for item in needs_review)
    assert report["summary"]["review_queue_item_count"] >= 1
    assert (
        report["summary"]["needs_review_source_counts"]["review_queue"] == report["summary"]["review_queue_item_count"]
    )
    assert report["summary"]["active_signal_count"] == 1

    markdown = render_task_brief_markdown(report)
    assert "## Used" in markdown
    assert "## Ignored" in markdown
    assert "## Needs Review" in markdown


def test_task_brief_benchmark_covers_sections_without_auto_writeback() -> None:
    report = run_task_brief_benchmark()
    summary = report["summary"]

    assert report["schema"] == "memory.task_brief_benchmark.v1"
    assert summary["task_brief_used_count"] == 2
    assert summary["task_brief_ignored_count"] == 1
    assert summary["task_brief_needs_review_count"] == 4
    assert summary["task_brief_review_queue_item_count"] == 2
    assert summary["task_brief_active_signal_count"] == 1
    assert summary["task_brief_no_auto_writeback"] is True
    assert summary["task_brief_public_mcp_surface_change"] is False
    assert summary["task_brief_needs_review_source_type_count"] == 3
    assert "items" not in report


def test_task_brief_report_does_not_call_mutating_store_methods(tmp_path: Path, monkeypatch) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    store.store(
        namespace=NAMESPACE,
        kind="memory",
        title="[[Procedure]] current release handoff path",
        content=(
            "record_type: procedure\n"
            "goal: Run the current release handoff path.\n"
            "steps: assign owner | confirm queue | tag release\n"
        ),
        tags=["kind:procedure", "domain:release", "topic:handoff"],
    )

    def fail_mutation(*_args, **_kwargs):
        raise AssertionError("Task Brief generation must not call mutating AMB store methods")

    for method_name in (
        "store",
        "store_memory",
        "forget",
        "promote",
        "claim_signal",
        "ack_signal",
        "extend_signal_lease",
        "store_learning_candidate",
        "store_learning_review",
    ):
        monkeypatch.setattr(MemoryStore, method_name, fail_mutation)

    report = build_task_brief_report(store, query="release handoff", namespace=NAMESPACE, generated_at="fixed")

    assert report["summary"]["task_brief_no_auto_writeback"] is True
    assert report["public_mcp_surface_change"] is False


def test_task_brief_stays_cli_only_not_mcp_tool() -> None:
    tool_names = load_server_tool_names(ROOT / "src" / "agent_mem_bridge" / "server.py")

    assert tool_names == EXPECTED_PUBLIC_TOOLS
    assert "task_brief" not in tool_names
    assert "task-brief" not in tool_names


def test_task_brief_cli_renders_json_from_env_store(tmp_path: Path) -> None:
    db_path = tmp_path / "bridge.db"
    log_dir = tmp_path / "logs"
    store = MemoryStore(db_path, log_dir=log_dir)
    store.store(
        namespace=NAMESPACE,
        kind="memory",
        title="[[Procedure]] current release handoff path",
        content=(
            "record_type: procedure\n"
            "goal: Run the current release handoff path.\n"
            "steps: assign owner | confirm queue | tag release\n"
        ),
        tags=["kind:procedure", "domain:release", "topic:handoff"],
    )

    env = os.environ.copy()
    env["AGENT_MEMORY_BRIDGE_DB_PATH"] = str(db_path)
    env["AGENT_MEMORY_BRIDGE_LOG_DIR"] = str(log_dir)

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "agent_mem_bridge",
            "task-brief",
            "--namespace",
            NAMESPACE,
            "--query",
            "release handoff",
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
    assert payload["schema"] == "memory.task_brief.v1"
    assert payload["query"] == "release handoff"
    assert payload["summary"]["used_count"] == 1
    assert payload["sections"]["used"][0]["source_section"] == "procedure_hits"


def test_task_brief_benchmark_runner_writes_stable_summary(tmp_path: Path) -> None:
    report_path = tmp_path / "task-brief-report.json"

    completed = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--report-path", str(report_path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["summary"]["task_brief_used_count"] == 2
    before = report_path.read_bytes()

    subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--report-path", str(report_path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )

    assert report_path.read_bytes() == before


def _table_counts(*, db_path: Path) -> dict[str, int]:
    with sqlite3.connect(db_path) as conn:
        table_names = [
            row[0]
            for row in conn.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table'
                  AND name NOT LIKE 'sqlite_%'
                ORDER BY name
                """
            )
        ]
        return {name: int(conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]) for name in table_names}
