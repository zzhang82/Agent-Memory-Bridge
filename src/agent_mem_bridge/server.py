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
    expires_at: Annotated[
        str | None,
        Field(
            description=(
                "Optional ISO-8601 expiry timestamp for a signal. Use this when a coordination "
                "event should stop being claimable after a deadline."
            )
        ),
    ] = None,
    ttl_seconds: Annotated[
        int | None,
        Field(
            gt=0,
            description=(
                "Optional relative expiry in seconds for a signal. Useful for short-lived "
                "handoff or review events."
            ),
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
        expires_at=expires_at,
        ttl_seconds=ttl_seconds,
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
    signal_status: Annotated[
        Literal["pending", "claimed", "acked", "expired"] | None,
        Field(
            description=(
                "Optional status filter for signals. Useful when you want only pending handoffs, "
                "currently claimed work, or already-acked coordination events."
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
        signal_status=signal_status,
        tags_any=tags_any,
        session_id=session_id,
        actor=actor,
        correlation_id=correlation_id,
        since=since,
    )


@mcp.tool(structured_output=True)
def browse(
    namespace: Annotated[
        str,
        Field(
            description=(
                "Namespace to inspect without a text query, such as "
                "`project:<workspace>`, `domain:<name>`, or `global`."
            )
        ),
    ],
    domain: Annotated[
        str | None,
        Field(
            description=(
                "Optional domain tag to narrow the list, using the plain domain name "
                "without the `domain:` prefix."
            )
        ),
    ] = None,
    kind: Annotated[
        Literal["memory", "signal"] | None,
        Field(
            description=(
                "Optional type filter. Use `memory` for durable knowledge and `signal` "
                "for coordination events."
            )
        ),
    ] = None,
    signal_status: Annotated[
        Literal["pending", "claimed", "acked", "expired"] | None,
        Field(
            description="Optional status filter when browsing signal entries.",
        ),
    ] = None,
    limit: Annotated[
        int,
        Field(
            ge=1,
            le=100,
            description="Maximum number of items to list. Smaller values keep browse output readable.",
        ),
    ] = 10,
) -> dict[str, Any]:
    """Browse recent items when you do not yet know what to search for.

    Use this tool to inspect a namespace by filters alone. It is useful when you want
    to see recent memory, scan a domain bucket, or confirm that signals are flowing
    before writing a more specific recall query.
    """
    return bridge.browse(
        namespace=namespace,
        domain=domain,
        kind=kind,
        signal_status=signal_status,
        limit=limit,
    )


@mcp.tool(structured_output=True)
def stats(
    namespace: Annotated[
        str,
        Field(
            description=(
                "Namespace to summarize, such as `project:<workspace>`, `domain:<name>`, "
                "or `global`."
            )
        ),
    ],
) -> dict[str, Any]:
    """Return a quick health summary for one namespace.

    Use this tool when you want to inspect what is in the bridge without opening SQLite
    directly. It returns total item count, a kind breakdown, top domains, and the
    oldest and newest entry timestamps for the namespace.
    """
    return bridge.stats(namespace=namespace)


@mcp.tool(structured_output=True)
def forget(
    id: Annotated[
        str,
        Field(
            description=(
                "Exact memory identifier to remove. Use this when a record is noisy, wrong, "
                "or no longer belongs in the bridge."
            )
        ),
    ],
) -> dict[str, Any]:
    """Delete one stored entry by id.

    Use this tool to remove a bad memory, an accidental write, or a signal that should
    no longer exist. The response tells you whether anything was deleted and returns the
    removed item metadata when a match is found.
    """
    return bridge.forget(memory_id=id)


@mcp.tool(structured_output=True)
def claim_signal(
    namespace: Annotated[
        str,
        Field(
            description=(
                "Namespace that holds the coordination events to claim, such as "
                "`project:<workspace>`."
            )
        ),
    ],
    consumer: Annotated[
        str,
        Field(
            description=(
                "Stable worker or agent identifier that will own the lease, for example "
                "`reviewer-a` or `worker:planner`."
            )
        ),
    ],
    lease_seconds: Annotated[
        int,
        Field(
            gt=0,
            description="How long the claim lease should last before another consumer can reclaim the signal.",
        ),
    ] = 300,
    signal_id: Annotated[
        str | None,
        Field(
            description="Optional exact signal id to claim. Leave empty to claim the next eligible signal.",
        ),
    ] = None,
    tags_any: Annotated[
        list[str] | None,
        Field(
            description="Optional OR-style tag filter used to narrow which pending signals are claimable.",
        ),
    ] = None,
    correlation_id: Annotated[
        str | None,
        Field(
            description="Optional workflow correlation id used to claim signals from one handoff thread.",
        ),
    ] = None,
) -> dict[str, Any]:
    """Claim one signal with a short lease for lightweight work coordination.

    Use this when a worker should take ownership of a pending signal before it acts.
    If `signal_id` is omitted, the bridge claims the next eligible signal in the
    namespace that matches the optional filters.
    """
    return bridge.claim_signal(
        namespace=namespace,
        consumer=consumer,
        lease_seconds=lease_seconds,
        signal_id=signal_id,
        tags_any=tags_any,
        correlation_id=correlation_id,
    )


@mcp.tool(structured_output=True)
def ack_signal(
    id: Annotated[
        str,
        Field(
            description="Exact signal id to acknowledge after the work is done.",
        ),
    ],
    consumer: Annotated[
        str | None,
        Field(
            description="Optional consumer identity. When provided, the bridge checks that another active claimant does not own the lease.",
        ),
    ] = None,
) -> dict[str, Any]:
    """Acknowledge one claimed or pending signal so downstream polling can stop treating it as active work."""
    return bridge.ack_signal(memory_id=id, consumer=consumer)


@mcp.tool(structured_output=True)
def extend_signal_lease(
    id: Annotated[
        str,
        Field(
            description=(
                "Exact signal id whose active lease should be extended."
            ),
        ),
    ],
    consumer: Annotated[
        str,
        Field(
            description=(
                "Consumer identity that currently owns the lease. Only the active claimant can extend it."
            ),
        ),
    ],
    lease_seconds: Annotated[
        int,
        Field(
            gt=0,
            description=(
                "Additional lease duration in seconds. The bridge extends from the current lease end when possible, "
                "but never beyond the signal's hard expiry."
            ),
        ),
    ],
) -> dict[str, Any]:
    """Extend the active lease on one claimed signal.

    Use this when a worker still owns a signal but needs more time before another
    consumer can reclaim it. Expired leases cannot be extended; those signals must
    be reclaimed instead. Hard signal expiry still takes precedence over lease renewal.
    """
    return bridge.extend_signal_lease(memory_id=id, consumer=consumer, lease_seconds=lease_seconds)


@mcp.tool(structured_output=True)
def promote(
    id: Annotated[
        str,
        Field(
            description=(
                "Exact memory identifier to reclassify. Use this when a stored record should "
                "be treated as a stronger kind of durable memory."
            )
        ),
    ],
    to_kind: Annotated[
        Literal["learn", "gotcha", "domain-note"],
        Field(
            description=(
                "Target durable record type. Use `learn` for reusable claims, `gotcha` for "
                "pitfalls and fixes, or `domain-note` for broader synthesized guidance."
            )
        ),
    ],
) -> dict[str, Any]:
    """Manually promote one stored memory to a stronger durable record type.

    Use this tool when you know a record should be treated as a learn, gotcha, or
    domain note even if the reflex layer has not promoted it yet. Promotion keeps the
    same id and updates the stored title, tags, and structured content in place.
    """
    return bridge.promote(memory_id=id, to_kind=to_kind)


@mcp.tool(structured_output=True)
def export(
    namespace: Annotated[
        str,
        Field(
            description=(
                "Namespace to export, such as `project:<workspace>`, `domain:<name>`, or `global`."
            )
        ),
    ],
    format: Annotated[
        Literal["markdown", "json", "text"],
        Field(
            description=(
                "Output format for the exported memory. Use `markdown` for readable notes, "
                "`json` for structured interchange, or `text` for plain text."
            )
        ),
    ] = "markdown",
    query: Annotated[
        str,
        Field(
            description=(
                "Optional full-text query to narrow the export. Leave empty to export by "
                "filters alone."
            )
        ),
    ] = "",
    kind: Annotated[
        Literal["memory", "signal"] | None,
        Field(description="Optional type filter for the export."),
    ] = None,
    signal_status: Annotated[
        Literal["pending", "claimed", "acked", "expired"] | None,
        Field(description="Optional status filter when exporting signal entries."),
    ] = None,
    tags_any: Annotated[
        list[str] | None,
        Field(
            description=(
                "Optional OR-style tag filter. Any matching tag is enough for an entry "
                "to be included."
            )
        ),
    ] = None,
    limit: Annotated[
        int,
        Field(
            ge=1,
            le=500,
            description="Maximum number of entries to export in one call.",
        ),
    ] = 100,
) -> dict[str, Any]:
    """Export bridge content into a readable or portable format.

    Use this tool when you want to inspect a namespace outside the MCP client, create
    a human-readable snapshot, or move memory into another system without opening the
    database directly.
    """
    return bridge.export(
        namespace=namespace,
        format=format,
        query=query,
        kind=kind,
        signal_status=signal_status,
        tags_any=tags_any,
        limit=limit,
    )


def main() -> None:
    mcp.run(transport="stdio")
