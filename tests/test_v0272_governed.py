from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from agent_mem_bridge.cli import main
from agent_mem_bridge.storage import MemoryStore


def _store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")


def _begin_governed(store: MemoryStore, *, risk_level: str = "low") -> dict[str, object]:
    return store.begin_run(
        workspace_key="project:governed",
        goal="Prove governed evidence behavior.",
        idempotency_key=f"begin:governed:{risk_level}",
        evidence_profile="governed-v2",
        acceptance_criteria=[{"id": "tests", "description": "Focused tests pass."}],
        constraints=["No public tool expansion."],
        non_goals=["No learning-policy changes."],
        risk_level=risk_level,
    )


def _preflight(store: MemoryStore, run: dict[str, object], *, rollback_plan: str | None = None) -> dict[str, object]:
    payload: dict[str, object] = {
        "approved": True,
        "confirmed_facts": ["The bounded scope and tool surface are known."],
        "reasonable_inferences": [],
        "unverified_hypotheses": [],
        "missing_information": [],
        "alternatives_considered": ["Keep the existing public tool contract."],
        "hidden_risks": [],
        "maintenance_cost": [],
        "maintenance_impact": [],
        "verification_plan": ["Run the focused governed tests."],
    }
    if rollback_plan is not None:
        payload["rollback_plan"] = rollback_plan
    return store.record_run_event(
        workspace_key="project:governed",
        run_id=str(run["run_id"]),
        work_item_id=str(run["root_work_item_id"]),
        event_type="preflight_review",
        event_schema_version=2,
        summary="Operator preflight approved the bounded change.",
        payload=payload,
        idempotency_key="event:preflight",
        expected_database_epoch=str(run["database_epoch"]),
        expected_run_generation=int(run["run_generation"]),
        expected_last_sequence=0,
        expected_work_item_status="active",
    )


def _complete_work_item(store: MemoryStore, run: dict[str, object], prior: dict[str, object]) -> dict[str, object]:
    return store.record_run_event(
        workspace_key="project:governed",
        run_id=str(run["run_id"]),
        work_item_id=str(run["root_work_item_id"]),
        event_type="work_item_completed",
        event_schema_version=2,
        summary="The governed work item completed.",
        payload={},
        idempotency_key="event:complete",
        expected_database_epoch=str(prior["database_epoch"]),
        expected_run_generation=int(prior["run_generation"]),
        expected_last_sequence=int(prior["sequence"]),
        expected_work_item_status="active",
    )


def _mint_receipt(store: MemoryStore, run: dict[str, object], preflight: dict[str, object]) -> dict[str, object]:
    digest = "a" * 64
    return store.mint_operator_verification_receipt(
        workspace_key="project:governed",
        run_id=str(run["run_id"]),
        preflight_event_id=str(preflight["event_id"]),
        evaluator_digest=digest,
        evaluator_version="operator-review-v1",
        criterion_results=[{"criterion_id": "tests", "result": "passed", "evidence_refs": ["pytest:focused"]}],
        result="verified_success",
        evidence=[{"kind": "test", "reference": "pytest:focused"}],
        actor="operator-a",
    )


