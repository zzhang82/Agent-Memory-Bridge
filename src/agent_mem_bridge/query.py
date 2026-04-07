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
    candidate_limit = max(limit, min(max(limit * 5, 20), 100))
    match_query = build_match_query(query)
    if match_query:
        rows = recall_via_fts(
            store,
            namespace=namespace,
            match_query=match_query,
            limit=candidate_limit,
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
            return rerank_items(query, items, limit)

    if query:
        rows = recall_via_like(
            store,
            namespace=namespace,
            query=query,
            limit=candidate_limit,
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
    if query:
        return rerank_items(query, items, limit)
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
            ORDER BY bm25(memories_fts), m.created_at ASC
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
            ORDER BY created_at ASC
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


def rerank_items(query: str, items: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    normalized_query = normalize_text(query)
    query_tokens = tokenize(normalized_query)
    if not query_tokens:
        return items[:limit]

    scored: list[tuple[tuple[float, ...], dict[str, Any]]] = []
    for index, item in enumerate(items):
        title = str(item.get("title") or "")
        content = str(item.get("content") or "")
        title_text = normalize_text(title)
        content_text = normalize_text(content)
        title_tokens = tokenize(title_text)
        content_tokens = tokenize(content_text)

        # Prefer clear answer-shaped titles, then use tight content spans as the fallback.
        score = (
            phrase_match_score(normalized_query, title_text) * 50.0
            + phrase_match_score(normalized_query, content_text) * 25.0
            + coverage_score(query_tokens, title_tokens) * 4.0
            + title_precision_score(query_tokens, title_tokens) * 13.0
            + coverage_score(query_tokens, content_tokens) * 1.0
            + ordered_span_score(query_tokens, title_tokens, base=16.0)
            + ordered_span_score(query_tokens, content_tokens, base=28.0)
            - unmatched_title_penalty(query_tokens, title_tokens, factor=0.3)
        )
        title_phrase = phrase_match_score(normalized_query, title_text)
        title_precision = title_precision_score(query_tokens, title_tokens)
        title_coverage = coverage_score(query_tokens, title_tokens)
        scored.append(((score, title_phrase, title_precision, title_coverage, float(-index)), item))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in scored[:limit]]


def phrase_match_score(query_text: str, field_text: str) -> float:
    if not query_text or not field_text:
        return 0.0
    return 1.0 if query_text in field_text else 0.0


def coverage_score(query_tokens: list[str], field_tokens: list[str]) -> float:
    if not query_tokens or not field_tokens:
        return 0.0
    field_set = set(field_tokens)
    return sum(1 for token in query_tokens if token in field_set) / len(query_tokens)


def title_precision_score(query_tokens: list[str], title_tokens: list[str]) -> float:
    if not query_tokens or not title_tokens:
        return 0.0
    query_set = set(query_tokens)
    return sum(1 for token in title_tokens if token in query_set) / len(title_tokens)


def ordered_span_score(query_tokens: list[str], field_tokens: list[str], *, base: float) -> float:
    span = find_ordered_span(query_tokens, field_tokens)
    if span is None:
        return 0.0
    return round(base / span, 6)


def find_ordered_span(query_tokens: list[str], field_tokens: list[str]) -> int | None:
    if not query_tokens or not field_tokens:
        return None
    best_span: int | None = None
    first = query_tokens[0]
    start_positions = [index for index, token in enumerate(field_tokens) if token == first]
    for start in start_positions:
        current = start
        matched = True
        for token in query_tokens[1:]:
            try:
                current = field_tokens.index(token, current + 1)
            except ValueError:
                matched = False
                break
        if not matched:
            continue
        span = current - start + 1
        if best_span is None or span < best_span:
            best_span = span
    return best_span


def unmatched_title_penalty(query_tokens: list[str], title_tokens: list[str], *, factor: float = 0.2) -> float:
    if not title_tokens:
        return 0.0
    query_set = set(query_tokens)
    unmatched = sum(1 for token in title_tokens if token not in query_set)
    return unmatched * factor


def normalize_text(value: str) -> str:
    return " ".join(value.lower().split())


def tokenize(text: str) -> list[str]:
    return TOKEN_RE.findall(text.lower())


def escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def alias_columns(alias: str) -> str:
    prefix = f"{alias}."
    columns = [line.strip().rstrip(",") for line in MEMORY_ROW_SELECT.strip().splitlines() if line.strip()]
    return ",\n                ".join(f"{prefix}{column}" for column in columns)
