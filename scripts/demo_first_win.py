"""Print the local first-win loop: store in one stdio process, recall from the next.

The transcript is the public demo. It uses the same decision, namespace, and
query as tests/test_first_win_cross_session.py. Paths, process ids, and recall
receipts stay off stdout.
"""

from __future__ import annotations

import asyncio
import ctypes
import os
import sys
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

NAMESPACE = "project:checkout-app"
TITLE = "CI-green merge gate"
CLAIM = "Merge pull requests only after CI is green on the target branch."
REASON = "Broken main blocked two releases this month."
QUERY = "What is required before we merge a pull request?"
SESSION_A_ID = "first-win-session-a"
DECISION_CONTENT = f"record_type: decision\nclaim: {CLAIM}\nreason: {REASON}\nscope: {NAMESPACE}\nconfidence: observed"


def main() -> None:
    asyncio.run(_run())


async def _run() -> None:
    with tempfile.TemporaryDirectory(prefix="amb-first-win-") as temp_name:
        root = Path(temp_name)
        home = root / "amb-home"
        home.mkdir()
        hook_dir = _pid_hook(root)
        pid_a = root / "pid-a.txt"
        pid_b = root / "pid-b.txt"

        stored = await _run_stdio_session(_server_params(home, hook_dir, pid_a), _store_decision)
        process_a = _read_pid(pid_a)
        _assert_process_dead(process_a)

        recalled = await _run_stdio_session(
            _server_params(home, hook_dir, pid_b),
            lambda session: _recall_decision(session),
        )
        process_b = _read_pid(pid_b)
        _assert_process_dead(process_b)
        if process_b == process_a:
            raise SystemExit("recall reused the store process")

        item = next(entry for entry in recalled["items"] if entry["id"] == stored["id"])
        if item["content"] != DECISION_CONTENT:
            raise SystemExit("recalled content did not match the stored decision")
        if item["namespace"] != NAMESPACE or item["record_type"] != "decision":
            raise SystemExit("recalled record was not the stored project decision")

    print("# Session A: store one explicit decision")
    print("command: python -m agent_mem_bridge")
    print("home: $AMB_HOME")
    print(f"namespace: {NAMESPACE}")
    print(f"title: {TITLE}")
    print(f"claim: {CLAIM}")
    print(f"reason: {REASON}")
    print("stored: true")
    print("stdio process: exited")
    print()
    print("# Session B: a second stdio process, same home, recall at start")
    print("command: python -m agent_mem_bridge")
    print("home: $AMB_HOME")
    print(f"namespace: {NAMESPACE}")
    print(f"query: {QUERY}")
    print("kind: memory")
    print(f"claim: {CLAIM}")
    print(f"reason: {REASON}")
    print("record_type: decision")
    print("same_record: true")
    print("fresh_process: true")
    print("stdio process: exited")


async def _store_decision(session: ClientSession) -> dict[str, Any]:
    stored = _payload(
        await session.call_tool(
            "store",
            arguments={
                "namespace": NAMESPACE,
                "kind": "memory",
                "title": TITLE,
                "content": DECISION_CONTENT,
                "session_id": SESSION_A_ID,
            },
        )
    )
    if stored.get("stored") is not True or not stored.get("id"):
        raise SystemExit("store did not return a durable id")
    return stored


async def _recall_decision(session: ClientSession) -> dict[str, Any]:
    recalled = _payload(
        await session.call_tool(
            "recall",
            arguments={"namespace": NAMESPACE, "query": QUERY, "kind": "memory", "limit": 5},
        )
    )
    if not isinstance(recalled.get("items"), list):
        raise SystemExit("recall did not return items")
    return recalled


async def _run_stdio_session(
    params: StdioServerParameters,
    operation: Callable[[ClientSession], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await operation(session)


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
        cwd=str(Path(__file__).resolve().parents[1]),
        env=env,
    )


def _pid_hook(root: Path) -> Path:
    hook_dir = root / "pidhook"
    hook_dir.mkdir()
    (hook_dir / "sitecustomize.py").write_text(
        "import os\nfrom pathlib import Path\n\nPath(os.environ['FIRST_WIN_PID_FILE']).write_text(str(os.getpid()))\n",
        encoding="utf-8",
    )
    return hook_dir


def _read_pid(path: Path) -> int:
    pid = int(path.read_text(encoding="utf-8").strip())
    if pid <= 0:
        raise SystemExit("stdio process did not record a pid")
    return pid


def _process_exists(pid: int) -> bool:
    """Non-destructive liveness check. Never sends a fatal signal.

    On Windows, os.kill(pid, 0) goes through TerminateProcess. A still-open
    process handle is also not proof the process is running. WaitForSingleObject
    with a zero timeout is the check: WAIT_TIMEOUT means running.
    """

    if pid <= 0:
        return False
    if os.name == "nt":
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        synchronize = 0x00100000
        wait_timeout = 0x00000102
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_bool, ctypes.c_uint32]
        kernel32.WaitForSingleObject.restype = ctypes.c_uint32
        kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel32.OpenProcess(synchronize, False, pid)
        if not handle:
            return False
        try:
            return kernel32.WaitForSingleObject(handle, 0) == wait_timeout
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _assert_process_dead(pid: int) -> None:
    if _process_exists(pid):
        raise SystemExit(f"stdio process {pid} was still running")


def _payload(response: Any) -> dict[str, Any]:
    payload = getattr(response, "structured_content", None) or getattr(response, "structuredContent", None) or {}
    if not isinstance(payload, dict):
        raise SystemExit("tool response was not a mapping")
    return payload


if __name__ == "__main__":
    main()
