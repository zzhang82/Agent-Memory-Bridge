from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_mem_bridge.cli import main
from agent_mem_bridge.evidence_inspect import (
    MEMORY_INSPECT_SCHEMA,
    build_memory_inspect_report,
    render_memory_inspect_markdown,
)
from agent_mem_bridge.onboarding import TOOL_NAMES
from agent_mem_bridge.release_contract import load_server_tool_names
from agent_mem_bridge.storage import MemoryStore

ROOT = Path(__file__).resolve().parents[1]


def test_inspect_real_governed_projection_is_deterministic_relevant_and_read_only(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    namespace = "project:inspect"
    expired_until = (datetime.now(UTC) - timedelta(days=1)).isoformat()

    predecessor = store.store(
        namespace=namespace,
        kind="memory",
        title="[[Procedure]] release predecessor path",
        content=(
            "record_type: procedure\nprocedure_status: validated\ngoal: Use predecessor release path.\n"
            "steps: old release check\n"
        ),
        tags=["kind:procedure", "domain:release", "topic:cutover"],
    )
    current = store.store(
        namespace=namespace,
        kind="memory",
        title="[[Procedure]] release governed path",
        content=(
            "record_type: procedure\nprocedure_status: validated\ngoal: Run release with proof gates.\n"
            "steps: run checks | tag release\n"
            f"supersedes: {predecessor['id']}\n"
        ),
        tags=["kind:procedure", "domain:release", "topic:cutover"],
    )
    stale = store.store(
        namespace=namespace,
        kind="memory",
        title="[[Procedure]] release expired path",
        content=(
            "record_type: procedure\nprocedure_status: validated\ngoal: Run old release path.\n"
            f"valid_until: {expired_until}\n"
        ),
        tags=["kind:procedure", "domain:release", "topic:cutover"],
    )
    unsafe = store.store(
        namespace=namespace,
        kind="memory",
        title="[[Procedure]] release unsafe shortcut",
        content=("record_type: procedure\nprocedure_status: unsafe\ngoal: Skip release proof.\nsteps: tag release\n"),
        tags=["kind:procedure", "domain:release", "topic:cutover"],
    )
    dependent = store.store(
        namespace=namespace,
        kind="memory",
        title="[[Procedure]] release dependency blocked path",
        content=(
            "record_type: procedure\nprocedure_status: validated\ngoal: Run dependency path.\n"
            f"depends_on: {unsafe['id']}\n"
        ),
        tags=["kind:procedure", "domain:release", "topic:cutover"],
    )
    unrelated = store.store(
        namespace=namespace,
        kind="memory",
        title="[[Procedure]] garden watering notes",
        content="record_type: procedure\nprocedure_status: validated\ngoal: Water garden plants.\n",
        tags=["kind:procedure", "domain:garden", "topic:plants"],
    )

    with store._connect() as conn:
        before_rows = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    report = build_memory_inspect_report(store, namespace=namespace, query="release cutover proof", technical=True)
    repeated = build_memory_inspect_report(store, namespace=namespace, query="release cutover proof", technical=True)
    with store._connect() as conn:
        after_rows = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]

    assert report == repeated
    assert report["schema"] == MEMORY_INSPECT_SCHEMA
    assert report["mutation_boundary"] == "read_only_with_respect_to_user_memory_state_and_configuration"
    assert before_rows == after_rows
    assert current["id"] in {item["memory_id"] for item in report["selected"]}
    assert predecessor["id"] not in {item["memory_id"] for item in report["selected"]}
    assert {
        predecessor["id"],
        stale["id"],
        unsafe["id"],
        dependent["id"],
    }.issubset({item["memory_id"] for item in report["excluded"]})
    assert unrelated["id"] not in {item["memory_id"] for item in report["excluded"]}
    assert unsafe["id"] in {item["memory_id"] for item in report["needs_review"]}
    assert any("Superseded by a newer memory." in item["why"] for item in report["excluded"])
    assert any("Out of date for this task." in item["why"] for item in report["excluded"])
    assert any("Marked unsafe to use." in item["why"] for item in report["excluded"])
    assert any("Depends on evidence that is no longer eligible." in item["why"] for item in report["excluded"])
    assert any("It belongs to this project namespace." in item["why"] for item in report["selected"])
    encoded = json.dumps(report, sort_keys=True)
    assert "recall_token" not in encoded
    assert "feedback_token" not in encoded
    assert "applied" in report["explanation"]["causal_boundary"].lower()
    assert "caused" in report["explanation"]["causal_boundary"]
    markdown = render_memory_inspect_markdown(report)
    assert "# AMB Inspect" in markdown
    assert "What AMB left out" in markdown
    assert "Needs review" in markdown


def test_inspect_cli_supports_human_and_json_output_without_new_mcp_tool(tmp_path: Path, monkeypatch, capsys) -> None:
    db_path = tmp_path / "bridge.db"
    log_dir = tmp_path / "logs"
    store = MemoryStore(db_path, log_dir=log_dir)
    store.store(
        namespace="project:test",
        kind="memory",
        title="[[Procedure]] submission checks",
        content="record_type: procedure\nprocedure_status: validated\ngoal: Check submission.\n",
        tags=["kind:procedure", "domain:submission", "topic:changes"],
    )
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_DB_PATH", str(db_path))
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_LOG_DIR", str(log_dir))

    assert main(["inspect", "--namespace", "project:test", "--query", "submission changes"]) == 0
    human = capsys.readouterr().out
    assert "# AMB Inspect" in human
    assert "What AMB remembered" in human
    assert (
        main(
            [
                "inspect",
                "--namespace",
                "project:test",
                "--query",
                "submission changes",
                "--format",
                "json",
                "--technical",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == MEMORY_INSPECT_SCHEMA
    assert payload["technical_details"]["enabled"] is True
    assert load_server_tool_names(ROOT / "src" / "agent_mem_bridge" / "server.py") == TOOL_NAMES
    assert len(TOOL_NAMES) == 17


def test_inspect_help_exposes_only_narrow_public_controls(capsys) -> None:
    with pytest.raises(SystemExit) as exited:
        main(["inspect", "--help"])
    assert exited.value.code == 0
    help_text = capsys.readouterr().out
    assert "--namespace" in help_text
    assert "--query" in help_text
    assert "--format" in help_text
    assert "--technical" in help_text
    assert "--apply" not in help_text
    assert "--html" not in help_text
