from __future__ import annotations

import argparse
import ast
import asyncio
import importlib.metadata
import json
import os
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import Implementation


def _payload(result: Any) -> dict[str, Any]:
    payload = getattr(result, "structured_content", None) or getattr(result, "structuredContent", None)
    if not isinstance(payload, dict):
        raise RuntimeError("legacy client received no structured tool result")
    return payload


def _canonical_tool_order(project_root: Path) -> tuple[str, ...]:
    source_path = project_root / "src" / "agent_mem_bridge" / "mcp_boundary.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "PUBLIC_TOOL_ORDER" for target in node.targets
        ):
            value = ast.literal_eval(node.value)
            if isinstance(value, tuple) and all(isinstance(item, str) for item in value):
                return value
    raise RuntimeError("PUBLIC_TOOL_ORDER was not found in mcp_boundary.py")


async def run_legacy_compat(server_python: Path, project_root: Path, runtime_dir: Path) -> dict[str, Any]:
    runtime_dir.mkdir(parents=True, exist_ok=True)
    db_path = runtime_dir / "legacy-compat.db"
    server = StdioServerParameters(
        command=str(server_python),
        args=["-m", "agent_mem_bridge"],
        cwd=str(project_root),
        env={
            **os.environ,
            "AGENT_MEMORY_BRIDGE_HOME": str(runtime_dir),
            "AGENT_MEMORY_BRIDGE_DB_PATH": str(db_path),
            "AGENT_MEMORY_BRIDGE_LOG_DIR": str(runtime_dir / "logs"),
        },
    )
    client_info = Implementation(name="amb-python-sdk-1x-proof", version="1.28.1")
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write, client_info=client_info) as session:
            initialized = await session.initialize()
            listed = await session.list_tools()
            tool_names = tuple(tool.name for tool in listed.tools)
            stored = await session.call_tool(
                "store",
                arguments={
                    "namespace": "project:python-sdk-1x-proof",
                    "content": "Python MCP SDK 1.28.1 legacy interoperability proof.",
                    "kind": "memory",
                },
            )
            stored_payload = _payload(stored)
            recalled = await session.call_tool(
                "recall",
                arguments={
                    "namespace": "project:python-sdk-1x-proof",
                    "query": "legacy interoperability proof",
                    "kind": "memory",
                    "limit": 5,
                },
            )
            recalled_payload = _payload(recalled)
            begun = await session.call_tool(
                "begin_run",
                arguments={
                    "workspace_key": "project:python-sdk-1x-proof",
                    "goal": "Prove legacy SDK access to the episode ledger.",
                    "idempotency_key": "begin:python-sdk-1x-proof",
                },
            )
            begun_payload = _payload(begun)
            run_event = await session.call_tool(
                "record_run_event",
                arguments={
                    "workspace_key": "project:python-sdk-1x-proof",
                    "run_id": begun_payload["run_id"],
                    "work_item_id": begun_payload["root_work_item_id"],
                    "event_type": "checkpoint",
                    "summary": "Legacy SDK episode checkpoint passed.",
                    "idempotency_key": "event:python-sdk-1x-proof",
                },
            )
            event_payload = _payload(run_event)
            restored = await session.call_tool(
                "get_run",
                arguments={
                    "workspace_key": "project:python-sdk-1x-proof",
                    "run_id": begun_payload["run_id"],
                },
            )
            restored_payload = _payload(restored)
            terminal_event = await session.call_tool(
                "record_run_event",
                arguments={
                    "workspace_key": "project:python-sdk-1x-proof",
                    "run_id": begun_payload["run_id"],
                    "work_item_id": begun_payload["root_work_item_id"],
                    "event_type": "work_item_completed",
                    "summary": "Legacy SDK root work item completed.",
                    "idempotency_key": "event:python-sdk-1x-proof:completed",
                },
            )
            terminal_event_payload = _payload(terminal_event)
            completed = await session.call_tool(
                "complete_run",
                arguments={
                    "workspace_key": "project:python-sdk-1x-proof",
                    "run_id": begun_payload["run_id"],
                    "outcome": "verified_success",
                    "evaluator_type": "deterministic_verifier",
                    "evidence": [{"kind": "python-sdk-1x-proof", "passed": True}],
                    "idempotency_key": "outcome:python-sdk-1x-proof",
                },
            )
            completed_payload = _payload(completed)

    sdk_version = importlib.metadata.version("mcp")
    expected_tools = _canonical_tool_order(project_root)
    protocol_version = getattr(initialized, "protocol_version", None) or getattr(initialized, "protocolVersion", None)
    checks = {
        "sdk_version": sdk_version == "1.28.1",
        "protocol_version": protocol_version == "2025-11-25",
        "tool_surface": tool_names == expected_tools,
        "store": bool(stored_payload.get("stored")),
        "recall": int(recalled_payload.get("count", 0)) >= 1,
        "begin_run": str(begun_payload.get("run_id", "")).startswith("run_"),
        "record_run_event": event_payload.get("sequence") == 1,
        "get_run": restored_payload.get("latest_sequence") == 1,
        "work_item_completed": terminal_event_payload.get("sequence") == 2,
        "complete_run": completed_payload.get("outcome") == "verified_success",
    }
    return {
        "ok": all(checks.values()),
        "client": f"mcp=={sdk_version}",
        "protocol_version": protocol_version,
        "tool_count": len(tool_names),
        "tool_names": list(tool_names),
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Prove AMB interoperability with the real Python MCP 1.28.1 client.")
    parser.add_argument("--server-python", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--runtime-dir", type=Path, required=True)
    args = parser.parse_args()
    report = asyncio.run(
        run_legacy_compat(
            args.server_python.absolute(),
            args.project_root.resolve(),
            args.runtime_dir.resolve(),
        )
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
