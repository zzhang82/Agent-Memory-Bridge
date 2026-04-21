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
from .relation_metadata import parse_relation_metadata
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
from .signals import ack_signal_entry, claim_signal_entry, extend_signal_lease_entry, normalize_signal_status_filter
from .telemetry import Telemetry, hash_label


class MemoryStore:
    def __init__(self, db_path: Path, log_dir: Path | None = None, telemetry: Telemetry | None = None) -> None:
        self.db_path = Path(db_path)
        self.log_dir = Path(log_dir) if log_dir is not None else self.db_path.parent / "logs"
        self.telemetry = telemetry or Telemetry.from_env()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @classmethod
    def from_env(cls) -> "MemoryStore":
        return cls(
            db_path=resolve_bridge_db_path(),
            log_dir=resolve_bridge_log_dir(),
            telemetry=Telemetry.from_env(),
        )

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
        source_client: str | None = None,
        source_model: str | None = None,
        client_session_id: str | None = None,
        client_workspace: str | None = None,
        client_transport: str | None = None,
        expires_at: str | None = None,
        ttl_seconds: int | None = None,
    ) -> dict[str, Any]:
        relation_metadata = parse_relation_metadata(content)
        with self.telemetry.span(
            "amb.store.write",
            {
                "namespace": namespace.strip(),
                "kind": kind,
                "tags_count": len(tags or []),
                "has_session_id": bool(session_id),
                "has_actor": bool(actor),
                "has_correlation_id": bool(correlation_id),
                "has_source_app": bool(source_app),
                "has_source_client": bool(source_client),
                "has_source_model": bool(source_model),
                "has_client_session_id": bool(client_session_id),
                "has_client_workspace": bool(client_workspace),
                "client_transport": client_transport,
                "has_expires_at": bool(expires_at),
                "has_ttl_seconds": ttl_seconds is not None,
                "has_relation_metadata": relation_metadata["has_relation_metadata"],
                "has_validity_window": relation_metadata["has_validity_window"],
            },
        ) as span:
            payload = store_entry(
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
                source_client=source_client,
                source_model=source_model,
                client_session_id=client_session_id,
                client_workspace=client_workspace,
                client_transport=client_transport,
                expires_at=expires_at,
                ttl_seconds=ttl_seconds,
            )
            span.set_attributes(
                {
                    "stored": payload.get("stored"),
                    "duplicate": payload.get("duplicate_of") is not None,
                    "signal_status": payload.get("signal_status"),
                }
            )
            return payload

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
        with self.telemetry.span(
            "amb.store.recall",
            {
                "namespace": cleaned_namespace,
                "query_present": bool(query_text),
                "query_length": len(query_text),
                "limit": search_limit,
                "kind": kind,
                "signal_status": normalized_signal_status,
                "tags_count": len(tags_any or []),
                "has_session_id": bool(session_id),
                "has_actor": bool(actor),
                "has_correlation_id": bool(correlation_id),
                "has_since": bool(since),
            },
        ) as span:
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
            span.set_attributes(
                {
                    "result_count": payload["count"],
                    "advanced_since": bool(items) and next_since != since,
                }
            )
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
        with self.telemetry.span(
            "amb.store.browse",
            {
                "namespace": cleaned_namespace,
                "domain": domain.strip() if domain and domain.strip() else None,
                "kind": kind,
                "signal_status": normalized_signal_status,
                "limit": search_limit,
            },
        ) as span:
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
            span.set_attribute("result_count", payload["count"])
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
        with self.telemetry.span(
            "amb.store.forget",
            {
                "has_memory_id": bool(memory_id),
            },
        ) as span:
            payload = forget_entry(self, memory_id)
            span.set_attribute("deleted", payload.get("deleted"))
            return payload

    def stats(self, namespace: str) -> dict[str, Any]:
        with self.telemetry.span(
            "amb.store.stats",
            {
                "namespace": namespace.strip(),
            },
        ) as span:
            payload = stats_for_namespace(self, namespace)
            span.set_attributes(
                {
                    "total_count": payload.get("total_count"),
                    "namespace_kind_count": len(payload.get("kind_counts", {})),
                }
            )
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
        with self.telemetry.span(
            "amb.signal.claim",
            {
                "namespace": namespace.strip(),
                "consumer_hash": hash_label(consumer),
                "lease_seconds": lease_seconds,
                "has_signal_id": bool(signal_id),
                "tags_count": len(tags_any or []),
                "has_correlation_id": bool(correlation_id),
            },
        ) as span:
            payload = claim_signal_entry(
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
            span.set_attributes(
                {
                    "claimed": payload.get("claimed"),
                    "reason": payload.get("reason"),
                    "signal_status": (payload.get("item") or {}).get("signal_status"),
                }
            )
            return payload

    def ack_signal(self, memory_id: str, consumer: str | None = None) -> dict[str, Any]:
        with self.telemetry.span(
            "amb.signal.ack",
            {
                "has_memory_id": bool(memory_id),
                "consumer_hash": hash_label(consumer),
            },
        ) as span:
            payload = ack_signal_entry(
                store=self,
                memory_id=memory_id,
                consumer=consumer,
                fetch_row_by_id=fetch_row_by_id,
                row_to_item=lambda row: MemoryRow.from_sqlite(row).as_dict(),
            )
            span.set_attributes(
                {
                    "acked": payload.get("acked"),
                    "reason": payload.get("reason"),
                    "signal_status": (payload.get("item") or {}).get("signal_status"),
                }
            )
            return payload

    def extend_signal_lease(self, memory_id: str, consumer: str, lease_seconds: int) -> dict[str, Any]:
        with self.telemetry.span(
            "amb.signal.extend",
            {
                "has_memory_id": bool(memory_id),
                "consumer_hash": hash_label(consumer),
                "lease_seconds": lease_seconds,
            },
        ) as span:
            payload = extend_signal_lease_entry(
                store=self,
                memory_id=memory_id,
                consumer=consumer,
                lease_seconds=lease_seconds,
                fetch_row_by_id=fetch_row_by_id,
                row_to_item=lambda row: MemoryRow.from_sqlite(row).as_dict(),
            )
            span.set_attributes(
                {
                    "extended": payload.get("extended"),
                    "reason": payload.get("reason"),
                    "signal_status": (payload.get("item") or {}).get("signal_status"),
                }
            )
            return payload

    def promote(self, memory_id: str, to_kind: str) -> dict[str, Any]:
        with self.telemetry.span(
            "amb.memory.promote",
            {
                "has_memory_id": bool(memory_id),
                "to_kind": to_kind,
            },
        ) as span:
            payload = promote_entry(self, memory_id, to_kind)
            span.set_attributes(
                {
                    "changed": payload.get("changed"),
                    "record_type": payload.get("record_type"),
                }
            )
            return payload

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
        query_text = query.strip()
        export_limit = max(1, min(limit, 500))

        with self.telemetry.span(
            "amb.store.export",
            {
                "namespace": cleaned_namespace,
                "format": export_format,
                "query_present": bool(query_text),
                "query_length": len(query_text),
                "kind": kind,
                "signal_status": normalized_signal_status,
                "tags_count": len(tags_any or []),
                "limit": export_limit,
            },
        ) as span:
            items = recall_candidates(
                self,
                namespace=cleaned_namespace,
                query=query_text,
                limit=export_limit,
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
            span.set_attribute("result_count", len(items))
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
