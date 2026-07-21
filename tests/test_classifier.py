from __future__ import annotations

import sys
from pathlib import Path

import pytest

from agent_mem_bridge.classifier import Classification, ClassifierConfig, EnrichmentCandidate, EnrichmentClassifier


def _gateway_command() -> str:
    fixture = Path(__file__).parent / "fixtures" / "fake_classifier_gateway.py"
    return f'"{sys.executable}" "{fixture}"'


def test_command_classifier_returns_domain_and_topic_tags() -> None:
    classifier = EnrichmentClassifier(
        ClassifierConfig(
            mode="assist",
            command=_gateway_command(),
            batch_size=4,
        )
    )

    outcome = classifier.classify(
        [
            EnrichmentCandidate(key="a", text="Review handoff needs explicit ownership."),
            EnrichmentCandidate(key="b", text="SQLite WAL reduces writer contention."),
        ]
    )

    assert outcome.error is None
    assert outcome.requested_count == 2
    assert outcome.predictions["a"].domains == ("domain:orchestration",)
    assert outcome.predictions["a"].topics == ("topic:review-flow",)
    assert outcome.predictions["b"].domains == ("domain:sqlite",)
    assert outcome.predictions["b"].topics == ("topic:storage",)


def test_command_classifier_batches_multiple_requests() -> None:
    classifier = EnrichmentClassifier(
        ClassifierConfig(
            mode="shadow",
            command=_gateway_command(),
            batch_size=2,
        )
    )

    outcome = classifier.classify(
        [
            EnrichmentCandidate(key="1", text="Review handoff needs explicit ownership."),
            EnrichmentCandidate(key="2", text="SQLite WAL reduces writer contention."),
            EnrichmentCandidate(key="3", text="Context compaction helps recall."),
        ]
    )

    assert outcome.error is None
    assert outcome.requested_count == 3
    assert set(outcome.predictions) == {"1", "2", "3"}


def test_command_classifier_reports_invalid_json() -> None:
    classifier = EnrichmentClassifier(
        ClassifierConfig(
            mode="assist",
            command=f'"{sys.executable}" -c "print(\'not-json\')"',
        )
    )

    outcome = classifier.classify([EnrichmentCandidate(key="a", text="review handoff")])

    assert outcome.error is not None
    assert outcome.predictions == {}


def test_command_classifier_filters_low_confidence_tags_for_assist() -> None:
    classifier = EnrichmentClassifier(
        ClassifierConfig(
            mode="assist",
            command=_gateway_command(),
            minimum_confidence=0.6,
        )
    )

    outcome = classifier.classify(
        [EnrichmentCandidate(key="a", text="Punctuation-heavy values.yaml queries need a safe FTS fallback.")]
    )

    prediction = outcome.predictions["a"]
    assert prediction.confidence == 0.55
    assert list(prediction.tags) == ["domain:retrieval", "topic:fts"]
    assert classifier.accepted_tags(prediction) == []


def test_command_classifier_separates_reserved_suggestions_from_policy_tags() -> None:
    classifier = EnrichmentClassifier(ClassifierConfig(mode="assist", command=_gateway_command()))

    outcome = classifier.classify([EnrichmentCandidate(key="a", text="Reserved classifier tag injection attempt.")])

    prediction = outcome.predictions["a"]
    assert prediction.classifier_suggested_tags == (
        "source:reviewed",
        "confidence:human-reviewed",
        "kind:learning-review",
        "domain:security",
    )
    assert prediction.tags == ("domain:security",)
    assert classifier.accepted_tags(prediction) == ["domain:security"]


def test_classifier_defensively_rejects_reserved_tags_from_internal_classification() -> None:
    classifier = EnrichmentClassifier(ClassifierConfig(mode="assist"))
    prediction = Classification(
        key="a",
        tags=("source:reviewed", "kind:learning-review", "domain:security"),
        domains=("domain:security",),
        topics=(),
        confidence=0.95,
    )

    assert classifier.accepted_tags(prediction) == ["domain:security"]


def test_classifier_contains_malformed_collection_fields() -> None:
    classifier = EnrichmentClassifier(ClassifierConfig(mode="assist", command=_gateway_command()))

    outcome = classifier.classify(
        [EnrichmentCandidate(key="a", text="Malformed classifier collections must be rejected safely.")]
    )

    assert outcome.error is None
    assert outcome.predictions["a"].classifier_suggested_tags == ()
    assert outcome.predictions["a"].tags == ()
    assert classifier.accepted_tags(outcome.predictions["a"]) == []


@pytest.mark.parametrize("confidence", [None, float("nan"), float("inf"), -0.1, 1.1])
def test_classifier_rejects_missing_non_finite_or_out_of_range_confidence(
    confidence: float | None,
) -> None:
    classifier = EnrichmentClassifier(ClassifierConfig(mode="assist", minimum_confidence=0.6))
    prediction = Classification(
        key="a",
        tags=("domain:security",),
        domains=("domain:security",),
        topics=(),
        confidence=confidence,
    )

    assert classifier.accepted_tags(prediction) == []
    assert classifier._normalize_confidence(confidence) is None


@pytest.mark.parametrize("minimum_confidence", [float("nan"), float("inf"), -0.1, 1.1])
def test_classifier_rejects_invalid_minimum_confidence(minimum_confidence: float) -> None:
    with pytest.raises(ValueError, match="minimum_confidence"):
        EnrichmentClassifier(ClassifierConfig(mode="assist", minimum_confidence=minimum_confidence))
