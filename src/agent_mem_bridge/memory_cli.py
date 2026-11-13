from __future__ import annotations

import argparse
import asyncio
import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from .paths import resolve_repository_snapshot_root
from .project_resolution import namespace_for_host_adapter, resolve_project_context
from .query import _recheck_fields, escape_like
from .repository import (
    MEMORY_ROW_SELECT,
    MemoryRow,
    canonical_namespace,
    fetch_row_by_id,
    fetch_tombstone_metadata,
)
from .storage import MemoryStore


def resolve_default_namespace(explicit_namespace: str | None) -> str:
    """Resolve the default memory namespace from the current bound checkout or project:demo."""
    if explicit_namespace is not None and explicit_namespace.strip():
        return canonical_namespace(explicit_namespace.strip())
    resolved = resolve_project_context(Path.cwd(), snapshot_root=resolve_repository_snapshot_root())
    ns = namespace_for_host_adapter(resolved)
    return ns if ns else "project:demo"


def list_memories(
    store: MemoryStore,
    *,
    namespace: str | None = None,
    all_namespaces: bool = False,
    kind: str | None = None,
    query: str = "",
    limit: int = 20,
) -> dict[str, Any]:
    """Retrieve durable memories for listing via CLI."""
    limit = max(1, min(limit, 1_000))
    cleaned_query = query.strip()
    clauses: list[str] = ["COALESCE(is_learning_candidate, 0) = 0"]
    params: list[Any] = []

    if not all_namespaces:
        target_namespace = resolve_default_namespace(namespace)
        clauses.append("namespace = ?")
        params.append(target_namespace)
    else:
        target_namespace = None

    if kind is not None and kind.strip():
        clauses.append("kind = ?")
        params.append(kind.strip())

    if cleaned_query:
        like_val = f"%{escape_like(cleaned_query)}%"
        clauses.append("(content LIKE ? ESCAPE '\\' OR COALESCE(title, '') LIKE ? ESCAPE '\\')")
        params.extend([like_val, like_val])

    where_sql = " AND ".join(clauses)
    sql = f"""
        SELECT {MEMORY_ROW_SELECT}
        FROM memories
        WHERE {where_sql}
        ORDER BY created_at DESC, rowid DESC
        LIMIT ?
    """
    params.append(limit)

    with store._connect() as conn:
        rows = conn.execute(sql, params).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            memory = MemoryRow.from_sqlite(row)
            item = memory.as_dict()
            item.update(_recheck_fields(memory, conn, database_path=store.db_path))
            items.append(item)

    return {
        "count": len(items),
        "namespace": target_namespace,
        "all_namespaces": all_namespaces,
        "kind": kind,
        "query": cleaned_query if cleaned_query else None,
        "limit": limit,
        "items": items,
    }


def get_memory(store: MemoryStore, memory_id: str) -> dict[str, Any]:
    """Retrieve a single memory record or tombstone metadata."""
    cleaned_id = memory_id.strip()
    if not cleaned_id:
        return {"found": False, "forgotten": False, "item": None, "tombstone": None}

    with store._connect() as conn:
        row = fetch_row_by_id(conn, cleaned_id)
        if row is not None:
            memory = MemoryRow.from_sqlite(row)
            item = memory.as_dict()
            item.update(_recheck_fields(memory, conn, database_path=store.db_path))
            return {"found": True, "forgotten": False, "item": item, "tombstone": None}

        tombstone = fetch_tombstone_metadata(conn, cleaned_id)
        if tombstone is not None:
            return {"found": True, "forgotten": True, "item": None, "tombstone": tombstone}

        return {"found": False, "forgotten": False, "item": None, "tombstone": None}


def forget_memory(store: MemoryStore, memory_id: str, *, reason: str = "cli") -> dict[str, Any]:
    """Forget and tombstone a memory record."""
    cleaned_id = memory_id.strip()
    if not cleaned_id:
        return {"id": "", "deleted": False, "not_found": True}

    with store._connect() as conn:
        tombstone = fetch_tombstone_metadata(conn, cleaned_id)
        if tombstone is not None:
            return {
                "id": cleaned_id,
                "deleted": False,
                "already_forgotten": True,
                "tombstone": tombstone,
            }

    result = store.forget(cleaned_id)
    if not result.get("deleted"):
        return {"id": cleaned_id, "deleted": False, "not_found": True}

    with store._connect() as conn:
        result["tombstone"] = fetch_tombstone_metadata(conn, cleaned_id)

    return result


