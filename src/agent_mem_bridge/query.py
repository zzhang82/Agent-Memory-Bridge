from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime
from typing import Any

from .embedding_index import (
    EmbeddingProviderError,
    active_embedding_config,
    cosine_similarity,
    embed_texts,
    embedding_tokens,
    load_vector,
)
from .paths import resolve_hybrid_semantic_weight, resolve_retrieval_mode
from .poll_cursor import decode_poll_cursor
from .repository import MEMORY_ROW_SELECT, MemoryRow, memory_row_select, normalize_tags
from .schema import database_epoch as read_database_epoch

LEXICAL_FTS_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


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
    retrieval_mode: str | None = None,
    diagnostics: dict[str, Any] | None = None,
    include_rowid: bool = False,
) -> list[dict[str, Any]]:
    candidate_limit = max(limit, min(max(limit * 5, 20), 100))
    mode = normalize_retrieval_mode(retrieval_mode or resolve_retrieval_mode())
    if query.strip() and mode in {"semantic", "hybrid"}:
        lexical_items = _recall_lexical_candidates(
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
            include_rowid=include_rowid,
        )
        try:
            semantic_items = recall_via_semantic(
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
                include_rowid=include_rowid,
                diagnostics=diagnostics,
            )
        except EmbeddingProviderError as exc:
            if mode == "semantic":
                raise RuntimeError(
                    f"semantic recall failed because the embedding provider was unavailable ({exc.__class__.__name__})"
                ) from exc
            degraded = {
                **(diagnostics or {}),
                "mode": "hybrid",
                "degraded": True,
                "degraded_reason": "embedding-provider-failure",
                "semantic_available": False,
                "semantic_error_type": exc.__class__.__name__,
            }
            if diagnostics is not None:
                diagnostics.clear()
                diagnostics.update(degraded)
            return [
                {
                    **item,
                    "retrieval": {
                        **(item.get("retrieval") or {}),
                        **degraded,
                    },
                }
                for item in lexical_items[:limit]
            ]
        if mode == "semantic":
            if diagnostics is not None:
                diagnostics["mode"] = "semantic"
            return semantic_items[:limit]
        if diagnostics is not None:
            diagnostics["mode"] = "hybrid"
        return hybrid_rerank_items(
            query,
            lexical_items=lexical_items,
            semantic_items=semantic_items,
            limit=limit,
        )

    return _recall_lexical_candidates(
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
        include_rowid=include_rowid,
    )


def recall_signal_poll_page(
    store: Any,
    *,
    namespace: str,
    limit: int,
    signal_status: str | None,
    tags_any: list[str] | None,
    session_id: str | None,
    actor: str | None,
    correlation_id: str | None,
    since: str | None,
) -> tuple[list[dict[str, Any]], str]:
    """Read one Signal polling page and its database epoch from one snapshot."""

    rows, snapshot_epoch = _recall_via_filters_snapshot(
        store,
        namespace=namespace,
        limit=limit,
        kind="signal",
        signal_status=signal_status,
        tags_any=tags_any,
        session_id=session_id,
        actor=actor,
        correlation_id=correlation_id,
        since=since,
    )
    items = [_row_to_item(row, include_rowid=True) for row in rows]
    if signal_status is not None:
        items = [item for item in items if item.get("signal_status") == signal_status]
    return items, snapshot_epoch


