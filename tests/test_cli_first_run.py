from __future__ import annotations

import json
import os
import shlex
import sqlite3
from pathlib import Path, PurePosixPath, PureWindowsPath

from agent_mem_bridge.cli import main
from agent_mem_bridge.first_run import (
    _render_python_module_command,
    build_first_run_report,
    render_first_run_markdown,
)
from agent_mem_bridge.onboarding import TOOL_NAMES
from agent_mem_bridge.release_contract import load_server_tool_names
from agent_mem_bridge.storage import MemoryStore

ROOT = Path(__file__).resolve().parents[1]
NAMESPACE = "project:first-run-test"
EXPECTED_PUBLIC_TOOLS = TOOL_NAMES


def test_first_run_report_renders_install_verify_and_task_brief_without_mutation(tmp_path: Path) -> None:
    store = _seed_store(tmp_path)
    before_counts = _table_counts(db_path=store.db_path)
    before_stats = store.stats(NAMESPACE)
    python_path = Path("test-fixtures") / "python"

    report = build_first_run_report(
        store,
        client="opencode",
        namespace=NAMESPACE,
        query="release handoff",
        python_path=python_path,
        cwd=Path("test-project"),
        bridge_home=Path("bridge-home"),
        config_path=Path("config.toml"),
        example=False,
    )
    after_counts = _table_counts(db_path=store.db_path)
    after_stats = store.stats(NAMESPACE)

    assert before_counts == after_counts
    assert before_stats == after_stats
    assert report["schema"] == "memory.first_run.v1"
    assert "Linux systems use `python3`" in report["python_launcher_note"]
    assert report["boundary"]["mutation_allowed"] is False
    assert report["boundary"]["public_mcp_surface_change"] is False
    assert report["boundary"]["amh_required"] is False
    assert report["client_config"]["format"] == "json"
    assert report["first_task_brief"]["schema"] == "memory.task_brief.v1"
    assert report["first_task_brief"]["public_mcp_surface_change"] is False
    assert report["install"]["baseline"][0] == "python -m venv .amb-venv"
    assert ".absolute()" in report["install"]["baseline"][1]
    assert ".resolve()" not in report["install"]["baseline"][1]
    assert "archive/refs/tags/v0.23.1.zip" in report["install"]["baseline"][2]
    assert "<venv-python> -m pip install" in report["install"]["baseline"][2]
    assert report["install"]["github_install"] == report["install"]["baseline"]
    assert report["install"]["editable_install"][-1] == "<venv-python> -m pip install -e ."
    assert report["verify"] == [
        _render_python_module_command(str(python_path), "doctor", platform=os.name),
        _render_python_module_command(str(python_path), "verify", platform=os.name),
    ]
    assert report["install"]["smoke_test"] == report["verify"][1]
    assert report["install"]["optional_uv_smoke_test"].startswith("uvx --from git+")
    assert "Agent-Memory-Bridge@v0.23.1" in report["install"]["optional_uv_smoke_test"]

    markdown = render_first_run_markdown(report)
    assert "## Install" in markdown
    assert "Linux systems use `python3`" in markdown
    assert "archive/refs/tags/v0.23.1.zip" in markdown
    assert markdown.index("python -m venv .amb-venv") < markdown.index(".absolute()")
    assert markdown.index(".absolute()") < markdown.index("<venv-python> -m pip install")
    assert "Editable install:" not in markdown
    assert "Optional `uvx` shortcut (requires `uv`)" in markdown
    assert "## Verify" in markdown
    assert "Inspect the client's MCP status/tool visibility" in markdown
    assert "## First Task Brief" in markdown
    assert "write_mode: `manual_copy_only`" in markdown

    posix_python = str(PurePosixPath("fixture path") / "python")
    posix_command = _render_python_module_command(posix_python, "verify", platform="posix")
    assert shlex.split(posix_command) == [posix_python, "-m", "agent_mem_bridge", "verify"]

    windows_python = str(PureWindowsPath("fixture path") / "python.exe")
    assert (
        _render_python_module_command(
            windows_python,
            "verify",
            platform="nt",
        )
        == f"& '{windows_python}' -m agent_mem_bridge verify"
    )


def test_first_run_cli_renders_placeholder_safe_json(tmp_path: Path, monkeypatch, capsys) -> None:
    store = _seed_store(tmp_path)
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_DB_PATH", str(store.db_path))
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_LOG_DIR", str(tmp_path / "logs"))

    exit_code = main(
        [
            "first-run",
            "--client",
            "hermes",
            "--namespace",
            NAMESPACE,
            "--query",
            "release handoff",
            "--example",
            "--format",
            "json",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["client"] == "hermes"
    assert payload["client_config"]["format"] == "yaml"
    assert payload["boundary"]["client_config_write_mode"] == "manual_copy_only"
    assert payload["boundary"]["amh_required"] is False
    assert "/path/to/agent-memory-bridge" in payload["client_config"]["content"]
    assert ".amb-venv/bin/python" in payload["client_config"]["content"]
    assert "C:\\Users" not in payload["client_config"]["content"]
    assert "Linux systems use `python3`" in payload["python_launcher_note"]
    assert payload["install"]["baseline"][0] == "python -m venv .amb-venv"
    assert ".absolute()" in payload["install"]["baseline"][1]
    assert "archive/refs/tags/v0.23.1.zip" in payload["install"]["baseline"][2]


def test_first_run_stays_cli_only_not_mcp_tool() -> None:
    tool_names = load_server_tool_names(ROOT / "src" / "agent_mem_bridge" / "server.py")

    assert tool_names == EXPECTED_PUBLIC_TOOLS
    assert "first_run" not in tool_names
    assert "first-run" not in tool_names


def _seed_store(tmp_path: Path) -> MemoryStore:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    store.store(
        namespace=NAMESPACE,
        kind="memory",
        title="[[Procedure]] release handoff path",
        content=(
            "record_type: procedure\n"
            "goal: Run the release handoff path.\n"
            "steps: assign owner | confirm queue | tag release\n"
        ),
        tags=["kind:procedure", "domain:release", "topic:handoff"],
    )
    return store


def _table_counts(*, db_path: Path) -> dict[str, int]:
    with sqlite3.connect(db_path) as conn:
        table_names = [
            row[0]
            for row in conn.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table'
                  AND name NOT LIKE 'sqlite_%'
                ORDER BY name
                """
            )
        ]
        return {name: int(conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]) for name in table_names}
