from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated, Any, Literal, cast

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.types import Tool as MCPTool
from pydantic import Field

from .mcp_boundary import (
    MCP_CACHE_HINTS,
    PUBLIC_TOOL_NAMES,
    PUBLIC_TOOL_ORDER,
    SERVER_DESCRIPTION,
    SERVER_NAME,
    SERVER_TITLE,
    ProtocolObservabilityMiddleware,
    context_source_client,
    package_version,
    with_context_source_client,
)
from .paths import (
    resolve_default_client_session_id,
    resolve_default_client_transport,
    resolve_default_client_workspace,
    resolve_default_source_client,
    resolve_default_source_model,
)
from .repository_snapshot_store import load_repository_knowledge
from .storage import MemoryStore

mcp_logger = logging.getLogger("mcp.server.lowlevel.server")
mcp_logger.setLevel(logging.WARNING)


# MCP_BOUNDARY_COVERAGE_START
class _ContractMCPServer(MCPServer):
    async def list_tools(self) -> list[MCPTool]:
        tools = await super().list_tools()
        tools_by_name = {tool.name: tool for tool in tools}
        if set(tools_by_name) != PUBLIC_TOOL_NAMES:
            missing = sorted(PUBLIC_TOOL_NAMES - set(tools_by_name))
            unexpected = sorted(set(tools_by_name) - PUBLIC_TOOL_NAMES)
            raise RuntimeError(f"public MCP tool contract mismatch: missing={missing}, unexpected={unexpected}")
        return [tools_by_name[name] for name in PUBLIC_TOOL_ORDER]


# MCP_BOUNDARY_COVERAGE_END


@dataclass(frozen=True, slots=True)
class ServerDependencies:
    store: MemoryStore


BridgeFactory = Callable[[], MemoryStore]

# Backward-compatible direct-call injection seam for unit tests and local
# embedders. Production MCP requests use the lifespan-owned dependency instead.
bridge: MemoryStore | None = None


def _default_bridge_factory() -> MemoryStore:
    if bridge is not None:
        return bridge
    return MemoryStore.from_env()


def _bridge_for(ctx: Context[ServerDependencies] | None) -> MemoryStore:
    if ctx is not None:
        try:
            dependencies = ctx.request_context.lifespan_context
        except (AttributeError, ValueError):
            dependencies = None
        if dependencies is not None:
            if not isinstance(dependencies, ServerDependencies):
                raise RuntimeError("MCP lifespan returned incompatible server dependencies")
            return dependencies.store
    if bridge is not None:
        return bridge
    raise RuntimeError("MemoryStore is unavailable outside an active MCP lifespan; inject one explicitly")


def _optional_text(value: str | None) -> str | None:
    """Normalize placeholder empty strings from static-schema MCP clients."""

    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _provenance_text(provenance: dict[str, str] | None, key: str) -> str | None:
    if provenance is None:
        return None
    return _optional_text(provenance.get(key))


