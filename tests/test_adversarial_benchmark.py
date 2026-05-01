from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from agent_mem_bridge.adversarial_benchmark import (
    DEFAULT_CASES_PATH,
    evaluate_adversarial_case,
    load_adversarial_cases,
    run_adversarial_benchmark,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "run_adversarial_benchmark.py"


def test_bundled_adversarial_cases_cover_governance_realism_slice() -> None:
    suite = load_adversarial_cases(DEFAULT_CASES_PATH)
    cases = suite["cases"]

    assert len(cases) == 6
    assert {case["id"] for case in cases} == {
        "stale-project-vs-current-global",
        "contradictory-gotchas",
        "same-query-different-task-intent",
        "noisy-session-summary",
        "multi-client-provenance-collision",
        "expired-validity-window",
    }
    intent_case = next(case for case in cases if case["id"] == "same-query-different-task-intent")
    assert len(intent_case["tasks"]) == 2
    assert {task["task_intent"] for task in intent_case["tasks"]} == {"implementation", "review"}


def test_adversarial_benchmark_passes_governed_slice_without_live_state() -> None:
    report = run_adversarial_benchmark()
    summary = report["summary"]

    assert summary["case_count"] == 6
    assert summary["task_count"] == 7
    assert summary["governed_task_pass_rate"] == 1.0
    assert summary["governed_required_visible_hit_rate"] == 1.0
    assert summary["governed_blocked_record_leak_rate"] == 0.0
    assert summary["governed_required_warning_hit_rate"] == 1.0
    assert summary["raw_task_pass_rate"] < summary["governed_task_pass_rate"]
    assert "does not query live bridge state" in report["metadata"]["notes"]


def test_evaluator_blocks_expired_noise_and_intent_mismatches() -> None:
    case = {
        "id": "unit",
        "query": "release checklist",
        "records": [
            {
                "id": "expired",
                "valid_until": "2026-01-01T00:00:00+00:00",
                "content": "old",
            },
            {
                "id": "noisy",
                "record_type": "session-summary",
                "noise": "high",
                "content": "too much",
            },
            {
                "id": "wrong-intent",
                "applies_to_intents": ["review"],
                "content": "review only",
            },
            {
                "id": "current",
                "governance_status": "validated",
                "content": "use me",
            },
        ],
        "tasks": [
            {
                "id": "implementation",
                "task_intent": "implementation",
                "expectations": {
                    "expected_preferred_id": "current",
                    "required_visible_ids": ["current"],
                    "blocked_ids": ["expired", "noisy", "wrong-intent"],
                },
            }
        ],
    }

    result = evaluate_adversarial_case(
        case,
        as_of=datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
    )
    governed = result["task_results"][0]["governed"]

    assert governed["visible_ids"] == ["current"]
    assert governed["score"]["case_passed"] is True
    assert governed["blocked"] == [
        {"id": "expired", "reason": "validity:expired"},
        {"id": "noisy", "reason": "noisy-session-summary"},
        {"id": "wrong-intent", "reason": "intent-mismatch"},
    ]


def test_evaluator_reports_multi_client_provenance_collision() -> None:
    case = {
        "id": "collision",
        "query": "branch status",
        "records": [
            {
                "id": "codex-note",
                "title": "Same title",
                "source_client": "codex",
                "client_session_id": "session-1",
                "client_workspace": "workspace://repo",
            },
            {
                "id": "claude-note",
                "title": "Same title",
                "source_client": "claude",
                "client_session_id": "session-1",
                "client_workspace": "workspace://repo",
            },
        ],
        "tasks": [
            {
                "id": "review",
                "task_intent": "review",
                "expectations": {
                    "required_visible_ids": ["codex-note", "claude-note"],
                    "required_warnings": ["provenance-collision"],
                },
            }
        ],
    }

    result = evaluate_adversarial_case(
        case,
        as_of=datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
    )
    governed = result["task_results"][0]["governed"]

    assert governed["visible_ids"] == ["codex-note", "claude-note"]
    assert governed["warnings"] == ["provenance-collision"]
    assert governed["score"]["case_passed"] is True


def test_adversarial_benchmark_runner_writes_report(tmp_path: Path) -> None:
    report_path = tmp_path / "adversarial-report.json"

    completed = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--report-path", str(report_path)],
        check=True,
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    written = json.loads(report_path.read_text(encoding="utf-8"))
    printed = json.loads(completed.stdout)
    assert written["summary"]["governed_task_pass_rate"] == 1.0
    assert printed["summary"] == written["summary"]
