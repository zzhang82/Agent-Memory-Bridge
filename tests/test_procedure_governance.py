import ast
from pathlib import Path

import pytest

from agent_mem_bridge.procedure_governance import parse_procedure_artifact
from agent_mem_bridge.recall_first import recall_first
from agent_mem_bridge.storage import MemoryStore
from agent_mem_bridge.task_memory import assemble_task_memory


EXPECTED_PUBLIC_TOOLS = {
    "store",
    "recall",
    "browse",
    "stats",
    "forget",
    "claim_signal",
    "ack_signal",
    "extend_signal_lease",
    "promote",
    "export",
}


def test_parse_procedure_artifact_surfaces_governance_and_boundaries() -> None:
    procedure = parse_procedure_artifact(
        "record_type: procedure\n"
        "procedure_status: validated\n"
        "goal: Run a safe release cutover.\n"
        "when_to_use: Before public release tagging.\n"
        "when_not_to_use: For local-only experiments.\n"
        "prerequisites: clean tree | current benchmark\n"
        "steps: run tests | run release contract | tag release\n"
        "failure_mode: stale benchmark numbers mislead users.\n"
        "rollback_path: stop release and rerun checks.\n"
    )

    assert procedure["governance"]["status"] == "validated"
    assert procedure["governance"]["eligible"] is True
    assert procedure["governance"]["missing_minimum_fields"] == []
    assert procedure["when_not_to_use"] == "For local-only experiments."
    assert procedure["prerequisites"] == ["clean tree", "current benchmark"]
    assert procedure["steps"] == ["run tests", "run release contract", "tag release"]
    assert procedure["failure_mode"] == "stale benchmark numbers mislead users."
    assert procedure["rollback_path"] == "stop release and rerun checks."