def revise_memory(
    store: MemoryStore,
    memory_id: str,
    *,
    replacement_content: str,
    title: str | None = None,
    tags: list[str] | None = None,
    actor: str = "operator",
    reason: str = "cli revision",
) -> dict[str, Any]:
    """Revise an existing durable memory with successor content."""
    cleaned_id = memory_id.strip()
    lookup = get_memory(store, cleaned_id)
    if not lookup["found"]:
        return {"revised": False, "not_found": True, "error": f"memory id not found: {cleaned_id}"}
    if lookup["forgotten"]:
        return {
            "revised": False,
            "forgotten": True,
            "error": f"memory id {cleaned_id} has been forgotten and cannot be revised",
        }

    payload = store.revise(
        cleaned_id,
        replacement_content=replacement_content,
        title=title,
        tags=tags,
        actor=actor,
        reason=reason,
    )
    successor_id = payload.get("successor_id")
    if successor_id:
        lookup_successor = get_memory(store, successor_id)
        payload["item"] = lookup_successor.get("item")
    return payload


def render_memory_list(report: dict[str, Any]) -> str:
    """Format memory list report as human-readable text."""
    items = report["items"]
    ns = report["namespace"] if not report["all_namespaces"] else "all namespaces"
    count = report["count"]

    if not items:
        return f"No memories found in {ns}."

    lines: list[str] = [
        f"Memories in {ns} (showing {count}):",
        "",
        f"{'ID':<25}  {'KIND':<8}  {'TYPE':<10}  {'TITLE'}",
        f"{'-' * 25}  {'-' * 8}  {'-' * 10}  {'-' * 40}",
    ]

    for item in items:
        item_id = str(item.get("id", ""))
        kind = str(item.get("kind", ""))
        record_type = str(item.get("record_type") or "-")
        title = str(item.get("title") or item.get("content", "").split("\n")[0])
        if len(title) > 50:
            title = title[:47] + "..."
        lines.append(f"{item_id:<25}  {kind:<8}  {record_type:<10}  {title}")

    return "\n".join(lines)


def render_memory_show(lookup: dict[str, Any]) -> str:
    """Format memory show record as human-readable text."""
    if not lookup["found"]:
        return "Record not found."

    if lookup["forgotten"]:
        tombstone = lookup["tombstone"] or {}
        return "\n".join(
            [
                f"Record {tombstone.get('forgotten_id')} has been forgotten.",
                f"Namespace:    {tombstone.get('namespace', '')}",
                f"Kind:         {tombstone.get('kind', '')}",
                f"Deleted At:   {tombstone.get('deleted_at', '')}",
                f"Cause:        {tombstone.get('cause', '')}",
                f"Root ID:      {tombstone.get('root_forget_id', '')}",
            ]
        )

    item = lookup["item"] or {}
    recheck_status = item.get("recheck_status") or "-"
    recheck_reasons = item.get("recheck_reasons") or []
    recheck_display = f"{recheck_status} ({', '.join(recheck_reasons)})" if recheck_reasons else recheck_status
    lines = [
        f"Memory ID:    {item.get('id', '')}",
        f"Namespace:    {item.get('namespace', '')}",
        f"Kind:         {item.get('kind', '')}",
        f"Record Type:  {item.get('record_type') or '-'}",
        f"Title:        {item.get('title') or '-'}",
        f"Created At:   {item.get('created_at', '')}",
        f"Actor:        {item.get('actor') or '-'}",
        f"Recheck:      {recheck_display}",
        f"Validity:     {item.get('validity_status') or '-'}",
    ]

    tags = item.get("tags") or []
    if tags:
        lines.append(f"Tags:         [{', '.join(tags)}]")

    relations = item.get("relations") or {}
    rel_lines: list[str] = []
    for rel_type in ("supersedes", "supports", "contradicts", "depends_on"):
        targets = relations.get(rel_type) or []
        if targets:
            rel_lines.append(f"  {rel_type}: {', '.join(targets)}")

    lines.append("")
    lines.append("Content:")
    lines.append("-" * 60)
    lines.append(str(item.get("content", "")).rstrip())
    lines.append("-" * 60)

    repo_divergence = item.get("repo_divergence") or []
    if repo_divergence:
        lines.append("")
        lines.append("Live Repo Drift:")
        lines.append("-" * 60)
        for div in repo_divergence:
            lines.append(f"  Path:            {div.get('path', '')}")
            lines.append(f"  Recorded Commit: {div.get('recorded_commit', '')}")
            lines.append(f"  Current Commit:  {div.get('current_commit', '')}")
            if div.get("stored_claim"):
                lines.append(f"  Stored Claim:    {div.get('stored_claim', '')}")
            if div.get("live_content_preview"):
                lines.append(f"  Live State:      {div.get('live_content_preview', '')}")
            if div.get("diff_summary"):
                lines.append(f"  Diff Summary:    {div.get('diff_summary', '')}")
            if div.get("offer_revise"):
                lines.append(f"  Offer Revise:    {div.get('offer_revise', '')}")
        lines.append("-" * 60)

    if rel_lines:
        lines.append("")
        lines.append("Relations:")
        lines.extend(rel_lines)

    return "\n".join(lines)


