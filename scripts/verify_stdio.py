import asyncio
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main() -> None:
    project_root = Path(__file__).resolve().parents[1]
    runtime_dir = project_root / ".runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)

    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "agent_mem_bridge"],
        cwd=str(project_root),
        env={
            **os.environ,
            "AGENT_MEMORY_BRIDGE_DB_PATH": str(runtime_dir / "bridge.db"),
            "AGENT_MEMORY_BRIDGE_LOG_DIR": str(runtime_dir / "logs"),
        },
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools_response = await session.list_tools()
            print("tools:", [tool.name for tool in tools_response.tools])

            first = await session.call_tool(
                "store",
                arguments={
                    "namespace": "smoke-test",
                    "content": "Agent Memory Bridge transport proof is working.",
                    "kind": "memory",
                    "tags": ["project:agent-memory-bridge", "check:transport"],
                    "session_id": "smoke-1",
                    "actor": "verify-script",
                    "source_app": "verify_stdio.py",
                },
            )
            print("first store:", first.structuredContent)

            second = await session.call_tool(
                "store",
                arguments={
                    "namespace": "smoke-test",
                    "content": "Agent Memory Bridge transport proof is working.",
                    "kind": "memory",
                    "tags": ["project:agent-memory-bridge", "check:transport"],
                    "session_id": "smoke-1",
                    "actor": "verify-script",
                    "source_app": "verify_stdio.py",
                },
            )
            print("duplicate store:", second.structuredContent)

            recall = await session.call_tool(
                "recall",
                arguments={
                    "namespace": "smoke-test",
                    "query": "transport proof",
                    "limit": 5,
                },
            )
            print("recall:", recall.structuredContent)


if __name__ == "__main__":
    asyncio.run(main())

