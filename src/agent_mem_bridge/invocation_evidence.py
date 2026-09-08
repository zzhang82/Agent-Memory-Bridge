"""Bounded, sanitized evidence records for semantic memory invocation trials.

These records are evaluation/observation artifacts.  They do not write to AMB
and intentionally omit memory bodies, recall tokens, transcripts, and hidden
reasoning.  A caller may copy the bounded payload into an existing ``observation``
run event when durable run evidence is already warranted.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

EVIDENCE_SCHEMA = "amb.semantic-memory-invocation.v1"
TRIAL_DOCUMENT_SCHEMA = "amb.semantic-memory-trials.v1"

ARMS = frozenset({"A", "B", "C"})
HOSTS = frozenset({"generic", "codex", "opencode"})
TRACE_STATES = frozenset({"observed", "unavailable"})
POLICY_DECISIONS = frozenset(
    {
        "must_recall",
        "may_recall",
        "do_not_recall",
        "recall_and_reconcile",
        "must_recall_then_no_hit",
        "recall_or_report_unavailable",
        "do_not_repeat",
    }
)
ADAPTER_STATES = frozenset({"applied", "failed", "not_used", "unknown"})
TOOL_STATES = frozenset({"available", "unavailable", "not_applicable", "unknown"})
INVOCATION_STATES = frozenset({"recalled", "not_recalled", "unavailable", "not_observed"})
RESULT_STATES = frozenset({"relevant_hit", "no_relevant_hit", "stale_or_conflicting", "not_applicable", "unknown"})
CURRENT_EVIDENCE_STATES = frozenset({"checked", "not_needed", "not_checked", "unknown"})
MEMORY_USE_STATES = frozenset({"applied", "rejected", "not_applicable", "unsupported_claim", "unknown"})
TASK_OUTCOMES = frozenset({"pass", "fail", "inconclusive"})
FAILURE_CLASSES = frozenset(
    {
        "policy_miss",
        "host_miss",
        "adapter_miss",
        "tool_unavailable",
        "retrieval_miss",
        "memory_misuse",
        "evidence_unavailable",
        "unsupported_memory_claim",
    }
)


class InvocationEvidenceError(ValueError):
    """Raised when an evidence record would be ambiguous or unsafe to evaluate."""


@dataclass(frozen=True)
class InvocationEvidence:
    case_id: str
    arm: str
    host: str
    runtime: str
    source_version: str
    policy_version: str | None
    policy_sha256: str | None
    trace: str
    policy_decision: str
    adapter: str
    tool: str
    invocation: str
    result: str
    current_evidence: str
    memory_use: str
    task_outcome: str
    amb_calls: int
    failure_class: str | None = None
    prior_recall_sufficient: bool | None = None
    evidence_refs: tuple[str, ...] = ()

    @classmethod
    def from_mapping(
        cls, value: Mapping[str, Any], *, defaults: Mapping[str, Any] | None = None
    ) -> "InvocationEvidence":
        if not isinstance(value, Mapping):
            raise InvocationEvidenceError("evidence case must be an object")
        merged = dict(defaults or {})
        merged.update(value)
        schema = merged.pop("schema", None)
        if schema is not None and schema != EVIDENCE_SCHEMA:
            raise InvocationEvidenceError(f"evidence schema must be {EVIDENCE_SCHEMA}")
        policy = merged.pop("policy", None)
        if policy is not None:
            if not isinstance(policy, Mapping):
                raise InvocationEvidenceError("evidence policy must be an object")
            merged.setdefault("policy_version", policy.get("version"))
            merged.setdefault("policy_sha256", policy.get("sha256"))
        allowed = {
            "case_id",
            "arm",
            "host",
            "runtime",
            "source_version",
            "policy_version",
            "policy_sha256",
            "trace",
            "policy_decision",
            "adapter",
            "tool",
            "invocation",
            "result",
            "current_evidence",
            "memory_use",
            "task_outcome",
            "amb_calls",
            "failure_class",
            "prior_recall_sufficient",
            "evidence_refs",
        }
        unknown = sorted(str(key) for key in merged if key not in allowed)
        if unknown:
            raise InvocationEvidenceError(f"unsupported evidence fields: {unknown}")
        case_id = _required_text("case_id", merged.get("case_id"), max_chars=128)
        arm = _enum_text("arm", merged.get("arm"), ARMS)
        host = _enum_text("host", merged.get("host"), HOSTS)
        runtime = _required_text("runtime", merged.get("runtime"), max_chars=128)
        source_version = _required_text("source_version", merged.get("source_version"), max_chars=32)
        policy_version = _optional_text("policy_version", merged.get("policy_version"), max_chars=128)
        policy_sha256 = _optional_digest("policy_sha256", merged.get("policy_sha256"))
        trace = _enum_text("trace", merged.get("trace"), TRACE_STATES)
        policy_decision = _enum_text("policy_decision", merged.get("policy_decision"), POLICY_DECISIONS)
        adapter = _enum_text("adapter", merged.get("adapter"), ADAPTER_STATES)
        tool = _enum_text("tool", merged.get("tool"), TOOL_STATES)
        invocation = _enum_text("invocation", merged.get("invocation"), INVOCATION_STATES)
        result = _enum_text("result", merged.get("result"), RESULT_STATES)
        current_evidence = _enum_text("current_evidence", merged.get("current_evidence"), CURRENT_EVIDENCE_STATES)
        memory_use = _enum_text("memory_use", merged.get("memory_use"), MEMORY_USE_STATES)
        task_outcome = _enum_text("task_outcome", merged.get("task_outcome"), TASK_OUTCOMES)
        amb_calls = merged.get("amb_calls")
        if isinstance(amb_calls, bool) or not isinstance(amb_calls, int) or not 0 <= amb_calls <= 32:
            raise InvocationEvidenceError("amb_calls must be an integer between 0 and 32")
        failure_class = merged.get("failure_class")
        if failure_class is not None:
            failure_class = _enum_text("failure_class", failure_class, FAILURE_CLASSES)
        prior_recall_sufficient = merged.get("prior_recall_sufficient")
        if prior_recall_sufficient is not None and not isinstance(prior_recall_sufficient, bool):
            raise InvocationEvidenceError("prior_recall_sufficient must be a boolean or null")
        evidence_refs = _refs(merged.get("evidence_refs", ()))
        return cls(
            case_id=case_id,
            arm=arm,
            host=host,
            runtime=runtime,
            source_version=source_version,
            policy_version=policy_version,
            policy_sha256=policy_sha256,
            trace=trace,
            policy_decision=policy_decision,
            adapter=adapter,
            tool=tool,
            invocation=invocation,
            result=result,
            current_evidence=current_evidence,
            memory_use=memory_use,
            task_outcome=task_outcome,
            amb_calls=amb_calls,
            failure_class=failure_class,
            prior_recall_sufficient=prior_recall_sufficient,
            evidence_refs=evidence_refs,
        )

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema": EVIDENCE_SCHEMA,
            "case_id": self.case_id,
            "arm": self.arm,
            "host": self.host,
            "runtime": self.runtime,
            "source_version": self.source_version,
            "policy": {"version": self.policy_version, "sha256": self.policy_sha256},
            "trace": self.trace,
            "policy_decision": self.policy_decision,
            "adapter": self.adapter,
            "tool": self.tool,
            "invocation": self.invocation,
            "result": self.result,
            "current_evidence": self.current_evidence,
            "memory_use": self.memory_use,
            "task_outcome": self.task_outcome,
            "amb_calls": self.amb_calls,
            "failure_class": self.failure_class,
            "prior_recall_sufficient": self.prior_recall_sufficient,
            "evidence_refs": list(self.evidence_refs),
        }
        return payload

    def observation_payload(self) -> dict[str, Any]:
        """Return a safe payload for an existing ``observation`` run event."""

        payload = self.as_dict()
        payload.pop("schema", None)
        payload["kind"] = "semantic_memory_invocation"
        return payload


def load_trial_document(path: Path) -> tuple[InvocationEvidence, ...]:
    """Load and validate one host's JSON trial document."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InvocationEvidenceError(f"could not read trial document {path}: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise InvocationEvidenceError("trial document must be an object")
    if payload.get("schema") != TRIAL_DOCUMENT_SCHEMA:
        raise InvocationEvidenceError(f"trial document schema must be {TRIAL_DOCUMENT_SCHEMA}")
    if payload.get("version") != 1:
        raise InvocationEvidenceError("unsupported trial document version")
    defaults = {
        "host": payload.get("host"),
        "runtime": payload.get("runtime"),
        "source_version": payload.get("source_version"),
        "policy": payload.get("policy"),
    }
    cases = payload.get("cases")
    if not isinstance(cases, list) or len(cases) > 64:
        raise InvocationEvidenceError("trial document cases must be a list of at most 64 entries")
    records = tuple(InvocationEvidence.from_mapping(case, defaults=defaults) for case in cases)
    identities = [(record.host, record.arm, record.case_id) for record in records]
    if len(set(identities)) != len(identities):
        raise InvocationEvidenceError("trial document contains duplicate host/arm/case entries")
    return records


