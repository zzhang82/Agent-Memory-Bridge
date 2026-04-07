from pathlib import Path

from agent_mem_bridge.proof import run_deterministic_proof


def test_run_deterministic_proof_returns_expected_sections() -> None:
    report = run_deterministic_proof(
        corpus_dir=Path("D:/playground/MCPs/mem-store/cole-mem-bridge/benchmark/corpus"),
        questions_path=Path("D:/playground/MCPs/mem-store/cole-mem-bridge/benchmark/questions.json"),
    )

    assert report["summary"]["check_count"] == 3
    assert report["summary"]["checks_passed"] == 3

    signal_report = report["signal_correctness"]
    assert signal_report["passed"] is True
    assert signal_report["checks"]["ack_rejects_wrong_consumer"] is True
    assert signal_report["checks"]["stale_lease_can_be_reclaimed"] is True

    recall_report = report["recall_latency"]
    assert recall_report["question_count"] == 5
    assert recall_report["hit_count"] == 5
    assert recall_report["avg_latency_ms"] >= 0

    duplicate_report = report["duplicate_suppression"]
    assert duplicate_report["attempt_count"] == 4
    assert duplicate_report["stored_count"] == 1
    assert duplicate_report["duplicate_count"] == 3
    assert duplicate_report["suppression_rate"] == 1.0