def test_governed_receipt_is_required_and_evaluator_mismatch_is_atomic(tmp_path: Path) -> None:
    store = _store(tmp_path)
    run = _begin_governed(store)
    preflight = _preflight(store, run)
    finished = _complete_work_item(store, run, preflight)

    with pytest.raises(ValueError, match="server-minted governed verification receipt"):
        store.complete_run(
            workspace_key="project:governed",
            run_id=str(run["run_id"]),
            outcome="verified_success",
            evaluator_type="human",
            idempotency_key="outcome:forged",
            expected_database_epoch=str(finished["database_epoch"]),
            expected_run_generation=int(finished["run_generation"]),
            expected_last_sequence=int(finished["sequence"]),
        )
    receipt = _mint_receipt(store, run, preflight)
    with pytest.raises(ValueError, match="evaluator digest"):
        store.complete_run(
            workspace_key="project:governed",
            run_id=str(run["run_id"]),
            outcome="verified_success",
            evaluator_type="human",
            evaluator_digest="b" * 64,
            evaluator_version="operator-review-v1",
            verification_receipt_id=str(receipt["verification_receipt_id"]),
            idempotency_key="outcome:mismatched-evaluator",
            expected_database_epoch=str(finished["database_epoch"]),
            expected_run_generation=int(finished["run_generation"]),
            expected_last_sequence=int(finished["sequence"]),
        )
    with store._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM run_outcomes").fetchone()[0] == 0

    outcome = store.complete_run(
        workspace_key="project:governed",
        run_id=str(run["run_id"]),
        outcome="verified_success",
        evaluator_type="human",
        evaluator_digest="a" * 64,
        evaluator_version="operator-review-v1",
        verification_receipt_id=str(receipt["verification_receipt_id"]),
        idempotency_key="outcome:verified",
        expected_database_epoch=str(finished["database_epoch"]),
        expected_run_generation=int(finished["run_generation"]),
        expected_last_sequence=int(finished["sequence"]),
    )
    assert outcome["strong_verified"] is True
    restored = store.get_run(workspace_key="project:governed", run_id=str(run["run_id"]))
    assert restored["run"]["acceptance_criteria"][0]["criterion_id"] == "tests"
    assert restored["verification_receipts"][0]["issuer_channel"] == "operator_cli"


def test_governed_typed_events_preflight_blocked_resume_and_cas_replay(tmp_path: Path) -> None:
    store = _store(tmp_path)
    run = _begin_governed(store, risk_level="high")
    with pytest.raises(ValueError, match="rollback_plan"):
        _preflight(store, run)
    preflight = _preflight(store, run, rollback_plan="Revert the bounded change and inspect the run receipt.")

    blocked = store.record_run_event(
        workspace_key="project:governed",
        run_id=str(run["run_id"]),
        work_item_id=str(run["root_work_item_id"]),
        event_type="blocker",
        event_schema_version=2,
        summary="A governed blocker is active.",
        payload={},
        idempotency_key="event:blocker",
        expected_database_epoch=str(preflight["database_epoch"]),
        expected_run_generation=int(preflight["run_generation"]),
        expected_last_sequence=int(preflight["sequence"]),
        expected_work_item_status="active",
    )
    with pytest.raises(ValueError, match="only via work_item_resumed or blocker_resolved"):
        store.record_run_event(
            workspace_key="project:governed",
            run_id=str(run["run_id"]),
            work_item_id=str(run["root_work_item_id"]),
            event_type="work_item_started",
            event_schema_version=2,
            summary="An invalid second logical start.",
            payload={},
            idempotency_key="event:bad-resume",
            expected_database_epoch=str(blocked["database_epoch"]),
            expected_run_generation=int(blocked["run_generation"]),
            expected_last_sequence=int(blocked["sequence"]),
            expected_work_item_status="blocked",
        )
    resumed = store.record_run_event(
        workspace_key="project:governed",
        run_id=str(run["run_id"]),
        work_item_id=str(run["root_work_item_id"]),
        event_type="work_item_resumed",
        event_schema_version=2,
        summary="The blocker was resolved and work resumed.",
        payload={"reason": "dependency available"},
        idempotency_key="event:resume",
        expected_database_epoch=str(blocked["database_epoch"]),
        expected_run_generation=int(blocked["run_generation"]),
        expected_last_sequence=int(blocked["sequence"]),
        expected_work_item_status="blocked",
    )
    replay = store.record_run_event(
        workspace_key="project:governed",
        run_id=str(run["run_id"]),
        work_item_id=str(run["root_work_item_id"]),
        event_type="work_item_resumed",
        event_schema_version=2,
        summary="The blocker was resolved and work resumed.",
        payload={"reason": "dependency available"},
        idempotency_key="event:resume",
        expected_database_epoch=str(resumed["database_epoch"]),
        expected_run_generation=int(resumed["run_generation"]),
        expected_last_sequence=int(resumed["sequence"]),
        expected_work_item_status="active",
    )
    assert replay["event_id"] == resumed["event_id"]
    assert replay["idempotent_replay"] is True
    assert replay["run_generation"] == resumed["run_generation"]

    with pytest.raises(ValueError, match="run generation conflict"):
        store.record_run_event(
            workspace_key="project:governed",
            run_id=str(run["run_id"]),
            work_item_id=str(run["root_work_item_id"]),
            event_type="test_result",
            event_schema_version=2,
            summary="This CAS write must lose.",
            payload={"test_id": "focused", "result": "passed", "evidence_refs": ["pytest"]},
            idempotency_key="event:stale",
            expected_database_epoch=str(resumed["database_epoch"]),
            expected_run_generation=int(blocked["run_generation"]),
            expected_last_sequence=int(resumed["sequence"]),
            expected_work_item_status="active",
        )
    current = store.get_run(workspace_key="project:governed", run_id=str(run["run_id"]))
    assert current["run"]["run_generation"] == resumed["run_generation"]
    assert current["events"][-1]["event_type"] == "work_item_resumed"


