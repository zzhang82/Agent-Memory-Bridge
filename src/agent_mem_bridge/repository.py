from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import deque
from dataclasses import dataclass
from typing import Any

from .lineage import Lineage, parse_lineage
from .relation_metadata import extract_relation_tags, parse_relation_metadata
from .signals import SignalSnapshot, effective_signal_status, resolve_signal_expiry


HASHTAG_RE = re.compile(r"(?<!\w)#([A-Za-z][A-Za-z0-9_/-]*)")
WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]")
ALLOWED_KINDS = {"memory", "signal"}
LEARNING_CANDIDATE_TAG = "kind:learning-candidate"
LEARNING_REVIEW_TAG = "kind:learning-review"
HIDDEN_REVIEW_LANE_TAGS = {LEARNING_CANDIDATE_TAG, LEARNING_REVIEW_TAG}
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
is_learning_candidate,
lineage_status,
lineage_issues_json,
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
    is_learning_candidate: bool
    lineage_status: str
    lineage_issues: list[dict[str, Any]]
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
            is_learning_candidate=bool(row["is_learning_candidate"]),
            lineage_status=str(row["lineage_status"] or "intact"),
            lineage_issues=_load_lineage_issues(row["lineage_issues_json"]),
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
            "is_learning_candidate": self.is_learning_candidate,
            "lineage_status": self.lineage_status,
            "lineage_issues": self.lineage_issues,
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
    is_learning_candidate = int(bool(HIDDEN_REVIEW_LANE_TAGS.intersection(payload_tags)))
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
                    is_learning_candidate,
                    content_hash,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    is_learning_candidate,
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
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = fetch_row_by_id(conn, cleaned_id)
            if row is None:
                conn.rollback()
                store._log("forget", {"id": cleaned_id, "deleted": False})
                return {"id": cleaned_id, "deleted": False, "item": None}

            all_rows = conn.execute(
                f"""
                SELECT
                    {MEMORY_ROW_SELECT}
                FROM memories
                ORDER BY created_at ASC, id ASC
                """
            ).fetchall()
            rows_by_id = {str(candidate["id"]): candidate for candidate in all_rows}
            lineage_by_id = {str(candidate["id"]): parse_lineage(str(candidate["content"])) for candidate in all_rows}
            deleted_ids, cascade_deleted_ids = _derivation_closure(
                all_rows,
                lineage_by_id=lineage_by_id,
                root_id=cleaned_id,
            )
            retained_dependent_ids = _degrade_retained_dependents(
                conn,
                all_rows,
                lineage_by_id=lineage_by_id,
                deleted_ids=deleted_ids,
                root_forget_id=cleaned_id,
            )

            deletion_order = [cleaned_id, *cascade_deleted_ids]
            deleted_at = store._utc_now()
            for forgotten_id in deletion_order:
                forgotten_row = rows_by_id[forgotten_id]
                conn.execute(
                    """
                    INSERT INTO memory_tombstones (
                        forgotten_id, namespace, kind, deleted_at, root_forget_id, cause
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        forgotten_id,
                        forgotten_row["namespace"],
                        forgotten_row["kind"],
                        deleted_at,
                        cleaned_id,
                        "explicit_forget" if forgotten_id == cleaned_id else "machine_derived_cascade",
                    ),
                )
            conn.executemany("DELETE FROM memories_fts WHERE memory_id = ?", ((item_id,) for item_id in deletion_order))
            conn.executemany("DELETE FROM memory_embeddings WHERE memory_id = ?", ((item_id,) for item_id in deletion_order))
            conn.executemany("DELETE FROM memories WHERE id = ?", ((item_id,) for item_id in deletion_order))
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    item = MemoryRow.from_sqlite(row).as_dict()
    store._log(
        "forget",
        {
            "id": cleaned_id,
            "deleted": True,
            "namespace": item["namespace"],
            "kind": item["kind"],
            "tombstoned": True,
            "cascade_deleted_count": len(cascade_deleted_ids),
            "retained_dependent_count": len(retained_dependent_ids),
        },
    )
    return {
        "id": cleaned_id,
        "deleted": True,
        "item": item,
        "tombstoned": True,
        "cascade_deleted_ids": cascade_deleted_ids,
        "retained_dependent_ids": retained_dependent_ids,
    }


def _derivation_closure(
    rows: list[sqlite3.Row],
    *,
    lineage_by_id: dict[str, Lineage],
    root_id: str,
) -> tuple[set[str], list[str]]:
    children_by_source: dict[str, list[str]] = {}
    for row in rows:
        row_id = str(row["id"])
        for source_id in _machine_owned_source_ids(row, lineage_by_id[row_id]):
            children_by_source.setdefault(source_id, []).append(row_id)

    deleted_ids = {root_id}
    cascade_deleted_ids: list[str] = []
    pending_sources = deque([root_id])
    while pending_sources:
        source_id = pending_sources.popleft()
        for row_id in children_by_source.get(source_id, []):
            if row_id in deleted_ids:
                continue
            deleted_ids.add(row_id)
            cascade_deleted_ids.append(row_id)
            pending_sources.append(row_id)
    return deleted_ids, cascade_deleted_ids


def _machine_owned_source_ids(row: sqlite3.Row, lineage: Lineage) -> set[str]:
    tags = set(json.loads(row["tags_json"] or "[]"))
    record_type = _structured_record_type(str(row["content"]))
    generated_by_consolidation = (
        "source:consolidation" in tags
        or row["actor"] == "bridge-consolidation"
        or row["source_app"] == "agent-memory-bridge-consolidation"
    )
    if generated_by_consolidation and record_type == "belief" and "kind:belief" in tags:
        return _optional_id_set(lineage.derived_from_candidate_id)
    if generated_by_consolidation and record_type == "concept-note" and "kind:concept-note" in tags:
        return _optional_id_set(lineage.derived_from_belief_id)
    if generated_by_consolidation and record_type == "belief-candidate" and "kind:belief-candidate" in tags:
        return set(lineage.evidence_refs)

    if bool(row["is_learning_candidate"]) and HIDDEN_REVIEW_LANE_TAGS.intersection(tags):
        if LEARNING_REVIEW_TAG in tags or record_type == "learning-review":
            return _optional_id_set(lineage.source_candidate_id)
        return set(lineage.evidence_refs)

    if (
        row["kind"] == "signal"
        and "kind:governance-trigger" in tags
        and record_type == "governance-trigger"
    ):
        return _optional_id_set(lineage.candidate_id)
    return set()


def _degrade_retained_dependents(
    conn: sqlite3.Connection,
    rows: list[sqlite3.Row],
    *,
    lineage_by_id: dict[str, Lineage],
    deleted_ids: set[str],
    root_forget_id: str,
) -> list[str]:
    forgotten_superseders_by_predecessor: dict[str, list[str]] = {}
    for row in rows:
        superseder_id = str(row["id"])
        if superseder_id not in deleted_ids:
            continue
        for predecessor_id in lineage_by_id[superseder_id].supersedes:
            if predecessor_id in deleted_ids or predecessor_id not in lineage_by_id:
                continue
            forgotten_superseders_by_predecessor.setdefault(predecessor_id, []).append(superseder_id)

    retained_dependent_ids: list[str] = []
    for row in rows:
        row_id = str(row["id"])
        if row_id in deleted_ids:
            continue
        references = lineage_by_id[row_id].degrading_references
        missing_ids = sorted({reference.target_id for reference in references}.intersection(deleted_ids))
        forgotten_superseder_ids = forgotten_superseders_by_predecessor.get(row_id, [])
        if not missing_ids and not forgotten_superseder_ids:
            continue

        issues = _load_lineage_issues(row["lineage_issues_json"])
        for missing_id in missing_ids:
            relations = sorted(
                {
                    reference.relation.value
                    for reference in references
                    if reference.target_id == missing_id
                }
            )
            issue = {
                "type": "missing_dependency",
                "missing_record_id": missing_id,
                "relations": relations,
                "root_forget_id": root_forget_id,
            }
            if issue not in issues:
                issues.append(issue)
        for missing_id in forgotten_superseder_ids:
            issue = {
                "type": "forgotten_superseder",
                "missing_record_id": missing_id,
                "root_forget_id": root_forget_id,
            }
            if issue not in issues:
                issues.append(issue)
        conn.execute(
            """
            UPDATE memories
            SET lineage_status = 'degraded', lineage_issues_json = ?
            WHERE id = ?
            """,
            (json.dumps(issues, ensure_ascii=True, sort_keys=True, separators=(",", ":")), row_id),
        )
        retained_dependent_ids.append(row_id)
    return retained_dependent_ids


def _structured_record_type(content: str) -> str | None:
    for raw_line in content.splitlines():
        label, separator, remainder = raw_line.partition(":")
        if separator and label.strip().lower().replace("-", "_") == "record_type":
            return remainder.strip().lower() or None
    return None


def _optional_id_set(value: str | None) -> set[str]:
    return {value} if value is not None else set()


def _load_lineage_issues(raw_value: Any) -> list[dict[str, Any]]:
    try:
        parsed = json.loads(str(raw_value or "[]"))
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict)]


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
            AND is_learning_candidate = 0
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


def fetch_tombstone_metadata(conn: sqlite3.Connection, memory_id: str) -> dict[str, str] | None:
    """Return redacted forget metadata without exposing deleted record content."""
    row = conn.execute(
        """
        SELECT forgotten_id, namespace, kind, deleted_at, root_forget_id, cause
        FROM memory_tombstones
        WHERE forgotten_id = ?
        LIMIT 1
        """,
        (memory_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "forgotten_id": str(row["forgotten_id"]),
        "namespace": str(row["namespace"]),
        "kind": str(row["kind"]),
        "deleted_at": str(row["deleted_at"]),
        "root_forget_id": str(row["root_forget_id"]),
        "cause": str(row["cause"]),
    }


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
