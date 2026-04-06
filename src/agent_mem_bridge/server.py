from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field

from mcp.server.fastmcp import FastMCP

from .storage import MemoryStore

mcp = FastMCP("agent-memory-bridge", json_response=True)
bridge = MemoryStore.from_env()


@mcp.tool(structured_output=True)
def store(
    namespace: Annotated[
        str,
        Field(
            description=(
                "Logical memory bucket to write into, such as "
                "`project:<workspace>`, `domain:<name>`, or `global`."
            )
        ),
    ],
    content: Annotated[
        str,
        Field(
            description=(
                "Machine-readable memory payload to persist. Prefer compact claims, "
                "fixes, decisions, or signals over long transcript-style prose."
            )
        ),
    ],
    kind: Annotated[
        Literal["memory", "signal"],
        Field(
            description=(
                "`memory` stores durable knowledge for later retrieval. `signal` stores "
                "pollable coordination events for handoff or workflow triggers."
            )
        ),
    ] = "memory",
    tags: Annotated[
        list[str] | None,
        Field(
            description=(
                "Optional stable labels for retrieval and filtering, for example "
                "`kind:gotcha`, `domain:retrieval`, or `project:mem-store`."
            )
        ),
    ] = None,
    session_id: Annotated[
        str | None,
        Field(
            description=(
                "Optional session or thread identifier used to trace entries back to one "
                "conversation or work unit."
            )
        ),
    ] = None,
    actor: Annotated[
        str | None,
        Field(
            description=(
                "Optional writer identity such as an agent, reviewer, or user profile."
            )
        ),
    ] = None,
    title: Annotated[
        str | None,
        Field(
            description=(
                "Optional short label for UI display or scanning. Keep it concise and "
                "stable when possible."
            )
        ),
    ] = None,
    correlation_id: Annotated[
        str | None,
        Field(
            description=(
                "Optional shared identifier used to link related writes, handoffs, or "
                "workflow events across entries."
            )
        ),
    ] = None,
    source_app: Annotated[
        str | None,
        Field(
            description=(
                "Optional source name for the writer, such as `codex`, "
                "`codex-session-watcher`, or another local automation."
            )
        ),
    ] = None,
) -> dict[str, Any]:
    """Store one entry in the bridge for later retrieval or coordination.

    Use this tool when you want to persist a compact memory record or write a signal
    that another workflow can poll later. Write durable knowledge as `kind="memory"`
    and transient coordination events as `kind="signal"`.

    Returns the stored entry identifier, timestamp, and duplicate information. Repeated
    `memory` writes may deduplicate; `signal` writes are intended to remain append-like.
    """
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
    namespace: Annotated[
        str,
        Field(
            description=(
                "Namespace to search or poll, such as `project:<workspace>`, "
                "`domain:<name>`, or `global`."
            )
        ),
    ],
    query: Annotated[
        str,
        Field(
            description=(
                "Optional text query for full-text recall. Leave empty to use filter-only "
                "retrieval or polling."
            )
        ),
    ] = "",
    limit: Annotated[
        int,
        Field(
            ge=1,
            le=100,
            description="Maximum number of entries to return. Smaller values keep context tighter.",
        ),
    ] = 5,
    kind: Annotated[
        Literal["memory", "signal"] | None,
        Field(
            description=(
                "Optional type filter. Use `memory` for durable knowledge recall and "
                "`signal` for coordination or polling flows."
            )
        ),
    ] = None,
    tags_any: Annotated[
        list[str] | None,
        Field(
            description=(
                "Optional OR-style tag filter. Any matching tag is enough for an entry "
                "to qualify."
            )
        ),
    ] = None,
    session_id: Annotated[
        str | None,
        Field(description="Optional session filter to narrow results to one conversation or run."),
    ] = None,
    actor: Annotated[
        str | None,
        Field(description="Optional actor filter for entries written by a specific agent or user."),
    ] = None,
    correlation_id: Annotated[
        str | None,
        Field(
            description=(
                "Optional correlation filter to recall entries linked to the same workflow, "
                "handoff, or task."
            )
        ),
    ] = None,
    since: Annotated[
        str | None,
        Field(
            description=(
                "Optional cursor for polling only entries newer than a previously seen "
                "entry id. Most useful with `kind=\"signal\"`."
            )
        ),
    ] = None,
) -> dict[str, Any]:
    """Recall matching entries or poll for new signals from the bridge.

    Use this tool to search durable memory, filter by metadata, or poll for fresh
    coordination signals. For issue-like work, prefer project and domain recall before
    external search. For workflow polling, pass `since` and usually `kind="signal"`.

    Returns matching items plus a `next_since` cursor that can be reused for the next
    polling cycle.
    """
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
