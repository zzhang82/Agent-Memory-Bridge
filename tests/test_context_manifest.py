from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent_mem_bridge.context_manifest import ContextManifest, compile_context, render_context
from agent_mem_bridge.storage import MemoryStore
from agent_mem_bridge.task_memory import assemble_task_memory


def _store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")


def _procedure_content(status: str, label: str) -> str:
    return (
        "record_type: procedure\n"
        f"goal: Execute the {label} checkout deployment procedure.\n"
        "when_to_use: Before a production checkout deployment.\n"
        "steps: verify deployment | deploy checkout | validate checkout\n"
        f"procedure_status: {status}\n"
    )


def _current_state_snapshot(store: MemoryStore) -> dict[str, Any]:
    absent = store.dynamic_state.read(workspace_key="project:checkout", state_key="release:current")
    store.dynamic_state.transition_status(
        workspace_key="project:checkout",
        state_key="release:current",
        to_status="draft",
        expected_version=0,
        expected_database_epoch=str(absent["database_epoch"]),
        idempotency_key="context-manifest:state-draft",
        provenance={"actor": "context-manifest-test"},
    )
    return store.dynamic_state.read(workspace_key="project:checkout", state_key="release:current")


def _empty_sections(*, query: str) -> dict[str, object]:
    return {
        "query": query,
        "procedure_hits": [],
        "concept_hits": [],
        "belief_hits": [],
        "domain_hits": [],
        "supporting_hits": [],
        "corrective_items": [],
        "suppressed_items": [],
    }


def test_compile_context_consumes_governed_task_memory_and_state_references(tmp_path: Path) -> None:
    store = _store(tmp_path)
    predecessor = store.store(
        namespace="project:checkout",
        title="Obsolete checkout procedure",
        content=_procedure_content("validated", "obsolete"),
        tags=["kind:procedure"],
    )
    revision = store.revise(
        str(predecessor["id"]),
        replacement_content=_procedure_content("validated", "current"),
        title="Current checkout procedure",
    )
    unsafe = store.store(
        namespace="project:checkout",
        title="Unsafe checkout procedure",
        content=_procedure_content("unsafe", "unsafe"),
        tags=["kind:procedure"],
    )
    snapshot = _current_state_snapshot(store)
    report = assemble_task_memory(
        store,
        query="checkout deployment procedure",
        project_namespace="project:checkout",
        task_domain="release",
    )

    manifest = compile_context(
        task_memory=report,
        state_snapshots=[snapshot],
        session_items=[{"title": "handoff", "content": "Operator confirmed the checkout window."}],
        budget_chars=8_000,
    )
    rendered = render_context(manifest)
    selected_ids = {item.item_id for item in manifest.items if item.item_id}
    omitted_reasons = {str(item["reason"]) for item in manifest.omissions}
    state_items = [item for item in manifest.items if item.source == "dynamic_state"]

    assert str(predecessor["id"]) not in selected_ids
    assert str(unsafe["id"]) not in selected_ids
    assert str(revision["successor_id"]) in selected_ids
    assert "governed_suppressed:superseded_revision" in omitted_reasons
    assert "governed_suppressed:procedure_status:unsafe" in omitted_reasons
    assert len(state_items) == 1
    assert state_items[0].workspace_key == snapshot["workspace_key"]
    assert state_items[0].state_key == snapshot["state_key"]
    assert state_items[0].version == snapshot["version"]
    assert state_items[0].value_hash == snapshot["value_hash"]
    assert state_items[0].database_epoch == snapshot["database_epoch"]
    assert str(snapshot["value"]["status"]) not in rendered
    assert "Operator confirmed the checkout window." in rendered


