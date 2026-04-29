from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from agent_mem_bridge.signal_contention_benchmark import run_signal_contention_benchmark


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "run_signal_contention_benchmark.py"


def test_signal_contention_benchmark_covers_core_lifecycle_edges() -> None:
    report = run_signal_contention_benchmark()

    assert {case["id"] for case in report["cases"]} == {
        "unique-active-claims",
        "claim-is-not-renew",
        "stale-reclaim-before-ack",
        "pending-not-starved-by-active-claims",
        "initial-claim-hard-expiry-cap",
    }


def test_signal_contention_benchmark_passes_local_contention_contract() -> None:
    report = run_signal_contention_benchmark()
    summary = report["summary"]

    assert summary["case_count"] == 5
    assert summary["case_pass_rate"] == 1.0
    assert summary["unique_active_claim_rate"] == 1.0
    assert summary["duplicate_active_claim_count"] == 0
    assert summary["active_reclaim_block_rate"] == 1.0
    assert summary["stale_ack_blocked_rate"] == 1.0
    assert summary["stale_reclaim_success_rate"] == 1.0
    assert summary["pending_under_pressure_claim_rate"] == 1.0
    assert summary["initial_hard_expiry_cap_rate"] == 1.0
    assert "not a scheduler" in report["metadata"]["notes"]


def test_signal_contention_runner_writes_report(tmp_path: Path) -> None:
    report_path = tmp_path / "signal-contention-report.json"

    completed = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--report-path", str(report_path)],
        check=True,
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    written = json.loads(report_path.read_text(encoding="utf-8"))
    printed = json.loads(completed.stdout)
    assert written["summary"]["case_pass_rate"] == 1.0
    assert printed["summary"] == written["summary"]
