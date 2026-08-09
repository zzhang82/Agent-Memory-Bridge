"""Deterministic, shadow-only synthesis of completed run evidence.

This module deliberately does not participate in the historical memory
consolidation engine.  It reads the episode ledger, emits reviewable lesson
proposals, and may *stage* those proposals in the existing hidden learning
candidate lane.  It never changes run authority, projections, recall ranking,
or ordinary memory.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import defaultdict
from collections.abc import Mapping, Sequence
from contextlib import nullcontext
from typing import Any

from .durable_data_policy import forbidden_durable_structured_field
from .learning_policy import evaluate_learning_candidate
from .procedure_governance import parse_procedure_artifact
from .run_outcome_authority import is_strong_verified_outcome, outcome_authority_class

SHADOW_SCHEMA = "amb.run-consolidation-shadow.v1"
EVIDENCE_SCHEMA = "amb.run-consolidation-evidence.v1"
MAX_LIMIT = 500
DEFAULT_LIMIT = 100
ALLOWED_AUTHORITY_CLASSES = frozenset({"belief_proposal", "decision", "procedure", "release_evidence"})
ALLOWED_PAYLOAD_FIELDS = frozenset(
    {
        "schema",
        "claim",
        "evidence_refs",
        "authority_class",
        "domain_tags",
        "tags",
        "boundary",
        "observed_conditions",
        "when_not_to_use",
        "failure_mode",
        "rollback_path",
        "goal",
        "when_to_use",
        "prerequisites",
        "steps",
        "applies_to_domains",
    }
)
PROCEDURE_FIELDS = frozenset(
    {
        "goal",
        "when_to_use",
        "when_not_to_use",
        "prerequisites",
        "steps",
        "failure_mode",
        "rollback_path",
        "applies_to_domains",
    }
)
REPORT_TEXT_FIELDS = frozenset(
    {
        "claim",
        "boundary",
        "observed_conditions",
        "when_not_to_use",
        "failure_mode",
        "rollback_path",
        "goal",
        "when_to_use",
        "prerequisites",
        "steps",
    }
)
OPAQUE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@=+-]{0,255}$")
DOMAIN_TAG_RE = re.compile(r"^domain:[a-z0-9][a-z0-9._/-]{0,127}$")
WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")
SECRET_REF_RE = re.compile(
    r"(?:sk-[A-Za-z0-9_-]{8,}|ghp_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16})",
    re.IGNORECASE,
)
PRIVATE_TEXT_RE = re.compile(
    r"(?:\b(?:sk-[A-Za-z0-9_-]{8,}|ghp_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
    r"AKIA[0-9A-Z]{16})\b|\b(?:api[_ -]?key|token|password|passwd|secret|private[_ -]?key)\b|"
    r"(?:^|\s)(?:[A-Za-z]:[\\/]|\\\\|/|~[\\/])|\bfile://)",
    re.IGNORECASE,
)
PATH_LIKE_TEXT_RE = re.compile(r"(?:[A-Za-z]:[\\/]|\\|/(?:[A-Za-z0-9._~:-]+))")


def build_run_consolidation_report(
    store: Any | None,
    *,
    workspace_key: str,
    limit: int = DEFAULT_LIMIT,
    stage: bool = False,
    connection: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Read a bounded workspace slice and return deterministic lesson proposals.

    ``stage=False`` is strictly read-only from this module's perspective.  A
    caller may pass an already-opened read-only connection to ensure even store
    initialization cannot create or migrate a database.
    """

    workspace = _bounded_text("workspace_key", workspace_key, 512)
    if limit < 1 or limit > MAX_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_LIMIT}")

    if connection is not None:
        connection_context = nullcontext(connection)
    elif store is not None:
        connection_context = store._connect()
    else:
        raise ValueError("store or read-only connection is required")
    with connection_context as conn:
        workspace_run_count = int(
            conn.execute(
                "SELECT COUNT(*) FROM agent_runs WHERE workspace_key = ?",
                (workspace,),
            ).fetchone()[0]
        )
        runs = conn.execute(
            """
            SELECT run_id, thread_id, client_session_id, created_at
            FROM agent_runs
            WHERE workspace_key = ?
            ORDER BY created_at ASC, run_id ASC
            LIMIT ?
            """,
            (workspace, limit),
        ).fetchall()
        run_ids = [str(row["run_id"]) for row in runs]
        scan_complete = len(run_ids) == workspace_run_count
        outcomes = conn.execute(
            """
            SELECT run_outcomes.outcome_id, run_outcomes.run_id,
                   run_outcomes.outcome_type, run_outcomes.evaluator_type,
                   run_outcomes.evaluator_digest, run_outcomes.evaluator_version,
                   run_outcomes.evidence_json, run_outcomes.supersedes_outcome_id,
                   run_outcomes.regression_of_run_id,
                   run_outcomes.termination_reason, run_outcomes.created_at,
                   agent_runs.thread_id AS run_thread_id,
                   agent_runs.client_session_id AS run_client_session_id
            FROM run_outcomes
            JOIN agent_runs ON agent_runs.run_id = run_outcomes.run_id
            WHERE agent_runs.workspace_key = ?
            ORDER BY run_outcomes.run_id ASC, run_outcomes.created_at ASC,
                     run_outcomes.outcome_id ASC
            """,
            (workspace,),
        ).fetchall()
        if run_ids:
            placeholders = ", ".join("?" for _ in run_ids)
            events = conn.execute(
                f"""
                SELECT event_id, run_id, sequence, event_type, payload_json
                FROM run_events
                WHERE run_id IN ({placeholders})
                ORDER BY run_id ASC, sequence ASC, event_id ASC
                """,
                run_ids,
            ).fetchall()
        else:
            events = []

    runs_by_id = {str(row["run_id"]): row for row in runs}
    outcome_by_id = {str(row["outcome_id"]): row for row in outcomes}
    current_outcome = _current_outcome_heads(outcomes)
    inbound_regressions = _inbound_current_regressions(current_outcome)
    outcomes_by_run: dict[str, list[Any]] = defaultdict(list)
    for row in outcomes:
        outcomes_by_run[str(row["run_id"])].append(row)

    excluded: list[dict[str, Any]] = []
    run_status: dict[str, tuple[str, Any | None]] = {}
    for run_id in run_ids:
        head = current_outcome.get(run_id)
        if head is None:
            run_status[run_id] = ("missing_current_outcome", None)
            excluded.append(_excluded(run_id, None, "missing_current_outcome"))
        elif _is_watcher_rollout_idle(head):
            run_status[run_id] = ("watcher_rollout_idle", head)
            excluded.append(_excluded(run_id, None, "watcher_rollout_idle"))
        else:
            run_status[run_id] = ("eligible_outcome", head)

    grouped: dict[tuple[str, str, str, tuple[str, ...]], list[dict[str, Any]]] = defaultdict(list)
    for row in events:
        run_id = str(row["run_id"])
        if str(row["event_type"]) != "decision":
            excluded.append(_excluded(run_id, str(row["event_id"]), "event_type_ignored"))
            continue
        status, head = run_status.get(run_id, ("missing_current_outcome", None))
        if status != "eligible_outcome" or head is None:
            excluded.append(_excluded(run_id, str(row["event_id"]), status))
            continue
        parsed, reason = _parse_evidence_payload(row["payload_json"])
        if parsed is None:
            excluded.append(_excluded(run_id, str(row["event_id"]), reason or "invalid_evidence_payload"))
            continue
        parsed["event_id"] = str(row["event_id"])
        parsed["sequence"] = int(row["sequence"])
        parsed["run_id"] = run_id
        parsed["outcome"] = head
        grouped[parsed["group_key"]].append(parsed)

    candidates = [
        _build_candidate(
            group_key,
            entries,
            runs_by_id=runs_by_id,
            outcomes_by_run=outcomes_by_run,
            outcome_by_id=outcome_by_id,
            inbound_regressions=inbound_regressions,
            scan_complete=scan_complete,
        )
        for group_key, entries in sorted(grouped.items(), key=lambda item: item[0])
    ]
    candidates.sort(key=lambda candidate: str(candidate["candidate_key"]))
    eligible = [candidate for candidate in candidates if candidate["eligible"]]

    excluded.sort(key=lambda item: (str(item["run_id"]), str(item.get("event_id") or ""), str(item["reason_code"])))
    report = {
        "schema": SHADOW_SCHEMA,
        "workspace_key": workspace,
        "limit": limit,
        "stage": False,
        "scanned_run_count": len(run_ids),
        "scan": {
            "complete": scan_complete,
            "workspace_run_count": workspace_run_count,
            "scanned_run_count": len(run_ids),
            "omitted_run_count": workspace_run_count - len(run_ids),
            "outcome_head_scope": "workspace",
        },
        "eligible_candidate_count": len(eligible),
        "staged_count": 0,
        "write_counts": {"stored": 0, "duplicate": 0, "error": 0},
        "candidates": candidates,
        "excluded": excluded,
        "stage_results": [],
    }
    if stage:
        if store is None:
            raise ValueError("staging requires a writable MemoryStore")
        stage_run_consolidation_report(store, report)
    return report