def test_governed_preflight_and_typed_evidence_payloads_are_closed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    run = _begin_governed(store)
    incomplete_preflight = {
        "approved": True,
        "confirmed_facts": [],
        "reasonable_inferences": [],
        "unverified_hypotheses": [],
        "missing_information": [],
        "alternatives_considered": [],
        "hidden_risks": [],
        "maintenance_cost": [],
        "maintenance_impact": [],
    }
    with pytest.raises(ValueError, match="missing required fields"):
        store.record_run_event(
            workspace_key="project:governed",
            run_id=str(run["run_id"]),
            work_item_id=str(run["root_work_item_id"]),
            event_type="preflight_review",
            event_schema_version=2,
            summary="The incomplete preflight cannot become authority.",
            payload=incomplete_preflight,
            idempotency_key="event:incomplete-preflight",
            expected_database_epoch=str(run["database_epoch"]),
            expected_run_generation=int(run["run_generation"]),
            expected_last_sequence=0,
        )
    with pytest.raises(ValueError, match="unsupported fields"):
        store.record_run_event(
            workspace_key="project:governed",
            run_id=str(run["run_id"]),
            work_item_id=str(run["root_work_item_id"]),
            event_type="preflight_review",
            event_schema_version=2,
            summary="Unknown preflight data must not become authority.",
            payload={**incomplete_preflight, "verification_plan": [], "unexpected": "value"},
            idempotency_key="event:unknown-preflight",
            expected_database_epoch=str(run["database_epoch"]),
            expected_run_generation=int(run["run_generation"]),
            expected_last_sequence=0,
        )

    preflight = _preflight(store, run)
    common = {
        "workspace_key": "project:governed",
        "run_id": str(run["run_id"]),
        "work_item_id": str(run["root_work_item_id"]),
        "event_schema_version": 2,
        "expected_database_epoch": str(preflight["database_epoch"]),
        "expected_run_generation": int(preflight["run_generation"]),
        "expected_last_sequence": int(preflight["sequence"]),
    }
    with pytest.raises(ValueError, match="missing required fields"):
        store.record_run_event(
            **common,
            event_type="hypothesis",
            summary="A hypothesis needs a falsifier.",
            payload={"claim": "The test passes.", "epistemic_status": "tentative", "confidence": 0.5},
            idempotency_key="event:bad-hypothesis",
        )
    with pytest.raises(ValueError, match="test_result"):
        store.record_run_event(
            **common,
            event_type="test_failure",
            summary="Governed tests use the typed result event.",
            payload={},
            idempotency_key="event:legacy-test-failure",
        )
    hypothesis = store.record_run_event(
        **common,
        event_type="hypothesis",
        summary="The focused contract remains testable.",
        payload={
            "claim": "The focused contract can be checked.",
            "falsifier": "A focused test failure would refute it.",
            "epistemic_status": "tentative",
            "confidence": 0.5,
        },
        idempotency_key="event:hypothesis",
    )
    with pytest.raises(ValueError, match="evidence_refs must not be empty"):
        store.record_run_event(
            workspace_key="project:governed",
            run_id=str(run["run_id"]),
            work_item_id=str(run["root_work_item_id"]),
            event_type="hypothesis_confirmed",
            event_schema_version=2,
            summary="Source-hypothesis confirmation needs evidence.",
            payload={"hypothesis_event_id": hypothesis["event_id"], "evidence_refs": []},
            idempotency_key="event:hypothesis-no-evidence",
            expected_database_epoch=str(hypothesis["database_epoch"]),
            expected_run_generation=int(hypothesis["run_generation"]),
            expected_last_sequence=int(hypothesis["sequence"]),
        )
    with pytest.raises(ValueError, match="maintenance_summary"):
        store.record_run_event(
            workspace_key="project:governed",
            run_id=str(run["run_id"]),
            work_item_id=str(run["root_work_item_id"]),
            event_type="decision",
            event_schema_version=2,
            summary="A decision needs a compact maintenance summary.",
            payload={
                "selected_decision": "Use the typed contract.",
                "alternatives_considered": [],
                "evidence_refs": ["pytest:focused"],
            },
            idempotency_key="event:bad-decision",
            expected_database_epoch=str(hypothesis["database_epoch"]),
            expected_run_generation=int(hypothesis["run_generation"]),
            expected_last_sequence=int(hypothesis["sequence"]),
        )


