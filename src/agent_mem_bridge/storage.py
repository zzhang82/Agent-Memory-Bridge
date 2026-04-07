from __future__ import annotations

import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .exporters import render_export
from .paths import resolve_bridge_db_path, resolve_bridge_log_dir
from .promotion import promote_entry
from .query import build_tag_filter, recall_candidates
from .repository import (
    ALLOWED_KINDS,
    MEMORY_ROW_SELECT,
    MemoryRow,
    fetch_row_by_id,
    forget_entry,
    stats_for_namespace,
    store_entry,
)
from .schema import init_db
from .signals import ack_signal_entry, claim_signal_entry, normalize_signal_status_filter


class MemoryStore:
    def __init__(self, db_path: Path, log_dir: Path | None = None) -> None:
        self.db_path = Path(db_path)
        self.log_dir = Path(log_dir) if log_dir is not None else self.db_path.parent / "logs"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @classmethod
    def from_env(cls) -> "MemoryStore":
        return cls(db_path=resolve_bridge_db_path(), log_dir=resolve_bridge_log_dir())

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
        return store_entry(
            self,
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
        items = recall_candidates(
            self,
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
        items = recall_candidates(
            self,
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
        return forget_entry(self, memory_id)

    def stats(self, namespace: str) -> dict[str, Any]:
        return stats_for_namespace(self, namespace)

    def claim_signal(
        self,
        namespace: str,
        consumer: str,
        lease_seconds: int,
        signal_id: str | None = None,
        tags_any: list[str] | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        return claim_signal_entry(
            store=self,
            namespace=namespace,
            consumer=consumer,
            lease_seconds=lease_seconds,
            signal_id=signal_id,
            tags_any=tags_any,
            correlation_id=correlation_id,
            row_select_sql=MEMORY_ROW_SELECT,
            build_tag_filter=build_tag_filter,
            fetch_row_by_id=fetch_row_by_id,
            row_to_item=lambda row: MemoryRow.from_sqlite(row).as_dict(),
        )

    def ack_signal(self, memory_id: str, consumer: str | None = None) -> dict[str, Any]:
        return ack_signal_entry(
            store=self,
            memory_id=memory_id,
            consumer=consumer,
            fetch_row_by_id=fetch_row_by_id,
            row_to_item=lambda row: MemoryRow.from_sqlite(row).as_dict(),
        )

    def promote(self, memory_id: str, to_kind: str) -> dict[str, Any]:
        return promote_entry(self, memory_id, to_kind)

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

        items = recall_candidates(
            self,
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
        payload = {"namespace": cleaned_namespace, "format": export_format, "count": len(items), "content": rendered}
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

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            init_db(conn)

    def _log(self, event_type: str, payload: dict[str, Any]) -> None:
        log_path = self.log_dir / f"{event_type}.log"
        entry = {"ts": self._utc_now(), **payload}
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=True) + "\n")

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _new_id() -> str:
        return f"{datetime.now(UTC):%Y%m%d%H%M%S%f}-{os.urandom(4).hex()}"
