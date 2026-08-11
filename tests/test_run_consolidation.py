from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from agent_mem_bridge import cli as cli_module
from agent_mem_bridge.cli import main
from agent_mem_bridge.run_consolidation import (
    EVIDENCE_SCHEMA,
    OPPOSITION_SCHEMA,
    _parse_evidence_payload,
    build_run_consolidation_report,
    render_run_consolidation_markdown,
    stage_run_consolidation_report,
)
from agent_mem_bridge.run_projection import apply_run_outcome_projection
from agent_mem_bridge.storage import MemoryStore


def _store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(tmp_path / "bridge.sqlite3", log_dir=tmp_path / "logs")


def _decision_payload(
    claim: str = "Run the deterministic proof before release.",
    *,
    evidence_ref: str = "event:proof",
    authority_class: str = "decision",
    domains: list[str] | None = None,
    **extra: object,
) -> dict[str, object]:
    return {
        "schema": EVIDENCE_SCHEMA,
        "claim": claim,
        "evidence_refs": [evidence_ref],
        "authority_class": authority_class,
        "domain_tags": domains or ["domain:release"],
        **extra,
    }


def _run_with_decision(
    store: MemoryStore,
    suffix: str,
    *,
    payload: dict[str, object] | None = None,
    outcome: str = "partial_success",
    evaluator_type: str = "deterministic_verifier",
    outcome_evidence: list[object] | None = None,
    thread_id: str | None = None,
    session_id: str | None = None,
    termination_reason: str | None = None,
) -> dict[str, object]:
    begun = store.begin_run(
        workspace_key="project:bridge",
        goal=f"goal {suffix}",
        idempotency_key=f"begin:{suffix}",
        thread_id=thread_id or f"thread:{suffix}",
        provenance={"client_session_id": session_id or f"session:{suffix}"},
    )
    store.record_run_event(
        workspace_key="project:bridge",
        run_id=str(begun["run_id"]),
        work_item_id=str(begun["root_work_item_id"]),
        event_type="decision",
        summary="Record bounded release evidence.",
        payload=payload or _decision_payload(evidence_ref=f"event:{suffix}"),
        idempotency_key=f"event:{suffix}",
    )
    store.record_run_event(
        workspace_key="project:bridge",
        run_id=str(begun["run_id"]),
        work_item_id=str(begun["root_work_item_id"]),
        event_type="work_item_completed",
        summary="Complete the root work item before recording the outcome.",
        idempotency_key=f"event:{suffix}:completed",
    )
    completed = store.complete_run(
        workspace_key="project:bridge",
        run_id=str(begun["run_id"]),
        outcome=outcome,
        evaluator_type=evaluator_type,
        evidence=outcome_evidence if outcome_evidence is not None else [f"test:{suffix}"],
        termination_reason=termination_reason,
        idempotency_key=f"outcome:{suffix}",
    )
    return {**begun, **{f"outcome_{key}": value for key, value in completed.items()}}


def _run_with_outcome_only(
    store: MemoryStore,
    suffix: str,
    *,
    outcome: str,
    evaluator_type: str = "agent",
    outcome_evidence: list[object] | None = None,
    regression_of_run_id: str | None = None,
) -> dict[str, object]:
    begun = store.begin_run(
        workspace_key="project:bridge",
        goal=f"outcome-only goal {suffix}",
        idempotency_key=f"begin:outcome-only:{suffix}",
        thread_id=f"thread:outcome-only:{suffix}",
        provenance={"client_session_id": f"session:outcome-only:{suffix}"},
    )
    store.record_run_event(
        workspace_key="project:bridge",
        run_id=str(begun["run_id"]),
        work_item_id=str(begun["root_work_item_id"]),
        event_type="work_item_completed",
        summary="Complete the root work item before recording the outcome.",
        idempotency_key=f"event:outcome-only:{suffix}:completed",
    )
    evidence = outcome_evidence if outcome_evidence is not None else [f"test:outcome-only:{suffix}"]
    if outcome == "regression":
        # Schema-v8/v1 regression rows remain readable contradiction evidence, but
        # 0.27.1 no longer allows callers to create them against declared success.
        completed = _insert_legacy_regression_outcome(
            store,
            run_id=str(begun["run_id"]),
            target_run_id=str(regression_of_run_id),
            suffix=suffix,
            evidence=evidence,
        )
    else:
        completed = store.complete_run(
            workspace_key="project:bridge",
            run_id=str(begun["run_id"]),
            outcome=outcome,
            evaluator_type=evaluator_type,
            evidence=evidence,
            regression_of_run_id=regression_of_run_id,
            idempotency_key=f"outcome:outcome-only:{suffix}",
        )
    return {**begun, **{f"outcome_{key}": value for key, value in completed.items()}}