def stage_run_consolidation_report(store: Any, report: dict[str, Any]) -> dict[str, Any]:
    """Stage existing eligible report candidates in the hidden review lane only."""

    workspace = _bounded_text("workspace_key", report.get("workspace_key"), 512)
    scan = report.get("scan")
    if not isinstance(scan, Mapping) or scan.get("complete") is not True:
        report["stage"] = True
        report["staged_count"] = 0
        report["write_counts"] = {"stored": 0, "duplicate": 0, "error": 0}
        report["stage_results"] = []
        return report
    candidates = report.get("candidates")
    if not isinstance(candidates, list):
        raise ValueError("run consolidation report candidates must be a list")
    stage_results: list[dict[str, Any]] = []
    write_counts = {"stored": 0, "duplicate": 0, "error": 0}
    for candidate in sorted(candidates, key=lambda item: str(item.get("candidate_key", ""))):
        if not isinstance(candidate, Mapping) or not candidate.get("eligible"):
            continue
        stage_result = _stage_candidate(store, workspace, candidate)
        stage_results.append(stage_result)
        disposition = str(stage_result["disposition"])
        if disposition in write_counts:
            write_counts[disposition] += 1
    report["stage"] = True
    report["staged_count"] = write_counts["stored"]
    report["write_counts"] = write_counts
    report["stage_results"] = stage_results
    return report