def store(
    namespace: Annotated[
        str,
        Field(
            description=(
                "Logical memory bucket to write into, such as `project:<workspace>`, `domain:<name>`, or `global`."
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
                "`kind:gotcha`, `domain:retrieval`, or `project:demo-app`."
            )
        ),
    ] = None,
    session_id: Annotated[
        str | None,
        Field(
            description=(
                "Optional session or thread identifier used to trace entries back to one conversation or work unit."
            )
        ),
    ] = None,
    actor: Annotated[
        str | None,
        Field(description=("Optional writer identity such as an agent, reviewer, or user profile.")),
    ] = None,
    title: Annotated[
        str | None,
        Field(
            description=("Optional short label for UI display or scanning. Keep it concise and stable when possible.")
        ),
    ] = None,
    correlation_id: Annotated[
        str | None,
        Field(
            description=(
                "Optional shared identifier used to link related writes, handoffs, or workflow events across entries."
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
    source_client: Annotated[
        str | None,
        Field(description=("Optional external client identifier such as `codex`, `antigravity`, or `claude-code`.")),
    ] = None,
    source_model: Annotated[
        str | None,
        Field(description=("Optional external model identifier such as `gpt-5.4` or `gemini-2.5-pro`.")),
    ] = None,
    client_session_id: Annotated[
        str | None,
        Field(description=("Optional external client session or thread identifier when the caller can provide one.")),
    ] = None,
    client_workspace: Annotated[
        str | None,
        Field(description=("Optional external client workspace root or project label when useful for provenance.")),
    ] = None,
    client_transport: Annotated[
        str | None,
        Field(description=("Optional transport label such as `stdio`, `http`, or `sse`.")),
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
                "Optional relative expiry in seconds for a signal. Useful for short-lived handoff or review events."
            ),
        ),
    ] = None,
    ctx: Context[ServerDependencies] | None = None,
) -> dict[str, Any]:
    """Store one entry in the bridge for later retrieval or coordination.

    Use this tool when you want to persist a compact memory record or write a signal
    that another workflow can poll later. Write durable knowledge as `kind="memory"`
    and transient coordination events as `kind="signal"`.

    Returns the stored entry identifier, timestamp, and duplicate information. Repeated
    `memory` writes may deduplicate; `signal` writes are intended to remain append-like.
    """
    session_id = _optional_text(session_id)
    actor = _optional_text(actor)
    title = _optional_text(title)
    correlation_id = _optional_text(correlation_id)
    source_app = _optional_text(source_app)
    source_client = _optional_text(source_client) or context_source_client(ctx) or resolve_default_source_client()
    source_model = _optional_text(source_model) or resolve_default_source_model()
    client_session_id = _optional_text(client_session_id) or resolve_default_client_session_id()
    client_workspace = _optional_text(client_workspace) or resolve_default_client_workspace()
    client_transport = _optional_text(client_transport) or resolve_default_client_transport()
    expires_at = _optional_text(expires_at)

    # MCP clients expose one static schema for both durable memory and expiring
    # signals. Some clients still send signal-only fields with placeholder values
    # when storing kind="memory". Normalize at the MCP boundary so durable memory
    # writes do not fail because the client could not truly omit optional signal
    # fields. The lower-level repository API remains strict for direct callers.
    if kind == "memory":
        expires_at = None
        ttl_seconds = None

    return _bridge_for(ctx).store(
        namespace=namespace,
        content=content,
        kind=kind,
        tags=tags,
        session_id=session_id,
        actor=actor,
        title=title,
        correlation_id=correlation_id,
        source_app=source_app,
        source_client=source_client,
        source_model=source_model,
        client_session_id=client_session_id,
        client_workspace=client_workspace,
        client_transport=client_transport,
        expires_at=expires_at,
        ttl_seconds=ttl_seconds,
    )


def feedback(
    namespace: Annotated[
        str,
        Field(description="Namespace used by the recall receipt, such as `project:<workspace>` or `global`."),
    ],
    recall_receipt: Annotated[
        str,
        Field(description="Signed recall receipt token returned by a durable memory text `recall` response."),
    ],
    memory_id: Annotated[
        str,
        Field(description="Exact recalled memory id being evaluated."),
    ],
    result_rank: Annotated[
        int,
        Field(gt=0, description="One-based rank of the memory in the recalled result list."),
    ],
    outcome: Annotated[
        Literal["helpful", "misleading", "outdated", "not_applicable", "not_used"] | None,
        Field(description="Declared retrieval outcome. Required for votes and corrections; optional for retractions."),
    ] = None,
    reason: Annotated[
        str | None,
        Field(description="Optional compact reason. Required for `misleading` and `outdated` outcomes."),
    ] = None,
    feedback_type: Annotated[
        Literal["vote", "correction", "retraction"],
        Field(description="Append-only feedback event type. Defaults to a root vote."),
    ] = "vote",
    supersedes_feedback_id: Annotated[
        int | None,
        Field(
            gt=0,
            description="Current feedback head id. Required for corrections and retractions; omitted for root votes.",
        ),
    ] = None,
    provenance: Annotated[
        dict[str, str] | None,
        Field(
            description=(
                "Optional declared provenance fields such as source_app, source_client, source_model, "
                "client_session_id, client_workspace, client_transport, or actor."
            )
        ),
    ] = None,
    ctx: Context[ServerDependencies] | None = None,
) -> dict[str, Any]:
    """Record structured retrieval feedback for one recalled memory result.

    Evidence is append-only/shadow-only. Provenance is caller-declared and not
    authenticated. This tool does not mutate memory records, recall results, or
    ranking behavior.
    """
    source_app = _provenance_text(provenance, "source_app")
    source_client = (
        _provenance_text(provenance, "source_client") or context_source_client(ctx) or resolve_default_source_client()
    )
    source_model = _provenance_text(provenance, "source_model") or resolve_default_source_model()
    client_session_id = _provenance_text(provenance, "client_session_id") or resolve_default_client_session_id()
    client_workspace = _provenance_text(provenance, "client_workspace") or resolve_default_client_workspace()
    client_transport = _provenance_text(provenance, "client_transport") or resolve_default_client_transport()
    actor = _provenance_text(provenance, "actor")

    return _bridge_for(ctx).feedback(
        namespace=namespace,
        recall_receipt=recall_receipt,
        memory_id=memory_id,
        result_rank=result_rank,
        outcome=outcome,
        reason=_optional_text(reason),
        feedback_type=feedback_type,
        supersedes_feedback_id=supersedes_feedback_id,
        source_app=source_app,
        source_client=source_client,
        source_model=source_model,
        client_session_id=client_session_id,
        client_workspace=client_workspace,
        client_transport=client_transport,
        actor=actor,
    )


def recall(
    namespace: Annotated[
        str,
        Field(
            description=("Namespace to search or poll, such as `project:<workspace>`, `domain:<name>`, or `global`.")
        ),
    ],
    query: Annotated[
        str,
        Field(
            description=(
                "Optional text query for full-text recall. Leave empty to use filter-only retrieval or polling."
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
        Field(description=("Optional OR-style tag filter. Any matching tag is enough for an entry to qualify.")),
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
            description=("Optional correlation filter to recall entries linked to the same workflow, handoff, or task.")
        ),
    ] = None,
    since: Annotated[
        str | None,
        Field(
            description=(
                "Optional cursor for polling only entries newer than a previously seen "
                "same-namespace entry id used as a Signal polling anchor. Requires an "
                'empty query and `kind="signal"`.'
            )
        ),
    ] = None,
    evidence_context: Annotated[
        dict[str, str] | None,
        Field(
            max_length=3,
            description=(
                "Optional caller-declared evidence labels. Only `model`, `harness`, and `chat_template` "
                "are accepted; signed receipts contain bounded SHA-256 digests, never raw values. "
                "These labels are not authenticated and do not affect retrieval order or feedback identity."
            ),
        ),
    ] = None,
    ctx: Context[ServerDependencies] | None = None,
) -> dict[str, Any]:
    """Recall matching entries or poll for new signals from the bridge.

    Use this tool to search durable memory, filter by metadata, or poll for fresh
    coordination signals. For issue-like work, prefer project and domain recall before
    external search. For workflow polling, pass `since`, an empty query, and `kind="signal"`.

    Empty-query Signal recall returns a `next_since` cursor for the next polling cycle;
    other recall modes return `next_since=null`. The cursor tracks later insertions,
    not status changes to older Signals.
    """
    # MCP clients expose one static schema for memory and signal recall. Some
    # wrappers pass placeholder values (empty strings, empty arrays, or a signal
    # status like "pending") even when they mean "not filtered". Normalize at
    # this boundary so durable memory recall is not silently hidden by client UI
    # placeholders. The lower-level API still receives precise filters.
    query = query.strip()
    session_id = _optional_text(session_id)
    actor = _optional_text(actor)
    correlation_id = _optional_text(correlation_id)
    since = _optional_text(since)
    if tags_any == []:
        tags_any = None
    if kind == "memory":
        signal_status = None

    result = _bridge_for(ctx).recall(
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
        evidence_context=evidence_context,
    )
    if kind in (None, "memory") and query:
        result["repository_knowledge"] = load_repository_knowledge(namespace=namespace, query=query, limit=limit)
    return result


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
                "Optional domain tag to narrow the list, using the plain domain name without the `domain:` prefix."
            )
        ),
    ] = None,
    kind: Annotated[
        Literal["memory", "signal"] | None,
        Field(
            description=(
                "Optional type filter. Use `memory` for durable knowledge and `signal` for coordination events."
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
    ctx: Context[ServerDependencies] | None = None,
) -> dict[str, Any]:
    """Browse recent items when you do not yet know what to search for.

    Use this tool to inspect a namespace by filters alone. It is useful when you want
    to see recent memory, scan a domain bucket, or confirm that signals are flowing
    before writing a more specific recall query.
    """
    if kind == "memory":
        signal_status = None

    return _bridge_for(ctx).browse(
        namespace=namespace,
        domain=domain,
        kind=kind,
        signal_status=signal_status,
        limit=limit,
    )


def stats(
    namespace: Annotated[
        str,
        Field(description=("Namespace to summarize, such as `project:<workspace>`, `domain:<name>`, or `global`.")),
    ],
    ctx: Context[ServerDependencies] | None = None,
) -> dict[str, Any]:
    """Return a quick health summary for one namespace.

    Use this tool when you want to inspect what is in the bridge without opening SQLite
    directly. It returns total item count, a kind breakdown, top domains, and the
    oldest and newest entry timestamps for the namespace.
    """
    return _bridge_for(ctx).stats(namespace=namespace)


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
    ctx: Context[ServerDependencies] | None = None,
) -> dict[str, Any]:
    """Delete one stored entry by id.

    Use this tool to remove a bad memory, an accidental write, or a signal that should
    no longer exist. The response tells you whether anything was deleted and returns the
    removed item metadata when a match is found.
    """
    return _bridge_for(ctx).forget(memory_id=id)


def claim_signal(
    namespace: Annotated[
        str,
        Field(description=("Namespace that holds the coordination events to claim, such as `project:<workspace>`.")),
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
    ctx: Context[ServerDependencies] | None = None,
) -> dict[str, Any]:
    """Claim one signal with a short lease for lightweight work coordination.

    Use this when a worker should take ownership of a pending signal before it acts.
    If `signal_id` is omitted, the bridge claims the next eligible signal in the
    namespace that matches the optional filters, with a small fairness bias inside
    the oldest pending window so one polling consumer does not keep winning by accident.
    """
    return _bridge_for(ctx).claim_signal(
        namespace=namespace,
        consumer=consumer,
        lease_seconds=lease_seconds,
        signal_id=_optional_text(signal_id),
        tags_any=tags_any,
        correlation_id=_optional_text(correlation_id),
    )


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
            description=(
                "Consumer identity that must match the owner of an active claim. "
                "It may be omitted only when acknowledging a pending, unclaimed signal."
            ),
        ),
    ] = None,
    ctx: Context[ServerDependencies] | None = None,
) -> dict[str, Any]:
    """Acknowledge one claimed or pending signal so downstream polling can stop treating it as active work."""
    return _bridge_for(ctx).ack_signal(memory_id=id, consumer=consumer)