def _insert_legacy_regression_outcome(
    store: MemoryStore,
    *,
    run_id: str,
    target_run_id: str,
    suffix: str,
    evidence: list[object],
) -> dict[str, object]:
    outcome_id = f"outcome_{hashlib.sha256(f'outcome:{suffix}'.encode()).hexdigest()[:32]}"
    idempotency_digest = hashlib.sha256(f"idempotency:{suffix}".encode()).hexdigest()
    request_digest = hashlib.sha256(f"request:{suffix}".encode()).hexdigest()
    created_at = store._utc_now()
    with store._connect() as conn:
        conn.execute(
            """
            INSERT INTO run_outcomes (
                outcome_id, run_id, outcome_type, evaluator_type, evidence_json,
                metrics_json, regression_of_run_id, idempotency_key_digest,
                request_digest, created_at
            ) VALUES (?, ?, 'regression', 'agent', ?, '{}', ?, ?, ?, ?)
            """,
            (
                outcome_id,
                run_id,
                json.dumps(evidence),
                target_run_id,
                idempotency_digest,
                request_digest,
                created_at,
            ),
        )
        apply_run_outcome_projection(
            conn,
            run_id=run_id,
            outcome_id=outcome_id,
            outcome_type="regression",
            termination_reason=None,
            created_at=created_at,
        )
    return {
        "outcome_id": outcome_id,
        "run_id": run_id,
        "outcome": "regression",
        "regression_of_run_id": target_run_id,
    }


def _counts(store: MemoryStore) -> dict[str, int]:
    tables = ("agent_runs", "run_events", "run_outcomes", "run_memory_links", "memories", "memory_utility_shadow")
    with store._connect() as conn:
        return {table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]) for table in tables}