def render_run_consolidation_markdown(report: Mapping[str, Any]) -> str:
    """Render the same report without adding timestamps or non-determinism."""

    lines = [
        "# AMB Run Consolidation (Shadow)",
        "",
        f"- workspace: `{report['workspace_key']}`",
        f"- scanned runs: `{report['scanned_run_count']}`",
        f"- scan complete: `{str(bool(report.get('scan', {}).get('complete'))).lower()}`",
        f"- eligible candidates: `{report['eligible_candidate_count']}`",
        f"- staged candidates: `{report['staged_count']}`",
        "",
    ]
    candidates = report.get("candidates", [])
    if not candidates:
        lines.append("No eligible run-evidence groups were found.")
        return "\n".join(lines)
    for candidate in candidates:
        assert isinstance(candidate, Mapping)
        lines.extend(
            [
                f"## {candidate['claim']}",
                "",
                f"- candidate key: `{candidate['candidate_key']}`",
                f"- authority: `{candidate['authority_class']}`",
                f"- confidence label: `{candidate['confidence_label']}`",
                f"- eligible: `{str(bool(candidate['eligible'])).lower()}`",
                f"- basis: `{', '.join(candidate['basis_reason_codes'])}`",
                f"- review action: {candidate['review_action']}",
                "",
            ]
        )
    return "\n".join(lines).rstrip()


