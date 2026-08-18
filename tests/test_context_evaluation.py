from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agent_mem_bridge.context_evaluation import (
    CONTEXT_ATTESTATION_MIME_TYPE,
    build_context_attestation,
    get_context_evaluation_linkage,
    record_context_attestation,
)
from agent_mem_bridge.context_manifest import compile_context, render_context
from agent_mem_bridge.storage import MemoryStore
from agent_mem_bridge.task_memory import assemble_task_memory


def _store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")


def _preflight_payload() -> dict[str, object]:
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


def _procedure_content() -> str:
    return (
        "record_type: procedure\n"
        "goal: Deploy customer-secret-zeta using the private operational instruction.\n"
        "when_to_use: During the approved release window.\n"
        "when_not_to_use: When release state is draft.\n"
        "prerequisites: approved change ticket\n"
        "steps: verify state | deploy | validate\n"
        "procedure_status: validated\n"
    )


def _manifest(store: MemoryStore) -> Any:
    store.store(
        namespace="project:bridge",
        title="Deploy customer-secret-zeta procedure",
        content=_procedure_content(),
        tags=["kind:procedure", "domain:release"],
    )
    absent = store.dynamic_state.read(workspace_key="project:bridge", state_key="release:current")
    store.dynamic_state.transition_status(
        workspace_key="project:bridge",
        state_key="release:current",
        to_status="draft",
        expected_version=0,
        expected_database_epoch=str(absent["database_epoch"]),
        idempotency_key="context-evaluation:state",
        provenance={"actor": "context-evaluation-test"},
    )
    report = assemble_task_memory(
        store,
        query="deploy customer-secret-zeta",
        project_namespace="project:bridge",
        global_namespace="global",
        task_domain="release",
    )
    return compile_context(
        task_memory=report,
        state_snapshots=[store.dynamic_state.read(workspace_key="project:bridge", state_key="release:current")],
        session_items=[{"title": "handoff", "content": "api_key: never-store-this\nUse the private session plan."}],
        budget_tokens=2_048,
    )


def _begin_governed_run(store: MemoryStore, suffix: str) -> tuple[dict[str, object], dict[str, object]]:
    begun = store.begin_run(
        workspace_key="project:bridge",
        goal=f"Governed context evaluation run {suffix}",
        idempotency_key=f"context-evaluation:begin:{suffix}",
        evidence_profile="governed-v2",
        acceptance_criteria=[{"criterion_id": "tests", "description": "Focused verification passes."}],
        constraints=[],
        non_goals=[],
        risk_level="low",
    )
    preflight = store.record_run_event(
        workspace_key="project:bridge",
        run_id=str(begun["run_id"]),
        work_item_id=str(begun["root_work_item_id"]),
        event_type="preflight_review",
        event_schema_version=2,
        summary="Approved preflight for bounded context evaluation evidence.",
        payload=_preflight_payload(),
        idempotency_key=f"context-evaluation:preflight:{suffix}",
        expected_database_epoch=str(begun["database_epoch"]),
        expected_run_generation=int(begun["run_generation"]),
        expected_last_sequence=0,
        expected_work_item_status="active",
    )
    return begun, preflight


def _record_attestation(
    store: MemoryStore,
    *,
    begun: dict[str, object],
    predecessor: dict[str, object],
    manifest: Any,
    suffix: str,
) -> dict[str, object]:
    return record_context_attestation(
        store,
        workspace_key="project:bridge",
        run_id=str(begun["run_id"]),
        work_item_id=str(begun["root_work_item_id"]),
        manifest=manifest,
        idempotency_key=f"context-evaluation:attestation:{suffix}",
        event_schema_version=2,
        expected_database_epoch=str(predecessor["database_epoch"]),
        expected_run_generation=int(predecessor["run_generation"]),
        expected_last_sequence=int(predecessor["sequence"]),
        expected_work_item_status="active",
    )


def _finish_work_item(
    store: MemoryStore, *, begun: dict[str, object], predecessor: dict[str, object], suffix: str
) -> dict[str, object]:
    verification = store.record_run_event(
        workspace_key="project:bridge",
        run_id=str(begun["run_id"]),
        work_item_id=str(begun["root_work_item_id"]),
        event_type="verification_result",
        event_schema_version=2,
        summary="The governed criterion passed for context evaluation.",
        payload={"criterion_id": "tests", "result": "passed", "evidence_refs": [f"pytest:{suffix}"]},
        idempotency_key=f"context-evaluation:verification:{suffix}",
        expected_database_epoch=str(predecessor["database_epoch"]),
        expected_run_generation=int(predecessor["run_generation"]),
        expected_last_sequence=int(predecessor["sequence"]),
        expected_work_item_status="active",
    )
    return store.record_run_event(
        workspace_key="project:bridge",
        run_id=str(begun["run_id"]),
        work_item_id=str(begun["root_work_item_id"]),
        event_type="work_item_completed",
        event_schema_version=2,
        summary="The governed context evaluation work item completed.",
        payload={},
        idempotency_key=f"context-evaluation:completed:{suffix}",
        expected_database_epoch=str(verification["database_epoch"]),
        expected_run_generation=int(verification["run_generation"]),
        expected_last_sequence=int(verification["sequence"]),
        expected_work_item_status="active",
    )


