from agent_mem_bridge.contradiction import assess_contradiction_claim, claim_counts_as_contradiction


def test_boundary_only_fallback_default_wording_is_not_contradiction() -> None:
    assert (
        claim_counts_as_contradiction(
            "But keep old startup references as fallback instead of the default operating payload."
        )
        is False
    )


def test_boundary_only_manual_policy_wording_is_not_contradiction() -> None:
    assert (
        claim_counts_as_contradiction(
            "However, keep core policy manual while belief evidence is still accumulating."
        )
        is False
    )


def test_boundary_only_project_vs_global_wording_is_not_contradiction() -> None:
    assert (
        claim_counts_as_contradiction(
            "However, keep project-specific workflow rules in project memory while the global core stays reserved for durable operating lessons."
        )
        is False
    )


def test_strong_auto_policy_warning_still_counts_as_contradiction() -> None:
    assert claim_counts_as_contradiction(
        "But belief_candidate remains a bridge layer, not a license to automate policy writing."
    )


def test_runtime_split_warning_still_counts_as_contradiction() -> None:
    assert claim_counts_as_contradiction(
        "However, keep a temporary fallback path during cutover windows."
    )


def test_assessment_reports_reason_codes_for_boundary_and_strong_cases() -> None:
    fallback = assess_contradiction_claim(
        "But keep old startup references as fallback instead of the default operating payload."
    )
    assert fallback.counts_as_contradiction is False
    assert fallback.reason_code == "boundary-exempt:fallback-default"

    strong = assess_contradiction_claim(
        "But belief_candidate remains a bridge layer, not a license to automate policy writing."
    )
    assert strong.counts_as_contradiction is True
    assert strong.reason_code == "strong-cue"
