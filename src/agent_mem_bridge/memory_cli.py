from __future__ import annotations

import argparse
import json
import sys
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
    lines = [
        f"Memory ID:    {item.get('id', '')}",
        f"Namespace:    {item.get('namespace', '')}",
        f"Kind:         {item.get('kind', '')}",
        f"Record Type:  {item.get('record_type') or '-'}",
        f"Title:        {item.get('title') or '-'}",
        f"Created At:   {item.get('created_at', '')}",
        f"Actor:        {item.get('actor') or '-'}",
        f"Recheck:      {item.get('recheck_status') or '-'}",
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
