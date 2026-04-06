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
            assert tool_names == {
                "store",
                "recall",
                "browse",
                "stats",
                "forget",
                "claim_signal",
                "ack_signal",
                "promote",
                "export",
            }

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
                    "ttl_seconds": 300,
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
            assert polling.structuredContent["items"][0]["signal_status"] == "pending"

            claimed = await session.call_tool(
                "claim_signal",
                arguments={
                    "namespace": "bridge",
                    "consumer": "reviewer-a",
                    "lease_seconds": 120,
                    "signal_id": signal.structuredContent["id"],
                },
            )
            assert claimed.structuredContent["claimed"] is True
            assert claimed.structuredContent["item"]["signal_status"] == "claimed"

            acked = await session.call_tool(
                "ack_signal",
                arguments={
                    "id": signal.structuredContent["id"],
                    "consumer": "reviewer-a",
                },
            )
            assert acked.structuredContent["acked"] is True
            assert acked.structuredContent["item"]["signal_status"] == "acked"

            stats = await session.call_tool(
                "stats",
                arguments={"namespace": "bridge"},
            )
            assert stats.structuredContent["total_count"] == 2
            assert stats.structuredContent["kind_counts"]["memory"] == 1
            assert stats.structuredContent["kind_counts"]["signal"] == 1
            assert stats.structuredContent["signal_status_counts"]["acked"] == 1

            browse = await session.call_tool(
                "browse",
                arguments={"namespace": "bridge", "kind": "signal", "signal_status": "acked", "limit": 5},
            )
            assert browse.structuredContent["count"] == 1
            assert browse.structuredContent["items"][0]["id"] == signal.structuredContent["id"]

            forgotten = await session.call_tool(
                "forget",
                arguments={"id": first.structuredContent["id"]},
            )
            assert forgotten.structuredContent["deleted"] is True

            after_forget = await session.call_tool(
                "recall",
                arguments={
                    "namespace": "bridge",
                    "query": "context loss",
                    "limit": 5,
                    "kind": "memory",
                },
            )
            assert after_forget.structuredContent["count"] == 0

            learn = await session.call_tool(
                "store",
                arguments={
                    "namespace": "bridge",
                    "content": "record_type: learn\nclaim: Use one shared DB.\nscope: global\nconfidence: observed",
                    "kind": "memory",
                    "tags": ["kind:learn", "domain:memory-bridge"],
                    "title": "[[Learn]] Use one shared DB.",
                },
            )

            promoted = await session.call_tool(
                "promote",
                arguments={"id": learn.structuredContent["id"], "to_kind": "gotcha"},
            )
            assert promoted.structuredContent["changed"] is True
            assert promoted.structuredContent["record_type"] == "gotcha"
            assert "kind:gotcha" in promoted.structuredContent["item"]["tags"]
            assert "record_type: gotcha" in promoted.structuredContent["item"]["content"]

            exported = await session.call_tool(
                "export",
                arguments={"namespace": "bridge", "format": "markdown", "kind": "signal", "signal_status": "acked", "limit": 10},
            )
            assert exported.structuredContent["count"] == 1
            assert "# Memory Export: bridge" in exported.structuredContent["content"]


def test_stdio_server_round_trip(tmp_path: Path) -> None:
    asyncio.run(_exercise_server(tmp_path))

