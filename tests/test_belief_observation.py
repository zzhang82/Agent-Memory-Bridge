from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from agent_mem_bridge.belief_observation import (
    BeliefObservationConfig,
    observe_belief_ladder,
    render_belief_observation_text,
)
from agent_mem_bridge.storage import MemoryStore


def _store_ladder_record(
    store: MemoryStore,
    *,
    title: str,
    record_type: str,
    domain: str,
    claim: str,
    support_count: int,
    distinct_session_count: int,
    contradiction_count: int,
    contradiction_reasons: str = "",
    confidence: str,
    status: str,
    claim_hash: str,
    boundary_hash: str,
    supersedes: str | None = None,
    tags: list[str] | None = None,
) -> str:
    lines = [
        f"record_type: {record_type}",
        f"domain: {domain}",
        f"claim: {claim}",
        "boundary: This is the boundary.",
        f"support_count: {support_count}",
        f"distinct_session_count: {distinct_session_count}",
        f"contradiction_count: {contradiction_count}",
        f"contradiction_reasons: {contradiction_reasons}",
        f"confidence: {confidence}",
        "evidence_refs: evidence-1 | evidence-2",
        f"claim_hash: {claim_hash}",
        f"boundary_hash: {boundary_hash}",
        f"status: {status}",
        "staleness_policy: decay_if_unseen",
        "scope: global",
    ]
    if supersedes:
        lines.append(f"supersedes: {supersedes}")
    result = store.store(
        namespace="global",
        kind="memory",
        title=title,
        content="\n".join(lines),
        tags=tags or [f"kind:{record_type}", "source:consolidation", "control:belief", domain],
        actor="bridge-consolidation",
        source_app="agent-memory-bridge-consolidation",
    )
    return str(result["id"])


def _set_created_at(store: MemoryStore, memory_id: str, value: datetime) -> None:
    with store._connect() as conn:
        conn.execute(
            "UPDATE memories SET created_at = ? WHERE id = ?",
            (value.astimezone(UTC).isoformat(), memory_id),
        )
        conn.commit()


