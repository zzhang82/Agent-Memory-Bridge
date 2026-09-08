"""Frozen semantic invocation evaluation for bounded host evidence."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from .invocation_evidence import ARMS, HOSTS, InvocationEvidence
from .semantic_policy import POLICY_VERSION, policy_sha256

EVAL_SCHEMA = "amb.semantic-invocation-evaluation.v1"


def evaluate_semantic_invocation(
    fixture: Mapping[str, Any],
    records: Sequence[InvocationEvidence],
    *,
    source_version: str,
    hosts: Sequence[str] = ("codex", "opencode", "generic"),
) -> dict[str, Any]:
    """Adjudicate observed records without treating missing trials as passes."""

    cases = _fixture_cases(fixture)
    by_identity: dict[tuple[str, str, str], InvocationEvidence] = {}
    validation_errors: list[str] = []
    for record in records:
        identity = (record.host, record.arm, record.case_id)
        if record.case_id not in cases:
            validation_errors.append(f"unknown case: {record.case_id}")
        if record.source_version != source_version:
            validation_errors.append(
                f"{record.host}/{record.arm}/{record.case_id} source version {record.source_version} != {source_version}"
            )
        if record.arm == "C" and (record.policy_version != POLICY_VERSION or record.policy_sha256 != policy_sha256()):
            validation_errors.append(f"{record.host}/C/{record.case_id} does not identify the canonical policy")
        if identity in by_identity:
            validation_errors.append(f"duplicate evidence: {'/'.join(identity)}")
        by_identity[identity] = record

    selected_hosts = tuple(dict.fromkeys(hosts))
    unknown_hosts = [host for host in selected_hosts if host not in HOSTS]
    if unknown_hosts:
        validation_errors.extend(f"unknown evaluation host: {host}" for host in unknown_hosts)

    rows: list[dict[str, Any]] = []
    for arm in sorted(ARMS):
        for host in selected_hosts:
            for case_id, case in cases.items():
                observed_record = by_identity.get((host, arm, case_id))
                if observed_record is None:
                    rows.append(
                        {
                            "case_id": case_id,
                            "case_class": case.get("class"),
                            "expected_decision": case.get("expected_decision"),
                            "arm": arm,
                            "host": host,
                            "status": "NOT RUN / UNOBSERVED",
                            "reason": "No sanitized host evidence was supplied.",
                        }
                    )
                    continue
                status, reason = _adjudicate(case, observed_record)
                rows.append(
                    {
                        "case_id": case_id,
                        "case_class": case.get("class"),
                        "expected_decision": case.get("expected_decision"),
                        "arm": arm,
                        "host": host,
                        "status": status,
                        "reason": reason,
                        "failure_class": _failure_class(case, observed_record),
                        "evidence": observed_record.as_dict(),
                    }
                )

    metrics = {f"{arm}/{host}": _metrics(rows, arm=arm, host=host) for arm in sorted(ARMS) for host in selected_hosts}
    return {
        "schema": EVAL_SCHEMA,
        "version": 1,
        "source_version": source_version,
        "policy": {"version": POLICY_VERSION, "sha256": policy_sha256()},
        "fixture": {
            "schema": fixture.get("schema"),
            "version": fixture.get("version"),
            "baseline": fixture.get("baseline"),
            "case_count": len(cases),
        },
        "validation_errors": validation_errors,
        "rows": rows,
        "metrics": metrics,
        "comparison": _comparison(metrics),
        "observed_evidence_count": len(records),
    }


def _fixture_cases(fixture: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    if fixture.get("schema") != "amb.semantic-invocation-eval.v1" or fixture.get("version") != "1":
        raise ValueError("unsupported semantic invocation fixture")
    raw_cases = fixture.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("semantic invocation fixture must contain cases")
    cases: dict[str, Mapping[str, Any]] = {}
    for case in raw_cases:
        if not isinstance(case, Mapping) or not isinstance(case.get("id"), str):
            raise ValueError("semantic invocation fixture case is malformed")
        case_id = case["id"]
        if case_id in cases:
            raise ValueError(f"duplicate fixture case: {case_id}")
        if case.get("class") not in {"positive_recall", "governance", "negative_control"}:
            raise ValueError(f"unsupported semantic invocation case class: {case.get('class')!r}")
        if not isinstance(case.get("expected_decision"), str):
            raise ValueError(f"semantic invocation case {case_id} has no expected decision")
        cases[case_id] = case
    return cases


def _adjudicate(case: Mapping[str, Any], record: InvocationEvidence) -> tuple[str, str]:
    if record.trace != "observed":
        return "INCONCLUSIVE", "Invocation trace was unavailable."
    if record.adapter == "failed":
        return "FAIL", "The host adapter failed to apply the reviewed policy placement."
    if record.arm == "C" and record.adapter != "applied":
        return "FAIL", "The canonical policy was not observed as applied by the host adapter."
    if record.task_outcome == "fail":
        return "FAIL", "The task outcome failed even though invocation evidence was observable."
    if record.task_outcome == "inconclusive":
        return "INCONCLUSIVE", "The task outcome was inconclusive."
    expected = str(case.get("expected_decision"))
    if record.policy_decision != expected:
        return "FAIL", f"The observed policy decision {record.policy_decision!r} did not match {expected!r}."
    if expected == "must_recall":
        if record.invocation != "recalled":
            return "FAIL", "A material history dependency did not produce an observed recall."
        if record.result != "relevant_hit":
            return "FAIL", "Recall did not expose the relevant project memory."
        if record.current_evidence != "checked":
            return "FAIL", "Relevant memory was not reconciled with current evidence."
        if record.memory_use != "applied":
            return "FAIL", "Relevant recalled memory was not applied with bounded evidence."
        return "PASS", "Relevant recall, current-evidence check, and bounded use were observed."
    if expected == "must_recall_then_no_hit":
        if record.invocation != "recalled" or record.result != "no_relevant_hit":
            return "FAIL", "The successful no-hit recall contract was not observed."
        if record.current_evidence != "checked":
            return "FAIL", "The successful no-hit recall was not followed by a current-evidence check."
        if record.memory_use not in {"not_applicable", "rejected"}:
            return "FAIL", "The no-hit result was treated as usable or as an unsupported memory claim."
        return "PASS", "A successful no-hit recall was distinguished from an invented project exception."
    if expected == "recall_and_reconcile":
        if record.invocation != "recalled" or record.result != "stale_or_conflicting":
            return "FAIL", "The stale or conflicting memory was not surfaced through recall."
        if record.current_evidence != "checked" or record.memory_use != "rejected":
            return "FAIL", "Current evidence did not visibly outrank stale or conflicting memory."
        return "PASS", "Stale/conflicting memory was recalled, checked, and rejected."
    if expected == "recall_or_report_unavailable":
        if record.invocation == "unavailable":
            if record.tool != "unavailable" or record.failure_class != "tool_unavailable":
                return "FAIL", "An unavailable route was not classified as tool unavailability."
            if record.memory_use == "unsupported_claim":
                return "FAIL", "Unavailable AMB was followed by an unsupported memory claim."
            if record.current_evidence != "checked":
                return "FAIL", "The unavailable route was not followed by a current-evidence check."
            return "PASS", "AMB unavailability was reported distinctly from a successful no-hit."
        if record.invocation == "recalled" and record.tool == "available":
            if record.result == "unknown" or record.current_evidence != "checked":
                return "FAIL", "The available route did not expose a usable result reconciled with current evidence."
            return "PASS", "The available AMB route was observed and classified."
        return "FAIL", "The host neither recalled nor reported the unavailable route distinctly."
    if expected == "do_not_recall":
        if record.invocation != "not_recalled" or record.amb_calls != 0:
            return "FAIL", "A deterministic task triggered an unnecessary AMB recall."
        return "PASS", "The deterministic task completed without an AMB recall."
    if expected == "do_not_repeat":
        if record.invocation != "not_recalled" or record.amb_calls != 0:
            return "FAIL", "A still-sufficient recall was repeated."
        if record.prior_recall_sufficient is not True:
            return "FAIL", "The evidence did not establish that the prior recall remained sufficient."
        return "PASS", "The sufficient prior recall was reused without a duplicate call."
    return "INCONCLUSIVE", f"Unsupported fixture decision: {expected}"


def _failure_class(case: Mapping[str, Any], record: InvocationEvidence) -> str | None:
    """Attribute an observed miss without capturing model reasoning."""

    if record.failure_class is not None:
        return record.failure_class
    if record.trace != "observed":
        return "evidence_unavailable"
    if record.memory_use == "unsupported_claim":
        return "unsupported_memory_claim"
    if record.adapter == "failed":
        return "adapter_miss"
    expected = str(case.get("expected_decision"))
    if record.arm == "C" and record.adapter != "applied":
        return "host_miss"
    if record.tool == "unavailable" or record.invocation == "unavailable":
        return "tool_unavailable"
    if record.policy_decision != expected:
        return "policy_miss"
    if expected in {"must_recall", "must_recall_then_no_hit", "recall_and_reconcile"}:
        if record.invocation != "recalled":
            return "host_miss"
        expected_result = {
            "must_recall": "relevant_hit",
            "must_recall_then_no_hit": "no_relevant_hit",
            "recall_and_reconcile": "stale_or_conflicting",
        }[expected]
        if record.result != expected_result:
            return "retrieval_miss"
        if expected == "recall_and_reconcile":
            memory_safe = record.current_evidence == "checked" and record.memory_use == "rejected"
        elif expected == "must_recall_then_no_hit":
            memory_safe = record.current_evidence == "checked" and record.memory_use in {
                "not_applicable",
                "rejected",
            }
        else:
            memory_safe = record.current_evidence == "checked" and record.memory_use == "applied"
        if not memory_safe:
            return "memory_misuse"
    elif expected == "recall_or_report_unavailable":
        if record.invocation == "unavailable" or record.tool == "unavailable":
            return "tool_unavailable"
        if record.invocation != "recalled":
            return "host_miss"
    elif expected in {"do_not_recall", "do_not_repeat"}:
        if record.invocation != "not_recalled" or record.amb_calls != 0:
            return "host_miss"
        if expected == "do_not_repeat" and record.prior_recall_sufficient is not True:
            return "memory_misuse"
    return None


def _metrics(rows: Sequence[Mapping[str, Any]], *, arm: str, host: str) -> dict[str, Any]:
    selected = [row for row in rows if row["arm"] == arm and row["host"] == host]
    observed = [row for row in selected if row["status"] != "NOT RUN / UNOBSERVED"]
    by_case = {str(row["case_id"]): row for row in observed}
    critical = [row for row in observed if row["case_class"] == "positive_recall"]
    governance = [row for row in observed if row["case_class"] == "governance"]
    negative = [row for row in observed if row["case_class"] == "negative_control"]
    evidence = [row.get("evidence", {}) for row in observed]
    failure_classes = Counter(str(row["failure_class"]) for row in observed if row.get("failure_class") is not None)
    return {
        "observed": len(observed),
        "pass": sum(row["status"] == "PASS" for row in observed),
        "fail": sum(row["status"] == "FAIL" for row in observed),
        "inconclusive": sum(row["status"] == "INCONCLUSIVE" for row in observed),
        "not_run": len(selected) - len(observed),
        "critical_recall_miss_rate": _rate(sum(row["status"] == "FAIL" for row in critical), len(critical)),
        "governance_safe_handling_rate": _rate(sum(row["status"] == "PASS" for row in governance), len(governance)),
        "unnecessary_recall_rate": _rate(sum(int(item.get("amb_calls", 0)) > 0 for item in negative), len(negative)),
        "unsupported_memory_claim_count": sum(item.get("memory_use") == "unsupported_claim" for item in evidence),
        "stale_or_superseded_misuse_count": sum(
            row["expected_decision"] == "recall_and_reconcile" and row["status"] == "FAIL" for row in observed
        ),
        "correct_memory_use_count": sum(
            row["status"] == "PASS" and row.get("evidence", {}).get("memory_use") == "applied" for row in observed
        ),
        "task_outcome_pass_rate": _rate(sum(item.get("task_outcome") == "pass" for item in evidence), len(evidence)),
        "amb_calls": sum(int(item.get("amb_calls", 0)) for item in evidence),
        "evidence_completeness": _rate(sum(bool(item.get("evidence_refs")) for item in evidence), len(evidence)),
        "failure_class_counts": dict(sorted(failure_classes.items())),
        "case_status": {case_id: row["status"] for case_id, row in by_case.items()},
    }


def _comparison(metrics: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    comparisons: list[dict[str, Any]] = []
    for host in ("codex", "opencode", "generic"):
        baseline = metrics.get(f"B/{host}")
        candidate = metrics.get(f"C/{host}")
        if not baseline or not candidate or baseline["observed"] == 0 or candidate["observed"] == 0:
            comparisons.append({"host": host, "verdict": "NOT ENOUGH EVIDENCE"})
            continue
        no_regression = (
            candidate["critical_recall_miss_rate"] <= baseline["critical_recall_miss_rate"]
            and candidate["governance_safe_handling_rate"] >= baseline["governance_safe_handling_rate"]
            and candidate["unnecessary_recall_rate"] <= baseline["unnecessary_recall_rate"]
            and candidate["task_outcome_pass_rate"] >= baseline["task_outcome_pass_rate"]
        )
        material_gain = (
            candidate["critical_recall_miss_rate"] < baseline["critical_recall_miss_rate"]
            or candidate["governance_safe_handling_rate"] > baseline["governance_safe_handling_rate"]
            or candidate["stale_or_superseded_misuse_count"] < baseline["stale_or_superseded_misuse_count"]
            or candidate["evidence_completeness"] > baseline["evidence_completeness"]
        )
        c_better = no_regression and material_gain
        comparisons.append({"host": host, "verdict": "C PREFERRED" if c_better else "B RETAINED"})
    return {"arms": comparisons, "router_arm": "NOT RUN / OUT OF SCOPE"}


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 4)
