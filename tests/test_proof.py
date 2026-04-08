from pathlib import Path

from agent_mem_bridge.proof import run_deterministic_proof


def test_run_deterministic_proof_returns_expected_sections() -> None:
    root = Path(__file__).resolve().parents[1]
    report = run_deterministic_proof(
        corpus_dir=root / "benchmark" / "corpus",
        questions_path=root / "benchmark" / "questions.json",
    )

    assert report["summary"]["check_count"] == 3
    assert report["summary"]["checks_passed"] == 3

    signal_report = report["signal_correctness"]
    assert signal_report["passed"] is True
    assert signal_report["checks"]["owner_can_extend_lease"] is True
    assert signal_report["checks"]["extend_rejects_wrong_consumer"] is True
    assert signal_report["checks"]["ack_rejects_wrong_consumer"] is True
    assert signal_report["checks"]["acked_signal_cannot_extend"] is True
    assert signal_report["checks"]["expired_lease_cannot_be_extended"] is True
    assert signal_report["checks"]["stale_lease_can_be_reclaimed"] is True
    assert signal_report["checks"]["fair_claim_avoids_same_consumer_reclaim_bias"] is True
    assert signal_report["checks"]["hard_expiry_caps_extended_lease"] is True

    recall_report = report["recall_latency"]
    assert recall_report["question_count"] == 11
    assert recall_report["hit_count"] == 11
    assert recall_report["avg_latency_ms"] >= 0
    assert any(item["count"] > 1 for item in recall_report["results"])

    duplicate_report = report["duplicate_suppression"]
    assert duplicate_report["attempt_count"] == 4
    assert duplicate_report["stored_count"] == 1
    assert duplicate_report["duplicate_count"] == 3
    assert duplicate_report["suppression_rate"] == 1.0
