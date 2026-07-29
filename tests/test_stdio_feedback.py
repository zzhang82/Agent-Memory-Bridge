from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from agent_mem_bridge.onboarding import TOOL_NAMES
from agent_mem_bridge.retrieval_feedback import recall_receipt_hash
from agent_mem_bridge.telemetry import hash_label

ROOT = Path(__file__).resolve().parents[1]


async def _exercise_feedback_tool(tmp_path: Path) -> None:
    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "agent_mem_bridge"],
        cwd=str(ROOT),
        env={
            **os.environ,
            "AGENT_MEMORY_BRIDGE_DB_PATH": str(tmp_path / "bridge.db"),
            "AGENT_MEMORY_BRIDGE_LOG_DIR": str(tmp_path / "logs"),
            "AGENT_MEMORY_BRIDGE_DEFAULT_SOURCE_CLIENT": "feedback-client",
            "AGENT_MEMORY_BRIDGE_DEFAULT_SOURCE_MODEL": "feedback-model",
            "AGENT_MEMORY_BRIDGE_DEFAULT_CLIENT_SESSION_ID": "feedback-session",
            "AGENT_MEMORY_BRIDGE_DEFAULT_CLIENT_WORKSPACE": "project:feedback",
            "AGENT_MEMORY_BRIDGE_DEFAULT_CLIENT_TRANSPORT": "stdio",
        },
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools_response = await session.list_tools()
            tool_names = {tool.name for tool in tools_response.tools}
            assert tool_names == TOOL_NAMES
            assert len(tool_names) == 13
            assert "feedback" in tool_names

            stored = await session.call_tool(
                "store",
                arguments={
                    "namespace": "bridge",
                    "content": "Feedback MCP recall target.",
                    "kind": "memory",
                    "tags": ["domain:feedback"],
                    "title": "Feedback target",
                },
            )
            recalled = await session.call_tool(
                "recall",
                arguments={
                    "namespace": "bridge",
                    "query": "Feedback MCP recall target",
                    "kind": "memory",
                    "limit": 5,
                },
            )
            recalled_payload = _structured_payload(recalled)
            stored_payload = _structured_payload(stored)
            receipt_token = recalled_payload["recall_receipt"]["token"]
            memory_id = stored_payload["id"]
            request = {
                "namespace": "bridge",
                "recall_receipt": receipt_token,
                "memory_id": memory_id,
                "result_rank": 1,
                "outcome": "helpful",
                "reason": "",
                "provenance": {
                    "source_app": "pytest",
                    "actor": "feedback-test",
                },
            }

            first = await session.call_tool("feedback", arguments=request)
            retry = await session.call_tool("feedback", arguments=request)
            conflict = await session.call_tool(
                "feedback",
                arguments={**request, "outcome": "not_used"},
            )

    first_payload = _structured_payload(first)
    retry_payload = _structured_payload(retry)
    assert first_payload["stored"] is True
    assert first_payload["duplicate"] is False
    assert first_payload["namespace_hash"] == hash_label("bridge")
    assert first_payload["memory_id_hash"] == hash_label(memory_id)
    assert first_payload["receipt_bound"] is True
    assert first_payload["feedback_mode"] == "shadow_only"
    assert first_payload["ordering_unchanged"] is True
    assert first_payload["provenance"] == "caller_declared_not_authenticated"
    assert first_payload["authenticated_origin"] is False
    forbidden_keys = {
        "namespace",
        "memory_id",
        "reason",
        "receipt_hash",
        "source_app",
        "source_client",
        "source_model",
        "client_session_id",
        "client_workspace",
        "client_transport",
        "actor",
        "created_at",
    }
    assert forbidden_keys.isdisjoint(first_payload)
    encoded_feedback = json.dumps([first_payload, retry_payload], sort_keys=True)
    for value in (
        receipt_token,
        recall_receipt_hash(receipt_token),
        "pytest",
        "feedback-client",
        "feedback-model",
        "feedback-session",
        "project:feedback",
        "stdio",
        "feedback-test",
    ):
        assert value not in encoded_feedback

    assert retry_payload["stored"] is False
    assert retry_payload["duplicate"] is True
    assert retry_payload["feedback_id"] == first_payload["feedback_id"]

    assert getattr(conflict, "is_error", False) is True
    assert "conflicting plain feedback vote; submit a correction" in _response_text(conflict)


def test_stdio_feedback_tool_success_retry_conflict_and_startup_surface(tmp_path: Path) -> None:
    asyncio.run(_exercise_feedback_tool(tmp_path))


def _response_text(response: Any) -> str:
    return "\n".join(str(getattr(item, "text", "")) for item in getattr(response, "content", []))


def _structured_payload(response: Any) -> dict[str, Any]:
    payload = getattr(response, "structured_content", None) or getattr(response, "structuredContent", None) or {}
    if isinstance(payload, dict):
        return payload
    return {}
