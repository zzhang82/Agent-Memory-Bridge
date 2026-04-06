from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .exporters import render_export
from .paths import resolve_bridge_db_path, resolve_bridge_log_dir
from .signals import (
    SignalSnapshot,
    effective_signal_status,
    is_signal_claimable,
    normalize_signal_status_filter,
    resolve_signal_expiry,
)


TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")
HASHTAG_RE = re.compile(r"(?<!\w)#([A-Za-z][A-Za-z0-9_/-]*)")
WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]")
ALLOWED_KINDS = {"memory", "signal"}
PROMOTABLE_RECORD_TYPES = {"learn", "gotcha", "domain-note"}


@dataclass(slots=True)
class MemoryRow:
    id: str
    namespace: str
    kind: str
    title: str | None
    content: str
    tags: list[str]
    session_id: str | None
    actor: str | None
    correlation_id: str | None
    source_app: str | None
    signal_status: str | None
    claimed_by: str | None
    claimed_at: str | None
    lease_expires_at: str | None
    expires_at: str | None
    acknowledged_at: str | None
    created_at: str

    @classmethod
    def from_sqlite(cls, row: sqlite3.Row) -> "MemoryRow":
        return cls(
            id=row["id"],
            namespace=row["namespace"],
            kind=row["kind"],
            title=row["title"],
            content=row["content"],
            tags=json.loads(row["tags_json"] or "[]"),
            session_id=row["session_id"],
            actor=row["actor"],
            correlation_id=row["correlation_id"],
            source_app=row["source_app"],
            signal_status=row["signal_status"],
            claimed_by=row["claimed_by"],
            claimed_at=row["claimed_at"],
            lease_expires_at=row["lease_expires_at"],
            expires_at=row["expires_at"],
            acknowledged_at=row["acknowledged_at"],
            created_at=row["created_at"],
        )

    def as_dict(self) -> dict[str, Any]:
        signal_status = effective_signal_status(SignalSnapshot.from_row(self.as_sql_row()))
        return {
            "id": self.id,
            "namespace": self.namespace,
            "kind": self.kind,
            "title": self.title,
            "content": self.content,
            "tags": self.tags,
            "session_id": self.session_id,
            "actor": self.actor,
            "correlation_id": self.correlation_id,
            "source_app": self.source_app,
            "signal_status": signal_status,
            "claimed_by": self.claimed_by,
            "claimed_at": self.claimed_at,
            "lease_expires_at": self.lease_expires_at,
            "expires_at": self.expires_at,
            "acknowledged_at": self.acknowledged_at,
            "created_at": self.created_at,
        }

    def as_sql_row(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "signal_status": self.signal_status,
            "claimed_by": self.claimed_by,
            "claimed_at": self.claimed_at,
            "lease_expires_at": self.lease_expires_at,
            "expires_at": self.expires_at,
            "acknowledged_at": self.acknowledged_at,
        }


