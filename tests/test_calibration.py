import sys
from pathlib import Path

from agent_mem_bridge.calibration import rate, run_classifier_calibration, tag_match_score, write_classifier_calibration_report


def _gateway_command() -> str:
    fixture = Path(__file__).parent / "fixtures" / "fake_classifier_gateway.py"
    return f'"{sys.executable}" "{fixture}"'


def test_tag_match_score_behaves_like_f1() -> None:
    assert tag_match_score(["a", "b"], ["a", "b"]) == 1.0
    assert tag_match_score(["a", "b"], ["a"]) == 0.667
    assert tag_match_score(["a"], ["b"]) == 0.0
    assert rate(3, 6) == 0.5


def test_run_classifier_calibration_reports_winners_and_exact_match_rates() -> None:
    report = run_classifier_calibration(command=_gateway_command())
    summary = report["summary"]
    slices = report["slice_summaries"]

    assert summary["sample_count"] == 16
    assert summary["classifier_prediction_count"] == 16
    assert summary["classifier_retained_prediction_count"] == 14
    assert summary["classifier_filtered_low_confidence_count"] == 2
    assert summary["classifier_error"] is None
    assert summary["classifier_exact_match_count"] == 14
    assert summary["fallback_exact_match_count"] == 1
    assert summary["classifier_avg_score"] > summary["fallback_avg_score"]
    assert summary["classifier_missing_tag_total"] < summary["fallback_missing_tag_total"]
    assert summary["classifier_extra_tag_total"] < summary["fallback_extra_tag_total"]
    assert summary["classifier_better_count"] == 13
    assert summary["fallback_better_count"] == 2
    assert summary["tie_count"] == 1
    assert summary["classifier_false_positive_sample_count"] == 0
    assert summary["fallback_false_positive_sample_count"] >= 1
    assert any(result["winner"] == "classifier" for result in report["results"])
    assert any(result["winner"] == "fallback" for result in report["results"])
    assert any(result["classifier_filtered_low_confidence"] for result in report["results"])
    assert set(slices) == {
        "coordination",
        "memory-shaping",
        "model-routing",
        "retrieval",
        "runtime",
        "storage",
    }
    assert slices["coordination"]["classifier_exact_match_rate"] == 1.0
    assert slices["retrieval"]["classifier_filtered_low_confidence_count"] == 2
    assert slices["retrieval"]["fallback_better_count"] == 2
    assert slices["memory-shaping"]["tie_count"] == 1


def test_write_classifier_calibration_report_writes_json(tmp_path: Path) -> None:
    report_path = tmp_path / "calibration.json"
    report = write_classifier_calibration_report(
        report_path=report_path,
        command=_gateway_command(),
    )

    assert report_path.is_file()
    assert report["summary"]["sample_count"] == 16
    assert "slice_summaries" in report