def render_memory_forget(result: dict[str, Any]) -> str:
    """Format memory forget result as human-readable text."""
    if result.get("already_forgotten"):
        tombstone = result.get("tombstone") or {}
        return (
            f"Memory {result.get('id')} has already been forgotten "
            f"(deleted at {tombstone.get('deleted_at', 'unknown')})."
        )
    if not result.get("deleted"):
        return f"Memory {result.get('id')} not found."

    tombstone = result.get("tombstone") or {}
    return (
        f"Forgotten memory {result.get('id')}\n"
        f"Namespace:  {tombstone.get('namespace', '')}\n"
        f"Deleted At: {tombstone.get('deleted_at', '')}"
    )


def render_memory_revise(result: dict[str, Any]) -> str:
    """Format memory revise result as human-readable text."""
    if not result.get("revised"):
        return f"Revision failed: {result.get('error', 'unknown error')}"

    item = result.get("item") or {}
    pred_id = result.get("predecessor_id") or result.get("target_id")
    succ_id = result.get("successor_id") or result.get("id")
    return (
        f"Revised memory {pred_id}.\n"
        f"Successor ID: {succ_id}\n"
        f"Namespace:    {item.get('namespace', '')}\n"
        f"Title:        {item.get('title') or '-'}"
    )


def run_memory_list(args: argparse.Namespace) -> int:
    """CLI runner for memory list."""
    if getattr(args, "url", None):
        return _run_remote_memory_list(args)
    store = MemoryStore.from_env()
    report = list_memories(
        store,
        namespace=args.namespace,
        all_namespaces=bool(args.all),
        kind=args.kind,
        query=args.query,
        limit=args.limit,
    )
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(render_memory_list(report))
    return 0


def run_memory_show(args: argparse.Namespace) -> int:
    """CLI runner for memory show."""
    if getattr(args, "url", None):
        return _run_remote_memory_show(args)
    store = MemoryStore.from_env()
    lookup = get_memory(store, args.id)
    if args.json:
        print(json.dumps(lookup, indent=2, ensure_ascii=False))
        return 0 if lookup["found"] else 1

    if not lookup["found"]:
        print(f"agent-memory-bridge: record not found: {args.id}", file=sys.stderr)
        return 1

    print(render_memory_show(lookup))
    return 0