def extend_signal_lease(
    id: Annotated[
        str,
        Field(
            description=("Exact signal id whose active lease should be extended."),
        ),
    ],
    consumer: Annotated[
        str,
        Field(
            description=("Consumer identity that currently owns the lease. Only the active claimant can extend it."),
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
    ctx: Context[ServerDependencies] | None = None,
) -> dict[str, Any]:
    """Extend the active lease on one claimed signal.

    Use this when a worker still owns a signal but needs more time before another
    consumer can reclaim it. Expired leases cannot be extended; those signals must
    be reclaimed instead. Hard signal expiry still takes precedence over lease renewal.
    """
    return _bridge_for(ctx).extend_signal_lease(memory_id=id, consumer=consumer, lease_seconds=lease_seconds)


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
    ctx: Context[ServerDependencies] | None = None,
) -> dict[str, Any]:
    """Manually promote one stored memory to a stronger durable record type.

    Use this tool when you know a record should be treated as a learn, gotcha, or
    domain note even if the reflex layer has not promoted it yet. Promotion keeps the
    same id and updates the stored title, tags, and structured content in place.
    """
    return _bridge_for(ctx).promote(memory_id=id, to_kind=to_kind)


def annotate(
    id: Annotated[
        str,
        Field(description="Exact durable memory id whose metadata should be explicitly enriched."),
    ],
    tags: Annotated[
        list[str] | None,
        Field(description="Optional tags to add. Existing tags remain and the annotation is audited."),
    ] = None,
    title: Annotated[
        str | None,
        Field(description="Optional replacement title. Content and memory identity remain unchanged."),
    ] = None,
    provenance: Annotated[
        dict[str, str] | None,
        Field(
            description=(
                "Optional additional provenance fields such as source_client, source_model, "
                "session_id, or correlation_id. They are retained in the annotation audit trail."
            )
        ),
    ] = None,
    actor: Annotated[
        str | None,
        Field(description="Optional identity of the human or agent performing the explicit annotation."),
    ] = None,
    ctx: Context[ServerDependencies] | None = None,
) -> dict[str, Any]:
    """Explicitly enrich one durable memory without pretending the write was a new fact.

    Use this after `store` reports `duplicate_with_new_metadata`. The original content
    remains unchanged; title/tag changes and additional provenance are written to an
    auditable annotation record.
    """
    return _bridge_for(ctx).annotate(
        memory_id=id,
        tags=tags,
        title=_optional_text(title),
        provenance=with_context_source_client(cast("dict[str, str | None] | None", provenance), ctx),
        actor=_optional_text(actor),
    )


def revise(
    id: Annotated[
        str,
        Field(description="Exact durable memory id that the new revision supersedes."),
    ],
    replacement_content: Annotated[
        str,
        Field(description="Complete replacement content for the new auditable memory revision."),
    ],
    title: Annotated[
        str | None,
        Field(description="Optional title for the new revision. Defaults to the predecessor title."),
    ] = None,
    tags: Annotated[
        list[str] | None,
        Field(description="Optional tags for the new revision. Defaults to compatible predecessor tags."),
    ] = None,
    actor: Annotated[
        str | None,
        Field(description="Optional identity responsible for the revision."),
    ] = None,
    reason: Annotated[
        str | None,
        Field(description="Optional compact reason stored with the predecessor/successor audit link."),
    ] = None,
    provenance: Annotated[
        dict[str, str] | None,
        Field(description="Optional provenance overrides for the new revision."),
    ] = None,
    ctx: Context[ServerDependencies] | None = None,
) -> dict[str, Any]:
    """Create a new durable memory that explicitly supersedes an older record.

    The predecessor remains available for audit. The bridge adds an exact `supersedes`
    edge and records the revision receipt instead of silently mutating content in place.
    """
    return _bridge_for(ctx).revise(
        memory_id=id,
        replacement_content=replacement_content,
        title=_optional_text(title),
        tags=tags,
        actor=_optional_text(actor),
        reason=_optional_text(reason),
        provenance=with_context_source_client(cast("dict[str, str | None] | None", provenance), ctx),
    )


def export(
    namespace: Annotated[
        str,
        Field(description=("Namespace to export, such as `project:<workspace>`, `domain:<name>`, or `global`.")),
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
        Field(description=("Optional full-text query to narrow the export. Leave empty to export by filters alone.")),
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
        Field(description=("Optional OR-style tag filter. Any matching tag is enough for an entry to be included.")),
    ] = None,
    limit: Annotated[
        int,
        Field(
            ge=1,
            le=500,
            description="Maximum number of entries to export in one call.",
        ),
    ] = 100,
    ctx: Context[ServerDependencies] | None = None,
) -> dict[str, Any]:
    """Export bridge content into a readable or portable format.

    Use this tool when you want to inspect a namespace outside the MCP client, create
    a human-readable snapshot, or move memory into another system without opening the
    database directly.
    """
    if kind == "memory":
        signal_status = None

    return _bridge_for(ctx).export(
        namespace=namespace,
        format=format,
        query=query,
        kind=kind,
        signal_status=signal_status,
        tags_any=tags_any,
        limit=limit,
    )


def begin_run(
    workspace_key: Annotated[
        str,
        Field(
            description=(
                "Declared workspace scope for this run, such as `project:<workspace>`. "
                "The server uses it to reject cross-workspace run access."
            )
        ),
    ],
    goal: Annotated[
        str,
        Field(description="Root goal for the run. Keep it bounded and outcome-oriented."),
    ],
    idempotency_key: Annotated[
        str,
        Field(
            description=(
                "Caller-generated retry key. The bridge stores only its SHA-256 digest and "
                "returns the original server-minted run for an identical retry."
            )
        ),
    ],
    agent_id: Annotated[
        str | None,
        Field(
            description="Optional declared agent or worker identifier. It is provenance, not authenticated identity."
        ),
    ] = None,
    thread_id: Annotated[
        str | None,
        Field(description="Optional external thread or conversation identifier."),
    ] = None,
    model_digest: Annotated[
        str | None,
        Field(description="Optional lowercase SHA-256 digest of the model identity or configuration."),
    ] = None,
    harness_digest: Annotated[
        str | None,
        Field(description="Optional lowercase SHA-256 digest of the harness configuration."),
    ] = None,
    chat_template_digest: Annotated[
        str | None,
        Field(description="Optional lowercase SHA-256 digest of the chat template."),
    ] = None,
    tool_schema_digest: Annotated[
        str | None,
        Field(description="Optional lowercase SHA-256 digest of the tool schema used by the run."),
    ] = None,
    memory_scopes: Annotated[
        list[str] | None,
        Field(
            max_length=32,
            description="Optional declared memory scopes available to the run. These do not grant authorization.",
        ),
    ] = None,
    budget: Annotated[
        dict[str, Any] | None,
        Field(description="Optional bounded JSON budget metadata, such as token or time limits."),
    ] = None,
    evidence_profile: Annotated[
        Literal["observational", "governed-v2"],
        Field(description="Evidence governance profile. governed-v2 requires typed events and server-minted receipts."),
    ] = "observational",
    acceptance_criteria: Annotated[
        list[dict[str, Any]] | None,
        Field(description="Optional structured acceptance criteria with stable id or criterion_id values."),
    ] = None,
    constraints: Annotated[
        list[str] | None,
        Field(description="Optional bounded constraints declared for this run."),
    ] = None,
    non_goals: Annotated[
        list[str] | None,
        Field(description="Optional bounded non-goals declared for this run."),
    ] = None,
    risk_level: Annotated[
        Literal["low", "medium", "high", "critical"],
        Field(description="Declared run risk level; high and critical preflight reviews require a rollback plan."),
    ] = "medium",
    continuation_of_run_id: Annotated[
        str | None,
        Field(description="Optional same-workspace prior run that this run continues."),
    ] = None,
    provenance: Annotated[
        dict[str, str] | None,
        Field(description="Optional bounded declared provenance for the run."),
    ] = None,
    ctx: Context[ServerDependencies] | None = None,
) -> dict[str, Any]:
    """Start one explicit, stateless run and return server-minted run/work-item IDs.

    The bridge does not retain an implicit current run. Callers must pass the returned
    handles to later event, read, and completion calls. Idempotent retries return the
    same handles and reject conflicting payloads.
    """
    return _bridge_for(ctx).begin_run(
        workspace_key=workspace_key,
        goal=goal,
        idempotency_key=idempotency_key,
        agent_id=_optional_text(agent_id),
        thread_id=_optional_text(thread_id),
        model_digest=_optional_text(model_digest),
        harness_digest=_optional_text(harness_digest),
        chat_template_digest=_optional_text(chat_template_digest),
        tool_schema_digest=_optional_text(tool_schema_digest),
        memory_scopes=memory_scopes,
        budget=budget,
        evidence_profile=evidence_profile,
        acceptance_criteria=acceptance_criteria,
        constraints=constraints,
        non_goals=non_goals,
        risk_level=risk_level,
        continuation_of_run_id=_optional_text(continuation_of_run_id),
        provenance=with_context_source_client(cast("dict[str, str | None] | None", provenance), ctx),
    )


def record_run_event(
    workspace_key: Annotated[
        str,
        Field(description="Declared workspace scope that owns the run."),
    ],
    run_id: Annotated[
        str,
        Field(description="Server-minted run identifier returned by `begin_run`."),
    ],
    event_type: Annotated[
        Literal[
            "plan_created",
            "work_item_started",
            "checkpoint",
            "observation",
            "hypothesis",
            "hypothesis_confirmed",
            "hypothesis_rejected",
            "tool_result",
            "test_failure",
            "decision",
            "blocker",
            "memory_recalled",
            "memory_applied",
            "memory_rejected",
            "artifact_created",
            "compaction_boundary",
            "work_item_completed",
            "work_item_failed",
            "work_item_abandoned",
            "preflight_review",
            "test_result",
            "risk_identified",
            "information_gap",
            "verification_result",
            "work_item_resumed",
            "blocker_resolved",
        ],
        Field(description="Structured v1 or governed-v2 logical event type."),
    ],
    summary: Annotated[
        str,
        Field(description="Bounded factual event summary. Raw transcript or hidden reasoning is not allowed."),
    ],
    idempotency_key: Annotated[
        str,
        Field(description="Caller-generated retry key scoped to this run; only its SHA-256 digest is stored."),
    ],
    event_schema_version: Annotated[
        int,
        Field(ge=1, le=2, description="Event payload schema version; governed-v2 runs require version 2."),
    ] = 1,
    expected_database_epoch: Annotated[
        str | None,
        Field(description="Database epoch CAS precondition; required by governed-v2 runs."),
    ] = None,
    expected_run_generation: Annotated[
        int | None,
        Field(ge=1, description="Run generation CAS precondition; required by governed-v2 runs."),
    ] = None,
    expected_last_sequence: Annotated[
        int | None,
        Field(
            ge=0,
            description=(
                "Optional compare-and-swap precondition. The append is rejected without writes "
                "when the authority ledger has a different latest sequence."
            ),
        ),
    ] = None,
    expected_work_item_status: Annotated[
        Literal["pending", "active", "blocked", "completed", "failed", "abandoned"] | None,
        Field(
            description=(
                "Optional compare-and-swap precondition for an existing work item. "
                "Conflicts report the actual authority-derived status."
            )
        ),
    ] = None,
    work_item_id: Annotated[
        str | None,
        Field(
            description=(
                "Existing server-minted work-item identifier. Omit only for `work_item_started` "
                "when creating a child work item."
            )
        ),
    ] = None,
    parent_work_item_id: Annotated[
        str | None,
        Field(description="Parent work-item identifier, used only when creating a child work item."),
    ] = None,
    work_item_goal: Annotated[
        str | None,
        Field(description="Goal for a new child work item, used only with `work_item_started`."),
    ] = None,
    owner_agent_id: Annotated[
        str | None,
        Field(description="Optional declared owner for a newly created child work item."),
    ] = None,
    payload: Annotated[
        dict[str, Any] | None,
        Field(description="Optional structured JSON payload, limited to 32 KiB. Raw reasoning fields are rejected."),
    ] = None,
    evidence: Annotated[
        list[Any] | None,
        Field(description="Optional structured evidence references, limited to 32 KiB."),
    ] = None,
    memory_attribution: Annotated[
        dict[str, Any] | None,
        Field(
            description=(
                "Required receipt-bound or explicit manual memory attribution for memory_recalled, "
                "memory_applied, and memory_rejected. Recall receipt tokens are validated but never persisted."
            )
        ),
    ] = None,
    agent_id: Annotated[
        str | None,
        Field(description="Optional declared agent responsible for the event."),
    ] = None,
    thread_id: Annotated[
        str | None,
        Field(description="Optional external thread or conversation identifier for the event."),
    ] = None,
    provenance: Annotated[
        dict[str, str] | None,
        Field(description="Optional bounded declared provenance for the event."),
    ] = None,
    ctx: Context[ServerDependencies] | None = None,
) -> dict[str, Any]:
    """Append one structured event to a run's durable authority ledger.

    Sequence allocation, event persistence, child work-item creation, and projection
    updates occur in one transaction. This tool never sets an implicit current run.
    """
    return _bridge_for(ctx).record_run_event(
        workspace_key=workspace_key,
        run_id=run_id,
        event_type=event_type,
        summary=summary,
        idempotency_key=idempotency_key,
        event_schema_version=event_schema_version,
        expected_database_epoch=_optional_text(expected_database_epoch),
        expected_run_generation=expected_run_generation,
        expected_last_sequence=expected_last_sequence,
        expected_work_item_status=expected_work_item_status,
        work_item_id=_optional_text(work_item_id),
        parent_work_item_id=_optional_text(parent_work_item_id),
        work_item_goal=_optional_text(work_item_goal),
        owner_agent_id=_optional_text(owner_agent_id),
        payload=payload,
        evidence=evidence,
        memory_attribution=memory_attribution,
        agent_id=_optional_text(agent_id),
        thread_id=_optional_text(thread_id),
        provenance=with_context_source_client(cast("dict[str, str | None] | None", provenance), ctx),
    )


def get_run(
    workspace_key: Annotated[
        str,
        Field(description="Declared workspace scope that owns the run."),
    ],
    run_id: Annotated[
        str,
        Field(description="Server-minted run identifier returned by `begin_run`."),
    ],
    since_sequence: Annotated[
        int,
        Field(ge=0, description="Return only events whose per-run sequence is greater than this value."),
    ] = 0,
    event_limit: Annotated[
        int,
        Field(ge=1, le=500, description="Maximum number of ordered events to return in this page."),
    ] = 100,
    ctx: Context[ServerDependencies] | None = None,
) -> dict[str, Any]:
    """Read current run/work-item projections plus append-only events for reconnect or compaction recovery."""
    return _bridge_for(ctx).get_run(
        workspace_key=workspace_key,
        run_id=run_id,
        since_sequence=since_sequence,
        event_limit=event_limit,
    )


def complete_run(
    workspace_key: Annotated[
        str,
        Field(description="Declared workspace scope that owns the run."),
    ],
    run_id: Annotated[
        str,
        Field(description="Server-minted run identifier returned by `begin_run`."),
    ],
    outcome: Annotated[
        Literal[
            "verified_success",
            "partial_success",
            "unverified",
            "user_corrected",
            "regression",
            "failed",
            "abandoned",
        ],
        Field(description="Declared outcome for this append-only outcome revision."),
    ],
    evaluator_type: Annotated[
        Literal["agent", "deterministic_verifier", "human", "system"],
        Field(description="Evidence source class. Agent self-report cannot establish verified success."),
    ],
    idempotency_key: Annotated[
        str,
        Field(description="Caller-generated retry key scoped to this run; only its SHA-256 digest is stored."),
    ],
    evidence: Annotated[
        list[Any] | None,
        Field(description="Structured outcome evidence, limited to 32 KiB."),
    ] = None,
    metrics: Annotated[
        dict[str, Any] | None,
        Field(description="Optional structured outcome metrics, limited to 32 KiB."),
    ] = None,
    evaluator_digest: Annotated[
        str | None,
        Field(description="Optional lowercase SHA-256 digest of the evaluator implementation or configuration."),
    ] = None,
    evaluator_version: Annotated[
        str | None,
        Field(description="Optional bounded evaluator version label."),
    ] = None,
    termination_reason: Annotated[
        str | None,
        Field(description="Optional bounded reason the run ended."),
    ] = None,
    supersedes_outcome_id: Annotated[
        str | None,
        Field(description="Current outcome head to supersede when recording a correction."),
    ] = None,
    regression_of_run_id: Annotated[
        str | None,
        Field(
            description=(
                "Distinct same-workspace run with a current `verified_success` outcome; "
                "required for a `regression` outcome."
            )
        ),
    ] = None,
    verification_receipt_id: Annotated[
        str | None,
        Field(
            description=(
                "Server-minted governed verification receipt. Ordinary MCP callers cannot mint receipts; "
                "required for verified_success on new runs."
            )
        ),
    ] = None,
    expected_database_epoch: Annotated[
        str | None,
        Field(description="Database epoch CAS precondition; required by governed-v2 runs."),
    ] = None,
    expected_run_generation: Annotated[
        int | None,
        Field(ge=1, description="Run generation CAS precondition; required by governed-v2 runs."),
    ] = None,
    expected_last_sequence: Annotated[
        int | None,
        Field(ge=0, description="Current run sequence CAS precondition; required by governed-v2 runs."),
    ] = None,
    provenance: Annotated[
        dict[str, str] | None,
        Field(description="Optional bounded declared provenance for the outcome."),
    ] = None,
    ctx: Context[ServerDependencies] | None = None,
) -> dict[str, Any]:
    """Append or correct a run outcome without changing memory ranking or policy.

    On new runs, `verified_success` requires a matching current server-minted governed receipt.
    Outcome corrections form an append-only supersession chain.
    """
    return _bridge_for(ctx).complete_run(
        workspace_key=workspace_key,
        run_id=run_id,
        outcome=outcome,
        evaluator_type=evaluator_type,
        idempotency_key=idempotency_key,
        evidence=evidence,
        metrics=metrics,
        evaluator_digest=_optional_text(evaluator_digest),
        evaluator_version=_optional_text(evaluator_version),
        termination_reason=_optional_text(termination_reason),
        supersedes_outcome_id=_optional_text(supersedes_outcome_id),
        regression_of_run_id=_optional_text(regression_of_run_id),
        verification_receipt_id=_optional_text(verification_receipt_id),
        expected_database_epoch=_optional_text(expected_database_epoch),
        expected_run_generation=expected_run_generation,
        expected_last_sequence=expected_last_sequence,
        provenance=with_context_source_client(cast("dict[str, str | None] | None", provenance), ctx),
    )


_PUBLIC_TOOL_HANDLERS = (
    store,
    recall,
    browse,
    stats,
    forget,
    feedback,
    promote,
    annotate,
    revise,
    export,
    begin_run,
    record_run_event,
    get_run,
    complete_run,
    claim_signal,
    extend_signal_lease,
    ack_signal,
)


def create_mcp_server(
    *,
    store: MemoryStore | None = None,
    store_factory: BridgeFactory | None = None,
) -> _ContractMCPServer:
    """Build an MCP server whose durable dependency is opened inside lifespan."""

    if store is not None and store_factory is not None:
        raise ValueError("pass either store or store_factory, not both")
    factory = store_factory or _default_bridge_factory

    @asynccontextmanager
    async def lifespan(_: MCPServer) -> AsyncIterator[ServerDependencies]:
        yield ServerDependencies(store=store if store is not None else factory())

    server = _ContractMCPServer(
        name=SERVER_NAME,
        title=SERVER_TITLE,
        description=SERVER_DESCRIPTION,
        version=package_version(),
        log_level="WARNING",
        lifespan=lifespan,
        cache_hints=MCP_CACHE_HINTS,
        middleware=[ProtocolObservabilityMiddleware()],
    )
    for handler in _PUBLIC_TOOL_HANDLERS:
        server.tool(structured_output=True)(handler)
    return server


mcp = create_mcp_server()


def main() -> None:
    mcp.run(transport="stdio")