def _mint_receipt(
    store: MemoryStore, *, begun: dict[str, object], preflight: dict[str, object], suffix: str
) -> dict[str, object]:
    return store.mint_operator_verification_receipt(
        workspace_key="project:bridge",
        run_id=str(begun["run_id"]),
        preflight_event_id=str(preflight["event_id"]),
        evaluator_digest="a" * 64,
        evaluator_version="context-evaluation-v1",
        criterion_results=[{"criterion_id": "tests", "result": "passed", "evidence_refs": [f"pytest:{suffix}"]}],
        result="verified_success",
        evidence=[{"kind": "test", "reference": f"pytest:{suffix}"}],
        actor="operator-a",
    )


def _complete_verified(
    store: MemoryStore,
    *,
    begun: dict[str, object],
    completed: dict[str, object],
    receipt: dict[str, object],
    suffix: str,
) -> dict[str, object]:
    return store.complete_run(
        workspace_key="project:bridge",
        run_id=str(begun["run_id"]),
        outcome="verified_success",
        evaluator_type="human",
        evaluator_digest="a" * 64,
        evaluator_version="context-evaluation-v1",
        verification_receipt_id=str(receipt["verification_receipt_id"]),
        idempotency_key=f"context-evaluation:outcome:{suffix}",
        expected_database_epoch=str(completed["database_epoch"]),
        expected_run_generation=int(completed["run_generation"]),
        expected_last_sequence=int(completed["sequence"]),
    )


def test_context_attestation_is_bounded_metadata_only_and_uses_existing_artifact_path(tmp_path: Path) -> None:
    store = _store(tmp_path)
    manifest = _manifest(store)
    attestation = build_context_attestation(manifest)
    begun, preflight = _begin_governed_run(store, "bounded")
    event = _record_attestation(store, begun=begun, predecessor=preflight, manifest=manifest, suffix="bounded")

    assert set(attestation) == {
        "attestation_type",
        "attestation_version",
        "manifest_fingerprint",
        "input_fingerprint",
        "rendered_context_sha256",
        "task_identifier_sha256",
        "compiler_version",
        "selection_policy_version",
        "budget_tokens",
        "used_tokens",
        "selected_item_count",
        "omission_count",
        "metadata_manifest_sha256",
        "attestation_sha256",
    }
    assert event["event_type"] == "artifact_created"
    assert event["artifact"]["mime_type"] == CONTEXT_ATTESTATION_MIME_TYPE
    assert event["artifact"]["metadata"] == attestation
    run = store.get_run(workspace_key="project:bridge", run_id=str(begun["run_id"]))
    assert [artifact["digest"] for artifact in run["artifacts"]] == [attestation["attestation_sha256"]]

    rendered = render_context(manifest)
    persisted = json.dumps(
        {"events": run["events"], "artifacts": run["artifacts"], "metadata": event["artifact"]["metadata"]},
        sort_keys=True,
    )
    for forbidden in (
        "deploy customer-secret-zeta",
        "never-store-this",
        "private operational instruction",
        '"status":"draft"',
        rendered,
    ):
        assert forbidden not in persisted


def test_context_attestation_inherits_run_event_idempotency_and_rejects_conflicts(tmp_path: Path) -> None:
    store = _store(tmp_path)
    manifest = _manifest(store)
    begun, preflight = _begin_governed_run(store, "idempotency")
    first = _record_attestation(store, begun=begun, predecessor=preflight, manifest=manifest, suffix="stable")
    replay = _record_attestation(store, begun=begun, predecessor=preflight, manifest=manifest, suffix="stable")

    assert replay == {**first, "idempotent_replay": True}
    different = _manifest(_store(tmp_path / "other"))
    with pytest.raises(ValueError, match="different payload"):
        _record_attestation(store, begun=begun, predecessor=preflight, manifest=different, suffix="stable")


