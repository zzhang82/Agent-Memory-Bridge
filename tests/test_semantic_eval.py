from __future__ import annotations

import json
from pathlib import Path

from agent_mem_bridge.invocation_evidence import InvocationEvidence
from agent_mem_bridge.semantic_eval import evaluate_semantic_invocation
from agent_mem_bridge.semantic_policy import POLICY_VERSION, policy_sha256

ROOT = Path(__file__).resolve().parents[1]


def _fixture() -> dict[str, object]:
    return json.loads((ROOT / "benchmark" / "semantic-invocation-v1.json").read_text(encoding="utf-8"))


def _record(case: dict[str, object], *, arm: str = "C", host: str = "codex", **overrides: object) -> InvocationEvidence:
    expected = str(case["expected_decision"])
    value: dict[str, object] = {
        "case_id": case["id"],
        "arm": arm,
        "host": host,
        "runtime": f"{host}-test-runtime",
        "source_version": "0.33.1",
        "policy_version": POLICY_VERSION if arm == "C" else None,
        "policy_sha256": policy_sha256() if arm == "C" else None,
        "trace": "observed",
        "policy_decision": expected,
        "adapter": "applied",
        "tool": "available",
        "invocation": "recalled",
        "result": "relevant_hit",
        "current_evidence": "checked",
        "memory_use": "applied",
        "task_outcome": "pass",
        "amb_calls": 1,
        "evidence_refs": [f"trace:{case['id']}"],
    }
    if expected == "must_recall_then_no_hit":
        value.update(result="no_relevant_hit", memory_use="not_applicable")
    elif expected == "recall_and_reconcile":
        value.update(result="stale_or_conflicting", memory_use="rejected")
    elif expected == "recall_or_report_unavailable":
        value.update(
            invocation="unavailable",
            tool="unavailable",
            failure_class="tool_unavailable",
            amb_calls=1,
            memory_use="not_applicable",
        )
    elif expected == "do_not_recall":
        value.update(invocation="not_recalled", amb_calls=0, memory_use="not_applicable")
    elif expected == "do_not_repeat":
        value.update(invocation="not_recalled", amb_calls=0, memory_use="not_applicable", prior_recall_sufficient=True)
    value.update(overrides)
    return InvocationEvidence.from_mapping(value)


def test_missing_host_trials_are_not_scored_as_passes() -> None:
    report = evaluate_semantic_invocation(_fixture(), [], source_version="0.33.1")

    assert report["observed_evidence_count"] == 0
    assert report["metrics"]["C/codex"]["not_run"] == 13
    assert report["metrics"]["C/codex"]["pass"] == 0
    assert report["comparison"]["arms"][0]["verdict"] == "NOT ENOUGH EVIDENCE"


def test_complete_synthetic_evidence_passes_cases_and_keeps_equal_baseline_equal() -> None:
    cases = _fixture()["cases"]
    records = []
    for case in cases:
        records.append(_record(case, arm="B"))
        records.append(_record(case, arm="C"))

    report = evaluate_semantic_invocation(_fixture(), records, source_version="0.33.1", hosts=("codex",))
    candidate = report["metrics"]["C/codex"]

    assert report["validation_errors"] == []
    assert candidate["observed"] == 13
    assert candidate["pass"] == 13
    assert candidate["critical_recall_miss_rate"] == 0.0
    assert candidate["governance_safe_handling_rate"] == 1.0
    assert candidate["unnecessary_recall_rate"] == 0.0
    assert candidate["unsupported_memory_claim_count"] == 0
    codex_comparison = next(item for item in report["comparison"]["arms"] if item["host"] == "codex")
    assert codex_comparison == {"host": "codex", "verdict": "B RETAINED"}


def test_evaluator_prefers_canonical_policy_when_governance_improves() -> None:
    cases = _fixture()["cases"]
    records = []
    for case in cases:
        if case["expected_decision"] == "recall_and_reconcile":
            records.append(_record(case, arm="B", memory_use="applied", result="relevant_hit"))
        else:
            records.append(_record(case, arm="B"))
        records.append(_record(case, arm="C"))

    report = evaluate_semantic_invocation(_fixture(), records, source_version="0.33.1", hosts=("codex",))
    comparison = next(item for item in report["comparison"]["arms"] if item["host"] == "codex")

    assert report["metrics"]["C/codex"]["pass"] == 13
    assert report["metrics"]["B/codex"]["governance_safe_handling_rate"] < 1.0
    assert comparison == {"host": "codex", "verdict": "C PREFERRED"}


def test_evaluator_rejects_policy_self_labeling_and_no_hit_misuse() -> None:
    cases = {case["id"]: case for case in _fixture()["cases"]}
    positive = _record(cases["positive-explicit-prior-decision"], policy_decision="do_not_recall")
    no_hit = _record(cases["governance-no-relevant-memory"], memory_use="applied")

    report = evaluate_semantic_invocation(
        _fixture(),
        [positive, no_hit],
        source_version="0.33.1",
        hosts=("codex",),
    )
    rows = {(row["case_id"], row["arm"]): row for row in report["rows"] if row["status"] != "NOT RUN / UNOBSERVED"}

    assert rows[("positive-explicit-prior-decision", "C")]["status"] == "FAIL"
    assert "did not match" in rows[("positive-explicit-prior-decision", "C")]["reason"]
    assert rows[("governance-no-relevant-memory", "C")]["status"] == "FAIL"


def test_evaluator_attributes_invocation_failure_layers() -> None:
    cases = {case["id"]: case for case in _fixture()["cases"]}
    host_miss = _record(
        cases["positive-explicit-prior-decision"],
        invocation="not_recalled",
        result="not_applicable",
        current_evidence="not_needed",
        memory_use="not_applicable",
        amb_calls=0,
    )
    retrieval_miss = _record(
        cases["positive-fresh-session-continuation"],
        result="no_relevant_hit",
        memory_use="not_applicable",
    )
    memory_misuse = _record(cases["positive-repeated-gotcha"], memory_use="rejected")
    adapter_miss = _record(cases["positive-indirect-history-reference"], adapter="failed")
    unavailable = _record(cases["governance-amb-unavailable"])

    report = evaluate_semantic_invocation(
        _fixture(),
        [host_miss, retrieval_miss, memory_misuse, adapter_miss, unavailable],
        source_version="0.33.1",
        hosts=("codex",),
    )
    rows = {
        row["case_id"]: row for row in report["rows"] if row["arm"] == "C" and row["status"] != "NOT RUN / UNOBSERVED"
    }

    assert rows["positive-explicit-prior-decision"]["failure_class"] == "host_miss"
    assert rows["positive-fresh-session-continuation"]["failure_class"] == "retrieval_miss"
    assert rows["positive-repeated-gotcha"]["failure_class"] == "memory_misuse"
    assert rows["positive-indirect-history-reference"]["failure_class"] == "adapter_miss"
    assert rows["governance-amb-unavailable"]["failure_class"] == "tool_unavailable"
    assert report["metrics"]["C/codex"]["failure_class_counts"] == {
        "adapter_miss": 1,
        "host_miss": 1,
        "memory_misuse": 1,
        "retrieval_miss": 1,
        "tool_unavailable": 1,
    }