def _recall_lexical_candidates(
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
    include_rowid: bool,
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
            items = [_row_to_item(row, include_rowid=include_rowid) for row in rows]
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
    items = [_row_to_item(row, include_rowid=include_rowid) for row in rows]
    if signal_status is not None:
        items = [item for item in items if item.get("signal_status") == signal_status]
    if query:
        return rerank_items(query, items, limit)
    return items


def recall_via_semantic(
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
    include_rowid: bool = False,
    diagnostics: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
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
    try:
        embedding_config = active_embedding_config()
    except ValueError as exc:
        raise EmbeddingProviderError("embedding configuration is invalid") from exc
    with store._connect() as conn:
        stats = conn.execute(
            f"""
            SELECT
                COUNT(*) AS memory_count,
                COALESCE(SUM(
                    CASE
                        WHEN e.memory_id IS NOT NULL
                         AND e.embedding_model = ?
                         AND e.embedding_dim = ?
                         AND e.content_hash = m.content_hash
                        THEN 1 ELSE 0
                    END
                ), 0) AS precomputed_embedding_count,
                COALESCE(SUM(
                    CASE
                        WHEN e.memory_id IS NULL
                          OR e.embedding_model != ?
                          OR e.embedding_dim != ?
                        THEN 1 ELSE 0
                    END
                ), 0) AS missing_embedding_count,
                COALESCE(SUM(
                    CASE
                        WHEN e.memory_id IS NOT NULL
                         AND e.embedding_model = ?
                         AND e.embedding_dim = ?
                         AND e.content_hash != m.content_hash
                        THEN 1 ELSE 0
                    END
                ), 0) AS stale_embedding_count
            FROM memories m
            LEFT JOIN memory_embeddings e ON e.memory_id = m.id
            WHERE {where_sql}
            """,
            (
                embedding_config.model,
                embedding_config.dim,
                embedding_config.model,
                embedding_config.dim,
                embedding_config.model,
                embedding_config.dim,
                *params,
            ),
        ).fetchone()
        refreshed = conn.execute(
            f"""
            SELECT
                {alias_columns("m")},
                m.content_hash,
                e.vector_json
            FROM memories m
            JOIN memory_embeddings e ON e.memory_id = m.id
            WHERE {where_sql}
            AND e.content_hash = m.content_hash
            AND e.embedding_model = ?
            AND e.embedding_dim = ?
            ORDER BY (SELECT sequence FROM memory_insertions WHERE memory_id = m.id) ASC
            """,
            (*params, embedding_config.model, embedding_config.dim),
        ).fetchall()

    memory_count = int(stats["memory_count"] or 0)
    precomputed_count = int(stats["precomputed_embedding_count"] or 0)
    missing_count = int(stats["missing_embedding_count"] or 0)
    stale_count = int(stats["stale_embedding_count"] or 0)
    metadata = semantic_index_metadata(
        model=embedding_config.model,
        dim=embedding_config.dim,
        memory_count=memory_count,
        valid_embedding_count=precomputed_count,
        missing_embedding_count=missing_count,
        stale_embedding_count=stale_count,
        invalid_embedding_count=0,
    )
    if not refreshed:
        if diagnostics is not None:
            diagnostics.update(metadata)
        return []
    if diagnostics is not None:
        diagnostics.update(metadata)

    query_vector = embed_texts([query], config=embedding_config)[0]
    scored: list[tuple[float, str, dict[str, Any]]] = []
    invalid_count = 0
    for row in refreshed:
        vector = load_vector(row["vector_json"])
        if not vector or len(vector) != embedding_config.dim:
            invalid_count += 1
            continue
        score = cosine_similarity(query_vector, vector)
        if score <= 0:
            continue
        item = _row_to_item(row, include_rowid=include_rowid)
        item["retrieval"] = {
            **(item.get("retrieval") or {}),
            "semantic_score": score,
            "semantic_model": embedding_config.model,
            "semantic_scope": "precomputed-valid-only",
        }
        scored.append((score, normalize_text(str(item.get("title") or "")), item))
    metadata = semantic_index_metadata(
        model=embedding_config.model,
        dim=embedding_config.dim,
        memory_count=memory_count,
        valid_embedding_count=max(0, precomputed_count - invalid_count),
        missing_embedding_count=missing_count,
        stale_embedding_count=stale_count,
        invalid_embedding_count=invalid_count,
    )
    if diagnostics is not None:
        diagnostics.update(metadata)
    scored.sort(key=lambda entry: (-entry[0], entry[1]))
    return [item for _, _, item in scored[:limit]]


def semantic_index_metadata(
    *,
    model: str,
    dim: int,
    memory_count: int,
    valid_embedding_count: int,
    missing_embedding_count: int,
    stale_embedding_count: int,
    invalid_embedding_count: int,
) -> dict[str, Any]:
    if memory_count <= 0:
        completeness = "empty"
        completeness_ratio = 1.0
        degraded = False
        degraded_reason = None
    else:
        completeness_ratio = round(valid_embedding_count / memory_count, 6)
        if valid_embedding_count <= 0:
            completeness = "cold"
        elif valid_embedding_count < memory_count:
            completeness = "partial"
        else:
            completeness = "complete"
        degraded = completeness != "complete"
        if not degraded:
            degraded_reason = None
        elif invalid_embedding_count > 0:
            degraded_reason = "semantic-index-invalid"
        elif stale_embedding_count > 0:
            degraded_reason = "semantic-index-stale"
        elif missing_embedding_count > 0:
            degraded_reason = "semantic-index-cold" if valid_embedding_count <= 0 else "semantic-index-incomplete"
        else:
            degraded_reason = "semantic-index-incomplete"
    metadata: dict[str, Any] = {
        "semantic_available": valid_embedding_count > 0,
        "semantic_scope": "precomputed-valid-only",
        "semantic_model": model,
        "semantic_dim": dim,
        "semantic_completeness": completeness,
        "semantic_completeness_ratio": completeness_ratio,
        "semantic_memory_count": memory_count,
        "semantic_valid_embedding_count": valid_embedding_count,
        "semantic_missing_embedding_count": missing_embedding_count,
        "semantic_stale_embedding_count": stale_embedding_count,
        "semantic_invalid_embedding_count": invalid_embedding_count,
        "degraded": degraded,
    }
    if degraded_reason is not None:
        metadata["degraded_reason"] = degraded_reason
    return metadata


def normalize_retrieval_mode(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in {"lexical", "semantic", "hybrid"}:
        return normalized
    return "lexical"


def hybrid_rerank_items(
    query: str,
    *,
    lexical_items: list[dict[str, Any]],
    semantic_items: list[dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    lexical_rank = {str(item.get("id")): index for index, item in enumerate(lexical_items, start=1)}
    semantic_rank = {str(item.get("id")): index for index, item in enumerate(semantic_items, start=1)}
    semantic_scores = {
        str(item.get("id")): float((item.get("retrieval") or {}).get("semantic_score") or 0.0)
        for item in semantic_items
    }
    merged: dict[str, dict[str, Any]] = {}
    for item in [*lexical_items, *semantic_items]:
        item_id = str(item.get("id") or "")
        if not item_id or item_id in merged:
            continue
        merged[item_id] = item

    lexical_scored = rerank_items(query, list(merged.values()), limit=max(limit, len(merged)))
    lexical_score_rank = {str(item.get("id")): index for index, item in enumerate(lexical_scored, start=1)}
    semantic_weight = resolve_hybrid_semantic_weight()
    scored: list[tuple[float, str, dict[str, Any]]] = []
    for item_id, item in merged.items():
        lexical_rrf = 1.0 / (60 + lexical_rank[item_id]) if item_id in lexical_rank else 0.0
        lexical_rerank_rrf = 1.0 / (60 + lexical_score_rank[item_id]) if item_id in lexical_score_rank else 0.0
        semantic_rrf = 1.0 / (60 + semantic_rank[item_id]) if item_id in semantic_rank else 0.0
        semantic_score = semantic_scores.get(item_id, 0.0)
        normalized_semantic_weight = max(0.0, min(semantic_weight / 100.0, 1.0))
        score = (
            lexical_rrf * 0.45
            + lexical_rerank_rrf * 0.35
            + semantic_rrf * (0.20 + normalized_semantic_weight)
            + max(semantic_score, 0.0) * normalized_semantic_weight * 0.02
        )
        item["retrieval"] = {
            **(item.get("retrieval") or {}),
            "mode": "hybrid",
            "hybrid_score": round(score, 6),
            "lexical_rank": lexical_rank.get(item_id),
            "semantic_rank": semantic_rank.get(item_id),
            "semantic_score": semantic_score,
        }
        scored.append((score, normalize_text(str(item.get("title") or "")), item))
    scored.sort(key=lambda entry: (-entry[0], entry[1]))
    return [item for _, _, item in scored[:limit]]


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
                {alias_columns("m")}
            FROM memories m
            JOIN memories_fts f ON f.memory_id = m.id
            WHERE {where_sql} AND memories_fts MATCH ?
            ORDER BY bm25(memories_fts), m.created_at ASC, m.rowid ASC
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
            ORDER BY created_at ASC, rowid ASC
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
    rows, _snapshot_epoch = _recall_via_filters_snapshot(
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
    return rows


def _recall_via_filters_snapshot(
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
) -> tuple[list[sqlite3.Row], str]:
    with store._connect() as conn:
        conn.execute("BEGIN")
        snapshot_epoch = read_database_epoch(conn)
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
            connection=conn,
            current_database_epoch=snapshot_epoch,
        )
        order_sql = (
            "(SELECT sequence FROM memory_insertions WHERE memory_id = memories.id) ASC"
            if since is not None
            else "created_at DESC, rowid DESC"
        )
        rows = conn.execute(
            f"""
            SELECT
                {MEMORY_ROW_SELECT}
            FROM memories
            WHERE {where_sql}
            ORDER BY {order_sql}
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
    return rows, snapshot_epoch


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
    connection: sqlite3.Connection | None = None,
    current_database_epoch: str | None = None,
) -> tuple[str, list[Any]]:
    prefix = f"{alias}." if alias else ""
    clauses = [f"{prefix}namespace = ?"]
    params: list[Any] = [namespace]

    include_learning_candidates = should_include_learning_candidates(tags_any)
    if not include_learning_candidates:
        clauses.append(f"COALESCE({prefix}is_learning_candidate, 0) = 0")

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

    signal_filter_sql, signal_filter_params = build_signal_status_filter(signal_status, prefix=prefix)
    if signal_filter_sql:
        clauses.append(signal_filter_sql)
        params.extend(signal_filter_params)

    tag_filter_sql, tag_params = build_tag_filter(tags_any, prefix=prefix)
    if tag_filter_sql:
        clauses.append(tag_filter_sql)
        params.extend(tag_params)

    if since is not None:
        since_filter_sql, since_params = build_since_filter(
            store,
            since,
            namespace=namespace,
            prefix=prefix,
            connection=connection,
            current_database_epoch=current_database_epoch,
        )
        if since_filter_sql:
            clauses.append(since_filter_sql)
            params.extend(since_params)

    return " AND ".join(clauses), params


def should_include_learning_candidates(tags_any: list[str] | None) -> bool:
    normalized = set(normalize_tags(tags_any or []))
    if "kind:learning-candidate" in normalized:
        return True
    if "kind:learning-review" in normalized:
        return True
    return any(tag.startswith("candidate_status:") for tag in normalized)


def build_signal_status_filter(signal_status: str | None, prefix: str = "") -> tuple[str, list[str]]:
    if signal_status is None:
        return "", []

    now = datetime.now(UTC).isoformat()
    not_acked = f"({prefix}acknowledged_at IS NULL AND COALESCE({prefix}signal_status, '') != 'acked')"
    not_hard_expired = f"({prefix}expires_at IS NULL OR datetime({prefix}expires_at) > datetime(?))"

    if signal_status == "acked":
        return (
            f"({prefix}kind = 'signal' AND ({prefix}acknowledged_at IS NOT NULL OR {prefix}signal_status = 'acked'))",
            [],
        )
    if signal_status == "expired":
        return (
            f"({prefix}kind = 'signal' AND {not_acked} AND "
            f"(({prefix}expires_at IS NOT NULL AND datetime({prefix}expires_at) <= datetime(?)) "
            f"OR {prefix}signal_status = 'expired'))",
            [now],
        )
    if signal_status == "claimed":
        return (
            f"({prefix}kind = 'signal' AND {not_acked} AND {not_hard_expired} AND "
            f"{prefix}signal_status = 'claimed' AND "
            f"({prefix}lease_expires_at IS NULL OR datetime({prefix}lease_expires_at) > datetime(?)))",
            [now, now],
        )
    if signal_status == "pending":
        return (
            f"({prefix}kind = 'signal' AND {not_acked} AND {not_hard_expired} AND "
            f"({prefix}signal_status IS NULL OR {prefix}signal_status = 'pending' OR "
            f"({prefix}signal_status = 'claimed' AND {prefix}lease_expires_at IS NOT NULL "
            f"AND datetime({prefix}lease_expires_at) <= datetime(?))))",
            [now, now],
        )

    return "", []


def build_tag_filter(tags_any: list[str] | None, prefix: str = "") -> tuple[str, list[str]]:
    if not tags_any:
        return "", []

    normalized = normalize_tags(tags_any)
    if not normalized:
        return "", []

    placeholders = ", ".join("?" for _ in normalized)
    return (
        f"EXISTS (SELECT 1 FROM memory_tags mt WHERE mt.memory_id = {prefix}id AND mt.tag IN ({placeholders}))",
        normalized,
    )


def build_since_filter(
    store: Any,
    since_id: str,
    *,
    namespace: str,
    prefix: str = "",
    connection: sqlite3.Connection | None = None,
    current_database_epoch: str | None = None,
) -> tuple[str, list[Any]]:
    opaque = decode_poll_cursor(since_id)
    if opaque is not None:
        if opaque.namespace != namespace:
            raise ValueError("invalid since cursor: namespace mismatch")
        active_epoch = current_database_epoch
        if active_epoch is None:
            active_epoch = read_database_epoch(connection) if connection is not None else store.database_epoch()
        if opaque.database_epoch is not None and opaque.database_epoch != active_epoch:
            raise ValueError("invalid since cursor: database epoch mismatch after restore")
        return (
            f"(SELECT sequence FROM memory_insertions WHERE memory_id = {prefix}id) > ?",
            [opaque.sequence],
        )
    if connection is not None:
        row = connection.execute(
            """
            SELECT i.sequence, m.namespace
            FROM memories m
            JOIN memory_insertions i ON i.memory_id = m.id
            WHERE m.id = ?
            LIMIT 1
            """,
            (since_id,),
        ).fetchone()
    else:
        with store._connect() as conn:
            row = conn.execute(
                """
                SELECT i.sequence, m.namespace
                FROM memories m
                JOIN memory_insertions i ON i.memory_id = m.id
                WHERE m.id = ?
                LIMIT 1
                """,
                (since_id,),
            ).fetchone()
    if row is None:
        raise ValueError(f"invalid since cursor: {since_id}")
    if row["namespace"] != namespace:
        raise ValueError("invalid since cursor: namespace mismatch")
    return (
        f"(SELECT sequence FROM memory_insertions WHERE memory_id = {prefix}id) > ?",
        [row["sequence"]],
    )


def build_match_query(query: str) -> str:
    tokens = LEXICAL_FTS_TOKEN_RE.findall(query)
    if not tokens:
        return ""
    return " OR ".join(f'"{token}"' for token in tokens)


def rerank_items(query: str, items: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    normalized_query = normalize_text(query)
    query_tokens = tokenize(normalized_query)
    if not query_tokens:
        return items[:limit]

    scored: list[tuple[tuple[float, ...], str, dict[str, Any]]] = []
    for item in items:
        title = str(item.get("title") or "")
        content = str(item.get("content") or "")
        title_text = normalize_text(title)
        content_text = normalize_text(content)
        title_tokens = tokenize(title_text)
        content_tokens = tokenize(content_text)
        title_edge = title_edge_match_score(query_tokens, title_tokens) if len(query_tokens) >= 3 else 0.0

        # Prefer clear answer-shaped titles, then use tight content spans as the fallback.
        score = (
            phrase_match_score(normalized_query, title_text) * 50.0
            + phrase_match_score(normalized_query, content_text) * 25.0
            + coverage_score(query_tokens, title_tokens) * 4.0
            + title_precision_score(query_tokens, title_tokens) * 13.0
            + title_edge * 2.0
            + coverage_score(query_tokens, content_tokens) * 1.0
            + ordered_span_score(query_tokens, title_tokens, base=16.0)
            + ordered_span_score(query_tokens, content_tokens, base=28.0)
            - unmatched_title_penalty(query_tokens, title_tokens, factor=0.3)
        )
        title_phrase = phrase_match_score(normalized_query, title_text)
        title_precision = title_precision_score(query_tokens, title_tokens)
        title_coverage = coverage_score(query_tokens, title_tokens)
        scored.append(
            (
                (score, title_phrase, title_precision, title_coverage, title_edge),
                title_text,
                item,
            )
        )

    scored.sort(
        key=lambda pair: (
            -pair[0][0],
            -pair[0][1],
            -pair[0][2],
            -pair[0][3],
            -pair[0][4],
            pair[1],
        )
    )
    return [item for _, _, item in scored[:limit]]


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


def title_edge_match_score(query_tokens: list[str], title_tokens: list[str]) -> float:
    if not query_tokens or not title_tokens:
        return 0.0
    return float(
        max(
            query_prefix_match_length(query_tokens, title_tokens), query_suffix_match_length(query_tokens, title_tokens)
        )
    )


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


def query_prefix_match_length(query_tokens: list[str], title_tokens: list[str]) -> int:
    matched = 0
    for query_token, title_token in zip(query_tokens, title_tokens):
        if query_token != title_token:
            break
        matched += 1
    return matched


def query_suffix_match_length(query_tokens: list[str], title_tokens: list[str]) -> int:
    matched = 0
    for query_token, title_token in zip(reversed(query_tokens), reversed(title_tokens)):
        if query_token != title_token:
            break
        matched += 1
    return matched


def normalize_text(value: str) -> str:
    return " ".join(value.lower().split())


def tokenize(text: str) -> list[str]:
    return embedding_tokens(text)


def escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def alias_columns(alias: str) -> str:
    return memory_row_select(alias)


def _row_to_item(row: sqlite3.Row, *, include_rowid: bool) -> dict[str, Any]:
    item = MemoryRow.from_sqlite(row).as_dict()
    if include_rowid:
        item["_cursor_sequence"] = int(row["_insertion_sequence"])
    return item
