from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_mem_bridge.invocation_evidence import (
    EVIDENCE_SCHEMA,
    InvocationEvidence,
    InvocationEvidenceError,
    load_trial_document,
    trial_document,
)


def _case(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "case_id": "positive-explicit-prior-decision",
        "arm": "C",
        "host": "codex",
        "runtime": "codex-cli 0.147.0",
        "source_version": "0.33.1",
        "policy_version": "canonical-policy-v1",
        "policy_sha256": "a" * 64,
        "trace": "observed",
        "policy_decision": "must_recall",
        "adapter": "applied",
        "tool": "available",
        "invocation": "recalled",
        "result": "relevant_hit",
        "current_evidence": "checked",
        "memory_use": "applied",
        "task_outcome": "pass",
        "amb_calls": 1,
        "evidence_refs": ["trace:case-1", "run-event:7"],
    }
    value.update(overrides)
    return value


def test_evidence_is_bounded_and_excludes_raw_memory_material() -> None:
    record = InvocationEvidence.from_mapping(_case())

    assert record.as_dict()["schema"] == EVIDENCE_SCHEMA
    assert record.observation_payload()["kind"] == "semantic_memory_invocation"
    encoded = json.dumps(record.observation_payload())
    assert "memory_body" not in encoded
    assert "recall_token" not in encoded
    assert "secret" not in encoded
    assert record.evidence_refs == ("trace:case-1", "run-event:7")


def test_evidence_rejects_unknown_fields_and_unsafe_digest() -> None:
    with pytest.raises(InvocationEvidenceError, match="unsupported evidence fields"):
        InvocationEvidence.from_mapping(_case(memory_body="do not persist"))
    with pytest.raises(InvocationEvidenceError, match="lowercase SHA-256"):
        InvocationEvidence.from_mapping(_case(policy_sha256="B" * 64))
    with pytest.raises(InvocationEvidenceError, match="between 0 and 32"):
        InvocationEvidence.from_mapping(_case(amb_calls=33))


def test_trial_document_round_trips_and_rejects_duplicate_identity(tmp_path: Path) -> None:
    payload = trial_document(
        host="codex",
        runtime="codex-cli",
        source_version="0.33.1",
        policy={"version": "canonical-policy-v1", "sha256": "a" * 64},
        cases=[_case()],
    )
    path = tmp_path / "codex-trial.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = load_trial_document(path)
    assert len(loaded) == 1
    assert loaded[0].host == "codex"
    assert loaded[0].policy_version == "canonical-policy-v1"

    duplicate = dict(payload)
    duplicate["cases"] = [payload["cases"][0], payload["cases"][0]]
    path.write_text(json.dumps(duplicate), encoding="utf-8")
    with pytest.raises(InvocationEvidenceError, match="duplicate host/arm/case"):
        load_trial_document(path)