def _parse_evidence_payload(raw_payload: Any) -> tuple[dict[str, Any] | None, str | None]:
    try:
        payload = json.loads(str(raw_payload))
    except (TypeError, json.JSONDecodeError):
        return None, "malformed_event_payload"
    if not isinstance(payload, dict):
        return None, "payload_not_object"
    if forbidden_durable_structured_field(payload) is not None:
        return None, "forbidden_payload_field"
    unknown = sorted(set(payload).difference(ALLOWED_PAYLOAD_FIELDS))
    if unknown:
        return None, "unknown_payload_field"
    if _contains_private_report_text(payload):
        return None, "privacy_sensitive_payload"
    if str(payload.get("schema") or "").strip() != EVIDENCE_SCHEMA:
        return None, "invalid_evidence_schema"
    claim = _optional_bounded_text(payload.get("claim"), 1024)
    if not claim:
        return None, "missing_claim"
    authority_class = str(payload.get("authority_class") or "").strip()
    if authority_class not in ALLOWED_AUTHORITY_CLASSES:
        return None, "invalid_authority_class"
    evidence_refs = _normalize_refs(payload.get("evidence_refs"), required=True)
    if evidence_refs is None:
        return None, "invalid_evidence_refs"
    raw_tags = payload.get("domain_tags", payload.get("tags"))
    if "domain_tags" in payload and "tags" in payload:
        return None, "ambiguous_domain_tags"
    domains = _normalize_domain_tags(raw_tags)
    if domains is None:
        return None, "invalid_domain_tags"
    boundary = _optional_bounded_text(payload.get("boundary"), 512) or ""

    normalized: dict[str, Any] = {
        "claim": claim,
        "boundary": boundary,
        "authority_class": authority_class,
        "domain_tags": domains,
        "evidence_refs": evidence_refs,
        "observed_conditions": _optional_bounded_text(payload.get("observed_conditions"), 1024) or "",
        "when_not_to_use": _optional_bounded_text(payload.get("when_not_to_use"), 1024) or "",
        "failure_mode": _optional_bounded_text(payload.get("failure_mode"), 1024) or "",
        "rollback_path": _optional_bounded_text(payload.get("rollback_path"), 1024) or "",
    }
    if authority_class == "procedure":
        procedure, reason = _parse_procedure_payload(payload, domains)
        if procedure is None:
            return None, reason
        normalized["procedure"] = procedure
    elif any(
        key in payload for key in PROCEDURE_FIELDS.difference({"when_not_to_use", "failure_mode", "rollback_path"})
    ):
        return None, "procedure_fields_not_allowed"
    normalized["group_key"] = (claim, boundary, authority_class, tuple(domains))
    return normalized, None


def _parse_procedure_payload(
    payload: Mapping[str, Any], domains: list[str]
) -> tuple[dict[str, Any] | None, str | None]:
    goal = _optional_bounded_text(payload.get("goal"), 1024)
    when_to_use = _optional_bounded_text(payload.get("when_to_use"), 1024)
    prerequisites = _normalize_short_list(payload.get("prerequisites"), maximum=16, item_limit=256)
    steps = _normalize_short_list(payload.get("steps"), maximum=12, item_limit=180)
    applies = _normalize_domain_tags(payload.get("applies_to_domains", domains))
    if prerequisites is None or steps is None or applies is None:
        return None, "invalid_procedure_fields"
    content_fields = {
        "procedure_status": "draft",
        "goal": goal or "",
        "when_to_use": when_to_use or "",
        "when_not_to_use": _optional_bounded_text(payload.get("when_not_to_use"), 1024) or "",
        "prerequisites": " | ".join(prerequisites),
        "steps": " | ".join(steps),
        "failure_mode": _optional_bounded_text(payload.get("failure_mode"), 1024) or "",
        "rollback_path": _optional_bounded_text(payload.get("rollback_path"), 1024) or "",
        "applies_to_domains": " | ".join(applies),
    }
    content = "\n".join(f"{key}: {value}" for key, value in content_fields.items() if value)
    parsed = parse_procedure_artifact(content, tags=domains)
    governance = parsed["governance"]
    if not governance["eligible"] or governance["missing_minimum_fields"]:
        return None, "procedure_missing_minimum_fields"
    # The proof is a candidate regardless of source evidence.  Do not allow a
    # caller to smuggle a validated procedure status through this path.
    parsed["governance"]["status"] = "draft"
    parsed["governance"]["validated"] = False
    return parsed, None


