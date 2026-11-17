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
from .structured_record import parse_structured_content

# A record is superseded when a revision names it as a predecessor, or a current
# supersedes edge targets it. Current means the source is an ordinary memory that
# still exists, is not a hidden learning candidate, and is not itself superseded.
_SUPERSEDED_SQL = """
    (
        EXISTS(SELECT 1 FROM memory_revisions r WHERE r.predecessor_id = memories.id)
        OR EXISTS(
            SELECT 1
            FROM memory_edges e
            JOIN memories source ON source.id = e.source_id
            WHERE e.target_id = memories.id
              AND e.relation = 'supersedes'
              AND e.source_id != e.target_id
              AND source.kind = 'memory'
              AND COALESCE(source.is_learning_candidate, 0) = 0
              AND NOT EXISTS (
                  SELECT 1 FROM memory_revisions r WHERE r.predecessor_id = e.source_id
              )
        )
    )
"""


def _compact(text: str) -> str:
    return " ".join(text.split())


def truncate_on_word(text: str, max_len: int) -> str:
    """Compact whitespace and shorten to ``max_len`` without cutting a word.

    The returned value is at most ``max_len`` characters, and a shortened value
    ends in ``...``.
    """

    compact = _compact(text)
    if len(compact) <= max_len:
        return compact
    if max_len <= 3:
        return compact[:max_len]
    cutoff = max_len - 3
    head = compact[:cutoff]
    # Only backtrack when the cut lands inside a word; a cut that already falls
    # on whitespace keeps the requested width.
    if not compact[cutoff].isspace():
        boundary = head.rfind(" ")
        if boundary > 0:
            head = head[:boundary]
    return f"{head.rstrip()}..."


def display_memory_label(title: str | None, content: str, *, fallback: str = "-") -> str:
    """Choose the human-facing label for a memory record.

    Prefers a real title, then the structured ``claim:``, then the first content
    line that is not a ``record_type:`` marker, and finally ``fallback``.
    """

    if title and title.strip():
        return _compact(title)
    structured = parse_structured_content(content)
    if claim := structured.first("claim"):
        return _compact(claim)
    lines = content.splitlines()
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.lower().startswith("record_type:"):
            return _compact(stripped)
    for line in lines:
        if stripped := line.strip():
            return _compact(stripped)
    return fallback


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
    include_superseded: bool = False,
) -> dict[str, Any]:
    """Retrieve durable memories for listing via CLI.

    Superseded records are excluded by default so ``limit`` counts active
    records, matching ``recall`` and ``compile-index``. Pass
    ``include_superseded=True`` to keep them, annotated with the successor ids.
    """
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

    if not include_superseded:
        clauses.append(f"NOT {_SUPERSEDED_SQL}")

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
        if include_superseded and items:
            _annotate_supersession(conn, items)

    return {
        "count": len(items),
        "namespace": target_namespace,
        "all_namespaces": all_namespaces,
        "kind": kind,
        "query": cleaned_query if cleaned_query else None,
        "limit": limit,
        "include_superseded": include_superseded,
        "items": items,
    }


def _annotate_show_supersession(conn: Any, item: dict[str, Any]) -> None:
    """Attach supersession status, successor ids, and forgotten successors."""

    memory_id = str(item.get("id") or "")
    superseded_by = _successor_ids(conn, [memory_id]).get(memory_id, []) if memory_id else []
    item["superseded"] = bool(superseded_by)
    item["superseded_by"] = superseded_by
    if not superseded_by:
        item["superseded_by_forgotten"] = []
        return
    placeholders = ", ".join("?" for _ in superseded_by)
    forgotten_rows = conn.execute(
        f"SELECT forgotten_id FROM memory_tombstones WHERE forgotten_id IN ({placeholders})",
        superseded_by,
    ).fetchall()
    forgotten = {str(row["forgotten_id"]) for row in forgotten_rows}
    item["superseded_by_forgotten"] = [successor for successor in superseded_by if successor in forgotten]