def run_memory_forget(args: argparse.Namespace) -> int:
    """CLI runner for memory forget."""
    if getattr(args, "url", None):
        return _run_remote_memory_forget(args)
    store = MemoryStore.from_env()
    lookup = get_memory(store, args.id)
    if not lookup["found"]:
        if args.json:
            print(json.dumps({"id": args.id, "deleted": False, "not_found": True}, indent=2))
        else:
            print(f"agent-memory-bridge: memory not found: {args.id}", file=sys.stderr)
        return 1

    if lookup["forgotten"]:
        result = {
            "id": args.id,
            "deleted": False,
            "already_forgotten": True,
            "tombstone": lookup["tombstone"],
        }
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print(render_memory_forget(result))
        return 0

    if not args.yes:
        item = lookup["item"] or {}
        title = item.get("title") or item.get("content", "").split("\n")[0][:40]
        prompt = f"Forget memory {args.id} ('{title}')? [y/N]: "
        try:
            confirmed = input(prompt).strip().lower() in {"y", "yes"}
        except EOFError:
            confirmed = False
        if not confirmed:
            print("Aborted.")
            return 1

    result = forget_memory(store, args.id, reason=args.reason)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(render_memory_forget(result))
    return 0 if result.get("deleted") else 1


def run_memory_revise(args: argparse.Namespace) -> int:
    """CLI runner for memory revise."""
    if getattr(args, "url", None):
        return _run_remote_memory_revise(args)
    store = MemoryStore.from_env()
    lookup = get_memory(store, args.id)
    if not lookup["found"]:
        if args.json:
            print(json.dumps({"revised": False, "not_found": True, "error": f"memory id not found: {args.id}"}))
        else:
            print(f"agent-memory-bridge: memory not found: {args.id}", file=sys.stderr)
        return 1

    if lookup["forgotten"]:
        if args.json:
            print(
                json.dumps(
                    {
                        "revised": False,
                        "forgotten": True,
                        "error": f"memory id {args.id} has been forgotten and cannot be revised",
                    }
                )
            )
        else:
            print(
                f"agent-memory-bridge: memory {args.id} has been forgotten and cannot be revised",
                file=sys.stderr,
            )
        return 1

    if not args.yes:
        item = lookup["item"] or {}
        title = item.get("title") or item.get("content", "").split("\n")[0][:40]
        prompt = f"Revise memory {args.id} ('{title}') with new content? [y/N]: "
        try:
            confirmed = input(prompt).strip().lower() in {"y", "yes"}
        except EOFError:
            confirmed = False
        if not confirmed:
            print("Aborted.")
            return 1

    try:
        result = revise_memory(
            store,
            args.id,
            replacement_content=args.content,
            title=args.title,
            tags=args.tags,
            actor=args.actor,
            reason=args.reason,
        )
    except ValueError as exc:
        if args.json:
            print(json.dumps({"revised": False, "error": str(exc)}))
        else:
            print(f"agent-memory-bridge: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(render_memory_revise(result))
    return 0 if result.get("revised") else 1


def run_memory_audit(args: argparse.Namespace) -> int:
    """CLI runner for memory audit."""
    from .memory_audit import (
        build_memory_audit_report,
        render_memory_audit_markdown,
        stage_memory_audit_report,
    )
    from .paths import resolve_bridge_db_path

    db_path = resolve_bridge_db_path()
    if not db_path.is_file():
        print(f"agent-memory-bridge: database not found at {db_path}", file=sys.stderr)
        return 1

    store = MemoryStore.from_env()
    try:
        report = build_memory_audit_report(
            store,
            namespace=getattr(args, "namespace", None),
            repository_root=getattr(args, "path", None),
            limit=getattr(args, "limit", 20),
        )
        if getattr(args, "stage", False):
            stage_memory_audit_report(store, report)
    except Exception as exc:
        if getattr(args, "json", False) or getattr(args, "format", None) == "json":
            print(json.dumps({"ok": False, "error": str(exc)}))
        else:
            print(f"agent-memory-bridge: memory audit failed: {exc}", file=sys.stderr)
        return 2

    if getattr(args, "json", False) or getattr(args, "format", "markdown") == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_memory_audit_markdown(report))
    return 0


