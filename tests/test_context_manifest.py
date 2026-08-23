from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agent_mem_bridge.context_manifest import compile_context, render_context
from agent_mem_bridge.repository import content_hash_for_content
from agent_mem_bridge.schema import exact_content_hash
from agent_mem_bridge.storage import MemoryStore
from agent_mem_bridge.task_memory import assemble_task_memory


def _store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")


def _governed_report(store: MemoryStore, *, query: str) -> dict[str, Any]:
    return assemble_task_memory(
        store,
        query=query,
        project_namespace="project:checkout",
        global_namespace="global",
        task_domain="release",
    )


def _procedure_content(
    *,
    status: str = "validated",
    goal: str = "Run the checkout release.",
) -> str:
    return (
        "record_type: procedure\n"
        f"goal: {goal}\n"
        "when_to_use: During a checked release window.\n"
        "when_not_to_use: When the release state is draft.\n"
        "prerequisites: approved change ticket | verified backup\n"
        "steps: verify state | deploy | validate\n"
        "failure_mode: Deploying in draft state can publish an unsafe release.\n"
        "rollback_path: Restore the prior release.\n"
        f"procedure_status: {status}\n"
    )


def _current_state_snapshot(store: MemoryStore, *, status: str = "draft") -> dict[str, Any]:
    absent = store.dynamic_state.read(workspace_key="project:checkout", state_key="release:current")
    store.dynamic_state.transition_status(
        workspace_key="project:checkout",
        state_key="release:current",
        to_status=status,
        expected_version=0,
        expected_database_epoch=str(absent["database_epoch"]),
        idempotency_key=f"context-manifest:state:{status}",
        provenance={"actor": "context-manifest-test"},
    )
    return store.dynamic_state.read(workspace_key="project:checkout", state_key="release:current")


def _first_item(manifest: Any, *, source: str) -> dict[str, Any]:
    serialized = manifest.to_dict()
    return next(item for item in serialized["items"] if item["source"] == source)


