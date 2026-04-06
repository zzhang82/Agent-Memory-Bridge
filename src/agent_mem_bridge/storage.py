from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .paths import resolve_bridge_db_path, resolve_bridge_log_dir


TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")
HASHTAG_RE = re.compile(r"(?<!\w)#([A-Za-z][A-Za-z0-9_/-]*)")
WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]")
ALLOWED_KINDS = {"memory", "signal"}


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
            created_at=row["created_at"],
        )

    def as_dict(self) -> dict[str, Any]:
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
            "created_at": self.created_at,
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

        normalized_content = self._normalize_content(cleaned_content)
        payload_tags = self._merge_tags(tags, title=title, content=cleaned_content)
        content_hash = hashlib.sha256(normalized_content.encode("utf-8")).hexdigest()

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
                        content_hash,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            },
        )
        return {
            "id": memory_id,
            "stored": True,
            "duplicate": False,
            "duplicate_of": None,
            "created_at": created_at,
        }

    def recall(
        self,
        namespace: str,
        query: str = "",
        limit: int = 5,
        kind: str | None = None,
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

        items = self._recall_candidates(
            namespace=cleaned_namespace,
            query=query_text,
            limit=search_limit,
            kind=kind,
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
                "since": since,
            },
        )
        return payload

    def browse(
        self,
        namespace: str,
        domain: str | None = None,
        kind: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        cleaned_namespace = namespace.strip()
        if not cleaned_namespace:
            raise ValueError("namespace must not be empty")
        if kind is not None and kind not in ALLOWED_KINDS:
            raise ValueError(f"kind must be one of {sorted(ALLOWED_KINDS)}")

        search_limit = max(1, min(limit, 100))
        tags_any = [f"domain:{domain.strip()}"] if domain and domain.strip() else None
        items = self._recall_candidates(
            namespace=cleaned_namespace,
            query="",
            limit=search_limit,
            kind=kind,
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
        }
        self._log(
            "browse",
            {
                "namespace": cleaned_namespace,
                "count": payload["count"],
                "kind": kind,
                "domain": payload["domain"],
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
                SELECT kind, tags_json, created_at
                FROM memories
                WHERE namespace = ?
                ORDER BY created_at ASC
                """,
                (cleaned_namespace,),
            ).fetchall()

        kind_counts = {kind: 0 for kind in sorted(ALLOWED_KINDS)}
        domain_counts: dict[str, int] = {}
        oldest_entry_at = rows[0]["created_at"] if rows else None
        newest_entry_at = rows[-1]["created_at"] if rows else None

        for row in rows:
            kind = row["kind"]
            kind_counts[kind] = kind_counts.get(kind, 0) + 1
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
            "top_domains": top_domains,
            "oldest_entry_at": oldest_entry_at,
            "newest_entry_at": newest_entry_at,
        }
        self._log("stats", payload)
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
                tags_any=tags_any,
                session_id=session_id,
                actor=actor,
                correlation_id=correlation_id,
                since=since,
            )
            if rows:
                return [MemoryRow.from_sqlite(row).as_dict() for row in rows]

        if query:
            rows = self._recall_via_like(
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
        else:
            rows = self._recall_via_filters(
                namespace=namespace,
                limit=limit,
                kind=kind,
                tags_any=tags_any,
                session_id=session_id,
                actor=actor,
                correlation_id=correlation_id,
                since=since,
            )
        return [MemoryRow.from_sqlite(row).as_dict() for row in rows]

    def _recall_via_fts(
        self,
        namespace: str,
        match_query: str,
        limit: int,
        kind: str | None,
        tags_any: list[str] | None,
        session_id: str | None,
        actor: str | None,
        correlation_id: str | None,
        since: str | None,
    ) -> list[sqlite3.Row]:
        where_sql, params = self._build_filters(
            namespace=namespace,
            kind=kind,
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
        tags_any: list[str] | None,
        session_id: str | None,
        actor: str | None,
        correlation_id: str | None,
        since: str | None,
    ) -> list[sqlite3.Row]:
        where_sql, params = self._build_filters(
            namespace=namespace,
            kind=kind,
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
        tags_any: list[str] | None,
        session_id: str | None,
        actor: str | None,
        correlation_id: str | None,
        since: str | None,
    ) -> list[sqlite3.Row]:
        where_sql, params = self._build_filters(
            namespace=namespace,
            kind=kind,
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
