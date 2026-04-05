from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from .storage import MemoryStore

mcp = FastMCP("agent-memory-bridge", json_response=True)
bridge = MemoryStore.from_env()


@mcp.tool(structured_output=True)
def store(
    namespace: str,
    content: str,
    kind: str = "memory",
    tags: list[str] | None = None,
    session_id: str | None = None,
    actor: str | None = None,
    title: str | None = None,
    correlation_id: str | None = None,
    source_app: str | None = None,
) -> dict[str, Any]:
    """Store one shared memory or signal entry in the bridge."""
    return bridge.store(
        namespace=namespace,
        content=content,
        kind=kind,
        tags=tags,
        session_id=session_id,
        actor=actor,
        title=title,
        correlation_id=correlation_id,
        source_app=source_app,
    )


@mcp.tool(structured_output=True)
def recall(
    namespace: str,
    query: str = "",
    limit: int = 5,
    kind: str | None = None,
    tags_any: list[str] | None = None,
    session_id: str | None = None,
    actor: str | None = None,
    correlation_id: str | None = None,
    since: str | None = None,
) -> dict[str, Any]:
    """Recall matching entries or poll for new signals from the bridge."""
    return bridge.recall(
        namespace=namespace,
        query=query,
        limit=limit,
        kind=kind,
        tags_any=tags_any,
        session_id=session_id,
        actor=actor,
        correlation_id=correlation_id,
        since=since,
    )


def main() -> None:
    mcp.run(transport="stdio")
