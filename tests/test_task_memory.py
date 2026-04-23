from datetime import UTC, datetime, timedelta
from pathlib import Path

from agent_mem_bridge.storage import MemoryStore
from agent_mem_bridge.task_memory import assemble_task_memory, render_task_memory_text


def test_assemble_task_memory_composes_project_procedure_with_supporting_layers(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")

    belief = store.store(
        namespace="global",
        kind="memory",
        title="[[Belief]] release verification",
        content=(
            "record_type: belief\n"
            "claim: Verify benchmark and healthcheck before release cutover.\n"
            "support_count: 5\n"
            "distinct_session_count: 4\n"
            "contradiction_count: 0\n"
            "confidence: 0.82\n"
            "status: active\n"
        ),
        tags=["kind:belief", "domain:release", "topic:cutover"],
    )
    concept = store.store(
        namespace="global",
        kind="memory",
        title="[[Concept Note]] release cutover pattern",
        content=(
            "record_type: concept-note\n"
            "concept: Release cutover verification loop.\n"
            "claim: Release cutover stays safer when verification stays explicit.\n"
            "rule: Verify benchmark and healthcheck before release cutover.\n"
            f"depends_on: {belief['id']}\n"
        ),
        tags=["kind:concept-note", "domain:release", "topic:cutover"],
    )
    store.store(
        namespace="project:alpha",
        kind="memory",
        title="[[Procedure]] release cutover",
        content=(
            "record_type: procedure\n"
            "goal: Run release cutover safely.\n"
            "when_to_use: Before tagging a release.\n"
            "when_not_to_use: When benchmark or healthcheck cannot run.\n"
            "prerequisites: clean worktree | release owner assigned\n"
            "steps: run benchmark | run healthcheck | tag release\n"
            "failure_mode: Tagging without proof can ship stale behavior.\n"
            "rollback_path: delete tag | reopen release checklist\n"
            "procedure_status: validated\n"
            f"depends_on: {concept['id']}\n"
            f"supports: {belief['id']}\n"
        ),
        tags=["kind:procedure", "domain:release", "topic:cutover"],
    )

    report = assemble_task_memory(
        store,
        query="release cutover",
        project_namespace="project:alpha",
        global_namespace="global",
    )

    assert len(report["procedure_hits"]) == 1
    assert report["procedure_hits"][0]["procedure"]["goal"] == "Run release cutover safely."
    assert report["procedure_hits"][0]["procedure"]["when_to_use"] == "Before tagging a release."
    assert report["procedure_hits"][0]["procedure"]["when_not_to_use"] == (
        "When benchmark or healthcheck cannot run."
    )
    assert report["procedure_hits"][0]["procedure"]["prerequisites"] == [
        "clean worktree",
        "release owner assigned",
    ]
    assert report["procedure_hits"][0]["procedure"]["steps"] == [
        "run benchmark",
        "run healthcheck",
        "tag release",
    ]
    assert report["procedure_hits"][0]["procedure"]["failure_mode"] == (
        "Tagging without proof can ship stale behavior."
    )
    assert report["procedure_hits"][0]["procedure"]["rollback_path"] == (
        "delete tag | reopen release checklist"
    )
    assert report["procedure_hits"][0]["procedure"]["governance"]["status"] == "validated"
    assert len(report["concept_hits"]) == 1
    assert len(report["belief_hits"]) == 1
    assert report["assembly_mode"] == "relation-aware"
    assert report["supporting_hits"] == []
    assert report["procedure_hits"][0]["task_memory"]["selected_as"] == "procedure-anchor"
    assert "Procedures:" in report["summary"]
    assert "Domains:" in report["summary"]
    assert "steps: run benchmark | run healthcheck | tag release" in report["summary"]
    assert "procedure_status: validated" in report["summary"]

    flat_report = assemble_task_memory(
        store,
        query="release cutover",
        project_namespace="project:alpha",
        global_namespace="global",
        relation_aware=False,
    )
    assert flat_report["assembly_mode"] == "flat"
    assert {item["id"] for item in flat_report["supporting_hits"]} == {belief["id"], concept["id"]}


def test_render_task_memory_text_handles_empty_report(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")

    report = assemble_task_memory(
        store,
        query="unseen task",
        project_namespace="project:alpha",
        global_namespace="global",
    )
    rendered = render_task_memory_text(report)

    assert "Task memory for: unseen task" in rendered
    assert "Procedures:" in rendered
    assert "(none)" in rendered


def test_assemble_task_memory_skips_expired_project_procedure_for_current_global(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    expired_until = (datetime.now(UTC) - timedelta(days=1)).isoformat()

    store.store(
        namespace="project:alpha",
        kind="memory",
        title="[[Procedure]] release handoff stale project path",
        content=(
            "record_type: procedure\n"
            "goal: Run release handoff using the stale project path.\n"
            "steps: skip owner | merge release\n"
            f"valid_until: {expired_until}\n"
        ),
        tags=["kind:procedure", "domain:release", "topic:handoff"],
    )
    store.store(
        namespace="global",
        kind="memory",
        title="[[Procedure]] release handoff current global path",
        content=(
            "record_type: procedure\n"
            "goal: Run release handoff using the current global path.\n"
            "steps: assign owner | confirm queue | tag release\n"
        ),
        tags=["kind:procedure", "domain:release", "topic:handoff"],
    )

    report = assemble_task_memory(
        store,
        query="release handoff",
        project_namespace="project:alpha",
        global_namespace="global",
    )

    titles = _packet_titles(report)
    assert "[[Procedure]] release handoff current global path" in titles
    assert "[[Procedure]] release handoff stale project path" not in titles
    assert any(item["reason"] == "validity:expired" for item in report["suppressed_items"])


def test_assemble_task_memory_prefers_validated_procedure_over_draft_project_hit(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")

    store.store(
        namespace="project:alpha",
        kind="memory",
        title="[[Procedure]] release cutover draft shortcut",
        content=(
            "record_type: procedure\n"
            "goal: Run release cutover using an unreviewed shortcut.\n"
            "when_to_use: Draft experiment only.\n"
            "steps: skip benchmark | tag release\n"
            "procedure_status: draft\n"
        ),
        tags=["kind:procedure", "domain:release", "topic:cutover"],
    )
    store.store(
        namespace="global",
        kind="memory",
        title="[[Procedure]] release cutover validated path",
        content=(
            "record_type: procedure\n"
            "goal: Run release cutover using validated proof gates.\n"
            "when_to_use: Normal release cutover.\n"
            "steps: run benchmark | run release contract | tag release\n"
            "procedure_status: validated\n"
        ),
        tags=["kind:procedure", "domain:release", "topic:cutover"],
    )

    report = assemble_task_memory(
        store,
        query="release cutover procedure",
        project_namespace="project:alpha",
        global_namespace="global",
        procedure_limit=1,
    )

    assert [item["title"] for item in report["procedure_hits"]] == [
        "[[Procedure]] release cutover validated path"
    ]
    assert report["procedure_hits"][0]["procedure"]["governance"]["status"] == "validated"


def test_assemble_task_memory_suppresses_stale_replaced_and_unsafe_procedures(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")

    for status in ("stale", "replaced", "unsafe"):
        store.store(
            namespace="project:alpha",
            kind="memory",
            title=f"[[Procedure]] release cutover {status}",
            content=(
                "record_type: procedure\n"
                f"goal: Run the {status} release cutover procedure.\n"
                "steps: skip proof | tag release\n"
                f"procedure_status: {status}\n"
            ),
            tags=["kind:procedure", "domain:release", "topic:cutover"],
        )
    store.store(
        namespace="project:alpha",
        kind="memory",
        title="[[Procedure]] release cutover safe current",
        content=(
            "record_type: procedure\n"
            "goal: Run the safe current release cutover procedure.\n"
            "steps: run benchmark | run release contract | tag release\n"
            "procedure_status: validated\n"
        ),
        tags=["kind:procedure", "domain:release", "topic:cutover"],
    )

    report = assemble_task_memory(
        store,
        query="release cutover procedure",
        project_namespace="project:alpha",
        global_namespace="global",
    )

    assert [item["title"] for item in report["procedure_hits"]] == [
        "[[Procedure]] release cutover safe current"
    ]
    assert {item["reason"] for item in report["suppressed_items"]} >= {
        "procedure_status:stale",
        "procedure_status:replaced",
        "procedure_status:unsafe",
    }


def test_assemble_task_memory_suppresses_superseded_and_contradicted_beliefs(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    old_belief = store.store(
        namespace="global",
        kind="memory",
        title="[[Belief]] implicit release owner",
        content="record_type: belief\nclaim: Release owner can stay implicit.\n",
        tags=["kind:belief", "domain:release", "topic:handoff"],
    )
    contradicted_belief = store.store(
        namespace="global",
        kind="memory",
        title="[[Belief]] optional release owner",
        content="record_type: belief\nclaim: Release owner assignment is optional.\n",
        tags=["kind:belief", "domain:release", "topic:handoff"],
    )
    current_belief = store.store(
        namespace="global",
        kind="memory",
        title="[[Belief]] explicit release owner",
        content=(
            "record_type: belief\n"
            "claim: Release owner must be explicit before execution.\n"
            f"supersedes: {old_belief['id']}\n"
            f"contradicts: {contradicted_belief['id']}\n"
        ),
        tags=["kind:belief", "domain:release", "topic:handoff"],
    )

    report = assemble_task_memory(
        store,
        query="release owner",
        project_namespace="project:alpha",
        global_namespace="global",
    )

    assert [item["id"] for item in report["belief_hits"]] == [current_belief["id"]]
    suppressed_by_reason = {item["reason"]: item["title"] for item in report["suppressed_items"]}
    assert suppressed_by_reason["superseded"] == "[[Belief]] implicit release owner"
    assert suppressed_by_reason["contradicted"] == "[[Belief]] optional release owner"


def test_assemble_task_memory_filters_expired_and_future_relation_targets(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    expired_until = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    future_from = (datetime.now(UTC) + timedelta(days=30)).isoformat()

    current_support = store.store(
        namespace="global",
        kind="memory",
        title="Current rollback contact",
        content="claim: Use the current rollback contact during release handoff.\n",
        tags=["domain:release", "topic:handoff"],
    )
    expired_support = store.store(
        namespace="global",
        kind="memory",
        title="Expired rollback contact",
        content=(
            "claim: Use the expired rollback contact during release handoff.\n"
            f"valid_until: {expired_until}\n"
        ),
        tags=["domain:release", "topic:handoff"],
    )
    future_support = store.store(
        namespace="global",
        kind="memory",
        title="Future rollback contact",
        content=(
            "claim: Use the future rollback contact during release handoff.\n"
            f"valid_from: {future_from}\n"
        ),
        tags=["domain:release", "topic:handoff"],
    )
    store.store(
        namespace="project:alpha",
        kind="memory",
        title="[[Procedure]] release handoff support filtering",
        content=(
            "record_type: procedure\n"
            "goal: Run release handoff with valid support only.\n"
            "steps: assign owner | confirm rollback contact\n"
            f"depends_on: {current_support['id']} | {expired_support['id']} | {future_support['id']}\n"
        ),
        tags=["kind:procedure", "domain:release", "topic:handoff"],
    )

    report = assemble_task_memory(
        store,
        query="release handoff support filtering",
        project_namespace="project:alpha",
        global_namespace="global",
    )

    assert [item["title"] for item in report["supporting_hits"]] == ["Current rollback contact"]
    suppressed_titles = {item["title"] for item in report["suppressed_items"]}
    assert "Expired rollback contact" in suppressed_titles
    assert "Future rollback contact" in suppressed_titles


def _packet_titles(report: dict[str, object]) -> set[str]:
    titles: set[str] = set()
    for key in ("procedure_hits", "concept_hits", "belief_hits", "domain_hits", "supporting_hits"):
        for item in report.get(key, []) or []:  # type: ignore[union-attr]
            if isinstance(item, dict) and item.get("title"):
                titles.add(str(item["title"]))
    return titles