def trial_document(
    *, host: str, runtime: str, source_version: str, policy: Mapping[str, Any], cases: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Build a sanitized trial document for scripts and tests."""

    defaults = {
        "host": host,
        "runtime": runtime,
        "source_version": source_version,
        "policy": policy,
    }
    records = [InvocationEvidence.from_mapping(case, defaults=defaults) for case in cases]
    return {
        "schema": TRIAL_DOCUMENT_SCHEMA,
        "version": 1,
        "host": _enum_text("host", host, HOSTS),
        "runtime": _required_text("runtime", runtime, max_chars=128),
        "source_version": _required_text("source_version", source_version, max_chars=32),
        "policy": dict(policy),
        "cases": [record.as_dict() for record in records],
    }


def _required_text(name: str, value: Any, *, max_chars: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvocationEvidenceError(f"{name} must be a non-empty string")
    cleaned = value.strip()
    if len(cleaned) > max_chars or "\n" in cleaned or "\r" in cleaned:
        raise InvocationEvidenceError(f"{name} must be at most {max_chars} characters without newlines")
    return cleaned


def _optional_text(name: str, value: Any, *, max_chars: int) -> str | None:
    if value is None:
        return None
    return _required_text(name, value, max_chars=max_chars)


def _enum_text(name: str, value: Any, allowed: frozenset[str]) -> str:
    cleaned = _required_text(name, value, max_chars=128)
    if cleaned not in allowed:
        raise InvocationEvidenceError(f"unsupported {name}: {cleaned}")
    return cleaned


def _optional_digest(name: str, value: Any) -> str | None:
    cleaned = _optional_text(name, value, max_chars=64)
    if cleaned is None:
        return None
    if len(cleaned) != 64 or any(char not in "0123456789abcdef" for char in cleaned):
        raise InvocationEvidenceError(f"{name} must be a lowercase SHA-256 digest")
    return cleaned


def _refs(value: Any) -> tuple[str, ...]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise InvocationEvidenceError("evidence_refs must be a list of strings")
    if len(value) > 8:
        raise InvocationEvidenceError("evidence_refs must contain at most 8 entries")
    refs = tuple(_required_text("evidence_ref", item, max_chars=256) for item in value)
    return refs
