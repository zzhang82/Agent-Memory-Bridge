import asyncio
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def _exercise_server(tmp_path: Path) -> None:
    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "agent_mem_bridge"],
        cwd=str(tmp_path.parent),
        env={
            **os.environ,
            "AGENT_MEMORY_BRIDGE_DB_PATH": str(tmp_path / "bridge.db"),
            "AGENT_MEMORY_BRIDGE_LOG_DIR": str(tmp_path / "logs"),
        },
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools_response = await session.list_tools()
            tool_names = {tool.name for tool in tools_response.tools}
            assert tool_names == {"store", "recall"}

            first = await session.call_tool(
                "store",
                arguments={
                    "namespace": "bridge",
                    "content": "Shared memory survives model context loss.",
                    "kind": "memory",
                    "tags": ["project:bridge", "agent:cole"],
                    "session_id": "session-a",
                    "actor": "cole",
                    "correlation_id": "handoff-1",
                    "source_app": "codex",
                },
            )
            assert first.structuredContent["stored"] is True

            duplicate = await session.call_tool(
                "store",
                arguments={
                    "namespace": "bridge",
                    "content": "Shared memory survives model context loss.",
                    "kind": "memory",
                    "tags": ["project:bridge", "agent:cole"],
                    "session_id": "session-a",
                    "actor": "cole",
                    "correlation_id": "handoff-1",
                    "source_app": "codex",
                },
            )
            assert duplicate.structuredContent["stored"] is False

            signal = await session.call_tool(
                "store",
                arguments={
                    "namespace": "bridge",
                    "content": "Reviewer ready to take over.",
                    "kind": "signal",
                    "tags": ["handoff", "review"],
                    "session_id": "session-a",
                    "actor": "cole",
                    "correlation_id": "handoff-1",
                    "source_app": "codex",
                },
            )

            recall = await session.call_tool(
                "recall",
                arguments={
                    "namespace": "bridge",
                    "query": "context loss",
                    "limit": 5,
                    "kind": "memory",
                    "actor": "cole",
                },
            )
            assert recall.structuredContent["count"] == 1
            assert recall.structuredContent["items"][0]["source_app"] == "codex"

            polling = await session.call_tool(
                "recall",
                arguments={
                    "namespace": "bridge",
                    "kind": "signal",
                    "since": duplicate.structuredContent["id"],
                    "limit": 5,
                },
            )
            assert polling.structuredContent["count"] == 1
            assert polling.structuredContent["items"][0]["id"] == signal.structuredContent["id"]


def test_stdio_server_round_trip(tmp_path: Path) -> None:
    asyncio.run(_exercise_server(tmp_path))

