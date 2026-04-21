from agent_mem_bridge.belief_review import DEFAULT_REVIEWED_SAMPLES_PATH, run_belief_review


def test_run_belief_review_matches_default_reviewed_samples() -> None:
    report = run_belief_review(reviewed_samples_path=DEFAULT_REVIEWED_SAMPLES_PATH)

    assert report["filters"]["slices"] == []
    assert report["summary"]["sample_count"] == 13
    assert report["summary"]["exact_match_count"] == 13
    assert report["summary"]["exact_match_rate"] == 1.0
    assert report["summary"]["belief_count"] == 4
    assert report["summary"]["candidate_only_count"] == 9
    assert report["summary"]["blocking_reason_counts"] == {
        "blocked-contradiction": 5,
        "blocked-low-support": 2,
        "blocked-stability": 1,
        "stale": 1,
    }
    assert all(result["match"] is True for result in report["results"])


def test_run_belief_review_slice_summaries_show_expected_shape() -> None:
    report = run_belief_review(reviewed_samples_path=DEFAULT_REVIEWED_SAMPLES_PATH)

    assert report["slice_summaries"]["contradiction-quality"]["sample_count"] == 6
    assert report["slice_summaries"]["contradiction-quality"]["belief_count"] == 3
    assert report["slice_summaries"]["contradiction-quality"]["blocking_reason_counts"] == {
        "blocked-contradiction": 3
    }
    assert report["slice_summaries"]["contradiction-watchlist"]["sample_count"] == 1
    assert report["slice_summaries"]["contradiction-watchlist"]["blocking_reason_counts"] == {
        "blocked-contradiction": 1
    }
    assert report["slice_summaries"]["startup-protocol"]["belief_count"] == 1
    assert report["slice_summaries"]["startup-protocol"]["exact_match_rate"] == 1.0
    assert report["slice_summaries"]["runtime"]["blocking_reason_counts"] == {"blocked-contradiction": 1}
    assert report["slice_summaries"]["maintenance"]["blocking_reason_counts"] == {"stale": 1}
    assert report["slice_summaries"]["memory-shaping"]["blocking_reason_counts"] == {"blocked-low-support": 1}


def test_run_belief_review_can_filter_to_specific_slice() -> None:
    report = run_belief_review(
        reviewed_samples_path=DEFAULT_REVIEWED_SAMPLES_PATH,
        slices=("contradiction-quality",),
    )

    assert report["filters"]["slices"] == ["contradiction-quality"]
    assert report["summary"]["sample_count"] == 6
    assert report["summary"]["exact_match_count"] == 6
    assert report["summary"]["belief_count"] == 3
    assert report["summary"]["candidate_only_count"] == 3
    assert report["summary"]["blocking_reason_counts"] == {"blocked-contradiction": 3}
    assert set(report["slice_summaries"]) == {"contradiction-quality"}
    assert all(result["slice"] == "contradiction-quality" for result in report["results"])
    assert any(result["id"] == "b10" and result["actual"]["belief"] is True for result in report["results"])
    assert any(result["id"] == "b11" and result["actual"]["belief"] is True for result in report["results"])
    assert any(result["id"] == "b12" and result["actual"]["belief"] is True for result in report["results"])
