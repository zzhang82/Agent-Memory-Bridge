from __future__ import annotations

import re
import sqlite3
from typing import Any

from .repository import MEMORY_ROW_SELECT, MemoryRow, normalize_tags


TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


def recall_candidates(
    store: Any,
    *,
    namespace: str,
    query: str,
    limit: int,
    kind: str | None,
    signal_status: str | None,
    tags_any: list[str] | None,
    session_id: str | None,
    actor: str | None,
    correlation_id: str | None,
    since: str | None,
) -> list[dict[str, Any]]:
    match_query = build_match_query(query)
    if match_query:
        rows = recall_via_fts(
            store,
            namespace=namespace,
            match_query=match_query,
            limit=limit,
            kind=kind,
            signal_status=signal_status,
            tags_any=tags_any,
            session_id=session_id,
            actor=actor,
            correlation_id=correlation_id,
            since=since,
        )
        if rows:
            items = [MemoryRow.from_sqlite(row).as_dict() for row in rows]
            if signal_status is not None:
                items = [item for item in items if item.get("signal_status") == signal_status]
            return items

    if query:
        rows = recall_via_like(
            store,
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
    else:
        rows = recall_via_filters(
            store,
            namespace=namespace,
            limit=limit,
            kind=kind,
            signal_status=signal_status,
            tags_any=tags_any,
            session_id=session_id,
            actor=actor,
            correlation_id=correlation_id,
            since=since,
        )
    items = [MemoryRow.from_sqlite(row).as_dict() for row in rows]
    if signal_status is not None:
        items = [item for item in items if item.get("signal_status") == signal_status]
    return items


def recall_via_fts(
    store: Any,
    *,
    namespace: str,
    match_query: str,
    limit: int,
    kind: str | None,
    signal_status: str | None,
    tags_any: list[str] | None,
    session_id: str | None,
    actor: str | None,
    correlation_id: str | None,
    since: str | None,
) -> list[sqlite3.Row]:
    where_sql, params = build_filters(
        store,
        namespace=namespace,
        kind=kind,
        signal_status=signal_status,
        tags_any=tags_any,
        session_id=session_id,
        actor=actor,
        correlation_id=correlation_id,
        since=since,
        alias="m",
    )
    with store._connect() as conn:
        return conn.execute(
            f"""
            SELECT
                {alias_columns('m')}
            FROM memories m
            JOIN memories_fts f ON f.memory_id = m.id
            WHERE {where_sql} AND memories_fts MATCH ?
            ORDER BY bm25(memories_fts), m.created_at DESC
            LIMIT ?
            """,
            (*params, match_query, limit),
        ).fetchall()


def recall_via_like(
    store: Any,
    *,
    namespace: str,
    query: str,
    limit: int,
    kind: str | None,
    signal_status: str | None,
    tags_any: list[str] | None,
    session_id: str | None,
    actor: str | None,
    correlation_id: str | None,
    since: str | None,
) -> list[sqlite3.Row]:
    where_sql, params = build_filters(
        store,
        namespace=namespace,
        kind=kind,
        signal_status=signal_status,
        tags_any=tags_any,
        session_id=session_id,
        actor=actor,
        correlation_id=correlation_id,
        since=since,
    )
    like_value = f"%{escape_like(query)}%"
    with store._connect() as conn:
        return conn.execute(
            f"""
            SELECT
                {MEMORY_ROW_SELECT}
            FROM memories
            WHERE {where_sql}
            AND (content LIKE ? ESCAPE '\\' OR COALESCE(title, '') LIKE ? ESCAPE '\\')
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (*params, like_value, like_value, limit),
        ).fetchall()


def recall_via_filters(
    store: Any,
    *,
    namespace: str,
    limit: int,
    kind: str | None,
    signal_status: str | None,
    tags_any: list[str] | None,
    session_id: str | None,
    actor: str | None,
    correlation_id: str | None,
    since: str | None,
) -> list[sqlite3.Row]:
    where_sql, params = build_filters(
        store,
        namespace=namespace,
        kind=kind,
        signal_status=signal_status,
        tags_any=tags_any,
        session_id=session_id,
        actor=actor,
        correlation_id=correlation_id,
        since=since,
    )
    with store._connect() as conn:
        return conn.execute(
            f"""
            SELECT
                {MEMORY_ROW_SELECT}
            FROM memories
            WHERE {where_sql}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()


def build_filters(
    store: Any,
    *,
    namespace: str,
    kind: str | None,
    signal_status: str | None,
    tags_any: list[str] | None,
    session_id: str | None,
    actor: str | None,
    correlation_id: str | None,
    since: str | None,
    alias: str | None = None,
) -> tuple[str, list[Any]]:
    del signal_status
    prefix = f"{alias}." if alias else ""
    clauses = [f"{prefix}namespace = ?"]
    params: list[Any] = [namespace]

    if kind is not None:
        clauses.append(f"{prefix}kind = ?")
        params.append(kind)
    if session_id is not None:
        clauses.append(f"{prefix}session_id = ?")
        params.append(session_id)
    if actor is not None:
        clauses.append(f"{prefix}actor = ?")
        params.append(actor)
    if correlation_id is not None:
        clauses.append(f"{prefix}correlation_id = ?")
        params.append(correlation_id)

    tag_filter_sql, tag_params = build_tag_filter(tags_any, prefix=prefix)
    if tag_filter_sql:
        clauses.append(tag_filter_sql)
        params.extend(tag_params)

    if since is not None:
        since_filter_sql, since_params = build_since_filter(store, since, prefix=prefix)
        if since_filter_sql:
            clauses.append(since_filter_sql)
            params.extend(since_params)

    return " AND ".join(clauses), params


def build_tag_filter(tags_any: list[str] | None, prefix: str = "") -> tuple[str, list[str]]:
    if not tags_any:
        return "", []

    normalized = normalize_tags(tags_any)
    if not normalized:
        return "", []

    clauses = [f"{prefix}tags_json LIKE ? ESCAPE '\\'" for _ in normalized]
    params = [f'%"{escape_like(tag)}"%' for tag in normalized]
    return f"({' OR '.join(clauses)})", params


def build_since_filter(store: Any, since_id: str, prefix: str = "") -> tuple[str, list[str]]:
    with store._connect() as conn:
        row = conn.execute("SELECT created_at FROM memories WHERE id = ? LIMIT 1", (since_id,)).fetchone()
    if row is None:
        return "", []
    return f"{prefix}created_at > ?", [row["created_at"]]


def build_match_query(query: str) -> str:
    tokens = TOKEN_RE.findall(query)
    if not tokens:
        return ""
    return " OR ".join(f'"{token}"' for token in tokens)


def escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def alias_columns(alias: str) -> str:
    prefix = f"{alias}."
    columns = [line.strip().rstrip(",") for line in MEMORY_ROW_SELECT.strip().splitlines() if line.strip()]
    return ",\n                ".join(f"{prefix}{column}" for column in columns)