def _build_candidate(
    group_key: tuple[str, str, str, tuple[str, ...]],
    entries: Sequence[dict[str, Any]],
    *,
    runs_by_id: Mapping[str, Any],
    outcomes_by_run: Mapping[str, list[Any]],
    outcome_by_id: Mapping[str, Any],
    inbound_regressions: Mapping[str, Sequence[Any]],
    scan_complete: bool,
) -> dict[str, Any]:
    claim, boundary, authority_class, domains_tuple = group_key
    canonical = min(entries, key=lambda item: (int(item["sequence"]), str(item["event_id"]), str(item["run_id"])))
    grouped_by_run: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in entries:
        grouped_by_run[str(entry["run_id"])].append(entry)

    supporting: list[dict[str, Any]] = []
    contradicting: list[dict[str, Any]] = []
    neutral: list[dict[str, Any]] = []
    for run_id, run_entries in sorted(grouped_by_run.items()):
        outcome = run_entries[0]["outcome"]
        episode = _episode_for_run(run_id, run_entries, outcome, runs_by_id[run_id])
        bucket = _outcome_bucket(outcome, episode["outcome_evidence_refs"])
        if bucket == "supporting":
            supporting.append(episode)
        elif bucket == "contradicting":
            contradicting.append(episode)
        else:
            neutral.append(episode)

    known_contradictions = {str(episode["outcome_id"]) for episode in contradicting}
    for episode in [*supporting, *neutral]:
        for regression in inbound_regressions.get(str(episode["run_id"]), ()):
            outcome_id = str(regression["outcome_id"])
            if outcome_id not in known_contradictions:
                contradicting.append(_inbound_regression_episode(regression))
                known_contradictions.add(outcome_id)

    independent = _independent_supports(supporting, inbound_regressions)
    verifier_to_human = any(
        _has_verifier_to_human_chain(episode, outcomes_by_run.get(episode["run_id"], []), outcome_by_id)
        for episode in supporting
    )
    eligibility_reasons: list[str] = []
    if len(independent) >= 2:
        eligibility_reasons.append("two_independent_supporting_episodes")
    if verifier_to_human:
        eligibility_reasons.append("verifier_to_human_outcome_chain")
    eligible = bool(eligibility_reasons) and not contradicting and scan_complete
    if contradicting:
        confidence = "contested"
    elif verifier_to_human:
        confidence = "reviewed"
    elif any(episode["outcome_type"] == "partial_success" for episode in supporting):
        confidence = "provisional"
    elif eligible:
        confidence = "corroborated"
    else:
        confidence = "provisional"

    normalized_identity = {
        "schema": SHADOW_SCHEMA,
        "claim": claim,
        "boundary": boundary,
        "authority_class": authority_class,
        "domain_tags": list(domains_tuple),
        "supporting_episode_ids": [episode["run_id"] for episode in supporting],
        "contradicting_episode_ids": [episode["run_id"] for episode in contradicting],
        "neutral_episode_ids": [episode["run_id"] for episode in neutral],
        "evidence_refs": sorted(
            {ref for episode in [*supporting, *contradicting, *neutral] for ref in episode["evidence_refs"]}
        ),
    }
    candidate_hash = hashlib.sha256(_canonical_json(normalized_identity).encode("utf-8")).hexdigest()
    all_episodes = sorted([*supporting, *contradicting, *neutral], key=lambda item: str(item["run_id"]))
    union_refs = sorted({ref for episode in all_episodes for ref in episode["evidence_refs"]})
    procedure = canonical.get("procedure")
    review_action = "stage_hidden_candidate_for_human_review" if eligible else "collect_independent_evidence"
    if not scan_complete:
        review_action = "rescan_workspace_before_review"
    elif contradicting:
        review_action = "resolve_contradicting_evidence_before_review"
    return {
        "candidate_key": f"run-consolidation:{candidate_hash}",
        "candidate_hash": candidate_hash,
        "claim": claim,
        "boundary": boundary or None,
        "authority_class": authority_class,
        "domain_tags": list(domains_tuple),
        "procedure": procedure,
        "supporting_episode_ids": [episode["run_id"] for episode in supporting],
        "contradicting_episode_ids": [episode["run_id"] for episode in contradicting],
        "neutral_episode_ids": [episode["run_id"] for episode in neutral],
        "episodes": all_episodes,
        "evidence_refs": union_refs,
        "independence_count": len(independent),
        "independent_supporting_episode_ids": [episode["run_id"] for episode in independent],
        "eligibility_reason": (
            "scan_incomplete"
            if not scan_complete
            else "contradicting_episode_present"
            if contradicting
            else eligibility_reasons[0]
            if eligibility_reasons
            else "insufficient_independent_support"
        ),
        "basis_reason_codes": [
            *eligibility_reasons,
            *(["contradicting_episode_present"] if contradicting else []),
            *(["scan_incomplete"] if not scan_complete else []),
        ]
        or ["insufficient_independent_support"],
        "eligible": eligible,
        "confidence_label": confidence,
        "candidate_lesson": claim,
        "observed_conditions": canonical["observed_conditions"] or None,
        "when_not_to_use": canonical["when_not_to_use"] or None,
        "failure_mode": canonical["failure_mode"] or None,
        "rollback_path": canonical["rollback_path"] or None,
        "review_action": review_action,
    }


