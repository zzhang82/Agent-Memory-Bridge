from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from agent_mem_bridge.memory_evolution_benchmark import (
    DEFAULT_CASES_PATH,
    evaluate_memory_evolution_case,
    load_memory_evolution_cases,
    run_memory_evolution_benchmark,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "run_memory_evolution_benchmark.py"


def test_bundled_memory_evolution_cases_cover_reviewed_revision_slice() -> None:
    suite = load_memory_evolution_cases(DEFAULT_CASES_PATH)
    scenarios = {case["scenario"] for case in suite["cases"]}

    assert len(suite["cases"]) == 6
    assert scenarios == {
        "reviewed revision lineage",
        "forgetting audit without retained deleted content",
        "untrusted provenance quarantine",
        "principal scope warning",
        "point-in-time validity",
        "hidden review lane",
    }


def test_memory_evolution_benchmark_passes_without_live_state() -> None:
    report = run_memory_evolution_benchmark()
    summary = report["summary"]

    assert summary["case_count"] == 6
    assert summary["task_count"] == 7
    assert summary["raw_task_pass_rate"] < summary["governed_task_pass_rate"]
    assert summary["governed_task_pass_rate"] == 1.0
    assert summary["governed_blocked_record_leak_rate"] == 0.0
    assert summary["governed_disposition_reason_hit_rate"] == 1.0
    assert summary["governed_required_warning_hit_rate"] == 1.0
    assert report["metadata"]["comparison"] == "raw_fixture_visibility_vs_reviewed_evolution_governance"
    assert "does not query live bridge state" in report["metadata"]["notes"]
    assert "certify poisoning resistance" in report["metadata"]["notes"]


def test_memory_evolution_governance_blocks_quarantine_and_principal_scope() -> None:
    case = {
        "id": "scope-and-quarantine",
        "records": [
            {
                "id": "maintainer-only",
                "record_type": "learn",
                "governance_status": "current",
                "applies_to_principals": ["maintainer-runtime"],
            },
            {
                "id": "quarantined",
                "record_type": "learn",
                "governance_status": "current",
                "quarantine_status": "quarantined",
            },
            {
                "id": "shared",
                "record_type": "learn",
                "governance_status": "current",
                "applies_to_principals": ["hermes"],
            },
        ],
        "tasks": [
            {
                "id": "hermes-view",
                "principal": "hermes",
                "expectations": {
                    "required_visible_ids": ["shared"],
                    "blocked_ids": ["maintainer-only", "quarantined"],
                    "required_block_reasons": [
                        {"id": "maintainer-only", "reason": "principal-scope-mismatch"},
                        {"id": "quarantined", "reason": "quarantine:quarantined"},
                    ],
                },
            }
        ],
    }

    result = evaluate_memory_evolution_case(case, as_of=datetime(2026, 6, 23, tzinfo=UTC))
    task = result["task_results"][0]

    assert task["governed"]["visible_ids"] == ["shared"]
    assert task["governed"]["score"]["case_passed"] is True
    assert {item["id"]: item["reason"] for item in task["governed"]["blocked"]} == {
        "maintainer-only": "principal-scope-mismatch",
        "quarantined": "quarantine:quarantined",
    }


def test_memory_evolution_governance_requires_principal_for_scoped_records() -> None:
    case = {
        "id": "scope-required",
        "records": [
            {
                "id": "maintainer-only",
                "record_type": "learn",
                "governance_status": "current",
                "applies_to_principals": ["maintainer-runtime"],
            },
            {
                "id": "unscoped",
                "record_type": "learn",
                "governance_status": "current",
            },
        ],
        "tasks": [
            {
                "id": "no-principal",
                "expectations": {
                    "required_visible_ids": ["unscoped"],
                    "blocked_ids": ["maintainer-only"],
                    "required_block_reasons": [
                        {"id": "maintainer-only", "reason": "principal-scope-required"},
                    ],
                },
            }
        ],
    }

    result = evaluate_memory_evolution_case(case, as_of=datetime(2026, 6, 23, tzinfo=UTC))
    task = result["task_results"][0]

    assert task["governed"]["visible_ids"] == ["unscoped"]
    assert task["governed"]["score"]["case_passed"] is True
    assert task["governed"]["blocked"] == [
        {"id": "maintainer-only", "reason": "principal-scope-required"}
    ]


def test_review_lane_is_hidden_unless_explicitly_requested() -> None:
    case = {
        "id": "review-lane",
        "records": [
            {
                "id": "candidate",
                "record_type": "learning-candidate",
                "tags": ["kind:learning-candidate"],
            },
            {
                "id": "review",
                "record_type": "learning-review",
                "tags": ["kind:learning-review"],
            },
            {
                "id": "durable",
                "record_type": "learn",
            },
        ],
        "tasks": [
            {
                "id": "normal",
                "expectations": {
                    "required_visible_ids": ["durable"],
                    "blocked_ids": ["candidate", "review"],
                },
            },
            {
                "id": "review-mode",
                "review_mode": True,
                "expectations": {
                    "required_visible_ids": ["candidate", "review", "durable"],
                    "blocked_ids": [],
                },
            },
        ],
    }

    result = evaluate_memory_evolution_case(case, as_of=datetime(2026, 6, 23, tzinfo=UTC))

    assert result["task_results"][0]["governed"]["visible_ids"] == ["durable"]
    assert result["task_results"][1]["governed"]["visible_ids"] == ["candidate", "review", "durable"]


def test_memory_evolution_benchmark_runner_writes_report(tmp_path: Path) -> None:
    report_path = tmp_path / "memory-evolution-report.json"

    completed = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--report-path", str(report_path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["summary"]["governed_task_pass_rate"] == 1.0
    assert "review-lane-hidden" in report["summary"]["blocked_reason_counts"]