@asynccontextmanager
async def _remote_mcp_client(
    url: str,
    token_file: Path | None,
    timeout: float = 15.0,
):
    import httpx2
    from mcp.client import Client
    from mcp.client.streamable_http import streamable_http_client

    from .deployment_config import read_token_file
    from .http_probe import validate_http_url

    validate_http_url(url)
    headers: dict[str, str] = {}
    if token_file is not None:
        headers["Authorization"] = f"Bearer {read_token_file(token_file)}"

    async with asyncio.timeout(timeout):
        async with httpx2.AsyncClient(
            headers=headers, timeout=timeout, follow_redirects=False, trust_env=False
        ) as http:
            async with Client(streamable_http_client(url, http_client=http)) as client:
                yield client


async def _remote_call_tool(client: Any, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    result = await client.call_tool(tool_name, arguments)
    if result.is_error:
        error_msg = "Remote tool error"
        for content in getattr(result, "content", []) or []:
            text = getattr(content, "text", None)
            if text:
                error_msg = text
                break
        raise RuntimeError(error_msg)
    if getattr(result, "structured_content", None) is not None and isinstance(result.structured_content, dict):
        return result.structured_content
    for content in getattr(result, "content", []) or []:
        text = getattr(content, "text", None)
        if text:
            try:
                data = json.loads(text)
                if isinstance(data, dict):
                    return data
            except (ValueError, TypeError):
                pass
    return {}


async def _async_memory_list(
    url: str,
    token_file: Path | None,
    *,
    namespace: str | None,
    all_namespaces: bool,
    kind: str | None,
    query: str,
    limit: int,
) -> dict[str, Any]:
    if all_namespaces:
        raise ValueError(
            "--all across namespaces is only supported with local database; specify --namespace for remote"
        )
    target_namespace = resolve_default_namespace(namespace)
    cleaned_query = query.strip()
    async with _remote_mcp_client(url, token_file) as client:
        call_args: dict[str, Any] = {
            "namespace": target_namespace,
            "limit": limit,
        }
        if kind is not None:
            call_args["kind"] = kind

        if cleaned_query:
            call_args["query"] = cleaned_query
            payload = await _remote_call_tool(client, "recall", call_args)
        else:
            payload = await _remote_call_tool(client, "browse", call_args)

        items = payload.get("items", [])
        return {
            "count": len(items),
            "namespace": target_namespace,
            "all_namespaces": False,
            "kind": kind,
            "query": cleaned_query if cleaned_query else None,
            "limit": limit,
            "items": items,
        }


def _run_remote_memory_list(args: argparse.Namespace) -> int:
    try:
        report = asyncio.run(
            _async_memory_list(
                args.url,
                getattr(args, "token_file", None),
                namespace=args.namespace,
                all_namespaces=bool(args.all),
                kind=args.kind,
                query=args.query,
                limit=args.limit,
            )
        )
    except ValueError as exc:
        print(f"agent-memory-bridge: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"agent-memory-bridge: remote list failed: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(render_memory_list(report))
    return 0


async def _async_memory_show(
    url: str,
    token_file: Path | None,
    *,
    memory_id: str,
    namespace: str | None,
) -> dict[str, Any]:
    cleaned_id = memory_id.strip()
    if not cleaned_id:
        return {"found": False, "forgotten": False, "item": None, "tombstone": None}
    target_namespace = resolve_default_namespace(namespace)
    async with _remote_mcp_client(url, token_file) as client:
        payload = await _remote_call_tool(
            client,
            "recall",
            {
                "namespace": target_namespace,
                "query": cleaned_id,
                "limit": 20,
            },
        )
        for item in payload.get("items", []):
            if str(item.get("id")) == cleaned_id:
                return {"found": True, "forgotten": False, "item": item, "tombstone": None}
        browse_payload = await _remote_call_tool(
            client,
            "browse",
            {
                "namespace": target_namespace,
                "limit": 100,
            },
        )
        for item in browse_payload.get("items", []):
            if str(item.get("id")) == cleaned_id:
                return {"found": True, "forgotten": False, "item": item, "tombstone": None}
        return {"found": False, "forgotten": False, "item": None, "tombstone": None}


def _run_remote_memory_show(args: argparse.Namespace) -> int:
    try:
        lookup = asyncio.run(
            _async_memory_show(
                args.url,
                getattr(args, "token_file", None),
                memory_id=args.id,
                namespace=getattr(args, "namespace", None),
            )
        )
    except ValueError as exc:
        print(f"agent-memory-bridge: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"agent-memory-bridge: remote show failed: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(lookup, indent=2, ensure_ascii=False))
        return 0 if lookup["found"] else 1

    if not lookup["found"]:
        print(f"agent-memory-bridge: record not found: {args.id}", file=sys.stderr)
        return 1

    print(render_memory_show(lookup))
    return 0


async def _async_memory_forget(
    url: str,
    token_file: Path | None,
    *,
    memory_id: str,
    reason: str,
) -> dict[str, Any]:
    cleaned_id = memory_id.strip()
    if not cleaned_id:
        return {"id": "", "deleted": False, "not_found": True}
    async with _remote_mcp_client(url, token_file) as client:
        payload = await _remote_call_tool(
            client,
            "forget",
            {"id": cleaned_id},
        )
        if payload.get("deleted"):
            item = payload.get("item") or {}
            return {
                "id": cleaned_id,
                "deleted": True,
                "tombstone": {
                    "namespace": item.get("namespace", ""),
                    "deleted_at": "remote",
                    "cause": reason,
                },
            }
        return {"id": cleaned_id, "deleted": False, "not_found": True}


def _run_remote_memory_forget(args: argparse.Namespace) -> int:
    if not args.yes:
        prompt = f"Forget remote memory {args.id} at {args.url}? [y/N]: "
        try:
            confirmed = input(prompt).strip().lower() in {"y", "yes"}
        except EOFError:
            confirmed = False
        if not confirmed:
            print("Aborted.")
            return 1

    try:
        result = asyncio.run(
            _async_memory_forget(
                args.url,
                getattr(args, "token_file", None),
                memory_id=args.id,
                reason=args.reason,
            )
        )
    except ValueError as exc:
        print(f"agent-memory-bridge: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"agent-memory-bridge: remote forget failed: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(render_memory_forget(result))
    return 0 if result.get("deleted") else 1


async def _async_memory_revise(
    url: str,
    token_file: Path | None,
    *,
    memory_id: str,
    content: str,
    title: str | None,
    tags: list[str] | None,
    reason: str | None,
    actor: str,
) -> dict[str, Any]:
    cleaned_id = memory_id.strip()
    if not cleaned_id:
        return {"revised": False, "error": "id must not be empty"}
    args: dict[str, Any] = {
        "id": cleaned_id,
        "replacement_content": content,
        "actor": actor,
    }
    if title is not None:
        args["title"] = title
    if tags is not None:
        args["tags"] = tags
    if reason is not None:
        args["reason"] = reason

    async with _remote_mcp_client(url, token_file) as client:
        try:
            payload = await _remote_call_tool(client, "revise", args)
            return {
                "revised": True,
                "predecessor_id": cleaned_id,
                "successor_id": payload.get("successor_id"),
                "item": payload.get("item"),
            }
        except Exception as exc:
            return {"revised": False, "error": str(exc)}


def _run_remote_memory_revise(args: argparse.Namespace) -> int:
    if not args.yes:
        prompt = f"Revise remote memory {args.id} at {args.url}? [y/N]: "
        try:
            confirmed = input(prompt).strip().lower() in {"y", "yes"}
        except EOFError:
            confirmed = False
        if not confirmed:
            print("Aborted.")
            return 1

    try:
        result = asyncio.run(
            _async_memory_revise(
                args.url,
                getattr(args, "token_file", None),
                memory_id=args.id,
                content=args.content,
                title=args.title,
                tags=args.tags,
                reason=args.reason,
                actor=args.actor,
            )
        )
    except ValueError as exc:
        if args.json:
            print(json.dumps({"revised": False, "error": str(exc)}))
        else:
            print(f"agent-memory-bridge: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        if args.json:
            print(json.dumps({"revised": False, "error": str(exc)}))
        else:
            print(f"agent-memory-bridge: remote revision failed: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(render_memory_revise(result))
    return 0 if result.get("revised") else 1
