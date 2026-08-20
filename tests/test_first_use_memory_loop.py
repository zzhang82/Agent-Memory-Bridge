from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from agent_mem_bridge.onboarding import TOOL_NAMES

ROOT = Path(__file__).resolve().parents[1]
NAMESPACE = "project:first-use-loop"
QUERY = "What should I check before submitting changes to this project?"
CONTENT = "Run make check before submitting changes to this project."


def test_real_stdio_first_use_memory_loop_survives_fresh_server(tmp_path: Path) -> None:
    asyncio.run(_exercise_first_use_loop(tmp_path))


async def _exercise_first_use_loop(tmp_path: Path) -> None:
    database = tmp_path / "bridge.db"
    server_params = _server_params(tmp_path, database)

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            assert {tool.name for tool in tools.tools} == TOOL_NAMES
            assert len(tools.tools) == 17

            stored = _payload(
                await session.call_tool(
                    "store",
                    arguments={
                        "namespace": NAMESPACE,
                        "kind": "memory",
                        "title": "Submission check",
                        "content": CONTENT,
                        "tags": ["domain:delivery"],
                    },
                )
            )
            first_recall = _payload(
                await session.call_tool(
                    "recall",
                    arguments={"namespace": NAMESPACE, "query": QUERY, "kind": "memory", "limit": 3},
                )
            )
            memory_id = stored["id"]
            recalled_item = next(item for item in first_recall["items"] if item["id"] == memory_id)
            receipt_token = first_recall["recall_receipt"]["token"]
            feedback = _payload(
                await session.call_tool(
                    "feedback",
                    arguments={
                        "namespace": NAMESPACE,
                        "recall_receipt": receipt_token,
                        "memory_id": memory_id,
                        "result_rank": first_recall["items"].index(recalled_item) + 1,
                        "outcome": "helpful",
                        "reason": "",
                        "provenance": {"source_app": "pytest", "actor": "first-use-loop"},
                    },
                )
            )

    assert feedback["stored"] is True
    assert isinstance(feedback["feedback_id"], int)
    assert feedback["feedback_mode"] == "shadow_only"
    assert feedback["ordering_unchanged"] is True
    feedback_text = json.dumps(feedback, sort_keys=True)
    assert receipt_token not in feedback_text
    assert CONTENT not in feedback_text

    # This opens a separate stdio server process against the same durable DB.
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            second_recall = _payload(
                await session.call_tool(
                    "recall",
                    arguments={"namespace": NAMESPACE, "query": QUERY, "kind": "memory", "limit": 3},
                )
            )

    second_item = next(item for item in second_recall["items"] if item["id"] == memory_id)
    assert second_item["content"] == CONTENT
    assert second_item["title"] == "Submission check"
    assert second_item["id"] == memory_id
    assert second_recall["recall_receipt"]["token"] != receipt_token
    with sqlite3.connect(database) as conn:
        feedback_count = conn.execute("SELECT COUNT(*) FROM retrieval_feedback").fetchone()[0]
    assert feedback_count == 1


def _server_params(tmp_path: Path, database: Path) -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "agent_mem_bridge"],
        cwd=str(ROOT),
        env={
            **os.environ,
            "AGENT_MEMORY_BRIDGE_DB_PATH": str(database),
            "AGENT_MEMORY_BRIDGE_LOG_DIR": str(tmp_path / "logs"),
            "AGENT_MEMORY_BRIDGE_DEFAULT_SOURCE_CLIENT": "first-use-client",
            "AGENT_MEMORY_BRIDGE_DEFAULT_SOURCE_MODEL": "first-use-model",
            "AGENT_MEMORY_BRIDGE_DEFAULT_CLIENT_SESSION_ID": "first-use-session",
            "AGENT_MEMORY_BRIDGE_DEFAULT_CLIENT_WORKSPACE": NAMESPACE,
            "AGENT_MEMORY_BRIDGE_DEFAULT_CLIENT_TRANSPORT": "stdio",
        },
    )


def _payload(response: Any) -> dict[str, Any]:
    payload = getattr(response, "structured_content", None) or getattr(response, "structuredContent", None) or {}
    assert isinstance(payload, dict)
    return payload