def test_context_attestation_is_bound_to_current_governed_verification_and_strong_outcome(tmp_path: Path) -> None:
    store = _store(tmp_path)
    manifest = _manifest(store)
    begun, preflight = _begin_governed_run(store, "verified")
    attestation = _record_attestation(store, begun=begun, predecessor=preflight, manifest=manifest, suffix="verified")
    completed = _finish_work_item(store, begun=begun, predecessor=attestation, suffix="verified")
    receipt = _mint_receipt(store, begun=begun, preflight=preflight, suffix="verified")
    outcome = _complete_verified(store, begun=begun, completed=completed, receipt=receipt, suffix="verified")
    view = get_context_evaluation_linkage(store, workspace_key="project:bridge", run_id=str(begun["run_id"]))

    assert outcome["verification_profile"] == "governed-v2"
    assert view["strong_verified"] is True
    assert view["context_bound_to_current_verification"] is True
    assert view["verification_receipt_id"] == receipt["verification_receipt_id"]
    assert (
        view["context_attestations_bound_to_current_verification"][0]["artifact_id"]
        == attestation["artifact"]["artifact_id"]
    )


def test_stale_receipt_is_rejected_after_second_context_attestation_and_fresh_receipt_passes(tmp_path: Path) -> None:
    store = _store(tmp_path)
    manifest_one = _manifest(store)
    manifest_two = _manifest(_store(tmp_path / "second"))
    begun, preflight = _begin_governed_run(store, "stale")
    first = _record_attestation(store, begun=begun, predecessor=preflight, manifest=manifest_one, suffix="stale-one")
    stale_receipt = _mint_receipt(store, begun=begun, preflight=preflight, suffix="stale-one")
    second = _record_attestation(store, begun=begun, predecessor=first, manifest=manifest_two, suffix="stale-two")
    completed = _finish_work_item(store, begun=begun, predecessor=second, suffix="stale")

    with pytest.raises(ValueError, match="artifacts are no longer current"):
        _complete_verified(store, begun=begun, completed=completed, receipt=stale_receipt, suffix="stale")

    fresh_receipt = _mint_receipt(store, begun=begun, preflight=preflight, suffix="stale-two")
    accepted = _complete_verified(store, begun=begun, completed=completed, receipt=fresh_receipt, suffix="fresh")
    view = get_context_evaluation_linkage(store, workspace_key="project:bridge", run_id=str(begun["run_id"]))

    assert accepted["outcome"] == "verified_success"
    assert [item["sequence"] for item in view["context_attestations"]] == [first["sequence"], second["sequence"]]
    assert view["latest_context_attestation"]["artifact_id"] == second["artifact"]["artifact_id"]
    assert len(view["context_attestations_bound_to_current_verification"]) == 2


def test_evaluation_view_follows_current_outcome_correction_and_context_inclusion_has_no_memory_credit(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    manifest = _manifest(store)
    begun, preflight = _begin_governed_run(store, "correction")
    attestation = _record_attestation(store, begun=begun, predecessor=preflight, manifest=manifest, suffix="correction")
    completed = _finish_work_item(store, begun=begun, predecessor=attestation, suffix="correction")
    receipt = _mint_receipt(store, begun=begun, preflight=preflight, suffix="correction")
    verified = _complete_verified(store, begun=begun, completed=completed, receipt=receipt, suffix="correction")
    before = get_context_evaluation_linkage(store, workspace_key="project:bridge", run_id=str(begun["run_id"]))
    snapshot = store.get_run(workspace_key="project:bridge", run_id=str(begun["run_id"]))

    corrected = store.complete_run(
        workspace_key="project:bridge",
        run_id=str(begun["run_id"]),
        outcome="user_corrected",
        evaluator_type="human",
        idempotency_key="context-evaluation:correction:outcome",
        supersedes_outcome_id=str(verified["outcome_id"]),
        expected_database_epoch=str(snapshot["snapshot_epoch"]),
        expected_run_generation=int(snapshot["run"]["run_generation"]),
        expected_last_sequence=int(snapshot["latest_sequence"]),
    )
    after = get_context_evaluation_linkage(store, workspace_key="project:bridge", run_id=str(begun["run_id"]))

    assert before["strong_verified"] is True
    assert corrected["outcome"] == "user_corrected"
    assert after["current_outcome_type"] == "user_corrected"
    assert after["strong_verified"] is False
    assert after["context_bound_to_current_verification"] is False
    with store._connect() as conn:
        assert (
            conn.execute("SELECT COUNT(*) FROM run_memory_links WHERE run_id = ?", (begun["run_id"],)).fetchone()[0]
            == 0
        )
        assert conn.execute("SELECT COUNT(*) FROM memory_utility_shadow").fetchone()[0] == 0