def _episode_for_run(run_id: str, entries: Sequence[dict[str, Any]], outcome: Any, run: Any) -> dict[str, Any]:
    proposal_refs = sorted({ref for entry in entries for ref in entry["evidence_refs"]})
    outcome_refs = _outcome_evidence_refs(outcome["evidence_json"])
    return {
        "run_id": run_id,
        "event_ids": [
            entry["event_id"] for entry in sorted(entries, key=lambda item: (item["sequence"], item["event_id"]))
        ],
        "outcome_id": str(outcome["outcome_id"]),
        "outcome_type": str(outcome["outcome_type"]),
        "outcome_authority_class": outcome_authority_class(outcome),
        "strong_verified": is_strong_verified_outcome(outcome),
        "evaluator_type": str(outcome["evaluator_type"]),
        "evaluator_id": _evaluator_id(outcome),
        "outcome_evidence_refs": outcome_refs,
        "proposal_evidence_refs": proposal_refs,
        "evidence_refs": sorted(set(proposal_refs).union(outcome_refs)),
        "thread_id": _identity_or_run(run["thread_id"], run_id),
        "client_session_id": _identity_or_run(run["client_session_id"], run_id),
    }


def _inbound_regression_episode(outcome: Any) -> dict[str, Any]:
    """Render a workspace-wide current regression without inventing a decision event."""

    outcome_refs = _outcome_evidence_refs(outcome["evidence_json"])
    run_id = str(outcome["run_id"])
    return {
        "run_id": run_id,
        "event_ids": [],
        "outcome_id": str(outcome["outcome_id"]),
        "outcome_type": str(outcome["outcome_type"]),
        "outcome_authority_class": outcome_authority_class(outcome),
        "strong_verified": is_strong_verified_outcome(outcome),
        "evaluator_type": str(outcome["evaluator_type"]),
        "evaluator_id": _evaluator_id(outcome),
        "outcome_evidence_refs": outcome_refs,
        "proposal_evidence_refs": [],
        "evidence_refs": outcome_refs,
        "thread_id": _identity_or_run(outcome["run_thread_id"], run_id),
        "client_session_id": _identity_or_run(outcome["run_client_session_id"], run_id),
        "regression_of_run_id": str(outcome["regression_of_run_id"]),
    }


def _outcome_bucket(outcome: Any, evidence_refs: Sequence[str]) -> str:
    outcome_type = str(outcome["outcome_type"])
    if is_strong_verified_outcome(outcome) and evidence_refs:
        return "supporting"
    if outcome_type in {"failed", "regression", "user_corrected"} and evidence_refs:
        return "contradicting"
    return "neutral"


