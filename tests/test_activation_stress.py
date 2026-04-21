from agent_mem_bridge.activation_stress import (
    DEFAULT_ACTIVATION_STRESS_PACK_PATH,
    render_activation_stress_text,
    run_activation_stress_pack,
)


def test_run_activation_stress_pack_matches_default_manifest() -> None:
    report = run_activation_stress_pack(pack_path=DEFAULT_ACTIVATION_STRESS_PACK_PATH)

    assert report["summary"]["case_count"] == 16
    assert report["summary"]["pass_count"] == 16
    assert report["summary"]["pass_rate"] == 1.0
    assert report["summary"]["reviewed_case_count"] == 13
    assert report["summary"]["replay_scenario_count"] == 3
    assert report["bucket_summaries"]["promote"]["case_count"] == 4
    assert report["bucket_summaries"]["candidate"]["case_count"] == 3
    assert report["bucket_summaries"]["block"]["case_count"] == 8
    assert report["bucket_summaries"]["red-flag"]["case_count"] == 1
    assert report["bucket_summaries"]["red-flag"]["pass_count"] == 1
    assert report["cleanup_posture"]["touches_live_data"] is False
    assert report["cleanup_posture"]["runtime_cleanup"] == "automatic-temp-store-removal"
    assert len(report["cleanup_posture"]["durable_regression_ids"]) == 16
    assert all(result["match"] is True for result in report["results"])


def test_run_activation_stress_pack_can_filter_to_one_bucket() -> None:
    report = run_activation_stress_pack(
        pack_path=DEFAULT_ACTIVATION_STRESS_PACK_PATH,
        buckets=("red-flag",),
    )

    assert report["summary"]["case_count"] == 1
    assert report["summary"]["pass_count"] == 1
    assert set(report["bucket_summaries"]) == {"red-flag"}
    assert report["results"][0]["id"] == "r1"
    assert report["results"][0]["kind"] == "replay"
    assert "belief-to-domain-note-ratio" in report["results"][0]["actual"]["final_red_flags"]


def test_render_activation_stress_text_includes_summary_and_cleanup() -> None:
    report = run_activation_stress_pack(
        pack_path=DEFAULT_ACTIVATION_STRESS_PACK_PATH,
        buckets=("candidate", "red-flag"),
    )

    rendered = render_activation_stress_text(report)

    assert "Activation Stress Pack" in rendered
    assert "Summary" in rendered
    assert "Cleanup" in rendered
    assert "touches_live_data: False" in rendered
    assert "Failures" in rendered