def _annotate_supersession(conn: Any, items: list[dict[str, Any]]) -> None:
    """Attach successor ids and a superseded flag to each listed item."""

    ids = [str(item["id"]) for item in items if item.get("id")]
    successors = _successor_ids(conn, ids)
    for item in items:
        superseded_by = successors.get(str(item.get("id")), [])
        item["superseded"] = bool(superseded_by)
        item["superseded_by"] = superseded_by


def _successor_ids(conn: Any, memory_ids: list[str]) -> dict[str, list[str]]:
    """Map each id to the deduplicated ids that supersede it.

    Covers both revision successors and sources of a current supersedes edge.
    """

    if not memory_ids:
        return {}
    placeholders = ", ".join("?" for _ in memory_ids)
    successors: dict[str, list[str]] = {memory_id: [] for memory_id in memory_ids}
    revision_rows = conn.execute(
        f"""
        SELECT predecessor_id, successor_id
        FROM memory_revisions
        WHERE predecessor_id IN ({placeholders})
        ORDER BY created_at, revision_id
        """,
        memory_ids,
    ).fetchall()
    edge_rows = conn.execute(
        f"""
        SELECT e.target_id AS predecessor_id, e.source_id AS successor_id
        FROM memory_edges e
        JOIN memories source ON source.id = e.source_id
        WHERE e.target_id IN ({placeholders})
          AND e.relation = 'supersedes'
          AND e.source_id != e.target_id
          AND source.kind = 'memory'
          AND COALESCE(source.is_learning_candidate, 0) = 0
          AND NOT EXISTS (
              SELECT 1 FROM memory_revisions r WHERE r.predecessor_id = e.source_id
          )
        ORDER BY e.position, e.source_id
        """,
        memory_ids,
    ).fetchall()
    for row in (*revision_rows, *edge_rows):
        successors[str(row["predecessor_id"])].append(str(row["successor_id"]))
    return {memory_id: list(dict.fromkeys(values)) for memory_id, values in successors.items()}


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
            _annotate_show_supersession(conn, item)
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
        title = display_memory_label(item.get("title"), str(item.get("content") or ""), fallback="-")
        if item.get("superseded"):
            title = f"[superseded] {title}"
        title = truncate_on_word(title, 50)
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
    status_line = _supersession_status_line(item)
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
        status_line,
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
            if item.get("superseded"):
                lines.append(f"  Note:            {_superseded_note(item)}")
            elif div.get("offer_revise"):
                lines.append(f"  Offer Revise:    {div.get('offer_revise', '')}")
        lines.append("-" * 60)

    if rel_lines:
        lines.append("")
        lines.append("Relations:")
        lines.extend(rel_lines)

    return "\n".join(lines)


def _supersession_status_line(item: dict[str, Any]) -> str:
    if not item.get("superseded"):
        return "Status:       current"
    return f"Status:       superseded (replaced by {_successor_display(item)})"


def _superseded_note(item: dict[str, Any]) -> str:
    return f"Already superseded by {_successor_display(item)}; inspect the successor instead of revising this record."


def _successor_display(item: dict[str, Any]) -> str:
    forgotten = set(item.get("superseded_by_forgotten") or [])
    labels = [
        f"{successor_id} (forgotten)" if successor_id in forgotten else successor_id
        for successor_id in item.get("superseded_by") or []
    ]
    return ", ".join(labels)


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
        include_superseded=bool(getattr(args, "include_superseded", False)),
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


