from __future__ import annotations

import asyncio
import importlib.metadata
import os
import sqlite3
import sys
from pathlib import Path
from typing import Literal

import pytest
from mcp import StdioServerParameters
from mcp.client import Client
from mcp.client.stdio import stdio_client
from mcp.server import MCPServer
from mcp.types import Implementation

from agent_mem_bridge.mcp_boundary import (
    DISCOVER_CACHE_HINT,
    MCP_LEGACY_TEST_VERSION,
    MCP_MODERN_VERSION,
    PUBLIC_TOOL_ORDER,
    TOOLS_LIST_CACHE_HINT,
)
from agent_mem_bridge.schema import CURRENT_SCHEMA_VERSION, schema_version
from agent_mem_bridge.storage import MemoryStore

ROOT = Path(__file__).resolve().parents[1]
PROOF_CLIENT = Implementation(name="amb-v0261-proof", version="0.26.1")


def _database_dump(db_path: Path) -> tuple[str, ...]:
    conn = sqlite3.connect(db_path)
    try:
        return tuple(sorted(conn.iterdump()))
    finally:
        conn.close()


async def _exercise_stdio_era(
    mode: Literal["auto", "legacy"],
    expected_protocol_version: str,
    *,
    bridge_home: Path,
    db_path: Path,
) -> None:
    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "agent_mem_bridge"],
        cwd=ROOT,
        env={
            **os.environ,
            "AGENT_MEMORY_BRIDGE_HOME": str(bridge_home),
            "AGENT_MEMORY_BRIDGE_DB_PATH": str(db_path),
            "AGENT_MEMORY_BRIDGE_LOG_DIR": str(bridge_home / "logs"),
        },
    )

    async with Client(stdio_client(server_params), mode=mode, client_info=PROOF_CLIENT) as client:
        assert client.protocol_version == expected_protocol_version
        assert client.server_info is not None
        assert client.server_info.name == "agent-memory-bridge"

        if mode == "auto":
            assert client.session.discover_result is not None
            assert client.session.discover_result.result_type == "complete"
            assert MCP_MODERN_VERSION in client.session.discover_result.supported_versions
            assert client.session.discover_result.ttl_ms == DISCOVER_CACHE_HINT.ttl_ms
            assert client.session.discover_result.cache_scope == DISCOVER_CACHE_HINT.scope
            assert client.session.initialize_result is None
        else:
            assert client.session.initialize_result is not None
            assert client.session.initialize_result.protocol_version == MCP_LEGACY_TEST_VERSION
            assert client.session.discover_result is None

        for _ in range(100):
            tools_result = await client.list_tools(cache_mode="bypass")
            assert tools_result.result_type == "complete"
            assert tools_result.ttl_ms == TOOLS_LIST_CACHE_HINT.ttl_ms
            assert tools_result.cache_scope == TOOLS_LIST_CACHE_HINT.scope
            assert [tool.name for tool in tools_result.tools] == list(PUBLIC_TOOL_ORDER)

        stats_result = await client.call_tool("stats", {"namespace": "project:v026-proof"})
        assert stats_result.result_type == "complete"
        assert stats_result.is_error is False
        assert stats_result.structured_content is not None
        assert stats_result.structured_content["total_count"] == 0


def test_mcp_2_dual_era_surface_preserves_schema_and_durable_data(
    tmp_path: Path,
) -> None:
    assert importlib.metadata.version("mcp") == "2.0.0"

    bridge_home = tmp_path / "dual-era-home"
    db_path = bridge_home / "bridge.db"
    MemoryStore(db_path, log_dir=bridge_home / "logs")

    before = _database_dump(db_path)
    with sqlite3.connect(db_path) as conn:
        assert schema_version(conn) == CURRENT_SCHEMA_VERSION == 7

    asyncio.run(
        _exercise_stdio_era(
            "auto",
            MCP_MODERN_VERSION,
            bridge_home=bridge_home,
            db_path=db_path,
        )
    )
    asyncio.run(
        _exercise_stdio_era(
            "legacy",
            MCP_LEGACY_TEST_VERSION,
            bridge_home=bridge_home,
            db_path=db_path,
        )
    )

    assert _database_dump(db_path) == before
    with sqlite3.connect(db_path) as conn:
        assert schema_version(conn) == CURRENT_SCHEMA_VERSION == 7


