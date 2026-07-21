from hypothesis import given
from hypothesis import strategies as st

from agent_mem_bridge.classifier import Classification, ClassifierConfig, EnrichmentClassifier
from agent_mem_bridge.poll_cursor import decode_poll_cursor, encode_poll_cursor


@given(
    namespace=st.text(min_size=1).filter(lambda value: bool(value.strip())),
    sequence=st.integers(min_value=0, max_value=2**63 - 1),
)
def test_poll_cursor_round_trips_namespace_and_sequence(namespace: str, sequence: int) -> None:
    cursor = encode_poll_cursor(namespace=namespace, sequence=sequence, database_epoch="epoch-a")

    decoded = decode_poll_cursor(cursor)

    assert decoded is not None
    assert decoded.namespace == namespace
    assert decoded.sequence == sequence
    assert decoded.database_epoch == "epoch-a"


@given(confidence=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False))
def test_classifier_acceptance_never_promotes_reserved_tags(confidence: float) -> None:
    classifier = EnrichmentClassifier(ClassifierConfig(mode="assist", minimum_confidence=0.0))
    prediction = Classification(
        key="candidate",
        tags=("source:reviewed", "kind:learning-review", "domain:security", "topic:boundary"),
        domains=("domain:security",),
        topics=("topic:boundary",),
        confidence=confidence,
    )

    assert classifier.accepted_tags(prediction) == ["domain:security", "topic:boundary"]