def test_compile_context_is_reproducible_and_serializes_deterministically() -> None:
    task_memory = _empty_sections(query="release cutover")
    task_memory["procedure_hits"] = [
        {
            "id": "procedure:cutover",
            "title": "Release cutover",
            "procedure": {"goal": "Run cutover.", "steps": ["verify", "deploy"]},
        }
    ]
    state = {
        "workspace_key": "project:checkout",
        "state_key": "release:current",
        "version": 4,
        "value_hash": "a" * 64,
        "database_epoch": "epoch-1",
        "exists": True,
    }
    session_items = [{"title": "handoff", "content": "Owner is on call."}]

    first = compile_context(
        task_memory=task_memory,
        state_snapshots=[state],
        session_items=session_items,
        budget_chars=8_000,
    )
    second = compile_context(
        task_memory=task_memory,
        state_snapshots=[state],
        session_items=session_items,
        budget_chars=8_000,
    )

    assert isinstance(first, ContextManifest)
    assert first == second
    assert first.fingerprint == second.fingerprint
    assert first.serialize() == second.serialize()
    assert render_context(first) == render_context(second)
    assert json.loads(first.serialize())["fingerprint"] == first.fingerprint
    assert all(len(item.fingerprint) == 64 for item in first.items)


def test_compile_context_applies_budget_in_fixed_input_order_and_records_omissions() -> None:
    task_memory = _empty_sections(query="budget")
    task_memory["procedure_hits"] = [
        {
            "id": "procedure:one",
            "title": "First procedure",
            "procedure": {"goal": "Keep the first item."},
        },
        {
            "id": "procedure:two",
            "title": "Second procedure",
            "procedure": {"goal": "This item must be omitted by budget."},
        },
    ]
    unconstrained = compile_context(task_memory=task_memory, budget_chars=8_000)
    first_item_budget = unconstrained.items[0].char_count

    manifest = compile_context(task_memory=task_memory, budget_chars=first_item_budget)

    assert [item.item_id for item in manifest.items] == ["procedure:one"]
    assert manifest.used_chars == first_item_budget
    assert manifest.remaining_chars == 0
    assert any(
        omission["id"] == "procedure:two" and omission["reason"] == "budget_exceeded" for omission in manifest.omissions
    )
    assert render_context(manifest) == manifest.items[0].text


def test_compile_context_redacts_sensitive_lines_and_never_renders_state_values() -> None:
    task_memory = _empty_sections(query="sanitized")
    task_memory["concept_hits"] = [
        {
            "id": "concept:credential-hygiene",
            "title": "Credential hygiene",
            "content": "Keep this guidance.\napi_key: do-not-render\nContinue safely.",
        }
    ]
    state = {
        "workspace_key": "project:checkout",
        "state_key": "release:current",
        "version": 1,
        "value": {"owner": "private-owner", "status": "draft"},
        "value_hash": "b" * 64,
        "database_epoch": "epoch-2",
        "exists": True,
    }

    manifest = compile_context(
        task_memory=task_memory,
        state_snapshots=[state],
        session_items=[{"title": "handoff", "content": "token: do-not-render\nSafe handoff note."}],
    )
    rendered = render_context(manifest)

    assert "do-not-render" not in rendered
    assert "private-owner" not in rendered
    assert "status: draft" not in rendered
    assert rendered.count("[redacted sensitive line]") == 2
    assert "Safe handoff note." in rendered
    assert manifest.serialize().count("do-not-render") == 0


def test_compile_context_reports_absent_or_incomplete_state_references_without_copying_state() -> None:
    manifest = compile_context(
        task_memory=_empty_sections(query="state"),
        state_snapshots=[
            {
                "workspace_key": "project:checkout",
                "state_key": "release:absent",
                "exists": False,
                "database_epoch": "epoch-3",
            },
            {
                "workspace_key": "project:checkout",
                "state_key": "release:broken",
                "version": 2,
                "exists": True,
                "value_hash": None,
                "database_epoch": "epoch-3",
            },
        ],
    )

    assert manifest.items == ()
    assert {str(omission["reason"]) for omission in manifest.omissions} == {
        "state_absent",
        "incomplete_state_reference",
    }


def test_compile_context_never_reads_or_persists_through_memory_store(tmp_path: Path) -> None:
    store = _store(tmp_path)
    before = store.stats(namespace="project:checkout")
    manifest = compile_context(
        task_memory=_empty_sections(query="transient"),
        session_items=["Only an explicit session-local item."],
    )
    after = store.stats(namespace="project:checkout")

    assert render_context(manifest) == "[Session] session-1\nOnly an explicit session-local item."
    assert before == after