async def _exercise_modern_client_info(mcp_server: MCPServer) -> None:
    async with Client(mcp_server, mode="auto", client_info=PROOF_CLIENT) as client:
        context_content = "v026contextprecedence7f4a durable record."
        explicit_content = "v026explicitprecedence9c2d durable record."
        explicit_source_client = "amb-v026-explicit-client"

        context_stored = await client.call_tool(
            "store",
            {
                "namespace": "project:v026-client-info",
                "content": context_content,
                "kind": "memory",
            },
        )
        assert context_stored.result_type == "complete"
        assert context_stored.is_error is False

        explicit_stored = await client.call_tool(
            "store",
            {
                "namespace": "project:v026-client-info",
                "content": explicit_content,
                "kind": "memory",
                "source_client": explicit_source_client,
            },
        )
        assert explicit_stored.result_type == "complete"
        assert explicit_stored.is_error is False

        context_recalled = await client.call_tool(
            "recall",
            {
                "namespace": "project:v026-client-info",
                "query": "v026contextprecedence7f4a",
                "kind": "memory",
                "limit": 1,
            },
        )
        assert context_recalled.result_type == "complete"
        assert context_recalled.is_error is False
        assert context_recalled.structured_content is not None
        assert context_recalled.structured_content["count"] == 1
        assert context_recalled.structured_content["items"][0]["content"] == context_content
        assert context_recalled.structured_content["items"][0]["source_client"] == PROOF_CLIENT.name

        explicit_recalled = await client.call_tool(
            "recall",
            {
                "namespace": "project:v026-client-info",
                "query": "v026explicitprecedence9c2d",
                "kind": "memory",
                "limit": 1,
            },
        )
        assert explicit_recalled.result_type == "complete"
        assert explicit_recalled.is_error is False
        assert explicit_recalled.structured_content is not None
        assert explicit_recalled.structured_content["count"] == 1
        assert explicit_recalled.structured_content["items"][0]["content"] == explicit_content
        assert explicit_recalled.structured_content["items"][0]["source_client"] == explicit_source_client

        reserved_tag_attempt = await client.call_tool(
            "annotate",
            {
                "id": context_stored.structured_content["id"],
                "tags": ["reviewed:true"],
                "actor": "caller-declared-client",
            },
        )
        assert reserved_tag_attempt.is_error is True
        error_text = "\n".join(str(getattr(item, "text", "")) for item in reserved_tag_attempt.content)
        assert "reserved policy tags" in error_text


def test_modern_client_info_reaches_provenance_when_exposed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge_home = tmp_path / "client-info-home"
    db_path = bridge_home / "bridge.db"
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_HOME", str(bridge_home))
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_DB_PATH", str(db_path))
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_LOG_DIR", str(bridge_home / "logs"))
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_DEFAULT_SOURCE_CLIENT", "amb-v026-env-default")
    for key in (
        "AGENT_MEMORY_BRIDGE_DEFAULT_SOURCE_MODEL",
        "AGENT_MEMORY_BRIDGE_DEFAULT_CLIENT_SESSION_ID",
        "AGENT_MEMORY_BRIDGE_DEFAULT_CLIENT_WORKSPACE",
        "AGENT_MEMORY_BRIDGE_DEFAULT_CLIENT_TRANSPORT",
    ):
        monkeypatch.delenv(key, raising=False)

    from agent_mem_bridge import server as amb_server

    monkeypatch.setattr(
        amb_server,
        "bridge",
        MemoryStore(db_path, log_dir=bridge_home / "logs"),
    )

    asyncio.run(_exercise_modern_client_info(amb_server.mcp))
