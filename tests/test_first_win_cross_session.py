from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from agent_mem_bridge.onboarding import TOOL_NAMES

ROOT = Path(__file__).resolve().parents[1]
NAMESPACE = "project:checkout-app"
OTHER_NAMESPACE = "project:other-app"
CLAIM = "Merge pull requests only after CI is green on the target branch."
REASON = "Broken main blocked two releases this month."
DECISION_CONTENT = f"record_type: decision\nclaim: {CLAIM}\nreason: {REASON}\nscope: {NAMESPACE}\nconfidence: observed"
QUERY = "What is required before we merge a pull request?"
SESSION_A_ID = "first-win-session-a"


def test_first_win_recalls_decision_from_fresh_stdio_process(tmp_path: Path) -> None:
    asyncio.run(_exercise_first_win(tmp_path))


def test_first_win_does_not_leak_across_home_or_namespace(tmp_path: Path) -> None:
    asyncio.run(_exercise_negative_controls(tmp_path))


async def _exercise_first_win(tmp_path: Path) -> None:
    home = tmp_path / "amb-home"
    home.mkdir()
    pid_a_file = tmp_path / "pid-a.txt"
    pid_b_file = tmp_path / "pid-b.txt"
    hook_dir = _pid_hook(tmp_path)
    durable_db = home / "bridge.db"

    stored = await _run_stdio_session(
        _server_params(home, hook_dir, pid_a_file),
        _store_decision,
    )
    process_a_pid = _read_pid(pid_a_file)
    _assert_process_dead(process_a_pid)
    assert durable_db.is_file()
    memory_id = str(stored["id"])
    del stored

    recalled = await _run_stdio_session(
        _server_params(home, hook_dir, pid_b_file),
        lambda session: _recall_decision(session, namespace=NAMESPACE, query=QUERY),
    )
    process_b_pid = _read_pid(pid_b_file)
    _assert_process_dead(process_b_pid)
    assert process_b_pid != process_a_pid

    item = next(entry for entry in recalled["items"] if entry["id"] == memory_id)
    assert item["content"] == DECISION_CONTENT
    assert CLAIM in item["content"]
    assert REASON in item["content"]
    assert item["record_type"] == "decision"
    assert item["namespace"] == NAMESPACE
    assert item["session_id"] == SESSION_A_ID
    assert recalled["recall_receipt"]["token"]


async def _exercise_negative_controls(tmp_path: Path) -> None:
    home = tmp_path / "amb-home"
    other_home = tmp_path / "other-home"
    home.mkdir()
    other_home.mkdir()
    hook_dir = _pid_hook(tmp_path)
    pid_store = tmp_path / "pid-store.txt"
    pid_wrong_ns = tmp_path / "pid-wrong-ns.txt"
    pid_wrong_home = tmp_path / "pid-wrong-home.txt"

    stored = await _run_stdio_session(
        _server_params(home, hook_dir, pid_store),
        _store_decision,
    )
    _assert_process_dead(_read_pid(pid_store))
    memory_id = str(stored["id"])

    wrong_namespace = await _run_stdio_session(
        _server_params(home, hook_dir, pid_wrong_ns),
        lambda session: _recall_decision(session, namespace=OTHER_NAMESPACE, query=QUERY),
    )
    _assert_process_dead(_read_pid(pid_wrong_ns))
    assert all(item["id"] != memory_id for item in wrong_namespace["items"])
    assert CLAIM not in str(wrong_namespace["items"])

    wrong_home = await _run_stdio_session(
        _server_params(other_home, hook_dir, pid_wrong_home),
        lambda session: _recall_decision(session, namespace=NAMESPACE, query=QUERY),
    )
    _assert_process_dead(_read_pid(pid_wrong_home))
    assert wrong_home["items"] == []
    assert not (other_home / "bridge.db").is_file() or wrong_home["count"] == 0


async def _store_decision(session: ClientSession) -> dict[str, Any]:
    tools = await session.list_tools()
    assert {tool.name for tool in tools.tools} == TOOL_NAMES
    assert len(tools.tools) == 17
    stored = _payload(
        await session.call_tool(
            "store",
            arguments={
                "namespace": NAMESPACE,
                "kind": "memory",
                "title": "CI-green merge gate",
                "content": DECISION_CONTENT,
                "session_id": SESSION_A_ID,
            },
        )
    )
    assert stored["stored"] is True
    assert stored["id"]
    return stored


async def _recall_decision(session: ClientSession, *, namespace: str, query: str) -> dict[str, Any]:
    recalled = _payload(
        await session.call_tool(
            "recall",
            arguments={"namespace": namespace, "query": query, "kind": "memory", "limit": 5},
        )
    )
    assert isinstance(recalled.get("items"), list)
    return recalled


async def _run_stdio_session(
    params: StdioServerParameters,
    operation: Callable[[ClientSession], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            payload = await operation(session)
    return payload


def _server_params(home: Path, hook_dir: Path, pid_file: Path) -> StdioServerParameters:
    env = {key: value for key, value in os.environ.items() if not key.startswith("AGENT_MEMORY_BRIDGE_")}
    inherited_pythonpath = env.get("PYTHONPATH")
    env.update(
        {
            "AGENT_MEMORY_BRIDGE_HOME": str(home),
            "FIRST_WIN_PID_FILE": str(pid_file),
            "PYTHONPATH": str(hook_dir)
            if not inherited_pythonpath
            else str(hook_dir) + os.pathsep + inherited_pythonpath,
        }
    )
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "agent_mem_bridge"],
        cwd=str(ROOT),
        env=env,
    )


def _pid_hook(tmp_path: Path) -> Path:
    hook_dir = tmp_path / "pidhook"
    hook_dir.mkdir(exist_ok=True)
    (hook_dir / "sitecustomize.py").write_text(
        "import os\nfrom pathlib import Path\n\nPath(os.environ['FIRST_WIN_PID_FILE']).write_text(str(os.getpid()))\n",
        encoding="utf-8",
    )
    return hook_dir


def _read_pid(path: Path) -> int:
    pid = int(path.read_text(encoding="utf-8").strip())
    assert pid > 0
    return pid


def _assert_process_dead(pid: int) -> None:
    try:
        os.kill(pid, 0)
    except OSError:
        return
    raise AssertionError(f"stdio process {pid} was still running")


def _payload(response: Any) -> dict[str, Any]:
    payload = getattr(response, "structured_content", None) or getattr(response, "structuredContent", None) or {}
    assert isinstance(payload, dict)
    return payload