def test_authoritative_state_value_renders_before_conflicting_memory_and_tiny_budget_fails_closed(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    stale_memory = store.store(
        namespace="global",
        title="Published release guidance",
        content="record_type: concept-note\nclaim: release status is published for the checkout rollout.\n",
        tags=["kind:concept-note"],
    )
    state = _current_state_snapshot(store, status="draft")
    report = _governed_report(store, query="checkout rollout release status")

    unconstrained = compile_context(task_memory=report, state_snapshots=[state], budget_tokens=2_048)
    state_item = unconstrained.items[0]
    constrained = compile_context(
        task_memory=report,
        state_snapshots=[state],
        budget_tokens=state_item.token_cost,
    )
    rendered = render_context(unconstrained)

    assert state_item.source == "dynamic_state"
    assert '"status":"draft"' in rendered
    assert "release status is published" in rendered
    assert [item.source for item in constrained.items] == ["dynamic_state"]
    assert str(stale_memory["id"]) not in {item.item_id for item in constrained.items}
    assert any(omission["reason"] == "budget_exceeded" for omission in constrained.omissions)
    with pytest.raises(ValueError, match="cannot fit required Dynamic State"):
        compile_context(
            task_memory=report,
            state_snapshots=[state],
            budget_tokens=state_item.token_cost - 1,
        )


def test_serialized_manifest_is_metadata_only_while_rendered_context_stays_transient(tmp_path: Path) -> None:
    store = _store(tmp_path)
    query = "confidential launch identifier zeta-42"
    memory_body = "confidential launch identifier zeta-42 memory instruction"
    store.store(
        namespace="global",
        title="Confidential launch guidance",
        content=f"record_type: concept-note\nclaim: {memory_body}\n",
        tags=["kind:concept-note"],
    )
    state = _current_state_snapshot(store)
    session_body = "session-local body that must never be archived"
    report = _governed_report(store, query=query)

    manifest = compile_context(
        task_memory=report,
        state_snapshots=[state],
        session_items=[{"title": "handoff", "content": session_body}],
    )
    serialized = manifest.serialize()
    rendered = render_context(manifest)
    payload = json.loads(serialized)

    assert query not in serialized
    assert memory_body not in serialized
    assert session_body not in serialized
    assert '"status":"draft"' not in serialized
    assert memory_body in rendered
    assert session_body in rendered
    assert '"status":"draft"' in rendered
    assert payload["rendered_context_sha256"] == manifest.rendered_context_sha256
    assert payload["task_identifier_sha256"] != query
    assert "render_text" not in serialized
    assert "render_text" not in json.dumps(payload["items"], sort_keys=True)


def test_manifest_retains_content_versions_provenance_and_selection_metadata(tmp_path: Path) -> None:
    store = _store(tmp_path)
    stored = store.store(
        namespace="project:checkout",
        title="Approved checkout procedure",
        content=_procedure_content(),
        tags=["kind:procedure", "domain:release"],
        actor="release-owner",
        source_client="pytest-client",
        source_model="fixture-model",
        client_session_id="session-123",
        client_workspace="checkout-workspace",
    )
    report = _governed_report(store, query="checkout release")

    manifest = compile_context(task_memory=report)
    report_procedure = next(item for item in report["procedure_hits"] if item["id"] == stored["id"])
    procedure = next(item for item in manifest.items if item.item_id == str(stored["id"]))
    metadata = _first_item(manifest, source="task_memory")

    assert procedure.content_hash == content_hash_for_content(str(report_procedure["content"]))
    assert procedure.exact_content_hash == exact_content_hash(str(report_procedure["content"]))
    assert procedure.selected_as == "procedure-anchor"
    assert procedure.selection_score is not None
    assert "direct:procedure" in procedure.selection_reasons
    assert dict(procedure.provenance) == {
        "actor": "release-owner",
        "source_client": "pytest-client",
        "source_model": "fixture-model",
        "client_session_id": "session-123",
        "client_workspace": "checkout-workspace",
    }
    assert metadata["exact_content_hash"] == exact_content_hash(str(report_procedure["content"]))
    assert metadata["selection"]["selected_as"] == "procedure-anchor"
    assert metadata["provenance"]["source_client"] == "pytest-client"
    assert manifest.compiler_version
    assert manifest.selection_policy_version
    assert len(manifest.input_fingerprint) == 64


def test_compiler_requires_relation_aware_governed_report_and_preserves_procedure_safety_fields(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store.store(
        namespace="project:checkout",
        title="Approved checkout procedure",
        content=_procedure_content(),
        tags=["kind:procedure", "domain:release"],
    )
    governed = _governed_report(store, query="checkout release")
    flat = assemble_task_memory(
        store,
        query="checkout release",
        project_namespace="project:checkout",
        global_namespace="global",
        relation_aware=False,
        task_domain="release",
    )

    rendered = render_context(compile_context(task_memory=governed))

    assert "when_not_to_use: When the release state is draft." in rendered
    assert "prerequisites: approved change ticket | verified backup" in rendered
    with pytest.raises(ValueError, match="relation-aware"):
        compile_context(task_memory=flat)
    with pytest.raises(ValueError, match="project_namespace"):
        compile_context(task_memory={"assembly_mode": "relation-aware", "query": "missing"})


def test_governed_suppression_and_current_session_parity_are_inherited_without_retrieval(tmp_path: Path) -> None:
    store = _store(tmp_path)
    predecessor = store.store(
        namespace="project:checkout",
        title="Old checkout release procedure",
        content=_procedure_content(goal="Run the old checkout release."),
        tags=["kind:procedure", "domain:release"],
    )
    revision = store.revise(
        str(predecessor["id"]),
        replacement_content=_procedure_content(goal="Run the current checkout release."),
        title="Current checkout release procedure",
    )
    unsafe = store.store(
        namespace="project:checkout",
        title="Unsafe checkout release procedure",
        content=_procedure_content(status="unsafe", goal="Skip release checks."),
        tags=["kind:procedure", "domain:release"],
    )
    report = _governed_report(store, query="checkout release")

    manifest = compile_context(
        task_memory=report,
        session_items=[{"title": "current handoff", "content": "Use the current release window.", "token_cost": 3}],
        budget_tokens=2_048,
    )
    rendered = render_context(manifest)
    selected_ids = {item.item_id for item in manifest.items if item.item_id}
    omissions = {str(item["reason"]) for item in manifest.omissions}

    assert str(predecessor["id"]) not in selected_ids
    assert str(unsafe["id"]) not in selected_ids
    assert str(revision["successor_id"]) in selected_ids
    assert "governed_suppressed:superseded_revision" in omissions
    assert "governed_suppressed:procedure_status:unsafe" in omissions
    assert rendered.startswith("[Session] current handoff")
    assert "Use the current release window." in rendered
    assert next(item for item in manifest.items if item.source == "session").token_cost == 3


def test_token_budget_is_deterministic_for_cjk_and_session_cost_override(tmp_path: Path) -> None:
    store = _store(tmp_path)
    report = _governed_report(store, query="发布")
    session = {"title": "中文", "content": "当前发布窗口", "token_cost": 4}

    first = compile_context(task_memory=report, session_items=[session], budget_tokens=4)
    second = compile_context(task_memory=report, session_items=[session], budget_tokens=4)

    assert first == second
    assert first.used_tokens == 4
    assert first.remaining_tokens == 0
    assert render_context(first) == "[Session] 中文\n当前发布窗口"
    with pytest.raises(ValueError, match="token_cost"):
        compile_context(
            task_memory=report,
            session_items=[{"title": "bad", "content": "cost", "token_cost": -1}],
        )


def test_compile_context_is_transient_and_does_not_read_or_write_storage(tmp_path: Path) -> None:
    store = _store(tmp_path)
    report = _governed_report(store, query="transient")
    before = store.stats(namespace="project:checkout")

    manifest = compile_context(
        task_memory=report,
        session_items=["Only an explicit session-local item."],
    )

    after = store.stats(namespace="project:checkout")
    assert before == after
    assert render_context(manifest) == "[Session] session-1\nOnly an explicit session-local item."


def test_transient_rendering_redacts_sensitive_session_fields_without_serializing_them(tmp_path: Path) -> None:
    store = _store(tmp_path)
    report = _governed_report(store, query="sanitized handoff")

    manifest = compile_context(
        task_memory=report,
        session_items=[{"title": "handoff", "content": "api_key: never-render\nUse the approved window."}],
    )
    rendered = render_context(manifest)

    assert "never-render" not in rendered
    assert "[redacted sensitive line]" in rendered
    assert "Use the approved window." in rendered
    assert "never-render" not in manifest.serialize()


@pytest.mark.parametrize("missing_key", ["value", "value_hash", "database_epoch"])
def test_existing_dynamic_state_snapshot_must_be_complete(tmp_path: Path, missing_key: str) -> None:
    store = _store(tmp_path)
    report = _governed_report(store, query="checkout release")
    state = _current_state_snapshot(store)
    malformed = dict(state)
    malformed[missing_key] = None

    with pytest.raises(ValueError, match="Dynamic State snapshot"):
        compile_context(task_memory=report, state_snapshots=[malformed])


def test_dynamic_state_snapshot_hash_identity_and_epoch_fail_closed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    report = _governed_report(store, query="checkout release")
    state = _current_state_snapshot(store)

    mismatched = dict(state)
    mismatched["value_hash"] = "0" * 64
    with pytest.raises(ValueError, match="value_hash mismatch"):
        compile_context(task_memory=report, state_snapshots=[mismatched])

    with pytest.raises(ValueError, match="duplicate Dynamic State snapshot identity"):
        compile_context(task_memory=report, state_snapshots=[state, dict(state)])

    different_epoch = dict(state)
    different_epoch["state_key"] = "release:next"
    different_epoch["database_epoch"] = "different-epoch"
    with pytest.raises(ValueError, match="share one database_epoch"):
        compile_context(task_memory=report, state_snapshots=[state, different_epoch])


def test_absent_dynamic_state_remains_an_explicit_nonblocking_omission(tmp_path: Path) -> None:
    store = _store(tmp_path)
    report = _governed_report(store, query="checkout release")
    absent = store.dynamic_state.read(workspace_key="project:checkout", state_key="release:absent")

    manifest = compile_context(task_memory=report, state_snapshots=[absent])

    assert manifest.items == ()
    assert [omission["reason"] for omission in manifest.omissions] == ["state_absent"]


def test_input_fingerprint_covers_session_title_and_canonical_memory_digests(tmp_path: Path) -> None:
    store = _store(tmp_path)
    stored = store.store(
        namespace="project:checkout",
        title="Canonical checkout release procedure",
        content=_procedure_content(goal="Preserve canonical digest semantics during checkout release."),
        tags=["kind:procedure", "domain:release"],
    )
    report = _governed_report(store, query="canonical checkout release")

    first = compile_context(
        task_memory=report,
        session_items=[{"title": "Session A", "content": "Same current handoff."}],
    )
    second = compile_context(
        task_memory=report,
        session_items=[{"title": "Session B", "content": "Same current handoff."}],
    )
    report_item = next(
        item
        for report_key in (
            "procedure_hits",
            "decision_hits",
            "constraint_hits",
            "concept_hits",
            "belief_hits",
            "domain_hits",
            "supporting_hits",
            "corrective_items",
        )
        for item in report[report_key]
        if item["id"] == stored["id"]
    )
    reference = next(item for item in first.items if item.item_id == stored["id"])

    assert first.input_fingerprint != second.input_fingerprint
    assert reference.content_hash == content_hash_for_content(str(report_item["content"]))
    assert reference.exact_content_hash == exact_content_hash(str(report_item["content"]))
    assert reference.content_hash
    assert reference.exact_content_hash