def test_observe_belief_ladder_summarizes_current_states(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    now = datetime.now(UTC)

    promoted_old = _store_ladder_record(
        store,
        title="[[Belief Candidate]] retrieval old",
        record_type="belief-candidate",
        domain="domain:retrieval",
        claim="Prefer bundle-first retrieval before legacy docs.",
        support_count=3,
        distinct_session_count=3,
        contradiction_count=0,
        confidence="candidate",
        status="candidate",
        claim_hash="hash-a",
        boundary_hash="boundary-a",
        tags=["kind:belief-candidate", "source:consolidation", "control:belief", "domain:retrieval"],
    )
    promoted_new = _store_ladder_record(
        store,
        title="[[Belief Candidate]] retrieval new",
        record_type="belief-candidate",
        domain="domain:retrieval",
        claim="Prefer bundle-first retrieval before legacy docs.",
        support_count=5,
        distinct_session_count=4,
        contradiction_count=0,
        confidence="strong-candidate",
        status="candidate",
        claim_hash="hash-a",
        boundary_hash="boundary-a",
        supersedes=promoted_old,
        tags=["kind:belief-candidate", "source:consolidation", "control:belief", "domain:retrieval"],
    )
    _store_ladder_record(
        store,
        title="[[Belief]] retrieval pattern",
        record_type="belief",
        domain="domain:retrieval",
        claim="Prefer bundle-first retrieval before legacy docs.",
        support_count=5,
        distinct_session_count=4,
        contradiction_count=0,
        confidence="0.83",
        status="active",
        claim_hash="hash-a",
        boundary_hash="boundary-a",
        tags=["kind:belief", "source:consolidation", "control:belief", "domain:retrieval", "status:active"],
    )

    _store_ladder_record(
        store,
        title="[[Belief Candidate]] contradiction",
        record_type="belief-candidate",
        domain="domain:memory-bridge",
        claim="Keep one runtime path while preserving however-branches.",
        support_count=4,
        distinct_session_count=3,
        contradiction_count=2,
        contradiction_reasons="marker-contrast:1 | strong-cue:1",
        confidence="tentative",
        status="candidate",
        claim_hash="hash-b",
        boundary_hash="boundary-b",
        tags=["kind:belief-candidate", "source:consolidation", "control:belief", "domain:memory-bridge"],
    )

    _store_ladder_record(
        store,
        title="[[Belief Candidate]] low support",
        record_type="belief-candidate",
        domain="domain:agent-memory",
        claim="Keep beliefs narrow when support is weak.",
        support_count=2,
        distinct_session_count=1,
        contradiction_count=0,
        confidence="candidate",
        status="candidate",
        claim_hash="hash-c",
        boundary_hash="boundary-c",
        tags=["kind:belief-candidate", "source:consolidation", "control:belief", "domain:agent-memory"],
    )

    stable_block = _store_ladder_record(
        store,
        title="[[Belief Candidate]] stability blocked",
        record_type="belief-candidate",
        domain="domain:startup-protocol",
        claim="Keep project memory explicit in startup.",
        support_count=4,
        distinct_session_count=3,
        contradiction_count=0,
        confidence="strong-candidate",
        status="candidate",
        claim_hash="hash-d",
        boundary_hash="boundary-d",
        tags=["kind:belief-candidate", "source:consolidation", "control:belief", "domain:startup-protocol"],
    )

    stale = _store_ladder_record(
        store,
        title="[[Belief Candidate]] stale",
        record_type="belief-candidate",
        domain="domain:maintenance",
        claim="Prune stale memory before it crowds startup.",
        support_count=4,
        distinct_session_count=3,
        contradiction_count=0,
        confidence="strong-candidate",
        status="candidate",
        claim_hash="hash-e",
        boundary_hash="boundary-e",
        tags=["kind:belief-candidate", "source:consolidation", "control:belief", "domain:maintenance"],
    )
    _set_created_at(store, stale, now - timedelta(days=30))

    _store_ladder_record(
        store,
        title="[[Domain Note]] retrieval synthesis",
        record_type="domain-note",
        domain="domain:retrieval",
        claim="Recent patterns concentrate on bundle-first retrieval.",
        support_count=5,
        distinct_session_count=0,
        contradiction_count=0,
        confidence="observed",
        status="active",
        claim_hash="note-a",
        boundary_hash="note-boundary-a",
        tags=["kind:domain-note", "source:consolidation", "domain:retrieval"],
    )
    _store_ladder_record(
        store,
        title="[[Domain Note]] startup synthesis",
        record_type="domain-note",
        domain="domain:startup-protocol",
        claim="Recent patterns concentrate on explicit project memory.",
        support_count=4,
        distinct_session_count=0,
        contradiction_count=0,
        confidence="observed",
        status="active",
        claim_hash="note-b",
        boundary_hash="note-boundary-b",
        tags=["kind:domain-note", "source:consolidation", "domain:startup-protocol"],
    )

    report = observe_belief_ladder(
        store,
        BeliefObservationConfig(namespace="global", actor="bridge-consolidation", top_n=10),
    )

    assert report["summary"]["belief_candidate_count"] == 5
    assert report["summary"]["belief_count"] == 1
    assert report["summary"]["domain_note_count"] == 2
    assert report["summary"]["candidate_to_belief_rate"] == 0.2
    assert report["summary"]["blocked_by_contradiction"] == 1
    assert report["summary"]["blocked_by_staleness"] == 1
    assert report["summary"]["blocked_by_low_support"] == 1
    assert report["summary"]["blocked_by_stability"] == 1
    assert report["summary"]["contradiction_reason_counts"] == {
        "marker-contrast": 1,
        "strong-cue": 1,
    }
    assert report["summary"]["supersede_rate"] == 0.2
    assert report["summary"]["startup_belief_default_loaded"] is False
    assert report["summary"]["startup_belief_hit_rate"] == 0.0

    candidate_statuses = {row["claim"]: row["status"] for row in report["leaderboards"]["candidates"]}
    assert candidate_statuses["Prefer bundle-first retrieval before legacy docs."] == "promoted"
    assert candidate_statuses["Keep one runtime path while preserving however-branches."] == "blocked-contradiction"
    assert candidate_statuses["Keep beliefs narrow when support is weak."] == "blocked-low-support"
    assert candidate_statuses["Keep project memory explicit in startup."] == "blocked-stability"
    assert candidate_statuses["Prune stale memory before it crowds startup."] == "stale"

    belief_row = report["leaderboards"]["beliefs"][0]
    assert belief_row["claim"] == "Prefer bundle-first retrieval before legacy docs."
    assert belief_row["status"] == "belief"
    contradiction_row = next(
        row for row in report["leaderboards"]["candidates"] if row["claim"] == "Keep one runtime path while preserving however-branches."
    )
    assert contradiction_row["contradiction_reason_counts"] == {"marker-contrast": 1, "strong-cue": 1}

    by_domain = {row["domain"]: row for row in report["cohorts"]["by_domain"]}
    assert by_domain["domain:retrieval"]["domain_note_count"] == 1
    assert by_domain["domain:retrieval"]["candidate_count"] == 1
    assert by_domain["domain:retrieval"]["belief_count"] == 1
    assert by_domain["domain:memory-bridge"]["blocked_by_contradiction"] == 1


def test_render_belief_observation_text_includes_key_metrics(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    _store_ladder_record(
        store,
        title="[[Belief Candidate]] startup",
        record_type="belief-candidate",
        domain="domain:startup-protocol",
        claim="Keep startup compact and explicit.",
        support_count=4,
        distinct_session_count=3,
        contradiction_count=0,
        confidence="strong-candidate",
        status="candidate",
        claim_hash="hash-z",
        boundary_hash="boundary-z",
        tags=["kind:belief-candidate", "source:consolidation", "control:belief", "domain:startup-protocol"],
    )

    report = observe_belief_ladder(
        store,
        BeliefObservationConfig(namespace="global", actor="bridge-consolidation", top_n=5),
    )
    rendered = render_belief_observation_text(report)

    assert "Belief Ladder Observation" in rendered
    assert "candidates: 1" in rendered
    assert "beliefs: 0" in rendered
    assert "Candidate Leaderboard" in rendered
    assert "contradiction_reason_counts" in rendered
    assert "Keep startup compact and explicit." in rendered