def test_assemble_task_memory_prefers_validated_procedure_over_draft(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    store.store(
        namespace="project:alpha",
        kind="memory",
        title="[[Procedure]] release checklist draft",
        content=(
            "record_type: procedure\n"
            "procedure_status: draft\n"
            "goal: Draft release checklist.\n"
            "when_to_use: Before tagging.\n"
            "steps: quick smoke | tag release\n"
        ),
        tags=["kind:procedure", "domain:release", "topic:checklist"],
    )
    store.store(
        namespace="project:alpha",
        kind="memory",
        title="[[Procedure]] release checklist validated",
        content=(
            "record_type: procedure\n"
            "procedure_status: validated\n"
            "goal: Validated release checklist.\n"
            "when_to_use: Before tagging.\n"
            "when_not_to_use: For local spike branches.\n"
            "prerequisites: clean tree | current benchmark\n"
            "steps: run benchmark | run contract | tag release\n"
            "failure_mode: stale checks can mislead users.\n"
            "rollback_path: stop release and rerun checks.\n"
        ),
        tags=["kind:procedure", "domain:release", "topic:checklist"],
    )

    report = assemble_task_memory(
        store,
        query="release checklist",
        project_namespace="project:alpha",
        global_namespace="global",
        procedure_limit=1,
    )

    assert [item["title"] for item in report["procedure_hits"]] == [
        "[[Procedure]] release checklist validated"
    ]
    procedure = report["procedure_hits"][0]["procedure"]
    assert procedure["governance"]["status"] == "validated"
    assert "when_not_to_use: For local spike branches." in report["summary"]
    assert "rollback_path: stop release and rerun checks." in report["summary"]


def test_assemble_task_memory_suppresses_stale_and_replaced_procedures(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    old = store.store(
        namespace="project:alpha",
        kind="memory",
        title="[[Procedure]] sync skills old",
        content=(
            "record_type: procedure\n"
            "procedure_status: replaced\n"
            "goal: Sync skills using the old path.\n"
            "steps: overwrite global skills\n"
        ),
        tags=["kind:procedure", "domain:skills", "topic:sync"],
    )
    store.store(
        namespace="project:alpha",
        kind="memory",
        title="[[Procedure]] sync skills stale",
        content=(
            "record_type: procedure\n"
            "procedure_status: stale\n"
            "goal: Sync skills using the stale path.\n"
            "steps: use old shared link\n"
        ),
        tags=["kind:procedure", "domain:skills", "topic:sync"],
    )
    store.store(
        namespace="project:alpha",
        kind="memory",
        title="[[Procedure]] sync skills current",
        content=(
            "record_type: procedure\n"
            "procedure_status: validated\n"
            "goal: Sync skills by importing local global updates into the repo.\n"
            "when_to_use: When local global skills drift.\n"
            "steps: compare | import | commit\n"
            f"supersedes: {old['id']}\n"
        ),
        tags=["kind:procedure", "domain:skills", "topic:sync"],
    )

    report = assemble_task_memory(
        store,
        query="sync skills",
        project_namespace="project:alpha",
        global_namespace="global",
    )

    assert [item["title"] for item in report["procedure_hits"]] == [
        "[[Procedure]] sync skills current"
    ]
    suppressed = {item["title"]: item["reason"] for item in report["suppressed_items"]}
    assert suppressed["[[Procedure]] sync skills old"] == "procedure_status:replaced"
    assert suppressed["[[Procedure]] sync skills stale"] == "procedure_status:stale"


def test_unspecified_legacy_procedure_stays_eligible_with_warning(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    store.store(
        namespace="project:alpha",
        kind="memory",
        title="[[Procedure]] legacy debug",
        content=(
            "record_type: procedure\n"
            "goal: Debug a legacy issue.\n"
            "when_to_use: When no newer procedure exists.\n"
            "steps: inspect logs | rerun command | record fix\n"
        ),
        tags=["kind:procedure", "domain:debugging", "topic:legacy"],
    )

    report = assemble_task_memory(
        store,
        query="legacy debug",
        project_namespace="project:alpha",
        global_namespace="global",
    )

    procedure = report["procedure_hits"][0]["procedure"]
    assert procedure["governance"]["status"] == "unspecified"
    assert procedure["governance"]["eligible"] is True
    assert "missing-procedure-status" in procedure["governance"]["warnings"]


def test_assemble_task_memory_suppresses_unsafe_procedure(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    store.store(
        namespace="project:alpha",
        kind="memory",
        title="[[Procedure]] release unsafe shortcut",
        content=(
            "record_type: procedure\n"
            "procedure_status: unsafe\n"
            "goal: Run release without proof.\n"
            "when_to_use: Never for public release.\n"
            "steps: skip benchmark | tag release\n"
        ),
        tags=["kind:procedure", "domain:release", "topic:cutover"],
    )
    store.store(
        namespace="project:alpha",
        kind="memory",
        title="[[Procedure]] release governed path",
        content=(
            "record_type: procedure\n"
            "procedure_status: validated\n"
            "goal: Run release with proof gates.\n"
            "when_to_use: Before public release tagging.\n"
            "steps: run benchmark | run release contract | tag release\n"
        ),
        tags=["kind:procedure", "domain:release", "topic:cutover"],
    )

    report = assemble_task_memory(
        store,
        query="release cutover",
        project_namespace="project:alpha",
        global_namespace="global",
    )

    assert [item["title"] for item in report["procedure_hits"]] == [
        "[[Procedure]] release governed path"
    ]
    assert "skip benchmark" not in report["summary"]
    assert any(
        item["title"] == "[[Procedure]] release unsafe shortcut"
        and item["reason"] == "procedure_status:unsafe"
        for item in report["suppressed_items"]
    )


@pytest.mark.parametrize(
    "query",
    [
        "release cutover procedure",
        "release cutover checklist",
        "release cutover runbook",
    ],
)
def test_recall_first_triggers_for_procedure_checklist_and_runbook_queries(
    tmp_path: Path,
    query: str,
) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    store.store(
        namespace="project:alpha",
        kind="memory",
        title="[[Procedure]] release cutover governed runbook",
        content=(
            "record_type: procedure\n"
            "procedure_status: validated\n"
            "goal: Run release cutover with governed proof gates.\n"
            "when_to_use: Before publishing a release.\n"
            "when_not_to_use: When release ownership is missing.\n"
            "prerequisites: owner assigned | benchmark report current\n"
            "steps: assign owner | run benchmark | tag release\n"
            "failure_mode: Missing owner leaves cutover work ambiguous.\n"
            "rollback_path: pause release | reopen checklist\n"
        ),
        tags=["kind:procedure", "domain:release", "topic:cutover"],
    )

    result = recall_first(
        store=store,
        query=query,
        project_namespace="project:alpha",
        limit=5,
    )

    assert result["should_search_local"] is True
    assert len(result["procedure_hits"]) == 1
    procedure = result["procedure_hits"][0]["procedure"]
    assert procedure["governance"]["status"] == "validated"
    assert procedure["when_not_to_use"] == "When release ownership is missing."
    assert procedure["rollback_path"] == "pause release | reopen checklist"
    assert "procedure_status: validated" in result["task_memory_summary"]


def test_public_mcp_tool_surface_stays_unchanged_for_procedure_governance() -> None:
    server_path = Path(__file__).resolve().parents[1] / "src" / "agent_mem_bridge" / "server.py"
    tree = ast.parse(server_path.read_text(encoding="utf-8"))

    tool_names = {
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and any(_is_mcp_tool_decorator(decorator) for decorator in node.decorator_list)
    }

    assert tool_names == EXPECTED_PUBLIC_TOOLS


def _is_mcp_tool_decorator(node: ast.expr) -> bool:
    target = node.func if isinstance(node, ast.Call) else node
    return (
        isinstance(target, ast.Attribute)
        and isinstance(target.value, ast.Name)
        and target.value.id == "mcp"
        and target.attr == "tool"
    )
