from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

PROTOCOL_VERSION = "2026-07-28"
PUBLIC_TOOL_COUNT = 17


def _meta() -> dict[str, Any]:
    return {
        "io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION,
        "io.modelcontextprotocol/clientInfo": {"name": "amb-installed-distribution-check", "version": "1"},
        "io.modelcontextprotocol/clientCapabilities": {},
    }


def _request(process: subprocess.Popen[bytes], request: dict[str, Any]) -> dict[str, Any]:
    assert process.stdin is not None
    assert process.stdout is not None
    process.stdin.write(json.dumps(request, separators=(",", ":")).encode("utf-8") + b"\n")
    process.stdin.flush()
    line = process.stdout.readline()
    if not line:
        stderr = b""
        if process.stderr is not None:
            stderr = process.stderr.read()
        raise AssertionError(f"installed stdio server closed before response: {stderr.decode(errors='replace')}")
    response = json.loads(line)
    assert response["jsonrpc"] == "2.0"
    assert response["id"] == request["id"]
    return response


def _call(process: subprocess.Popen[bytes], request_id: int, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return _request(
        process,
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments, "_meta": _meta()},
        },
    )


def _start(server_python: Path, runtime_dir: Path) -> subprocess.Popen[bytes]:
    env = {
        **os.environ,
        "AGENT_MEMORY_BRIDGE_HOME": str(runtime_dir),
        "AGENT_MEMORY_BRIDGE_DB_PATH": str(runtime_dir / "bridge.db"),
        "AGENT_MEMORY_BRIDGE_LOG_DIR": str(runtime_dir / "logs"),
    }
    return subprocess.Popen(
        [str(server_python), "-m", "agent_mem_bridge"],
        cwd=runtime_dir,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _stop(process: subprocess.Popen[bytes]) -> None:
    if process.stdin is not None:
        process.stdin.close()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.terminate()
        process.wait(timeout=10)
    assert process.returncode == 0, f"installed stdio server exited with {process.returncode}"


def _exercise(server_python: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="amb-installed-dist-") as raw_runtime:
        runtime_dir = Path(raw_runtime)
        namespace = "project:installed-distribution"
        decision = "record_type: decision\nclaim: Merge only after CI is green.\nreason: Protect the release branch."

        first = _start(server_python, runtime_dir)
        discover = _request(
            first,
            {"jsonrpc": "2.0", "id": 1, "method": "server/discover", "params": {"_meta": _meta()}},
        )
        assert discover["result"]["supportedVersions"] == [PROTOCOL_VERSION]

        tools = _request(
            first,
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {"_meta": _meta()}},
        )
        assert len(tools["result"]["tools"]) == PUBLIC_TOOL_COUNT
        assert {tool["name"] for tool in tools["result"]["tools"]} >= {"store", "recall", "stats"}

        stored = _call(
            first,
            3,
            "store",
            {"namespace": namespace, "kind": "memory", "title": "Release decision", "content": decision},
        )
        stored_payload = stored["result"]["structuredContent"]
        assert stored_payload.get("id") or stored_payload.get("memory_id")
        _stop(first)

        second = _start(server_python, runtime_dir)
        recalled = _call(
            second,
            4,
            "recall",
            {"namespace": namespace, "kind": "memory", "query": "What is required before merge?", "limit": 5},
        )
        items = recalled["result"]["structuredContent"]["items"]
        assert any("Merge only after CI is green" in item.get("content", "") for item in items)
        _stop(second)


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify an installed AMB distribution over real stdio MCP.")
    parser.add_argument("--server-python", type=Path, required=True)
    parser.add_argument("--expected-version", required=True)
    args = parser.parse_args()

    completed = subprocess.run(
        [
            str(args.server_python),
            "-c",
            "import importlib.metadata as m; print(m.version('agent-memory-bridge'))",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    actual = completed.stdout.strip()
    assert actual == args.expected_version, f"installed version mismatch: expected {args.expected_version}, got {actual}"

    _exercise(args.server_python)
    print(f"installed distribution stdio acceptance: PASS ({actual})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
