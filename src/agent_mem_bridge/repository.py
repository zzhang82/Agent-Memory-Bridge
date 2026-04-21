from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from typing import Any

from .relation_metadata import extract_relation_tags, parse_relation_metadata
from .signals import SignalSnapshot, effective_signal_status, resolve_signal_expiry


HASHTAG_RE = re.compile(r"(?<!\w)#([A-Za-z][A-Za-z0-9_/-]*)")
WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]")
ALLOWED_KINDS = {"memory", "signal"}
MEMORY_ROW_SELECT = """
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
source_client,
source_model,
client_session_id,
client_workspace,
client_transport,
signal_status,
claimed_by,
claimed_at,
lease_expires_at,
expires_at,
acknowledged_at,
created_at
"""


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
    source_client: str | None
    source_model: str | None
    client_session_id: str | None
    client_workspace: str | None
    client_transport: str | None
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
            source_client=row["source_client"],
            source_model=row["source_model"],
            client_session_id=row["client_session_id"],
            client_workspace=row["client_workspace"],
            client_transport=row["client_transport"],
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
        relation_metadata = parse_relation_metadata(self.content)
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
            "source_client": self.source_client,
            "source_model": self.source_model,
            "client_session_id": self.client_session_id,
            "client_workspace": self.client_workspace,
            "client_transport": self.client_transport,
            "signal_status": signal_status,
            "claimed_by": self.claimed_by,
            "claimed_at": self.claimed_at,
            "lease_expires_at": self.lease_expires_at,
            "expires_at": self.expires_at,
            "acknowledged_at": self.acknowledged_at,
            "created_at": self.created_at,
            "relations": relation_metadata["relations"],
            "valid_from": relation_metadata["valid_from"],
            "valid_until": relation_metadata["valid_until"],
            "validity_status": relation_metadata["validity_status"],
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


def store_entry(
    store: Any,
    *,
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

    normalized_content = normalize_content(cleaned_content)
    payload_tags = merge_tags(tags, title=title, content=cleaned_content)
    content_hash = hashlib.sha256(normalized_content.encode("utf-8")).hexdigest()
    resolved_expires_at = resolve_signal_expiry(expires_at=expires_at, ttl_seconds=ttl_seconds)
    signal_status = "pending" if cleaned_kind == "signal" else None

    with store._connect() as conn:
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
                store._log(
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

        memory_id = store._new_id()
        created_at = store._utc_now()
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
                    source_client,
                    source_model,
                    client_session_id,
                    client_workspace,
                    client_transport,
                    signal_status,
                    claimed_by,
                    claimed_at,
                    lease_expires_at,
                    expires_at,
                    acknowledged_at,
                    content_hash,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    source_client,
                    source_model,
                    client_session_id,
                    client_workspace,
                    client_transport,
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
            store._log(
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

    store._log(
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


def forget_entry(store: Any, memory_id: str) -> dict[str, Any]:
    cleaned_id = memory_id.strip()
    if not cleaned_id:
        raise ValueError("id must not be empty")

    with store._connect() as conn:
        row = fetch_row_by_id(conn, cleaned_id)
        if row is None:
            store._log("forget", {"id": cleaned_id, "deleted": False})
            return {"id": cleaned_id, "deleted": False, "item": None}

        conn.execute("DELETE FROM memories_fts WHERE memory_id = ?", (cleaned_id,))
        conn.execute("DELETE FROM memories WHERE id = ?", (cleaned_id,))
        conn.commit()

    item = MemoryRow.from_sqlite(row).as_dict()
    store._log("forget", {"id": cleaned_id, "deleted": True, "namespace": item["namespace"], "kind": item["kind"]})
    return {"id": cleaned_id, "deleted": True, "item": item}


def stats_for_namespace(store: Any, namespace: str) -> dict[str, Any]:
    cleaned_namespace = namespace.strip()
    if not cleaned_namespace:
        raise ValueError("namespace must not be empty")

    with store._connect() as conn:
        rows = conn.execute(
            f"""
            SELECT
                {MEMORY_ROW_SELECT}
            FROM memories
            WHERE namespace = ?
            ORDER BY created_at ASC
            """,
            (cleaned_namespace,),
        ).fetchall()

    kind_counts = {kind: 0 for kind in sorted(ALLOWED_KINDS)}
    signal_status_counts = {status: 0 for status in ("pending", "claimed", "acked", "expired")}
    relation_counts = {relation: 0 for relation in ("supports", "contradicts", "supersedes", "depends_on")}
    validity_counts = {status: 0 for status in ("unbounded", "current", "future", "expired", "invalid")}
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
        relation_metadata = parse_relation_metadata(str(row["content"]))
        for relation, targets in relation_metadata["relations"].items():
            relation_counts[relation] = relation_counts.get(relation, 0) + len(targets)
        validity_status = relation_metadata["validity_status"]
        validity_counts[validity_status] = validity_counts.get(validity_status, 0) + 1
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
        "relation_counts": relation_counts,
        "validity_counts": validity_counts,
        "top_domains": top_domains,
        "oldest_entry_at": oldest_entry_at,
        "newest_entry_at": newest_entry_at,
    }
    store._log("stats", payload)
    return payload


def fetch_row_by_id(conn: sqlite3.Connection, memory_id: str) -> sqlite3.Row | None:
    return conn.execute(
        f"""
        SELECT
            {MEMORY_ROW_SELECT}
        FROM memories
        WHERE id = ?
        LIMIT 1
        """,
        (memory_id,),
    ).fetchone()


def normalize_content(content: str) -> str:
    return " ".join(content.split())


def normalize_tags(tags: list[str] | None) -> list[str]:
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


def merge_tags(tags: list[str] | None, *, title: str | None, content: str) -> list[str]:
    explicit = normalize_tags(tags)
    extracted = extract_obsidian_tags(title=title, content=content)
    relation_tags = extract_relation_tags(content)
    return normalize_tags([*explicit, *extracted, *relation_tags])


def extract_obsidian_tags(*, title: str | None, content: str) -> list[str]:
    text = "\n".join(part for part in [title or "", content] if part)
    extracted: list[str] = []

    for match in HASHTAG_RE.findall(text):
        extracted.append(f"tag:{match}")

    for raw_link in WIKILINK_RE.findall(text):
        note_name = normalize_wikilink_target(raw_link)
        if note_name:
            extracted.append(f"link:{note_name}")

    return extracted


def normalize_wikilink_target(raw_link: str) -> str:
    return " ".join(raw_link.split()).strip()
