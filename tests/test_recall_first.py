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
        namespace="project:alpha",
        kind="memory",
        title="[[Codex]] auto closeout",
        content="record_type: summary\nclaim: sqlite timeout happened during sync",
        tags=["kind:summary", "project:alpha"],
    )
    store.store(
        namespace="cole-core",
        kind="memory",
        title="[[Cole Learn]] sqlite sync discipline",
        content="record_type: learn\nclaim: Keep sqlite write scopes short during sync\nscope: global",
        tags=["kind:learn", "domain:sqlite", "topic:session-sync"],
    )
    store.store(
        namespace="cole-core",
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
        namespace="cole-core",
        kind="memory",
        title="[[Cole Domain]] sqlite patterns",
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
    assert len(result["project_hits"]) == 1
    assert len(result["learn_hits"]) == 1
    assert len(result["gotcha_hits"]) == 1
    assert len(result["domain_hits"]) == 1
    assert result["recommended_action"] == "Search local memory first."


def test_recall_first_uses_tag_hints_for_short_issue_queries(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    store.store(
        namespace="cole-core",
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