def run_compile_index(args: argparse.Namespace) -> int:
    """CLI runner for compile-index."""
    from .memory_index import (
        build_memory_index_metadata,
        compile_memory_index,
        write_memory_index,
    )
    from .paths import resolve_bridge_db_path

    db_path = resolve_bridge_db_path()
    target_namespace = resolve_default_namespace(getattr(args, "namespace", None))

    if not db_path.is_file():
        if getattr(args, "fail_open", True):
            fallback = (
                f"# Memory Index: `{target_namespace}`\n\n"
                f"(No durable database found at {db_path}; failing open. Use targeted recall when database is initialized.)\n"
            )
            if getattr(args, "out", None):
                write_memory_index(fallback, args.out)
            else:
                sys.stdout.write(fallback)
            return 0
        print(f"agent-memory-bridge: database not found at {db_path}", file=sys.stderr)
        return 1

    store = MemoryStore.from_env()
    requested_max_lines = getattr(args, "max_lines", 200)
    requested_max_bytes = getattr(args, "max_bytes", 25600)
    try:
        content = compile_memory_index(
            store,
            namespace=target_namespace,
            max_lines=requested_max_lines,
            max_bytes=requested_max_bytes,
        )
    except Exception as exc:
        if getattr(args, "fail_open", True):
            fallback = f"# Memory Index: `{target_namespace}`\n\n(Compilation error: {exc}; failing open.)\n"
            sys.stdout.write(fallback)
            return 0
        print(f"agent-memory-bridge: compile-index failed: {exc}", file=sys.stderr)
        return 2

    if getattr(args, "json", False) or getattr(args, "format", "markdown") == "json":
        meta = build_memory_index_metadata(
            content,
            namespace=target_namespace,
            max_lines=requested_max_lines,
            max_bytes=requested_max_bytes,
        )
        print(json.dumps(meta, indent=2, sort_keys=True))
        return 0

    for adjustment in _index_limit_adjustments(requested_max_lines, requested_max_bytes):
        print(f"agent-memory-bridge: note: {adjustment}", file=sys.stderr)

    if getattr(args, "out", None):
        out_file = write_memory_index(content, args.out)
        print(f"Compiled memory index written to {out_file}")
    else:
        sys.stdout.write(content)
    return 0


def _index_limit_adjustments(max_lines: int, max_bytes: int) -> list[str]:
    """Human-readable notes for compile-index limits clamped to the allowed range."""

    from .memory_index import (
        MAX_INDEX_BYTES,
        MAX_INDEX_LINES,
        MIN_INDEX_BYTES,
        MIN_INDEX_LINES,
    )

    adjustments: list[str] = []
    for name, requested, floor, ceiling in (
        ("max_lines", max_lines, MIN_INDEX_LINES, MAX_INDEX_LINES),
        ("max_bytes", max_bytes, MIN_INDEX_BYTES, MAX_INDEX_BYTES),
    ):
        if requested < floor:
            adjustments.append(f"{name} raised from {requested} to minimum {floor}")
        elif requested > ceiling:
            adjustments.append(f"{name} lowered from {requested} to maximum {ceiling}")
    return adjustments


def run_log_audit(args: argparse.Namespace) -> int:
    """CLI runner for audit-logs."""
    from .log_audit import (
        build_log_audit_report,
        render_log_audit_markdown,
        stage_log_audit_report,
    )
    from .paths import resolve_bridge_db_path

    db_path = resolve_bridge_db_path()
    if not db_path.is_file():
        print(f"agent-memory-bridge: database not found at {db_path}", file=sys.stderr)
        return 1

    log_path = Path(args.log_file)
    if not log_path.is_file():
        print(f"agent-memory-bridge: log file not found: {log_path}", file=sys.stderr)
        return 1

    store = MemoryStore.from_env()
    try:
        report = build_log_audit_report(
            log_path,
            store,
            namespace=getattr(args, "namespace", None),
            limit=getattr(args, "limit", 10),
        )
        if getattr(args, "stage", False):
            stage_log_audit_report(store, report)
    except Exception as exc:
        if getattr(args, "json", False) or getattr(args, "format", None) == "json":
            print(json.dumps({"ok": False, "error": str(exc)}))
        else:
            print(f"agent-memory-bridge: log audit failed: {exc}", file=sys.stderr)
        return 2

    if getattr(args, "json", False) or getattr(args, "format", "markdown") == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_log_audit_markdown(report))
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
    include_superseded: bool = False,
) -> dict[str, Any]:
    if all_namespaces:
        raise ValueError(
            "--all across namespaces is only supported with local database; specify --namespace for remote"
        )
    if include_superseded:
        raise ValueError(
            "--include-superseded is only supported with local database; "
            "remote authorities filter default recall without supersession annotations"
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

        if cleaned_query or kind != "signal":
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
                include_superseded=bool(getattr(args, "include_superseded", False)),
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