def _database_dump(path: Path) -> tuple[str, ...]:
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        return tuple(connection.iterdump())
    finally:
        connection.close()


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _insert_workspace_runs(store: MemoryStore, *, count: int, workspace_key: str = "project:paged") -> None:
    rows = [
        (
            f"run_{index:032x}",
            workspace_key,
            "Page through existing runs.",
            _digest(f"idempotency:{index}"),
            _digest(f"request:{index}"),
            "2026-08-09T00:00:00+00:00",
        )
        for index in range(count)
    ]
    with store._connect() as conn:
        conn.executemany(
            """
            INSERT INTO agent_runs (
                run_id, workspace_key, root_goal, idempotency_key_digest,
                request_digest, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            rows,
        )


def _seed_legacy_v1_declared_run(store: MemoryStore, suffix: str) -> None:
    run_id = f"run_{_digest(f'legacy-run:{suffix}')[:32]}"
    work_item_id = f"work_{_digest(f'legacy-work:{suffix}')[:32]}"
    event_id = f"evt_{_digest(f'legacy-event:{suffix}')[:32]}"
    outcome_id = f"outcome_{_digest(f'legacy-outcome:{suffix}')[:32]}"
    created_at = "2026-08-09T00:00:00+00:00"
    with store._connect() as conn:
        conn.execute(
            """
            INSERT INTO agent_runs (
                run_id, workspace_key, root_goal, thread_id, client_session_id,
                evidence_profile, idempotency_key_digest, request_digest, created_at
            ) VALUES (?, 'project:bridge', 'Legacy declared evidence fixture.', ?, ?, 'legacy-v1', ?, ?, ?)
            """,
            (
                run_id,
                f"thread:legacy:{suffix}",
                f"session:legacy:{suffix}",
                _digest(f"legacy-run:idempotency:{suffix}"),
                _digest(f"legacy-run:request:{suffix}"),
                created_at,
            ),
        )
        conn.execute(
            """
            INSERT INTO run_work_items (work_item_id, run_id, parent_work_item_id, goal, created_at)
            VALUES (?, ?, NULL, 'Legacy declared evidence fixture.', ?)
            """,
            (work_item_id, run_id, created_at),
        )
        conn.execute(
            """
            INSERT INTO run_events (
                event_id, run_id, work_item_id, sequence, event_type, summary,
                payload_json, evidence_json, idempotency_key_digest, request_digest, created_at
            ) VALUES (?, ?, ?, 1, 'decision', 'Legacy declared decision.', ?, '[]', ?, ?, ?)
            """,
            (
                event_id,
                run_id,
                work_item_id,
                json.dumps(_decision_payload(evidence_ref=f"event:legacy:{suffix}")),
                _digest(f"legacy-event:idempotency:{suffix}"),
                _digest(f"legacy-event:request:{suffix}"),
                created_at,
            ),
        )
        conn.execute(
            """
            INSERT INTO run_outcomes (
                outcome_id, run_id, outcome_type, evaluator_type, evidence_json,
                metrics_json, idempotency_key_digest, request_digest, created_at
            ) VALUES (?, ?, 'verified_success', 'deterministic_verifier', ?, '{}', ?, ?, ?)
            """,
            (
                outcome_id,
                run_id,
                json.dumps([f"test:legacy:{suffix}"]),
                _digest(f"legacy-outcome:idempotency:{suffix}"),
                _digest(f"legacy-outcome:request:{suffix}"),
                created_at,
            ),
        )


def _governed_preflight_payload() -> dict[str, object]:
    return {
        "approved": True,
        "confirmed_facts": [],
        "reasonable_inferences": [],
        "unverified_hypotheses": [],
        "missing_information": [],
        "alternatives_considered": [],
        "hidden_risks": [],
        "maintenance_cost": [],
        "maintenance_impact": [],
        "verification_plan": [],
    }


def _run_with_governed_consolidation_evidence(
    store: MemoryStore,
    suffix: str,
    *,
    evaluator_digest: str,
    model_digest: str | None = None,
    payload: dict[str, object] | None = None,
) -> dict[str, object]:
    begun = store.begin_run(
        workspace_key="project:bridge",
        goal=f"governed goal {suffix}",
        idempotency_key=f"begin:governed:{suffix}",
        thread_id=f"thread:governed:{suffix}",
        model_digest=model_digest,
        evidence_profile="governed-v2",
        acceptance_criteria=[{"criterion_id": "tests", "description": "Focused tests pass."}],
        constraints=[],
        non_goals=[],
        risk_level="low",
        provenance={"client_session_id": f"session:governed:{suffix}"},
    )
    preflight = store.record_run_event(
        workspace_key="project:bridge",
        run_id=str(begun["run_id"]),
        work_item_id=str(begun["root_work_item_id"]),
        event_type="preflight_review",
        event_schema_version=2,
        summary="Operator preflight approved the bounded consolidation evidence.",
        payload=_governed_preflight_payload(),
        idempotency_key=f"event:governed:{suffix}:preflight",
        expected_database_epoch=str(begun["database_epoch"]),
        expected_run_generation=int(begun["run_generation"]),
        expected_last_sequence=0,
        expected_work_item_status="active",
    )
    decision = store.record_run_event(
        workspace_key="project:bridge",
        run_id=str(begun["run_id"]),
        work_item_id=str(begun["root_work_item_id"]),
        event_type="decision",
        event_schema_version=2,
        summary="Governed execution selected the bounded release decision.",
        payload={
            "selected_decision": "Run the deterministic proof before release.",
            "alternatives_considered": [],
            "evidence_refs": [f"governed-decision:{suffix}"],
            "maintenance_summary": {"cost": "bounded", "impact": "reviewed"},
        },
        idempotency_key=f"event:governed:{suffix}:decision",
        expected_database_epoch=str(preflight["database_epoch"]),
        expected_run_generation=int(preflight["run_generation"]),
        expected_last_sequence=int(preflight["sequence"]),
        expected_work_item_status="active",
    )
    completed = store.record_run_event(
        workspace_key="project:bridge",
        run_id=str(begun["run_id"]),
        work_item_id=str(begun["root_work_item_id"]),
        event_type="work_item_completed",
        event_schema_version=2,
        summary="The governed work item completed.",
        payload={},
        idempotency_key=f"event:governed:{suffix}:completed",
        expected_database_epoch=str(decision["database_epoch"]),
        expected_run_generation=int(decision["run_generation"]),
        expected_last_sequence=int(decision["sequence"]),
        expected_work_item_status="active",
    )
    receipt = store.mint_operator_verification_receipt(
        workspace_key="project:bridge",
        run_id=str(begun["run_id"]),
        preflight_event_id=str(preflight["event_id"]),
        evaluator_digest=evaluator_digest,
        evaluator_version="operator-review-v1",
        criterion_results=[{"criterion_id": "tests", "result": "passed", "evidence_refs": [f"pytest:{suffix}"]}],
        result="verified_success",
        evidence=[{"kind": "test", "reference": f"pytest:{suffix}"}],
        actor="operator-a",
    )
    outcome = store.complete_run(
        workspace_key="project:bridge",
        run_id=str(begun["run_id"]),
        outcome="verified_success",
        evaluator_type="human",
        evaluator_digest=evaluator_digest,
        evaluator_version="operator-review-v1",
        verification_receipt_id=str(receipt["verification_receipt_id"]),
        idempotency_key=f"outcome:governed:{suffix}",
        expected_database_epoch=str(completed["database_epoch"]),
        expected_run_generation=int(completed["run_generation"]),
        expected_last_sequence=int(completed["sequence"]),
    )
    with store._connect() as conn:
        conn.execute(
            """
            INSERT INTO run_events (
                event_id, run_id, work_item_id, sequence, event_type,
                event_schema_version, summary, payload_json, evidence_json,
                idempotency_key_digest, request_digest, created_at
            ) VALUES (?, ?, ?, 4, 'decision', 1, ?, ?, '[]', ?, ?, ?)
            """,
            (
                f"evt_{_digest(f'consolidation:{suffix}')[:32]}",
                str(begun["run_id"]),
                str(begun["root_work_item_id"]),
                "Compatibility consolidation evidence fixture.",
                json.dumps(payload or _decision_payload(evidence_ref=f"event:governed:{suffix}")),
                _digest(f"consolidation:idempotency:{suffix}"),
                _digest(f"consolidation:request:{suffix}"),
                store._utc_now(),
            ),
        )
    return {**begun, **{f"outcome_{key}": value for key, value in outcome.items()}}


def test_shadow_is_zero_write_and_observational_supports_stay_ineligible(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _run_with_decision(store, "one")
    _run_with_decision(store, "two")
    before = _counts(store)

    first = build_run_consolidation_report(store, workspace_key="project:bridge")
    second = build_run_consolidation_report(store, workspace_key="project:bridge")

    assert _counts(store) == before
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert first["schema"] == "amb.run-consolidation-shadow.v1"
    assert first["eligible_candidate_count"] == 0
    candidate = first["candidates"][0]
    assert candidate["eligible"] is False
    assert candidate["confidence_label"] == "provisional"
    assert candidate["independence_count"] == 0
    assert candidate["supporting_episode_ids"] == []
    assert len(candidate["neutral_episode_ids"]) == 2
    assert {episode["outcome_authority_class"] for episode in candidate["episodes"]} == {"observational"}
    assert not any(episode["strong_verified"] for episode in candidate["episodes"])
    assert "generated_at" not in candidate
    assert "Run the deterministic proof" in render_run_consolidation_markdown(first)


def test_legacy_v1_and_observational_verified_successes_remain_neutral(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed_legacy_v1_declared_run(store, "one")
    _run_with_decision(store, "two")

    candidate = build_run_consolidation_report(store, workspace_key="project:bridge")["candidates"][0]

    assert candidate["eligible"] is False
    assert candidate["supporting_episode_ids"] == []
    assert candidate["neutral_episode_ids"]
    assert {episode["outcome_authority_class"] for episode in candidate["episodes"]} == {
        "legacy_declared",
        "observational",
    }
    assert not any(episode["strong_verified"] for episode in candidate["episodes"])


def test_dependent_runs_do_not_qualify_on_shared_thread_session_or_evidence(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _run_with_decision(store, "one", thread_id="thread:shared")
    _run_with_decision(store, "two", thread_id="thread:shared")
    report = build_run_consolidation_report(store, workspace_key="project:bridge")

    candidate = report["candidates"][0]
    assert candidate["eligible"] is False
    assert candidate["independence_count"] == 0


def test_payload_privacy_unknown_fields_and_watcher_closeout_are_excluded(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(ValueError, match="durable run structured data rejects field"):
        _run_with_decision(store, "raw", payload=_decision_payload(reasoning_blob="not retained"))
    _, reason = _parse_evidence_payload(json.dumps(_decision_payload(nested={"Analysis": "not retained"})))
    assert reason == "forbidden_payload_field"
    _run_with_decision(store, "unknown", payload=_decision_payload(confidence=0.9))
    _run_with_decision(
        store,
        "watcher",
        outcome="unverified",
        evaluator_type="system",
        termination_reason="rollout_idle",
    )
    report = build_run_consolidation_report(store, workspace_key="project:bridge")

    reason_codes = {item["reason_code"] for item in report["excluded"]}
    assert {"unknown_payload_field", "watcher_rollout_idle"}.issubset(reason_codes)
    assert report["candidates"] == []


def test_procedure_uses_parser_and_never_marks_candidate_validated(tmp_path: Path) -> None:
    store = _store(tmp_path)
    payload = _decision_payload(
        authority_class="procedure",
        goal="Prove the migration.",
        when_to_use="Before a schema release.",
        steps=["Run the deterministic proof."],
        applies_to_domains=["domain:release"],
    )
    _run_with_decision(store, "one", payload=payload)
    _run_with_decision(store, "two", payload=payload)
    report = build_run_consolidation_report(store, workspace_key="project:bridge")

    procedure = report["candidates"][0]["procedure"]
    assert procedure["governance"]["status"] == "draft"
    assert procedure["governance"]["validated"] is False
    assert procedure["governance"]["missing_minimum_fields"] == []


def test_observational_verifier_to_human_chain_remains_neutral_and_ineligible(tmp_path: Path) -> None:
    store = _store(tmp_path)
    begun = store.begin_run(
        workspace_key="project:bridge",
        goal="chain",
        idempotency_key="begin:chain",
        thread_id="chain",
        provenance={"client_session_id": "chain"},
    )
    store.record_run_event(
        workspace_key="project:bridge",
        run_id=str(begun["run_id"]),
        work_item_id=str(begun["root_work_item_id"]),
        event_type="decision",
        summary="Decision",
        payload=_decision_payload(evidence_ref="event:chain"),
        idempotency_key="event:chain",
    )
    store.record_run_event(
        workspace_key="project:bridge",
        run_id=str(begun["run_id"]),
        work_item_id=str(begun["root_work_item_id"]),
        event_type="work_item_completed",
        summary="Complete the root work item before recording the outcome.",
        idempotency_key="event:chain:completed",
    )
    verifier = store.complete_run(
        workspace_key="project:bridge",
        run_id=str(begun["run_id"]),
        outcome="partial_success",
        evaluator_type="deterministic_verifier",
        evidence=["test:chain"],
        idempotency_key="outcome:verifier",
    )
    store.complete_run(
        workspace_key="project:bridge",
        run_id=str(begun["run_id"]),
        outcome="partial_success",
        evaluator_type="human",
        evidence=["human:chain"],
        supersedes_outcome_id=str(verifier["outcome_id"]),
        idempotency_key="outcome:human",
    )

    candidate = build_run_consolidation_report(store, workspace_key="project:bridge")["candidates"][0]
    assert candidate["eligible"] is False
    assert candidate["eligibility_reason"] == "insufficient_independent_support"
    assert candidate["confidence_label"] == "provisional"
    assert candidate["episodes"][0]["outcome_authority_class"] == "observational"
    assert candidate["episodes"][0]["strong_verified"] is False


def test_legacy_declared_candidates_do_not_stage_and_explicit_stage_stays_hidden(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _run_with_decision(store, "one")
    _run_with_decision(store, "two")

    declared = build_run_consolidation_report(store, workspace_key="project:bridge", stage=True)
    assert declared["write_counts"] == {"stored": 0, "duplicate": 0, "error": 0}

    staged_report = build_run_consolidation_report(store, workspace_key="project:bridge")
    staged_report["candidates"][0]["eligible"] = True
    staged = stage_run_consolidation_report(store, staged_report)
    retried_report = build_run_consolidation_report(store, workspace_key="project:bridge")
    retried_report["candidates"][0]["eligible"] = True
    retried = stage_run_consolidation_report(store, retried_report)

    assert staged["write_counts"] == {"stored": 1, "duplicate": 0, "error": 0}
    assert retried["write_counts"] == {"stored": 0, "duplicate": 1, "error": 0}
    with store._connect() as conn:
        row = conn.execute("SELECT is_learning_candidate, tags_json FROM memories").fetchone()
    assert row is not None
    assert row["is_learning_candidate"] == 1
    assert "candidate_status:needs_review" in json.loads(row["tags_json"])


def test_structured_outcome_evidence_is_hashed_not_reported_or_staged_raw(tmp_path: Path) -> None:
    store = _store(tmp_path)
    private_evidence = {"artifact": r"C:\\private\\source.py", "result": "pass", "token": "not-for-report"}
    _run_with_decision(store, "one", outcome_evidence=[private_evidence])
    _run_with_decision(store, "two", outcome_evidence=[["nested", {"artifact": "/tmp/private.log", "result": "pass"}]])

    report = build_run_consolidation_report(store, workspace_key="project:bridge", stage=True)

    encoded = json.dumps(report, sort_keys=True)
    markdown = render_run_consolidation_markdown(report)
    assert "private" not in encoded
    assert "not-for-report" not in encoded
    assert "private" not in markdown
    assert all(
        ref.startswith("outcome-evidence-sha256:")
        for ref in report["candidates"][0]["episodes"][0]["outcome_evidence_refs"]
    )
    with store._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("claim", r"Do not leak C:\\Users\\frank\\secret.txt"),
        ("boundary", "/tmp/private-artifact"),
        ("observed_conditions", r"\\server\\share\\private"),
        ("rollback_path", "file:///home/frank/private"),
    ],
)
def test_private_text_and_path_like_refs_are_excluded_without_leaking(tmp_path: Path, field: str, value: str) -> None:
    store = _store(tmp_path)
    payload = _decision_payload(**{field: value})
    _run_with_decision(store, "private", payload=payload)
    report = build_run_consolidation_report(store, workspace_key="project:bridge", stage=True)

    assert "privacy_sensitive_payload" in {item["reason_code"] for item in report["excluded"]}
    assert value not in json.dumps(report, sort_keys=True)
    assert value not in render_run_consolidation_markdown(report)
    assert report["write_counts"] == {"stored": 0, "duplicate": 0, "error": 0}
    for reference in ("/tmp/private", r"C:\\private", r"\\server\\share"):
        _run_with_decision(store, f"ref-{len(reference)}", payload=_decision_payload(evidence_ref=reference))
    references_report = build_run_consolidation_report(store, workspace_key="project:bridge")
    assert "invalid_evidence_refs" in {item["reason_code"] for item in references_report["excluded"]}


def test_empty_failure_evidence_is_neutral_not_a_contradiction(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _run_with_decision(store, "one")
    _run_with_decision(store, "two")
    _run_with_decision(store, "failure", outcome="failed", evaluator_type="agent", outcome_evidence=[])

    candidate = build_run_consolidation_report(store, workspace_key="project:bridge")["candidates"][0]
    assert candidate["contradicting_episode_ids"] == []
    assert candidate["confidence_label"] == "provisional"


def test_evidence_backed_contradiction_blocks_eligibility_and_staging(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _run_with_decision(store, "one")
    _run_with_decision(store, "two")
    _run_with_decision(
        store, "contradiction", outcome="failed", evaluator_type="agent", outcome_evidence=["test:failed"]
    )

    report = build_run_consolidation_report(store, workspace_key="project:bridge", stage=True)
    candidate = report["candidates"][0]

    assert candidate["contradicting_episode_ids"]
    assert candidate["confidence_label"] == "contested"
    assert candidate["eligible"] is False
    assert candidate["eligibility_reason"] == "contradicting_episode_present"
    assert candidate["review_action"] == "resolve_contradicting_evidence_before_review"
    assert report["stage_results"] == []
    assert report["write_counts"] == {"stored": 0, "duplicate": 0, "error": 0}
    with store._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 0


def test_current_inbound_regression_without_a_decision_event_blocks_supporting_candidate(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = _run_with_decision(store, "support-one")
    _run_with_decision(store, "support-two")
    regression = _run_with_outcome_only(
        store,
        "inbound-regression",
        outcome="regression",
        outcome_evidence=["test:inbound-regression"],
        regression_of_run_id=str(first["run_id"]),
    )

    report = build_run_consolidation_report(store, workspace_key="project:bridge", limit=3)
    candidate = report["candidates"][0]

    assert candidate["eligible"] is False
    assert candidate["eligibility_reason"] == "contradicting_episode_present"
    assert str(regression["run_id"]) in candidate["contradicting_episode_ids"]
    inbound_episode = next(episode for episode in candidate["episodes"] if episode["run_id"] == regression["run_id"])
    assert inbound_episode["event_ids"] == []
    assert inbound_episode["regression_of_run_id"] == first["run_id"]


def test_workspace_wide_inbound_regression_is_found_across_keyset_pages(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = _run_with_decision(store, "support-one")
    _run_with_decision(store, "support-two")
    regression = _run_with_outcome_only(
        store,
        "inbound-regression",
        outcome="regression",
        outcome_evidence=["test:inbound-regression"],
        regression_of_run_id=str(first["run_id"]),
    )

    report = build_run_consolidation_report(store, workspace_key="project:bridge", limit=2)
    candidate = report["candidates"][0]

    assert report["scan"]["complete"] is True
    assert report["scan"]["workspace_run_count"] == 3
    assert report["scan"]["scanned_run_count"] == 3
    assert report["scan"]["omitted_run_count"] == 0
    assert report["scan"]["page_count"] == 2
    assert report["scan"]["page_size"] == 2
    assert report["scan"]["snapshot_database_epoch"]
    assert report["scan"]["last_scanned_key"]["run_id"] == regression["run_id"]
    assert candidate["eligibility_reason"] == "contradicting_episode_present"
    assert str(regression["run_id"]) in candidate["contradicting_episode_ids"]


def test_superseded_inbound_regression_does_not_block_current_support(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = _run_with_decision(store, "support-one")
    _run_with_decision(store, "support-two")
    regression = _run_with_outcome_only(
        store,
        "inbound-regression",
        outcome="regression",
        outcome_evidence=["test:inbound-regression"],
        regression_of_run_id=str(first["run_id"]),
    )
    store.complete_run(
        workspace_key="project:bridge",
        run_id=str(regression["run_id"]),
        outcome="partial_success",
        evaluator_type="human",
        evidence=["review:regression-corrected"],
        supersedes_outcome_id=str(regression["outcome_outcome_id"]),
        idempotency_key="outcome:inbound-regression-corrected",
    )

    report = build_run_consolidation_report(store, workspace_key="project:bridge", limit=3)
    candidate = report["candidates"][0]

    assert candidate["eligible"] is False
    assert candidate["contradicting_episode_ids"] == []
    assert candidate["eligibility_reason"] == "insufficient_independent_support"


def test_keyset_scan_is_workspace_complete_and_legacy_candidates_stay_unstaged(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _run_with_decision(store, "support-one")
    _run_with_decision(store, "support-two")
    _run_with_outcome_only(store, "unscanned", outcome="unverified", evaluator_type="system")

    report = build_run_consolidation_report(store, workspace_key="project:bridge", limit=2, stage=True)
    candidate = report["candidates"][0]

    assert report["scan"]["complete"] is True
    assert candidate["eligible"] is False
    assert candidate["eligibility_reason"] == "insufficient_independent_support"
    assert candidate["review_action"] == "collect_independent_evidence"
    assert report["stage_results"] == []
    assert report["write_counts"] == {"stored": 0, "duplicate": 0, "error": 0}
    with store._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 0


@pytest.mark.parametrize("reference", ["sk-abcdefghi", "test:sk-abcdefghijkl", "github_pat_abcdefghijabcdefghij"])
def test_secret_like_evidence_refs_are_excluded_before_report_or_stage(tmp_path: Path, reference: str) -> None:
    store = _store(tmp_path)
    _run_with_decision(store, "secret", payload=_decision_payload(evidence_ref=reference))

    report = build_run_consolidation_report(store, workspace_key="project:bridge", stage=True)

    assert "invalid_evidence_refs" in {item["reason_code"] for item in report["excluded"]}
    assert reference not in json.dumps(report, sort_keys=True)
    assert reference not in render_run_consolidation_markdown(report)
    assert report["stage_results"] == []
    with store._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 0


@pytest.mark.parametrize(
    "value",
    ["artifact=/tmp/private.log", r"artifact=C:\\private\\source.py", r"artifact=\\server\\share\\secret"],
)
def test_embedded_path_text_is_excluded_without_report_or_stage_leak(tmp_path: Path, value: str) -> None:
    store = _store(tmp_path)
    _run_with_decision(store, "path", payload=_decision_payload(observed_conditions=value))

    report = build_run_consolidation_report(store, workspace_key="project:bridge", stage=True)

    encoded = json.dumps(report, sort_keys=True)
    assert "privacy_sensitive_payload" in {item["reason_code"] for item in report["excluded"]}
    assert value not in encoded
    assert value not in render_run_consolidation_markdown(report)
    assert report["stage_results"] == []
    with store._connect() as conn:
        contents = [str(row[0]) for row in conn.execute("SELECT content FROM memories")]
    assert all(value not in content for content in contents)


def test_candidate_subject_id_is_stable_while_evidence_revision_changes(tmp_path: Path) -> None:
    store = _store(tmp_path)
    # The earliest run conflicts with each later run via a different declared
    # identity. The later pair remains independent.
    _run_with_decision(store, "one", thread_id="thread:shared", session_id="session:one")
    _run_with_decision(store, "two", thread_id="thread:shared", session_id="session:two")
    _run_with_decision(store, "three", thread_id="thread:three", session_id="session:one")
    first = build_run_consolidation_report(store, workspace_key="project:bridge")["candidates"][0]

    assert first["eligible"] is False
    assert first["independence_count"] == 0
    _run_with_decision(store, "four")
    second = build_run_consolidation_report(store, workspace_key="project:bridge")["candidates"][0]
    assert first["candidate_key"] == second["candidate_key"]
    assert first["candidate_subject_id"] == second["candidate_subject_id"]
    assert first["evidence_revision_id"] != second["evidence_revision_id"]


def test_governed_receipts_enable_verified_independence_and_exact_revision_dedupe(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _run_with_governed_consolidation_evidence(store, "one", evaluator_digest="a" * 64)
    _run_with_governed_consolidation_evidence(store, "two", evaluator_digest="b" * 64)

    first = build_run_consolidation_report(store, workspace_key="project:bridge", stage=True)
    candidate = first["candidates"][0]

    assert candidate["eligible"] is True
    assert candidate["declared_independence_count"] == 2
    assert candidate["verified_independence_count"] == 2
    assert candidate["basis_reason_codes"] == ["two_verified_independent_supporting_episodes"]
    assert all(episode["strong_verified"] for episode in candidate["episodes"])
    assert {episode["outcome_authority_class"] for episode in candidate["episodes"]} == {"strong_verified"}
    assert first["write_counts"] == {"stored": 1, "duplicate": 0, "error": 0}

    duplicate = build_run_consolidation_report(store, workspace_key="project:bridge", stage=True)
    assert duplicate["candidates"][0]["candidate_subject_id"] == candidate["candidate_subject_id"]
    assert duplicate["candidates"][0]["evidence_revision_id"] == candidate["evidence_revision_id"]
    assert duplicate["write_counts"] == {"stored": 0, "duplicate": 1, "error": 0}

    _run_with_governed_consolidation_evidence(store, "three", evaluator_digest="c" * 64)
    revised = build_run_consolidation_report(store, workspace_key="project:bridge", stage=True)
    revised_candidate = revised["candidates"][0]
    assert revised_candidate["candidate_subject_id"] == candidate["candidate_subject_id"]
    assert revised_candidate["evidence_revision_id"] != candidate["evidence_revision_id"]
    assert revised["write_counts"] == {"stored": 1, "duplicate": 0, "error": 0}
    with store._connect() as conn:
        contents = [str(row[0]) for row in conn.execute("SELECT content FROM memories ORDER BY created_at")]
    assert len(contents) == 2
    assert all(f"candidate_subject_id: {candidate['candidate_subject_id']}" in content for content in contents)


def test_declared_thread_session_and_evidence_separation_is_not_verified_independence(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _run_with_governed_consolidation_evidence(store, "one", evaluator_digest="a" * 64)
    _run_with_governed_consolidation_evidence(store, "two", evaluator_digest="a" * 64)

    candidate = build_run_consolidation_report(store, workspace_key="project:bridge")["candidates"][0]

    assert candidate["declared_independence_count"] == 2
    assert candidate["verified_independence_count"] == 1
    assert candidate["eligible"] is False
    assert candidate["eligibility_reason"] == "insufficient_independent_support"


def test_distinct_execution_environment_is_a_verified_trusted_source(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _run_with_governed_consolidation_evidence(store, "one", evaluator_digest="a" * 64, model_digest="b" * 64)
    _run_with_governed_consolidation_evidence(store, "two", evaluator_digest="a" * 64, model_digest="c" * 64)

    candidate = build_run_consolidation_report(store, workspace_key="project:bridge")["candidates"][0]

    assert candidate["verified_independence_count"] == 2
    assert candidate["eligible"] is True
    environment_digests = {episode["execution_environment_digest"] for episode in candidate["episodes"]}
    assert None not in environment_digests
    assert len(environment_digests) == 2


def test_procedure_structure_conflict_is_contested_and_never_selects_a_variant(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first_payload = _decision_payload(
        authority_class="procedure",
        goal="Prove the migration.",
        when_to_use="Before a schema release.",
        steps=["Run the deterministic proof."],
        applies_to_domains=["domain:release"],
    )
    second_payload = _decision_payload(
        authority_class="procedure",
        goal="Prove the migration.",
        when_to_use="Before a schema release.",
        steps=["Run the deterministic proof.", "Publish the result."],
        applies_to_domains=["domain:release"],
    )
    _run_with_decision(store, "one", payload=first_payload)
    _run_with_decision(store, "two", payload=second_payload)

    report = build_run_consolidation_report(store, workspace_key="project:bridge", stage=True)
    candidate = report["candidates"][0]

    assert candidate["procedure"] is None
    assert candidate["confidence_label"] == "contested"
    assert candidate["eligible"] is False
    assert candidate["eligibility_reason"] == "procedure_structure_conflict"
    assert candidate["conflict_reason_codes"] == ["procedure_structure_conflict"]
    assert candidate["review_action"] == "resolve_procedure_conflict_before_review"
    assert report["stage_results"] == []


def test_explicit_structured_opposition_contests_the_target_subject_only(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _run_with_decision(store, "target")
    target = build_run_consolidation_report(store, workspace_key="project:bridge")["candidates"][0]
    opposition = {
        "schema": OPPOSITION_SCHEMA,
        "subject_id": target["candidate_subject_id"],
        "reason_code": "evidence_conflict",
    }
    _run_with_decision(
        store,
        "opposer",
        payload=_decision_payload(
            claim="The release proof result requires independent review.",
            evidence_ref="event:opposer",
            opposes_claims=[opposition],
        ),
    )

    report = build_run_consolidation_report(store, workspace_key="project:bridge", stage=True)
    candidates = {candidate["claim"]: candidate for candidate in report["candidates"]}
    contested = candidates["Run the deterministic proof before release."]
    source = candidates["The release proof result requires independent review."]

    assert contested["eligible"] is False
    assert contested["confidence_label"] == "contested"
    assert contested["eligibility_reason"] == "explicit_structured_opposition"
    assert contested["review_action"] == "resolve_structured_opposition_before_review"
    assert contested["opposed_by"] == [
        {"subject_id": source["candidate_subject_id"], "reason_code": "evidence_conflict"}
    ]
    assert source["opposes_claims"] == [
        {"subject_id": target["candidate_subject_id"], "reason_code": "evidence_conflict"}
    ]
    _, invalid_reason = _parse_evidence_payload(
        json.dumps(_decision_payload(opposes_claims=[{"reason_code": "free-text contradiction"}]))
    )
    assert invalid_reason == "invalid_opposes_claims"
    assert report["stage_results"] == []


@pytest.mark.parametrize(("run_count", "page_count"), [(501, 2), (1_000, 2), (10_000, 20)])
def test_keyset_pagination_scans_large_workspaces_without_a_500_run_dead_end(
    tmp_path: Path, run_count: int, page_count: int
) -> None:
    store = _store(tmp_path)
    _insert_workspace_runs(store, count=run_count)

    report = build_run_consolidation_report(store, workspace_key="project:paged", limit=500)

    assert report["scanned_run_count"] == run_count
    assert report["scan"]["complete"] is True
    assert report["scan"]["workspace_run_count"] == run_count
    assert report["scan"]["omitted_run_count"] == 0
    assert report["scan"]["page_count"] == page_count
    assert report["scan"]["page_size"] == 500
    assert report["scan"]["last_scanned_key"]["run_id"] == f"run_{run_count - 1:032x}"


def test_keyset_snapshot_excludes_a_concurrent_append(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _insert_workspace_runs(store, count=501)
    connection = sqlite3.connect(f"{store.db_path.resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    appended = False

    def append_after_first_page(statement: str) -> None:
        nonlocal appended
        if appended or "FROM run_events" not in statement:
            return
        appended = True
        store.begin_run(
            workspace_key="project:paged",
            goal="Concurrent append after the snapshot starts.",
            idempotency_key="begin:concurrent-append",
        )

    connection.set_trace_callback(append_after_first_page)
    try:
        report = build_run_consolidation_report(
            None,
            workspace_key="project:paged",
            limit=500,
            connection=connection,
        )
    finally:
        connection.close()

    assert appended is True
    assert report["scan"]["workspace_run_count"] == 501
    assert report["scanned_run_count"] == 501
    assert report["scan"]["complete"] is True
    with store._connect() as conn:
        assert (
            conn.execute("SELECT COUNT(*) FROM agent_runs WHERE workspace_key = 'project:paged'").fetchone()[0] == 502
        )


def test_cli_requires_shadow_and_renders_json(monkeypatch, tmp_path: Path, capsys) -> None:
    store = _store(tmp_path)
    _run_with_decision(store, "one")
    _run_with_decision(store, "two")
    monkeypatch.setattr(cli_module, "resolve_bridge_db_path", lambda: store.db_path)

    assert main(["consolidate-runs", "--workspace-key", "project:bridge"]) == 2
    assert "requires --shadow" in capsys.readouterr().err
    assert main(["consolidate-runs", "--shadow", "--workspace-key", "project:bridge", "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["eligible_candidate_count"] == 0


def test_cli_shadow_uses_existing_read_only_database_and_changes_nothing(monkeypatch, tmp_path: Path, capsys) -> None:
    store = _store(tmp_path)
    _run_with_decision(store, "one")
    _run_with_decision(store, "two")
    before = _database_dump(store.db_path)
    log_files_before = sorted(path.name for path in store.log_dir.iterdir())
    monkeypatch.setattr(cli_module, "resolve_bridge_db_path", lambda: store.db_path)
    monkeypatch.setattr(
        cli_module.MemoryStore, "from_env", lambda: (_ for _ in ()).throw(AssertionError("must not open store"))
    )

    assert main(["consolidate-runs", "--shadow", "--workspace-key", "project:bridge", "--format", "json"]) == 0

    assert _database_dump(store.db_path) == before
    assert sorted(path.name for path in store.log_dir.iterdir()) == log_files_before
    assert json.loads(capsys.readouterr().out)["eligible_candidate_count"] == 0