def _independent_supports(
    supporting: Sequence[dict[str, Any]], inbound_regressions: Mapping[str, Sequence[Any]]
) -> list[dict[str, Any]]:
    ordered = sorted(supporting, key=lambda item: str(item["run_id"]))
    # Eligibility needs only a pair.  Finding that pair exhaustively avoids a
    # greedy first run masking two later independent episodes.
    for index, left in enumerate(ordered):
        for right in ordered[index + 1 :]:
            if _episodes_independent(left, right, inbound_regressions):
                selected = [left, right]
                for candidate in ordered:
                    if candidate not in selected and all(
                        _episodes_independent(candidate, existing, inbound_regressions) for existing in selected
                    ):
                        selected.append(candidate)
                return selected
    return ordered[:1]


def _episodes_independent(
    left: Mapping[str, Any], right: Mapping[str, Any], inbound_regressions: Mapping[str, Sequence[Any]]
) -> bool:
    if left["run_id"] == right["run_id"]:
        return False
    if left["thread_id"] == right["thread_id"] or left["client_session_id"] == right["client_session_id"]:
        return False
    if set(left["evidence_refs"]).intersection(right["evidence_refs"]):
        return False
    return not inbound_regressions.get(str(left["run_id"])) and not inbound_regressions.get(str(right["run_id"]))


def _current_outcome_heads(outcomes: Sequence[Any]) -> dict[str, Any]:
    superseded = {str(row["supersedes_outcome_id"]) for row in outcomes if row["supersedes_outcome_id"]}
    return {str(row["run_id"]): row for row in outcomes if str(row["outcome_id"]) not in superseded}


def _inbound_current_regressions(current_outcomes: Mapping[str, Any]) -> dict[str, list[Any]]:
    """Index only evidence-backed current regression heads by their target run."""

    indexed: dict[str, list[Any]] = defaultdict(list)
    for outcome in current_outcomes.values():
        target_run_id = str(outcome["regression_of_run_id"] or "")
        if (
            target_run_id
            and str(outcome["outcome_type"]) == "regression"
            and _outcome_evidence_refs(outcome["evidence_json"])
        ):
            indexed[target_run_id].append(outcome)
    for regressions in indexed.values():
        regressions.sort(key=lambda item: (str(item["run_id"]), str(item["outcome_id"])))
    return dict(indexed)


def _has_verifier_to_human_chain(
    episode: Mapping[str, Any], outcomes: Sequence[Any], outcome_by_id: Mapping[str, Any]
) -> bool:
    current = next((row for row in outcomes if str(row["outcome_id"]) == episode["outcome_id"]), None)
    if current is None or not is_strong_verified_outcome(current) or str(current["evaluator_type"]) != "human":
        return False
    if not _outcome_evidence_refs(current["evidence_json"]):
        return False
    parent_id = current["supersedes_outcome_id"]
    while parent_id:
        parent = outcome_by_id.get(str(parent_id))
        if parent is None:
            return False
        if (
            is_strong_verified_outcome(parent)
            and str(parent["evaluator_type"]) == "deterministic_verifier"
            and _outcome_evidence_refs(parent["evidence_json"])
        ):
            return True
        parent_id = parent["supersedes_outcome_id"]
    return False


def _stage_candidate(store: Any, workspace: str, candidate: Mapping[str, Any]) -> dict[str, Any]:
    candidate_key = str(candidate["candidate_key"])
    payload = {
        "schema": "memory.candidate.v1",
        "namespace": workspace,
        "authority_class": str(candidate["authority_class"]),
        "claim": str(candidate["candidate_lesson"]),
        "evidence_refs": list(candidate["evidence_refs"]),
        "domain_tags": list(candidate["domain_tags"]),
        "source_runtime": "amb-run-consolidation",
        "source_session_id": str(candidate["candidate_hash"]),
        "source_task_id": str(candidate["candidate_hash"]),
    }
    decision = evaluate_learning_candidate(payload)
    try:
        stored = store.store_learning_candidate(payload, decision, candidate_status="needs_review")
    except (RuntimeError, ValueError, OSError, sqlite3.Error) as exc:
        return {"candidate_key": candidate_key, "disposition": "error", "error": str(exc)}
    if stored.get("stored"):
        disposition = "stored"
    else:
        disposition = "duplicate"
    return {
        "candidate_key": candidate_key,
        "disposition": disposition,
        "candidate_status": stored.get("candidate_status"),
        "decision": stored.get("decision"),
        "record_id": stored.get("id"),
    }


