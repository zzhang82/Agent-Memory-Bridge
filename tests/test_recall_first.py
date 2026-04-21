from pathlib import Path

from agent_mem_bridge.recall_first import plan_recall, recall_first
from agent_mem_bridge.storage import MemoryStore


def test_plan_recall_flags_issue_like_queries() -> None:
    issue_plan = plan_recall("We hit a wrong db recall bug", "project:alpha")
    memory_plan = plan_recall("human readable token memory gotcha", "project:alpha")
    calm_plan = plan_recall("What did we discuss about roadmap themes", "project:alpha")

    assert issue_plan.should_search_local is True
    assert "symptom:wrong-db" in issue_plan.tag_hints
    assert memory_plan.should_search_local is True
    assert "problem:narrative-memory" in memory_plan.tag_hints
    assert calm_plan.should_search_local is False


def test_recall_first_prefers_project_and_core_memory(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    store.store(
        namespace="global",
        kind="memory",
        title="[[Policy]] verify before done",
        content="claim: verify behavior before calling work done",
        tags=["record:core-policy", "control:policy"],
    )
    store.store(
        namespace="global",
        kind="memory",
        title="[[Persona]] steady partner",
        content="claim: collaborate calmly and explain the why",
        tags=["record:persona", "control:policy"],
    )
    store.store(
        namespace="global",
        kind="memory",
        title="[[Soul]] continuity matters",
        content="claim: build cumulative context across sessions",
        tags=["record:soul", "control:policy"],
    )
    store.store(
        namespace="project:alpha",
        kind="memory",
        title="[[Codex]] auto closeout",
        content="record_type: summary\nclaim: sqlite timeout happened during sync",
        tags=["kind:summary", "project:alpha"],
    )
    store.store(
        namespace="global",
        kind="memory",
        title="[[Learn]] sqlite sync discipline",
        content="record_type: learn\nclaim: Keep sqlite write scopes short during sync\nscope: global",
        tags=["kind:learn", "domain:sqlite", "topic:session-sync"],
    )
    store.store(
        namespace="global",
        kind="memory",
        title="[[Gotcha]] sqlite timeout",
        content=(
            "record_type: gotcha\n"
            "claim: sqlite timeouts can appear during sync\n"
            "trigger: long-running lock contention\n"
            "symptom: timeout during memory write\n"
            "fix: retry with tighter write scope\n"
        ),
        tags=["kind:gotcha", "domain:sqlite", "topic:session-sync"],
    )
    store.store(
        namespace="global",
        kind="memory",
        title="[[Domain Note]] sqlite patterns",
        content="record_type: domain-note\ndomain: domain:sqlite\nclaim: prefer WAL and short write scopes",
        tags=["kind:domain-note", "domain:sqlite"],
    )

    result = recall_first(
        store=store,
        query="sqlite timeout during sync",
        project_namespace="project:alpha",
        limit=5,
    )

    assert result["should_search_local"] is True
    assert [layer["label"] for layer in result["profile_bundle_hits"]] == ["core-policy", "persona", "soul"]
    assert len(result["profile_bundle_hits"][0]["items"]) == 1
    assert len(result["profile_bundle_hits"][1]["items"]) == 1
    assert len(result["profile_bundle_hits"][2]["items"]) == 1
    assert len(result["project_hits"]) == 1
    assert len(result["learn_hits"]) == 1
    assert len(result["gotcha_hits"]) == 1
    assert len(result["domain_hits"]) == 1
    assert result["reference_hits"] == []
    assert result["recommended_action"] == "Search local memory first."


def test_recall_first_uses_tag_hints_for_short_issue_queries(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    store.store(
        namespace="global",
        kind="memory",
        title="[[Gotcha]] canonical bridge path",
        content=(
            "record_type: gotcha\n"
            "claim: automation and interactive MCP must share one canonical bridge database\n"
            "trigger: automation path writes away from the interactive store\n"
            "symptom: sync appears healthy while recall misses new memories\n"
            "fix: use one canonical runtime path and one shared bridge.db\n"
        ),
        tags=[
            "kind:gotcha",
            "problem:split-store",
            "symptom:wrong-db",
            "fix:canonical-runtime-path",
            "topic:runtime-path",
        ],
    )

    result = recall_first(
        store=store,
        query="wrong db after sync",
        project_namespace="project:alpha",
        limit=5,
    )

    assert result["should_search_local"] is True
    assert "symptom:wrong-db" in result["tag_hints"]
    assert len(result["gotcha_hits"]) == 1


def test_recall_first_uses_reference_hits_only_as_fallback(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    store.store(
        namespace="global",
        kind="memory",
        title="[[Reference]] startup routing manual",
        content="claim: recall startup routing manual before guessing about runtime path",
        tags=["kind:reference", "record:legacy-doc"],
    )

    result = recall_first(
        store=store,
        query="recall startup routing manual",
        project_namespace="project:alpha",
        limit=5,
    )

    assert result["should_search_local"] is True
    assert all(layer["items"] == [] for layer in result["profile_bundle_hits"])
    assert result["learn_hits"] == []
    assert result["gotcha_hits"] == []
    assert result["domain_hits"] == []
    assert len(result["reference_hits"]) == 1
    assert result["reference_hits"][0]["title"] == "[[Reference]] startup routing manual"
    assert result["recommended_action"] == "Profile bundle missed; fallback reference memory may help before external search."


def test_recall_first_surfaces_procedures_and_supporting_task_memory(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    belief = store.store(
        namespace="global",
        kind="memory",
        title="[[Belief]] review queue ownership",
        content="record_type: belief\nclaim: Assign an explicit review queue owner.\nstatus: active\n",
        tags=["kind:belief", "domain:orchestration", "topic:review-flow"],
    )
    concept = store.store(
        namespace="global",
        kind="memory",
        title="[[Concept Note]] review queue pattern",
        content=(
            "record_type: concept-note\n"
            "concept: Review queue ownership.\n"
            "claim: Review handoff works better with one explicit owner.\n"
            f"depends_on: {belief['id']}\n"
        ),
        tags=["kind:concept-note", "domain:orchestration", "topic:review-flow"],
    )
    store.store(
        namespace="project:alpha",
        kind="memory",
        title="[[Procedure]] review handoff",
        content=(
            "record_type: procedure\n"
            "goal: Hand off review work cleanly.\n"
            "steps: assign owner | confirm queue | notify reviewer\n"
            f"depends_on: {concept['id']}\n"
            f"supports: {belief['id']}\n"
        ),
        tags=["kind:procedure", "domain:orchestration", "topic:review-flow"],
    )

    result = recall_first(
        store=store,
        query="review handoff owner",
        project_namespace="project:alpha",
        limit=5,
    )

    assert len(result["procedure_hits"]) == 1
    assert result["procedure_hits"][0]["procedure"]["steps"] == [
        "assign owner",
        "confirm queue",
        "notify reviewer",
    ]
    assert len(result["concept_hits"]) == 1
    assert len(result["belief_hits"]) == 1
    assert len(result["supporting_hits"]) == 2
    assert result["recommended_action"] == "Search local memory first, starting with applicable procedures and supporting concepts."
    assert "review handoff" in result["task_memory_summary"].lower()
