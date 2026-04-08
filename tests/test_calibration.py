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

    assert summary["sample_count"] == 6
    assert summary["classifier_prediction_count"] == 6
    assert summary["classifier_error"] is None
    assert summary["classifier_exact_match_count"] == 4
    assert summary["fallback_exact_match_count"] == 0
    assert summary["classifier_better_count"] > summary["fallback_better_count"]
    assert any(result["winner"] == "classifier" for result in report["results"])
    assert any(result["winner"] == "fallback" for result in report["results"])


def test_write_classifier_calibration_report_writes_json(tmp_path: Path) -> None:
    report_path = tmp_path / "calibration.json"
    report = write_classifier_calibration_report(
        report_path=report_path,
        command=_gateway_command(),
    )

    assert report_path.is_file()
    assert report["summary"]["sample_count"] == 6