def _normalize_refs(value: Any, *, required: bool) -> list[str] | None:
    if not isinstance(value, list) or not (1 <= len(value) <= 16 if required else len(value) <= 16):
        return None
    refs: list[str] = []
    for item in value:
        if not isinstance(item, str):
            return None
        cleaned = item.strip()
        if not _is_safe_opaque_ref(cleaned):
            return None
        refs.append(cleaned)
    if len(set(refs)) != len(refs):
        return None
    return sorted(refs)


def _normalize_domain_tags(value: Any) -> list[str] | None:
    if not isinstance(value, list) or len(value) > 16:
        return None
    tags: list[str] = []
    for raw in value:
        if not isinstance(raw, str):
            return None
        tag = raw.strip()
        if not DOMAIN_TAG_RE.fullmatch(tag):
            return None
        tags.append(tag)
    if len(set(tags)) != len(tags) or tags != sorted(tags):
        return None
    return tags


def _normalize_short_list(value: Any, *, maximum: int, item_limit: int) -> list[str] | None:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > maximum:
        return None
    result: list[str] = []
    for item in value:
        cleaned = _optional_bounded_text(item, item_limit)
        if not cleaned or cleaned in result:
            return None
        result.append(cleaned)
    return result


def _optional_bounded_text(value: Any, maximum: int) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split()).strip()
    if not cleaned or len(cleaned.encode("utf-8")) > maximum:
        return None
    return cleaned


def _contains_private_report_text(payload: Mapping[str, Any]) -> bool:
    for field in REPORT_TEXT_FIELDS:
        value = payload.get(field)
        values = value if isinstance(value, list) else [value]
        for item in values:
            if isinstance(item, str) and (PRIVATE_TEXT_RE.search(item) or PATH_LIKE_TEXT_RE.search(item)):
                return True
    return False


def _is_safe_opaque_ref(value: str) -> bool:
    if not OPAQUE_REF_RE.fullmatch(value):
        return False
    if "/" in value or "\\" in value or any(ord(character) < 32 for character in value):
        return False
    return not WINDOWS_DRIVE_RE.match(value) and SECRET_REF_RE.search(value) is None


def _outcome_evidence_refs(raw_evidence: Any) -> list[str]:
    """Hash structured outcome evidence without exposing it in reports or candidates."""

    evidence = _json_array(raw_evidence)
    if not evidence:
        return []
    refs: set[str] = set()
    for item in evidence:
        try:
            canonical = _canonical_json(item)
        except (TypeError, ValueError):
            return []
        if len(canonical.encode("utf-8")) > 32768:
            return []
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        refs.add(f"outcome-evidence-sha256:{digest}")
    return sorted(refs)


def _bounded_text(field: str, value: Any, maximum: int) -> str:
    cleaned = _optional_bounded_text(value, maximum)
    if not cleaned:
        raise ValueError(f"{field} must be a non-empty bounded string")
    return cleaned


def _json_array(value: Any) -> list[Any]:
    try:
        parsed = json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _identity_or_run(value: Any, run_id: str) -> str:
    text = str(value or "").strip()
    return text or run_id


def _evaluator_id(outcome: Any) -> str:
    digest = str(outcome["evaluator_digest"] or "").strip()
    version = str(outcome["evaluator_version"] or "").strip()
    suffix = digest or version or str(outcome["outcome_id"])
    return f"{outcome['evaluator_type']}:{suffix}"


def _is_watcher_rollout_idle(outcome: Any) -> bool:
    return str(outcome["evaluator_type"]) == "system" and str(outcome["termination_reason"] or "") == "rollout_idle"


def _excluded(run_id: str, event_id: str | None, reason_code: str) -> dict[str, Any]:
    payload: dict[str, Any] = {"run_id": run_id, "reason_code": reason_code}
    if event_id is not None:
        payload["event_id"] = event_id
    return payload


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