class MemoryStore:
    def __init__(self, db_path: Path, log_dir: Path | None = None) -> None:
        self.db_path = Path(db_path)
        self.log_dir = Path(log_dir) if log_dir is not None else self.db_path.parent / "logs"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @classmethod
    def from_env(cls) -> "MemoryStore":
        db_path = resolve_bridge_db_path()
        log_dir = resolve_bridge_log_dir()
        return cls(db_path=db_path, log_dir=log_dir)

    def store(
        self,
        namespace: str,
        content: str,
        kind: str = "memory",
        tags: list[str] | None = None,
        session_id: str | None = None,
        actor: str | None = None,
        title: str | None = None,
        correlation_id: str | None = None,
        source_app: str | None = None,
        expires_at: str | None = None,
        ttl_seconds: int | None = None,
    ) -> dict[str, Any]:
        cleaned_namespace = namespace.strip()
        cleaned_content = content.strip()
        cleaned_kind = kind.strip()
        if not cleaned_namespace:
            raise ValueError("namespace must not be empty")
        if not cleaned_content:
            raise ValueError("content must not be empty")
        if not cleaned_kind:
            raise ValueError("kind must not be empty")
        if cleaned_kind not in ALLOWED_KINDS:
            raise ValueError(f"kind must be one of {sorted(ALLOWED_KINDS)}")
        if cleaned_kind != "signal" and (expires_at is not None or ttl_seconds is not None):
            raise ValueError("expires_at and ttl_seconds are only valid for kind='signal'")

        normalized_content = self._normalize_content(cleaned_content)
        payload_tags = self._merge_tags(tags, title=title, content=cleaned_content)
        content_hash = hashlib.sha256(normalized_content.encode("utf-8")).hexdigest()
        resolved_expires_at = resolve_signal_expiry(expires_at=expires_at, ttl_seconds=ttl_seconds)
        signal_status = "pending" if cleaned_kind == "signal" else None

        with self._connect() as conn:
            if cleaned_kind != "signal":
                existing = conn.execute(
                    """
                    SELECT id, created_at
                    FROM memories
                    WHERE namespace = ? AND kind != 'signal' AND content_hash = ?
                    ORDER BY created_at ASC
                    LIMIT 1
                    """,
                    (cleaned_namespace, content_hash),
                ).fetchone()
                if existing is not None:
                    self._log(
                        "store",
                        {
                            "namespace": cleaned_namespace,
                            "kind": cleaned_kind,
                            "stored": False,
                            "duplicate_of": existing["id"],
                        },
                    )
                    return {
                        "id": existing["id"],
                        "stored": False,
                        "duplicate": True,
                        "duplicate_of": existing["id"],
                        "created_at": existing["created_at"],
                    }

            memory_id = self._new_id()
            created_at = self._utc_now()
            try:
                conn.execute(
                    """
                    INSERT INTO memories (
                        id,
                        namespace,
                        kind,
                        title,
                        content,
                        tags_json,
                        session_id,
                        actor,
                        correlation_id,
                        source_app,
                        signal_status,
                        claimed_by,
                        claimed_at,
                        lease_expires_at,
                        expires_at,
                        acknowledged_at,
                        content_hash,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        memory_id,
                        cleaned_namespace,
                        cleaned_kind,
                        title.strip() if title else None,
                        cleaned_content,
                        json.dumps(payload_tags),
                        session_id,
                        actor,
                        correlation_id,
                        source_app,
                        signal_status,
                        None,
                        None,
                        None,
                        resolved_expires_at,
                        None,
                        content_hash,
                        created_at,
                    ),
                )
                conn.execute(
                    "INSERT INTO memories_fts(memory_id, title, content) VALUES (?, ?, ?)",
                    (memory_id, title or "", cleaned_content),
                )
                conn.commit()
            except sqlite3.IntegrityError:
                conn.rollback()
                if cleaned_kind == "signal":
                    raise
                existing = conn.execute(
                    """
                    SELECT id, created_at
                    FROM memories
                    WHERE namespace = ? AND kind != 'signal' AND content_hash = ?
                    ORDER BY created_at ASC
                    LIMIT 1
                    """,
                    (cleaned_namespace, content_hash),
                ).fetchone()
                if existing is None:
                    raise
                self._log(
                    "store",
                    {
                        "namespace": cleaned_namespace,
                        "kind": cleaned_kind,
                        "stored": False,
                        "duplicate_of": existing["id"],
                        "race_recovered": True,
                    },
                )
                return {
                    "id": existing["id"],
                    "stored": False,
                    "duplicate": True,
                    "duplicate_of": existing["id"],
                    "created_at": existing["created_at"],
                }

        self._log(
            "store",
            {
                "namespace": cleaned_namespace,
                "kind": cleaned_kind,
                "stored": True,
                "id": memory_id,
                "signal_status": signal_status,
            },
        )
        return {
            "id": memory_id,
            "stored": True,
            "duplicate": False,
            "duplicate_of": None,
            "signal_status": signal_status,
            "expires_at": resolved_expires_at,
            "created_at": created_at,
        }

    def recall(
        self,
        namespace: str,
        query: str = "",
        limit: int = 5,
        kind: str | None = None,
        signal_status: str | None = None,
        tags_any: list[str] | None = None,
        session_id: str | None = None,
        actor: str | None = None,
        correlation_id: str | None = None,
        since: str | None = None,
    ) -> dict[str, Any]:
        cleaned_namespace = namespace.strip()
        if not cleaned_namespace:
            raise ValueError("namespace must not be empty")

        query_text = query.strip()
        search_limit = max(1, min(limit, 100))
        normalized_signal_status = normalize_signal_status_filter(signal_status)

        items = self._recall_candidates(
            namespace=cleaned_namespace,
            query=query_text,
            limit=search_limit,
            kind=kind,
            signal_status=normalized_signal_status,
            tags_any=tags_any,
            session_id=session_id,
            actor=actor,
            correlation_id=correlation_id,
            since=since,
        )
        next_since = items[-1]["id"] if items else since
        payload = {"count": len(items), "items": items, "next_since": next_since}
        self._log(
            "recall",
            {
                "namespace": cleaned_namespace,
                "query": query_text,
                "count": payload["count"],
                "kind": kind,
                "signal_status": normalized_signal_status,
                "since": since,
            },
        )
        return payload

    def browse(
        self,
        namespace: str,
        domain: str | None = None,
        kind: str | None = None,
        signal_status: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        cleaned_namespace = namespace.strip()
        if not cleaned_namespace:
            raise ValueError("namespace must not be empty")
        if kind is not None and kind not in ALLOWED_KINDS:
            raise ValueError(f"kind must be one of {sorted(ALLOWED_KINDS)}")

        search_limit = max(1, min(limit, 100))
        normalized_signal_status = normalize_signal_status_filter(signal_status)
        tags_any = [f"domain:{domain.strip()}"] if domain and domain.strip() else None
        items = self._recall_candidates(
            namespace=cleaned_namespace,
            query="",
            limit=search_limit,
            kind=kind,
            signal_status=normalized_signal_status,
            tags_any=tags_any,
            session_id=None,
            actor=None,
            correlation_id=None,
            since=None,
        )
        payload = {
            "count": len(items),
            "items": items,
            "namespace": cleaned_namespace,
            "domain": domain.strip() if domain and domain.strip() else None,
            "kind": kind,
            "signal_status": normalized_signal_status,
        }
        self._log(
            "browse",
            {
                "namespace": cleaned_namespace,
                "count": payload["count"],
                "kind": kind,
                "domain": payload["domain"],
                "signal_status": normalized_signal_status,
            },
        )
        return payload

    def forget(self, memory_id: str) -> dict[str, Any]:
        cleaned_id = memory_id.strip()
        if not cleaned_id:
            raise ValueError("id must not be empty")

        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    id,
                    namespace,
                    kind,
                    title,
                    content,
                    tags_json,
                    session_id,
                    actor,
                    correlation_id,
                    source_app,
                    signal_status,
                    claimed_by,
                    claimed_at,
                    lease_expires_at,
                    expires_at,
                    acknowledged_at,
                    created_at
                FROM memories
                WHERE id = ?
                LIMIT 1
                """,
                (cleaned_id,),
            ).fetchone()
            if row is None:
                self._log("forget", {"id": cleaned_id, "deleted": False})
                return {"id": cleaned_id, "deleted": False, "item": None}

            conn.execute("DELETE FROM memories_fts WHERE memory_id = ?", (cleaned_id,))
            conn.execute("DELETE FROM memories WHERE id = ?", (cleaned_id,))
            conn.commit()

        item = MemoryRow.from_sqlite(row).as_dict()
        self._log(
            "forget",
            {
                "id": cleaned_id,
                "deleted": True,
                "namespace": item["namespace"],
                "kind": item["kind"],
            },
        )
        return {"id": cleaned_id, "deleted": True, "item": item}

    def stats(self, namespace: str) -> dict[str, Any]:
        cleaned_namespace = namespace.strip()
        if not cleaned_namespace:
            raise ValueError("namespace must not be empty")

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    kind,
                    tags_json,
                    created_at,
                    signal_status,
                    claimed_by,
                    claimed_at,
                    lease_expires_at,
                    expires_at,
                    acknowledged_at
                FROM memories
                WHERE namespace = ?
                ORDER BY created_at ASC
                """,
                (cleaned_namespace,),
            ).fetchall()

        kind_counts = {kind: 0 for kind in sorted(ALLOWED_KINDS)}
        signal_status_counts = {status: 0 for status in ("pending", "claimed", "acked", "expired")}
        domain_counts: dict[str, int] = {}
        oldest_entry_at = rows[0]["created_at"] if rows else None
        newest_entry_at = rows[-1]["created_at"] if rows else None

        for row in rows:
            kind = row["kind"]
            kind_counts[kind] = kind_counts.get(kind, 0) + 1
            if kind == "signal":
                effective_status = effective_signal_status(SignalSnapshot.from_row(row))
                if effective_status:
                    signal_status_counts[effective_status] = signal_status_counts.get(effective_status, 0) + 1
            for tag in json.loads(row["tags_json"] or "[]"):
                if not isinstance(tag, str) or not tag.startswith("domain:"):
                    continue
                domain = tag.split(":", 1)[1].strip()
                if not domain:
                    continue
                domain_counts[domain] = domain_counts.get(domain, 0) + 1

        top_domains = [
            {"domain": domain, "count": count}
            for domain, count in sorted(domain_counts.items(), key=lambda item: (-item[1], item[0]))[:5]
        ]

        payload = {
            "namespace": cleaned_namespace,
            "total_count": len(rows),
            "kind_counts": kind_counts,
            "signal_status_counts": signal_status_counts,
            "top_domains": top_domains,
            "oldest_entry_at": oldest_entry_at,
            "newest_entry_at": newest_entry_at,
        }
        self._log("stats", payload)
        return payload

    def claim_signal(
        self,
        namespace: str,
        consumer: str,
        lease_seconds: int,
        signal_id: str | None = None,
        tags_any: list[str] | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        cleaned_namespace = namespace.strip()
        cleaned_consumer = consumer.strip()
        cleaned_signal_id = signal_id.strip() if signal_id else None
        if not cleaned_namespace:
            raise ValueError("namespace must not be empty")
        if not cleaned_consumer:
            raise ValueError("consumer must not be empty")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be greater than 0")

        now = datetime.now(UTC)
        claimed_at = now.isoformat()
        lease_expires_at = (now + timedelta(seconds=lease_seconds)).isoformat()

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            candidate = self._select_claimable_signal(
                conn,
                namespace=cleaned_namespace,
                signal_id=cleaned_signal_id,
                tags_any=tags_any,
                correlation_id=correlation_id,
                consumer=cleaned_consumer,
                now=now,
            )
            if candidate is None:
                conn.rollback()
                self._log(
                    "claim_signal",
                    {
                        "namespace": cleaned_namespace,
                        "consumer": cleaned_consumer,
                        "claimed": False,
                        "signal_id": cleaned_signal_id,
                    },
                )
                return {
                    "claimed": False,
                    "signal_id": cleaned_signal_id,
                    "namespace": cleaned_namespace,
                    "consumer": cleaned_consumer,
                    "item": None,
                }

            conn.execute(
                """
                UPDATE memories
                SET signal_status = 'claimed',
                    claimed_by = ?,
                    claimed_at = ?,
                    lease_expires_at = ?,
                    acknowledged_at = NULL
                WHERE id = ?
                """,
                (cleaned_consumer, claimed_at, lease_expires_at, candidate["id"]),
            )
            conn.commit()
            refreshed = self._fetch_row_by_id(conn, candidate["id"])

        item = MemoryRow.from_sqlite(refreshed).as_dict() if refreshed is not None else None
        self._log(
            "claim_signal",
            {
                "namespace": cleaned_namespace,
                "consumer": cleaned_consumer,
                "claimed": True,
                "signal_id": candidate["id"],
                "lease_expires_at": lease_expires_at,
            },
        )
        return {
            "claimed": True,
            "signal_id": candidate["id"],
            "namespace": cleaned_namespace,
            "consumer": cleaned_consumer,
            "lease_expires_at": lease_expires_at,
            "item": item,
        }

    def ack_signal(self, memory_id: str, consumer: str | None = None) -> dict[str, Any]:
        cleaned_id = memory_id.strip()
        cleaned_consumer = consumer.strip() if consumer else None
        if not cleaned_id:
            raise ValueError("id must not be empty")

        now = datetime.now(UTC)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._fetch_row_by_id(conn, cleaned_id)
            if row is None:
                conn.rollback()
                return {"id": cleaned_id, "acked": False, "reason": "missing", "item": None}
            if row["kind"] != "signal":
                conn.rollback()
                raise ValueError("only kind=signal entries can be acknowledged")

            snapshot = SignalSnapshot.from_row(row)
            status = effective_signal_status(snapshot, now=now)
            if status == "expired":
                conn.rollback()
                return {"id": cleaned_id, "acked": False, "reason": "expired", "item": MemoryRow.from_sqlite(row).as_dict()}
            if status == "acked":
                conn.rollback()
                return {"id": cleaned_id, "acked": False, "reason": "already-acked", "item": MemoryRow.from_sqlite(row).as_dict()}
            if cleaned_consumer and snapshot.claimed_by and snapshot.claimed_by != cleaned_consumer and status == "claimed":
                conn.rollback()
                return {"id": cleaned_id, "acked": False, "reason": "claimed-by-other", "item": MemoryRow.from_sqlite(row).as_dict()}

            conn.execute(
                """
                UPDATE memories
                SET signal_status = 'acked',
                    acknowledged_at = ?,
                    lease_expires_at = NULL
                WHERE id = ?
                """,
                (now.isoformat(), cleaned_id),
            )
            conn.commit()
            refreshed = self._fetch_row_by_id(conn, cleaned_id)

        item = MemoryRow.from_sqlite(refreshed).as_dict() if refreshed is not None else None
        self._log(
            "ack_signal",
            {
                "id": cleaned_id,
                "acked": True,
                "consumer": cleaned_consumer,
            },
        )
        return {"id": cleaned_id, "acked": True, "consumer": cleaned_consumer, "item": item}

    def promote(self, memory_id: str, to_kind: str) -> dict[str, Any]:
        cleaned_id = memory_id.strip()
        target_kind = to_kind.strip().lower()
        if not cleaned_id:
            raise ValueError("id must not be empty")
        if target_kind not in PROMOTABLE_RECORD_TYPES:
            raise ValueError(f"to_kind must be one of {sorted(PROMOTABLE_RECORD_TYPES)}")

        with self._connect() as conn:
            row = self._fetch_row_by_id(conn, cleaned_id)
            if row is None:
                raise ValueError(f"memory id not found: {cleaned_id}")
            if row["kind"] != "memory":
                raise ValueError("only kind=memory entries can be promoted")

            source = MemoryRow.from_sqlite(row)
            current_record_type = self._record_type_for_row(source)
            if current_record_type == target_kind:
                self._log("promote", {"id": cleaned_id, "changed": False, "reason": "already-target-kind"})
                return {
                    "id": cleaned_id,
                    "changed": False,
                    "record_type": target_kind,
                    "previous_record_type": current_record_type,
                    "item": source.as_dict(),
                }

            updated_item = self._build_promoted_item(source, target_kind=target_kind, current_record_type=current_record_type)
            content_hash = hashlib.sha256(self._normalize_content(updated_item["content"]).encode("utf-8")).hexdigest()

            duplicate = conn.execute(
                """
                SELECT id
                FROM memories
                WHERE namespace = ? AND kind = 'memory' AND content_hash = ? AND id != ?
                LIMIT 1
                """,
                (source.namespace, content_hash, cleaned_id),
            ).fetchone()
            if duplicate is not None:
                self._log(
                    "promote",
                    {
                        "id": cleaned_id,
                        "changed": False,
                        "duplicate_of": duplicate["id"],
                        "to_kind": target_kind,
                    },
                )
                return {
                    "id": cleaned_id,
                    "changed": False,
                    "record_type": target_kind,
                    "previous_record_type": current_record_type,
                    "duplicate_of": duplicate["id"],
                    "item": source.as_dict(),
                }

            conn.execute(
                """
                UPDATE memories
                SET title = ?, content = ?, tags_json = ?, content_hash = ?
                WHERE id = ?
                """,
                (
                    updated_item["title"],
                    updated_item["content"],
                    json.dumps(updated_item["tags"]),
                    content_hash,
                    cleaned_id,
                ),
            )
            conn.execute("DELETE FROM memories_fts WHERE memory_id = ?", (cleaned_id,))
            conn.execute(
                "INSERT INTO memories_fts(memory_id, title, content) VALUES (?, ?, ?)",
                (cleaned_id, updated_item["title"] or "", updated_item["content"]),
            )
            conn.commit()

            refreshed = self._fetch_row_by_id(conn, cleaned_id)

        promoted = MemoryRow.from_sqlite(refreshed).as_dict() if refreshed is not None else updated_item
        self._log(
            "promote",
            {
                "id": cleaned_id,
                "changed": True,
                "from_kind": current_record_type,
                "to_kind": target_kind,
            },
        )
        return {
            "id": cleaned_id,
            "changed": True,
            "record_type": target_kind,
            "previous_record_type": current_record_type,
            "item": promoted,
        }

    def export(
        self,
        namespace: str,
        format: str = "markdown",
        query: str = "",
        kind: str | None = None,
        signal_status: str | None = None,
        tags_any: list[str] | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        cleaned_namespace = namespace.strip()
        export_format = format.strip().lower()
        if not cleaned_namespace:
            raise ValueError("namespace must not be empty")
        if export_format not in {"markdown", "json", "text"}:
            raise ValueError("format must be one of ['json', 'markdown', 'text']")
        if kind is not None and kind not in ALLOWED_KINDS:
            raise ValueError(f"kind must be one of {sorted(ALLOWED_KINDS)}")
        normalized_signal_status = normalize_signal_status_filter(signal_status)

        items = self._recall_candidates(
            namespace=cleaned_namespace,
            query=query.strip(),
            limit=max(1, min(limit, 500)),
            kind=kind,
            signal_status=normalized_signal_status,
            tags_any=tags_any,
            session_id=None,
            actor=None,
            correlation_id=None,
            since=None,
        )
        rendered = render_export(items, namespace=cleaned_namespace, format=export_format)
        payload = {
            "namespace": cleaned_namespace,
            "format": export_format,
            "count": len(items),
            "content": rendered,
        }
        self._log(
            "export",
            {
                "namespace": cleaned_namespace,
                "format": export_format,
                "count": len(items),
                "kind": kind,
                "signal_status": normalized_signal_status,
            },
        )
        return payload

    def store_memory(self, **kwargs: Any) -> dict[str, Any]:
        return self.store(**kwargs)

    def recall_memory(self, **kwargs: Any) -> dict[str, Any]:
        return self.recall(**kwargs)

    def _recall_candidates(
        self,
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
        match_query = self._build_match_query(query)
        if match_query:
            rows = self._recall_via_fts(
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
            rows = self._recall_via_like(
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
            rows = self._recall_via_filters(
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

    def _recall_via_fts(
        self,
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
        where_sql, params = self._build_filters(
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
        with self._connect() as conn:
            return conn.execute(
                f"""
                SELECT
                    m.id,
                    m.namespace,
                    m.kind,
                    m.title,
                    m.content,
                    m.tags_json,
                    m.session_id,
                    m.actor,
                    m.correlation_id,
                    m.source_app,
                    m.signal_status,
                    m.claimed_by,
                    m.claimed_at,
                    m.lease_expires_at,
                    m.expires_at,
                    m.acknowledged_at,
                    m.created_at
                FROM memories m
                JOIN memories_fts f ON f.memory_id = m.id
                WHERE {where_sql} AND memories_fts MATCH ?
                ORDER BY bm25(memories_fts), m.created_at DESC
                LIMIT ?
                """,
                (*params, match_query, limit),
            ).fetchall()

    def _recall_via_like(
        self,
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
        where_sql, params = self._build_filters(
            namespace=namespace,
            kind=kind,
            signal_status=signal_status,
            tags_any=tags_any,
            session_id=session_id,
            actor=actor,
            correlation_id=correlation_id,
            since=since,
        )
        like_value = f"%{self._escape_like(query)}%"
        with self._connect() as conn:
            return conn.execute(
                f"""
                SELECT
                    id,
                    namespace,
                    kind,
                    title,
                    content,
                    tags_json,
                    session_id,
                    actor,
                    correlation_id,
                    source_app,
                    signal_status,
                    claimed_by,
                    claimed_at,
                    lease_expires_at,
                    expires_at,
                    acknowledged_at,
                    created_at
                FROM memories
                WHERE {where_sql}
                AND (content LIKE ? ESCAPE '\\' OR COALESCE(title, '') LIKE ? ESCAPE '\\')
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (*params, like_value, like_value, limit),
            ).fetchall()

    def _recall_via_filters(
        self,
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
        where_sql, params = self._build_filters(
            namespace=namespace,
            kind=kind,
            signal_status=signal_status,
            tags_any=tags_any,
            session_id=session_id,
            actor=actor,
            correlation_id=correlation_id,
            since=since,
        )
        with self._connect() as conn:
            return conn.execute(
                f"""
                SELECT
                    id,
                    namespace,
                    kind,
                    title,
                    content,
                    tags_json,
                    session_id,
                    actor,
                    correlation_id,
                    source_app,
                    signal_status,
                    claimed_by,
                    claimed_at,
                    lease_expires_at,
                    expires_at,
                    acknowledged_at,
                    created_at
                FROM memories
                WHERE {where_sql}
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (*params, limit),
            ).fetchall()

    def _build_filters(
        self,
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

        tag_filter_sql, tag_params = self._build_tag_filter(tags_any, prefix=prefix)
        if tag_filter_sql:
            clauses.append(tag_filter_sql)
            params.extend(tag_params)

        if since is not None:
            since_filter_sql, since_params = self._build_since_filter(since, prefix=prefix)
            if since_filter_sql:
                clauses.append(since_filter_sql)
                params.extend(since_params)

        return " AND ".join(clauses), params

    def _build_tag_filter(self, tags_any: list[str] | None, prefix: str = "") -> tuple[str, list[str]]:
        if not tags_any:
            return "", []

        normalized = self._normalize_tags(tags_any)
        if not normalized:
            return "", []

        clauses = [f"{prefix}tags_json LIKE ? ESCAPE '\\'" for _ in normalized]
        params = [f'%"{self._escape_like(tag)}"%' for tag in normalized]
        return f"({' OR '.join(clauses)})", params

    def _build_since_filter(self, since_id: str, prefix: str = "") -> tuple[str, list[str]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT created_at FROM memories WHERE id = ? LIMIT 1",
                (since_id,),
            ).fetchone()
        if row is None:
            return "", []
        return f"{prefix}created_at > ?", [row["created_at"]]

    def _build_match_query(self, query: str) -> str:
        tokens = TOKEN_RE.findall(query)
        if not tokens:
            return ""
        return " OR ".join(f'"{token}"' for token in tokens)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _fetch_row_by_id(self, conn: sqlite3.Connection, memory_id: str) -> sqlite3.Row | None:
        return conn.execute(
            """
            SELECT
                id,
                namespace,
                kind,
                title,
                content,
                tags_json,
                session_id,
                actor,
                correlation_id,
                source_app,
                signal_status,
                claimed_by,
                claimed_at,
                lease_expires_at,
                expires_at,
                acknowledged_at,
                created_at
            FROM memories
            WHERE id = ?
            LIMIT 1
            """,
            (memory_id,),
        ).fetchone()

    def _select_claimable_signal(
        self,
        conn: sqlite3.Connection,
        namespace: str,
        signal_id: str | None,
        tags_any: list[str] | None,
        correlation_id: str | None,
        consumer: str,
        now: datetime,
    ) -> sqlite3.Row | None:
        clauses = ["namespace = ?", "kind = 'signal'"]
        params: list[Any] = [namespace]
        if signal_id:
            clauses.append("id = ?")
            params.append(signal_id)
        if correlation_id:
            clauses.append("correlation_id = ?")
            params.append(correlation_id)
        tag_filter_sql, tag_params = self._build_tag_filter(tags_any)
        if tag_filter_sql:
            clauses.append(tag_filter_sql)
            params.extend(tag_params)

        query = f"""
            SELECT
                id,
                namespace,
                kind,
                title,
                content,
                tags_json,
                session_id,
                actor,
                correlation_id,
                source_app,
                signal_status,
                claimed_by,
                claimed_at,
                lease_expires_at,
                expires_at,
                acknowledged_at,
                created_at
            FROM memories
            WHERE {' AND '.join(clauses)}
            ORDER BY created_at ASC
            LIMIT 50
        """
        rows = conn.execute(query, params).fetchall()
        for row in rows:
            if is_signal_claimable(SignalSnapshot.from_row(row), consumer=consumer, now=now):
                return row
        return None

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    namespace TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    title TEXT,
                    content TEXT NOT NULL,
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    session_id TEXT,
                    actor TEXT,
                    correlation_id TEXT,
                    source_app TEXT,
                    signal_status TEXT,
                    claimed_by TEXT,
                    claimed_at TEXT,
                    lease_expires_at TEXT,
                    expires_at TEXT,
                    acknowledged_at TEXT,
                    content_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            self._ensure_column(conn, "memories", "title", "ALTER TABLE memories ADD COLUMN title TEXT")
            self._ensure_column(conn, "memories", "actor", "ALTER TABLE memories ADD COLUMN actor TEXT")
            self._ensure_column(
                conn,
                "memories",
                "correlation_id",
                "ALTER TABLE memories ADD COLUMN correlation_id TEXT",
            )
            self._ensure_column(conn, "memories", "signal_status", "ALTER TABLE memories ADD COLUMN signal_status TEXT")
            self._ensure_column(conn, "memories", "claimed_by", "ALTER TABLE memories ADD COLUMN claimed_by TEXT")
            self._ensure_column(conn, "memories", "claimed_at", "ALTER TABLE memories ADD COLUMN claimed_at TEXT")
            self._ensure_column(
                conn,
                "memories",
                "lease_expires_at",
                "ALTER TABLE memories ADD COLUMN lease_expires_at TEXT",
            )
            self._ensure_column(conn, "memories", "expires_at", "ALTER TABLE memories ADD COLUMN expires_at TEXT")
            self._ensure_column(
                conn,
                "memories",
                "acknowledged_at",
                "ALTER TABLE memories ADD COLUMN acknowledged_at TEXT",
            )
            conn.executescript(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_memories_dedup
                ON memories (namespace, content_hash)
                WHERE kind != 'signal';

                CREATE INDEX IF NOT EXISTS idx_memories_namespace_created_at
                ON memories (namespace, created_at DESC);

                CREATE INDEX IF NOT EXISTS idx_memories_session_id_created_at
                ON memories (session_id, created_at DESC);

                CREATE INDEX IF NOT EXISTS idx_memories_kind_namespace_created_at
                ON memories (kind, namespace, created_at DESC);

                CREATE INDEX IF NOT EXISTS idx_memories_actor_created_at
                ON memories (actor, created_at DESC);

                CREATE INDEX IF NOT EXISTS idx_memories_correlation_id_created_at
                ON memories (correlation_id, created_at DESC);

                CREATE INDEX IF NOT EXISTS idx_memories_signal_status_created_at
                ON memories (namespace, signal_status, created_at DESC);

                CREATE INDEX IF NOT EXISTS idx_memories_signal_claimed_by_created_at
                ON memories (claimed_by, created_at DESC);
                """
            )
            self._ensure_fts_columns(conn)
            conn.commit()

    def _ensure_column(self, conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
        columns = {
            row["name"]
            for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            conn.execute(ddl)

    def _ensure_fts_columns(self, conn: sqlite3.Connection) -> None:
        columns = [row["name"] for row in conn.execute("PRAGMA table_info(memories_fts)").fetchall()]
        if "title" in columns:
            return

        existing_rows = conn.execute(
            """
            SELECT id, COALESCE(title, '') AS title, content
            FROM memories
            ORDER BY created_at ASC
            """
        ).fetchall()
        conn.execute("DROP TABLE IF EXISTS memories_fts")
        conn.execute("CREATE VIRTUAL TABLE memories_fts USING fts5(memory_id UNINDEXED, title, content)")
        for row in existing_rows:
            conn.execute(
                "INSERT INTO memories_fts(memory_id, title, content) VALUES (?, ?, ?)",
                (row["id"], row["title"], row["content"]),
            )

    def _log(self, event_type: str, payload: dict[str, Any]) -> None:
        log_path = self.log_dir / f"{event_type}.log"
        entry = {"ts": self._utc_now(), **payload}
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=True) + "\n")

    @staticmethod
    def _normalize_content(content: str) -> str:
        return " ".join(content.split())

    @staticmethod
    def _normalize_tags(tags: list[str] | None) -> list[str]:
        if not tags:
            return []
        cleaned: list[str] = []
        seen: set[str] = set()
        for tag in tags:
            normalized = tag.strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            cleaned.append(normalized)
        return cleaned

    @classmethod
    def _merge_tags(cls, tags: list[str] | None, title: str | None, content: str) -> list[str]:
        explicit = cls._normalize_tags(tags)
        extracted = cls._extract_obsidian_tags(title=title, content=content)
        return cls._normalize_tags([*explicit, *extracted])

    @classmethod
    def _extract_obsidian_tags(cls, title: str | None, content: str) -> list[str]:
        text = "\n".join(part for part in [title or "", content] if part)
        extracted: list[str] = []

        for match in HASHTAG_RE.findall(text):
            extracted.append(f"tag:{match}")

        for raw_link in WIKILINK_RE.findall(text):
            note_name = cls._normalize_wikilink_target(raw_link)
            if note_name:
                extracted.append(f"link:{note_name}")

        return extracted

    @staticmethod
    def _normalize_wikilink_target(raw_link: str) -> str:
        normalized = " ".join(raw_link.split()).strip()
        return normalized

    @staticmethod
    def _parse_structured_record(content: str) -> dict[str, str]:
        fields: dict[str, str] = {}
        for line in str(content).splitlines():
            compact = " ".join(line.split()).strip()
            if not compact or ":" not in compact:
                continue
            key, _, value = compact.partition(":")
            key = key.strip()
            value = value.strip()
            if not key or not value:
                continue
            fields[key] = value
        return fields

    @staticmethod
    def _build_structured_record(fields: dict[str, str]) -> str:
        lines: list[str] = []
        for key, value in fields.items():
            normalized = " ".join(str(value).split()).strip()
            if not normalized:
                continue
            lines.append(f"{key}: {normalized}")
        return "\n".join(lines)

    @staticmethod
    def _truncate_title(text: str, limit: int = 72) -> str:
        compact = " ".join(text.split()).strip()
        if len(compact) <= limit:
            return compact
        return compact[: limit - 3].rstrip() + "..."

    @classmethod
    def _record_type_for_row(cls, row: MemoryRow) -> str:
        for tag in row.tags:
            if tag in {"kind:learn", "kind:gotcha", "kind:domain-note"}:
                return tag.split(":", 1)[1]
        return cls._parse_structured_record(row.content).get("record_type", "memory")

    @classmethod
    def _build_promoted_item(
        cls,
        row: MemoryRow,
        target_kind: str,
        current_record_type: str,
    ) -> dict[str, Any]:
        fields = cls._parse_structured_record(row.content)
        claim = fields.get("claim") or row.title or row.content
        claim = " ".join(str(claim).split()).strip(" -:;,.")
        if claim and claim[-1] not in ".!?":
            claim += "."

        domain_tags = [tag for tag in row.tags if tag.startswith("domain:")]
        topic_tags = [tag for tag in row.tags if tag.startswith("topic:")]
        base_fields: dict[str, str] = {}
        for key in ("scope", "trigger", "symptom", "fix", "confidence", "signals", "domain"):
            value = fields.get(key)
            if value:
                base_fields[key] = value

        if target_kind == "learn":
            promoted_fields = {
                "record_type": "learn",
                "claim": claim,
                "scope": base_fields.get("scope", "global"),
                "confidence": "manual",
                "domains": " | ".join(domain_tags),
                "topics": " | ".join(topic_tags),
            }
            title = f"[[Learn]] {cls._truncate_title(claim)}"
        elif target_kind == "gotcha":
            promoted_fields = {
                "record_type": "gotcha",
                "claim": claim,
                "trigger": base_fields.get("trigger", ""),
                "symptom": base_fields.get("symptom", claim),
                "fix": base_fields.get("fix", ""),
                "scope": base_fields.get("scope", "global"),
                "confidence": "manual",
            }
            title = f"[[Gotcha]] {cls._truncate_title(claim)}"
        else:
            primary_domain = base_fields.get("domain") or (domain_tags[0] if domain_tags else "domain:general")
            promoted_fields = {
                "record_type": "domain-note",
                "domain": primary_domain,
                "claim": claim,
                "scope": base_fields.get("scope", "global"),
                "signals": base_fields.get("signals", ""),
            }
            title = f"[[Domain Note]] {cls._truncate_title(claim)}"

        tags = [
            tag
            for tag in row.tags
            if not tag.startswith("kind:") and not tag.startswith("confidence:") and not tag.startswith("promoted-from:")
        ]
        tags.extend([f"kind:{target_kind}", "confidence:manual"])
        if current_record_type in PROMOTABLE_RECORD_TYPES:
            tags.append(f"promoted-from:{current_record_type}")
        return {
            "id": row.id,
            "namespace": row.namespace,
            "kind": row.kind,
            "title": title,
            "content": cls._build_structured_record(promoted_fields),
            "tags": cls._normalize_tags(tags),
            "session_id": row.session_id,
            "actor": row.actor,
            "correlation_id": row.correlation_id,
            "source_app": row.source_app,
            "created_at": row.created_at,
        }

    @staticmethod
    def _escape_like(value: str) -> str:
        return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _new_id() -> str:
        timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")
        digest = hashlib.sha1(os.urandom(16), usedforsecurity=False).hexdigest()[:8]
        return f"{timestamp}-{digest}"