def test_governed_completion_requires_criterion_coverage_and_rejects_open_information_gaps(tmp_path: Path) -> None:
    store = _store(tmp_path)
    uncovered = _begin_governed(store)
    uncovered_preflight = _preflight(store, uncovered)
    uncovered_finished = _complete_work_item(store, uncovered, uncovered_preflight)
    with pytest.raises(ValueError, match="verification_result coverage"):
        store.complete_run(
            workspace_key="project:governed",
            run_id=str(uncovered["run_id"]),
            outcome="partial_success",
            evaluator_type="agent",
            idempotency_key="outcome:missing-coverage",
            expected_database_epoch=str(uncovered_finished["database_epoch"]),
            expected_run_generation=int(uncovered_finished["run_generation"]),
            expected_last_sequence=int(uncovered_finished["sequence"]),
        )
    verified = store.record_run_event(
        workspace_key="project:governed",
        run_id=str(uncovered["run_id"]),
        work_item_id=str(uncovered["root_work_item_id"]),
        event_type="verification_result",
        event_schema_version=2,
        summary="The acceptance criterion was checked.",
        payload={"criterion_id": "tests", "result": "passed", "evidence_refs": ["pytest:focused"]},
        idempotency_key="event:criterion-result",
        expected_database_epoch=str(uncovered_finished["database_epoch"]),
        expected_run_generation=int(uncovered_finished["run_generation"]),
        expected_last_sequence=int(uncovered_finished["sequence"]),
    )
    partial = store.complete_run(
        workspace_key="project:governed",
        run_id=str(uncovered["run_id"]),
        outcome="partial_success",
        evaluator_type="agent",
        idempotency_key="outcome:covered",
        expected_database_epoch=str(verified["database_epoch"]),
        expected_run_generation=int(verified["run_generation"]),
        expected_last_sequence=int(verified["sequence"]),
    )
    assert partial["unresolved_information_gap_ids"] == []

    blocked = _begin_governed(store, risk_level="medium")
    blocked_preflight = _preflight(store, blocked)
    gap = store.record_run_event(
        workspace_key="project:governed",
        run_id=str(blocked["run_id"]),
        work_item_id=str(blocked["root_work_item_id"]),
        event_type="information_gap",
        event_schema_version=2,
        summary="One required fact remains unavailable.",
        payload={"gap_id": "dependency-version", "question": "Which dependency version is deployed?"},
        idempotency_key="event:information-gap",
        expected_database_epoch=str(blocked_preflight["database_epoch"]),
        expected_run_generation=int(blocked_preflight["run_generation"]),
        expected_last_sequence=int(blocked_preflight["sequence"]),
    )
    gap_verified = store.record_run_event(
        workspace_key="project:governed",
        run_id=str(blocked["run_id"]),
        work_item_id=str(blocked["root_work_item_id"]),
        event_type="verification_result",
        event_schema_version=2,
        summary="The declared criterion was checked.",
        payload={"criterion_id": "tests", "result": "passed", "evidence_refs": ["pytest:focused"]},
        idempotency_key="event:gap-criterion-result",
        expected_database_epoch=str(gap["database_epoch"]),
        expected_run_generation=int(gap["run_generation"]),
        expected_last_sequence=int(gap["sequence"]),
    )
    blocked_finished = _complete_work_item(store, blocked, gap_verified)
    receipt = _mint_receipt(store, blocked, blocked_preflight)
    with pytest.raises(ValueError, match="unresolved information gaps"):
        store.complete_run(
            workspace_key="project:governed",
            run_id=str(blocked["run_id"]),
            outcome="verified_success",
            evaluator_type="human",
            evaluator_digest="a" * 64,
            evaluator_version="operator-review-v1",
            verification_receipt_id=str(receipt["verification_receipt_id"]),
            idempotency_key="outcome:open-gap",
            expected_database_epoch=str(blocked_finished["database_epoch"]),
            expected_run_generation=int(blocked_finished["run_generation"]),
            expected_last_sequence=int(blocked_finished["sequence"]),
        )
    restored = store.get_run(workspace_key="project:governed", run_id=str(blocked["run_id"]))
    assert restored["unresolved_information_gap_ids"] == ["dependency-version"]


