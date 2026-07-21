from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .exporters import render_export
from .filesystem_safety import ensure_private_directory, ensure_private_file
from .learning_candidates import (
    store_learning_candidate as store_learning_candidate_entry,
)
from .learning_candidates import (
    store_learning_review as store_learning_review_entry,
)
from .log_maintenance import rotate_log_if_needed
from .paths import (
    resolve_bridge_db_path,
    resolve_bridge_home,
    resolve_bridge_log_dir,
    resolve_log_backup_count,
    resolve_log_max_bytes,
    resolve_require_claim_before_ack,
)
from .poll_cursor import encode_poll_cursor
from .promotion import promote_entry
from .query import build_tag_filter, recall_candidates, recall_signal_poll_page
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
from .revisions import annotate_entry, revise_entry
from .schema import database_epoch, init_db
from .signals import (
    ack_signal_entry,
    claim_signal_entry,
    extend_signal_lease_entry,
    normalize_signal_status_filter,
    repair_signal_entry,
)
from .telemetry import Telemetry, hash_label


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _optional_list(value: list[str] | None) -> list[str] | None:
    if value is None:
        return None
    cleaned = [str(item).strip() for item in value if str(item).strip()]
    return cleaned or None


class MemoryStore:
    def __init__(self, db_path: Path, log_dir: Path | None = None, telemetry: Telemetry | None = None) -> None:
        self.db_path = Path(db_path)
        self.log_dir = Path(log_dir) if log_dir is not None else self.db_path.parent / "logs"
        self.log_max_bytes = resolve_log_max_bytes()
        self.log_backup_count = resolve_log_backup_count()
        if self.log_max_bytes <= 0 or self.log_backup_count < 0:
            raise ValueError("log rotation limits must use max_bytes > 0 and backup_count >= 0")
        self.telemetry = telemetry or Telemetry.from_env()
        bridge_home = resolve_bridge_home()
        ensure_private_directory(
            self.db_path.parent,
            tighten_existing=self.db_path.parent == bridge_home,
        )
        ensure_private_directory(
            self.log_dir,
            tighten_existing=self.log_dir == bridge_home or self.log_dir.is_relative_to(bridge_home),
        )
        self._init_db()
        ensure_private_file(self.db_path)

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
        kind = kind.strip()
        tags = _optional_list(tags)
        session_id = _optional_text(session_id)
        actor = _optional_text(actor)
        title = _optional_text(title)
        correlation_id = _optional_text(correlation_id)
        source_app = _optional_text(source_app)
        source_client = _optional_text(source_client)
        source_model = _optional_text(source_model)
        client_session_id = _optional_text(client_session_id)
        client_workspace = _optional_text(client_workspace)
        client_transport = _optional_text(client_transport)
        expires_at = _optional_text(expires_at)
        if kind == "memory":
            if ttl_seconds is not None:
                raise ValueError("expires_at and ttl_seconds are only valid for kind='signal'")
            expires_at = None
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
        cleaned_since = _optional_text(since)
        if cleaned_since is not None:
            if query_text:
                raise ValueError("since requires an empty query")
            if kind != "signal":
                raise ValueError("since requires kind='signal'")
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
                "has_since": bool(cleaned_since),
            },
        ) as span:
            retrieval_diagnostics: dict[str, Any] = {}
            is_polling_recall = not query_text and kind == "signal"
            poll_snapshot_epoch: str | None = None
            if is_polling_recall:
                items, poll_snapshot_epoch = recall_signal_poll_page(
                    self,
                    namespace=cleaned_namespace,
                    limit=search_limit,
                    signal_status=normalized_signal_status,
                    tags_any=tags_any,
                    session_id=session_id,
                    actor=actor,
                    correlation_id=correlation_id,
                    since=cleaned_since,
                )
            else:
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
                    since=cleaned_since,
                    diagnostics=retrieval_diagnostics,
                    include_rowid=False,
                )
            next_since = (
                self._poll_cursor(
                    items,
                    current=cleaned_since,
                    database_epoch=poll_snapshot_epoch,
                )
                if is_polling_recall
                else None
            )
            self._strip_internal_fields(items)
            payload = {"count": len(items), "items": items, "next_since": next_since}
            if retrieval_diagnostics:
                payload["retrieval"] = retrieval_diagnostics
            span.set_attributes(
                {
                    "result_count": payload["count"],
                    "advanced_since": bool(items) and next_since != cleaned_since,
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
                    "since": cleaned_since,
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
            self._strip_internal_fields(items)
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
        signal_id = _optional_text(signal_id)
        tags_any = _optional_list(tags_any)
        correlation_id = _optional_text(correlation_id)
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
                require_claim_before_ack=resolve_require_claim_before_ack(),
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

    def repair_signal(self, memory_id: str, *, reason: str, actor: str | None = None) -> dict[str, Any]:
        with self.telemetry.span(
            "amb.signal.repair",
            {"has_memory_id": bool(memory_id), "has_reason": bool(reason), "has_actor": bool(actor)},
        ) as span:
            payload = repair_signal_entry(
                store=self,
                memory_id=memory_id,
                reason=reason,
                actor=actor,
                fetch_row_by_id=fetch_row_by_id,
                row_to_item=lambda row: MemoryRow.from_sqlite(row).as_dict(),
            )
            span.set_attributes({"repaired": payload.get("repaired"), "reason": payload.get("reason")})
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

    def annotate(
        self,
        memory_id: str,
        *,
        tags: list[str] | None = None,
        title: str | None = None,
        provenance: dict[str, str | None] | None = None,
        actor: str | None = None,
    ) -> dict[str, Any]:
        with self.telemetry.span(
            "amb.memory.annotate",
            {
                "has_memory_id": bool(memory_id),
                "tags_count": len(tags or []),
                "has_title": bool(title),
                "provenance_field_count": len(provenance or {}),
            },
        ) as span:
            payload = annotate_entry(
                self,
                memory_id,
                tags=tags,
                title=title,
                provenance=provenance,
                actor=actor,
            )
            span.set_attribute("changed", payload.get("changed"))
            return payload

    def revise(
        self,
        memory_id: str,
        *,
        replacement_content: str,
        title: str | None = None,
        tags: list[str] | None = None,
        actor: str | None = None,
        reason: str | None = None,
        provenance: dict[str, str | None] | None = None,
    ) -> dict[str, Any]:
        with self.telemetry.span(
            "amb.memory.revise",
            {
                "has_memory_id": bool(memory_id),
                "replacement_length": len(replacement_content),
                "tags_count": len(tags or []),
                "has_reason": bool(reason),
            },
        ) as span:
            payload = revise_entry(
                self,
                memory_id,
                replacement_content=replacement_content,
                title=title,
                tags=tags,
                actor=actor,
                reason=reason,
                provenance=provenance,
            )
            span.set_attribute("revised", payload.get("revised"))
            return payload

    def store_learning_candidate(
        self,
        candidate: dict[str, Any],
        decision: dict[str, Any],
        candidate_status: str = "pending",
    ) -> dict[str, Any]:
        with self.telemetry.span(
            "amb.learning_candidate.store",
            {
                "namespace": str(candidate.get("namespace", "")).strip(),
                "candidate_status": candidate_status,
                "decision": str(decision.get("decision", "")).strip(),
            },
        ) as span:
            payload = store_learning_candidate_entry(
                self,
                candidate,
                decision,
                candidate_status=candidate_status,
            )
            span.set_attributes(
                {
                    "stored": payload.get("stored"),
                    "candidate_status": payload.get("candidate_status"),
                    "decision": payload.get("decision"),
                }
            )
            return payload

    def store_learning_review(
        self,
        review: dict[str, Any],
    ) -> dict[str, Any]:
        with self.telemetry.span(
            "amb.learning_review.store",
            {
                "namespace": str(review.get("namespace", "")).strip(),
                "review_decision": str(review.get("review_decision", "")).strip(),
                "has_source_candidate_id": bool(str(review.get("source_candidate_id", "")).strip()),
                "has_target_record_id": bool(str(review.get("target_record_id", "")).strip()),
            },
        ) as span:
            payload = store_learning_review_entry(self, review)
            span.set_attributes(
                {
                    "stored": payload.get("stored"),
                    "candidate_status": payload.get("candidate_status"),
                    "review_decision": payload.get("review_decision"),
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
            self._strip_internal_fields(items)
            rendered = render_export(items, namespace=cleaned_namespace, format=export_format)
            payload = {
                "namespace": cleaned_namespace,
                "format": export_format,
                "count": len(items),
                "content": rendered,
            }
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
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def database_epoch(self) -> str:
        with self._connect() as conn:
            return database_epoch(conn)

    def _init_db(self) -> None:
        with self._connect() as conn:
            init_db(conn)

    def _log(self, event_type: str, payload: dict[str, Any]) -> None:
        log_path = self.log_dir / f"{event_type}.log"
        entry = {"ts": self._utc_now(), **payload}
        try:
            encoded = json.dumps(entry, ensure_ascii=True) + "\n"
            rotate_log_if_needed(
                log_path,
                incoming_bytes=len(encoded.encode("utf-8")),
                max_bytes=self.log_max_bytes,
                backup_count=self.log_backup_count,
            )
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(encoded)
            ensure_private_file(log_path)
        except OSError:
            try:
                print("agent-memory-bridge: operational log write failed", file=sys.stderr)
            except OSError:
                pass

    def _poll_cursor(
        self,
        items: list[dict[str, Any]],
        *,
        current: str | None,
        database_epoch: str | None,
    ) -> str | None:
        if not items:
            return current
        if not database_epoch:
            raise RuntimeError("poll cursor requires the database epoch from the query snapshot")
        latest = max(items, key=lambda item: int(item.get("_cursor_sequence") or 0))
        return encode_poll_cursor(
            namespace=str(latest["namespace"]),
            sequence=int(latest["_cursor_sequence"]),
            database_epoch=database_epoch,
        )

    @staticmethod
    def _strip_internal_fields(items: list[dict[str, Any]]) -> None:
        for item in items:
            item.pop("_cursor_sequence", None)

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _new_id() -> str:
        return f"{datetime.now(UTC):%Y%m%d%H%M%S%f}-{os.urandom(4).hex()}"