def test_operator_cli_mints_receipt_and_database_inverse_regression_guard(tmp_path: Path, monkeypatch, capsys) -> None:
    bridge_home = tmp_path / "bridge-home"
    db_path = bridge_home / "bridge.db"
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_HOME", str(bridge_home))
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_DB_PATH", str(db_path))
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_LOG_DIR", str(bridge_home / "logs"))
    store = MemoryStore(db_path, log_dir=bridge_home / "logs")
    run = _begin_governed(store)
    preflight = _preflight(store, run)
    exit_code = main(
        [
            "mint-verification-receipt",
            "--workspace-key",
            "project:governed",
            "--run-id",
            str(run["run_id"]),
            "--preflight-event-id",
            str(preflight["event_id"]),
            "--evaluator-digest",
            "a" * 64,
            "--evaluator-version",
            "operator-review-v1",
            "--criterion-results-json",
            json.dumps([{"criterion_id": "tests", "result": "passed", "evidence_refs": ["pytest"]}]),
            "--evidence-json",
            json.dumps([{"reference": "pytest"}]),
            "--actor",
            "operator-a",
            "--json",
        ]
    )
    receipt = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert receipt["issuer_channel"] == "operator_cli"

    target = store.begin_run(
        workspace_key="project:governed",
        goal="Target for inverse regression guard.",
        idempotency_key="begin:inverse-target",
    )
    with store._connect() as conn, pytest.raises(sqlite3.IntegrityError, match="regression_of_run_id is only valid"):
        conn.execute(
            """
            INSERT INTO run_outcomes (
                outcome_id, run_id, outcome_type, evaluator_type, evidence_json,
                metrics_json, regression_of_run_id, idempotency_key_digest, request_digest, created_at
            ) VALUES (?, ?, 'unverified', 'agent', '[]', '{}', ?, ?, ?, ?)
            """,
            (
                "outcome_" + ("f" * 32),
                str(target["run_id"]),
                str(run["run_id"]),
                hashlib.sha256(b"inverse").hexdigest(),
                hashlib.sha256(b"inverse-request").hexdigest(),
                "2026-08-09T00:00:00+00:00",
            ),
        )
