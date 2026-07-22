from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from typing import Any

from .lineage import DEGRADING_LINEAGE_RELATIONS, LineageRelation
from .record_projection import sync_record_projection
from .relation_metadata import extract_relation_tags, parse_relation_metadata, resolve_validity_status
from .schema import exact_content_hash as exact_content_hash_for_content
from .signals import SignalSnapshot, effective_signal_status, resolve_signal_expiry

HASHTAG_RE = re.compile(r"(?<!\w)#([A-Za-z][A-Za-z0-9_/-]*)")
WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]")
ALLOWED_KINDS = {"memory", "signal"}
LEARNING_CANDIDATE_TAG = "kind:learning-candidate"
LEARNING_REVIEW_TAG = "kind:learning-review"
HIDDEN_REVIEW_LANE_TAGS = {LEARNING_CANDIDATE_TAG, LEARNING_REVIEW_TAG}
MEMORY_ROW_COLUMNS = (
    "id",
    "namespace",
    "kind",
    "title",
    "content",
    "tags_json",
    "session_id",
    "actor",
    "correlation_id",
    "source_app",
    "source_client",
    "source_model",
    "client_session_id",
    "client_workspace",
    "client_transport",
    "signal_status",
    "claimed_by",
    "claimed_at",
    "lease_expires_at",
    "expires_at",
    "acknowledged_at",
    "is_learning_candidate",
    "lineage_status",
    "lineage_issues_json",
    "created_at",
)


def memory_row_select(alias: str | None = None) -> str:
    prefix = f"{alias}." if alias else ""
    id_expr = f"{alias}.id" if alias else "memories.id"
    base = [
        f"(SELECT sequence FROM memory_insertions WHERE memory_id = {id_expr}) AS _insertion_sequence",
        *(f"{prefix}{column}" for column in MEMORY_ROW_COLUMNS),
    ]
    projection = [
        f"(SELECT record_type FROM memory_metadata WHERE memory_id = {id_expr}) AS metadata_record_type",
        f"(SELECT status FROM memory_metadata WHERE memory_id = {id_expr}) AS metadata_status",
        f"(SELECT confidence FROM memory_metadata WHERE memory_id = {id_expr}) AS metadata_confidence",
        f"(SELECT confidence_label FROM memory_metadata WHERE memory_id = {id_expr}) AS metadata_confidence_label",
        f"(SELECT valid_from FROM memory_metadata WHERE memory_id = {id_expr}) AS metadata_valid_from",
        f"(SELECT valid_until FROM memory_metadata WHERE memory_id = {id_expr}) AS metadata_valid_until",
        f"(SELECT metadata_schema_version FROM memory_metadata WHERE memory_id = {id_expr}) AS metadata_schema_version",
        f"COALESCE((SELECT validation_issues_json FROM memory_metadata WHERE memory_id = {id_expr}), '[]') AS metadata_validation_issues_json",
        (
            "COALESCE((SELECT json_group_array(json_object("
            "'relation', projected_edges.relation, 'target_id', projected_edges.target_id)) "
            "FROM (SELECT relation, target_id FROM memory_edges "
            f"WHERE source_id = {id_expr} ORDER BY position ASC) projected_edges), '[]') "
            "AS metadata_edges_json"
        ),
        (
            "COALESCE((SELECT json_group_array(json_object("
            "'annotation_id', projected_annotations.annotation_id, "
            "'title_before', projected_annotations.title_before, "
            "'title_after', projected_annotations.title_after, "
            "'added_tags_json', projected_annotations.added_tags_json, "
            "'provenance_json', projected_annotations.provenance_json, "
            "'actor', projected_annotations.actor, "
            "'created_at', projected_annotations.created_at)) "
            "FROM (SELECT * FROM memory_annotations "
            f"WHERE memory_id = {id_expr} ORDER BY annotation_id ASC) projected_annotations), '[]') "
            "AS metadata_annotations_json"
        ),
    ]
    return ",\n".join([*base, *projection])


MEMORY_ROW_SELECT = memory_row_select()


@dataclass(slots=True)
class MemoryRow:
    insertion_sequence: int
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
    record_type: str | None
    status: str | None
    confidence: float | None
    confidence_label: str | None
    valid_from: str | None
    valid_until: str | None
    metadata_schema_version: int | None
    metadata_validation_issues: list[dict[str, Any]]
    metadata_edges: list[dict[str, Any]]
    annotations: list[dict[str, Any]]
    created_at: str

    @classmethod
    def from_sqlite(cls, row: sqlite3.Row) -> "MemoryRow":
        return cls(
            insertion_sequence=int(row["_insertion_sequence"]),
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
            record_type=_optional_row_text(row, "metadata_record_type"),
            status=_optional_row_text(row, "metadata_status"),
            confidence=_optional_row_float(row, "metadata_confidence"),
            confidence_label=_optional_row_text(row, "metadata_confidence_label"),
            valid_from=_optional_row_text(row, "metadata_valid_from"),
            valid_until=_optional_row_text(row, "metadata_valid_until"),
            metadata_schema_version=_optional_row_int(row, "metadata_schema_version"),
            metadata_validation_issues=_load_lineage_issues(_row_value(row, "metadata_validation_issues_json", "[]")),
            metadata_edges=_load_lineage_issues(_row_value(row, "metadata_edges_json", "[]")),
            annotations=_load_annotations(_row_value(row, "metadata_annotations_json", "[]")),
            created_at=row["created_at"],
        )

    def as_dict(self) -> dict[str, Any]:
        signal_status = effective_signal_status(SignalSnapshot.from_row(self.as_sql_row()))
        relations: dict[str, list[str]] = {
            relation: [] for relation in ("supports", "contradicts", "supersedes", "depends_on")
        }
        for edge in self.metadata_edges:
            relation = str(edge.get("relation") or "")
            target_id = str(edge.get("target_id") or "")
            if relation in relations and target_id and target_id not in relations[relation]:
                relations[relation].append(target_id)
        validity_status = resolve_validity_status(valid_from=self.valid_from, valid_until=self.valid_until)
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
            "record_type": self.record_type,
            "status": self.status,
            "confidence": self.confidence,
            "confidence_label": self.confidence_label,
            "metadata_schema_version": self.metadata_schema_version,
            "metadata_validation_issues": self.metadata_validation_issues,
            "annotations": self.annotations,
            "created_at": self.created_at,
            "relations": relations,
            "valid_from": self.valid_from,
            "valid_until": self.valid_until,
            "validity_status": validity_status,
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

    payload_tags = merge_tags(tags, title=title, content=cleaned_content)
    is_learning_candidate = int(bool(HIDDEN_REVIEW_LANE_TAGS.intersection(payload_tags)))
    content_hash = content_hash_for_content(cleaned_content)
    exact_hash = exact_content_hash_for_content(cleaned_content)
    resolved_expires_at = resolve_signal_expiry(expires_at=expires_at, ttl_seconds=ttl_seconds)
    signal_status = "pending" if cleaned_kind == "signal" else None

    with store._connect() as conn:
        if cleaned_kind != "signal":
            existing = conn.execute(
                f"""
                SELECT {MEMORY_ROW_SELECT}
                FROM memories
                WHERE namespace = ? AND kind != 'signal' AND exact_content_hash = ?
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (cleaned_namespace, exact_hash),
            ).fetchone()
            if existing is not None:
                duplicate_response = _duplicate_response(
                    existing,
                    requested_title=title,
                    requested_tags=payload_tags,
                    requested_provenance={
                        "session_id": session_id,
                        "actor": actor,
                        "correlation_id": correlation_id,
                        "source_app": source_app,
                        "source_client": source_client,
                        "source_model": source_model,
                        "client_session_id": client_session_id,
                        "client_workspace": client_workspace,
                        "client_transport": client_transport,
                    },
                )
                store._log(
                    "store",
                    {
                        "namespace": cleaned_namespace,
                        "kind": cleaned_kind,
                        "stored": False,
                        "duplicate_of": existing["id"],
                        "write_disposition": duplicate_response["write_disposition"],
                    },
                )
                return duplicate_response

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
                    exact_content_hash,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    exact_hash,
                    created_at,
                ),
            )
            conn.execute("INSERT INTO memory_insertions (memory_id) VALUES (?)", (memory_id,))
            sync_record_projection(
                conn,
                memory_id=memory_id,
                namespace=cleaned_namespace,
                content=cleaned_content,
                tags=payload_tags,
                kind=cleaned_kind,
                actor=actor,
                source_app=source_app,
                is_learning_candidate=bool(is_learning_candidate),
                reject_invalid=True,
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
                f"""
                SELECT {MEMORY_ROW_SELECT}
                FROM memories
                WHERE namespace = ? AND kind != 'signal' AND exact_content_hash = ?
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (cleaned_namespace, exact_hash),
            ).fetchone()
            if existing is None:
                raise
            duplicate_response = _duplicate_response(
                existing,
                requested_title=title,
                requested_tags=payload_tags,
                requested_provenance={
                    "session_id": session_id,
                    "actor": actor,
                    "correlation_id": correlation_id,
                    "source_app": source_app,
                    "source_client": source_client,
                    "source_model": source_model,
                    "client_session_id": client_session_id,
                    "client_workspace": client_workspace,
                    "client_transport": client_transport,
                },
            )
            store._log(
                "store",
                {
                    "namespace": cleaned_namespace,
                    "kind": cleaned_kind,
                    "stored": False,
                    "duplicate_of": existing["id"],
                    "race_recovered": True,
                    "write_disposition": duplicate_response["write_disposition"],
                },
            )
            duplicate_response["race_recovered"] = True
            return duplicate_response

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
        "write_disposition": "stored_new",
        "metadata_diff": None,
        "signal_status": signal_status,
        "expires_at": resolved_expires_at,
        "created_at": created_at,
    }


def _duplicate_response(
    existing: sqlite3.Row,
    *,
    requested_title: str | None,
    requested_tags: list[str],
    requested_provenance: dict[str, str | None],
) -> dict[str, Any]:
    existing_tags = set(json.loads(existing["tags_json"] or "[]"))
    new_tags = [tag for tag in requested_tags if tag not in existing_tags]
    cleaned_title = requested_title.strip() if requested_title else None
    different_title = cleaned_title is not None and cleaned_title != existing["title"]
    new_provenance = {
        field: {"existing": existing[field], "requested": value}
        for field, value in requested_provenance.items()
        if value is not None and value != existing[field]
    }
    has_diff = bool(new_tags or different_title or new_provenance)
    return {
        "id": existing["id"],
        "stored": False,
        "duplicate": True,
        "duplicate_of": existing["id"],
        "write_disposition": "duplicate_with_new_metadata" if has_diff else "duplicate_content",
        "metadata_diff": {
            "new_tags": new_tags,
            "different_title": different_title,
            "existing_title": existing["title"],
            "requested_title": cleaned_title,
            "new_provenance": new_provenance,
        },
        "created_at": existing["created_at"],
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
        preflight_deletion_order = _indexed_derivation_closure(conn, root_id=cleaned_id)

    with store._connect() as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            deletion = delete_entry_in_transaction(
                conn,
                root_id=cleaned_id,
                deleted_at=store._utc_now(),
                root_cause="explicit_forget",
            )
            if deletion is None:
                conn.rollback()
                store._log("forget", {"id": cleaned_id, "deleted": False, "race_recovered": True})
                return {"id": cleaned_id, "deleted": False, "item": None}
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    deletion_order = deletion["deletion_order"]
    cascade_deleted_ids = deletion["cascade_deleted_ids"]
    retained_dependent_ids = deletion["retained_dependent_ids"]
    item = MemoryRow.from_sqlite(deletion["root_row"]).as_dict()
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
            "preflight_changed": preflight_deletion_order != deletion_order,
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


def delete_entry_in_transaction(
    conn: sqlite3.Connection,
    *,
    root_id: str,
    deleted_at: str,
    root_cause: str,
) -> dict[str, Any] | None:
    """Delete one root and its proven machine-owned closure inside an active transaction."""

    root_row = fetch_row_by_id(conn, root_id)
    if root_row is None:
        return None
    deletion_order = _indexed_derivation_closure(conn, root_id=root_id)
    deleted_ids = set(deletion_order)
    rows_by_id = _fetch_rows_by_ids(conn, deletion_order)
    retained_updates = _indexed_retained_degradation_updates(
        conn,
        deleted_ids=deleted_ids,
        root_forget_id=root_id,
    )
    for retained_id, issues_json in retained_updates:
        conn.execute(
            """
            UPDATE memories
            SET lineage_status = 'degraded', lineage_issues_json = ?
            WHERE id = ?
            """,
            (issues_json, retained_id),
        )

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
                root_id,
                root_cause if forgotten_id == root_id else "machine_derived_cascade",
            ),
        )
    conn.executemany(
        "UPDATE memory_edges SET target_namespace = NULL, target_exists = 0 WHERE target_id = ?",
        ((item_id,) for item_id in deletion_order),
    )
    conn.executemany("DELETE FROM memories_fts WHERE memory_id = ?", ((item_id,) for item_id in deletion_order))
    conn.executemany("DELETE FROM memory_embeddings WHERE memory_id = ?", ((item_id,) for item_id in deletion_order))
    conn.executemany("DELETE FROM memories WHERE id = ?", ((item_id,) for item_id in deletion_order))
    return {
        "root_row": root_row,
        "deletion_order": deletion_order,
        "cascade_deleted_ids": deletion_order[1:],
        "retained_dependent_ids": [retained_id for retained_id, _ in retained_updates],
    }


def _indexed_derivation_closure(
    conn: sqlite3.Connection,
    *,
    root_id: str,
) -> list[str]:
    deletion_order = [root_id]
    deleted_ids = {root_id}
    frontier = [root_id]
    while frontier:
        discovered: dict[str, int] = {}
        for chunk in _chunks(frontier):
            placeholders = ", ".join("?" for _ in chunk)
            rows = conn.execute(
                f"""
                SELECT e.source_id, m.rowid
                FROM memory_edges e
                JOIN memories m ON m.id = e.source_id
                WHERE e.machine_owned = 1
                  AND e.target_id IN ({placeholders})
                """,
                chunk,
            ).fetchall()
            for row in rows:
                source_id = str(row["source_id"])
                if source_id not in deleted_ids:
                    discovered[source_id] = int(row["rowid"])
        frontier = [source_id for source_id, _ in sorted(discovered.items(), key=lambda item: item[1])]
        deleted_ids.update(frontier)
        deletion_order.extend(frontier)
    return deletion_order


def _indexed_retained_degradation_updates(
    conn: sqlite3.Connection,
    *,
    deleted_ids: set[str],
    root_forget_id: str,
) -> list[tuple[str, str]]:
    by_retained_id: dict[str, dict[str, Any]] = {}
    degrading_relations = tuple(relation.value for relation in DEGRADING_LINEAGE_RELATIONS)
    relation_placeholders = ", ".join("?" for _ in degrading_relations)
    for chunk in _chunks(sorted(deleted_ids)):
        placeholders = ", ".join("?" for _ in chunk)
        rows = conn.execute(
            f"""
            SELECT
                e.source_id AS retained_id,
                e.target_id AS missing_id,
                e.relation,
                m.rowid,
                m.lineage_issues_json
            FROM memory_edges e
            JOIN memories m ON m.id = e.source_id
            WHERE e.target_id IN ({placeholders})
              AND e.relation IN ({relation_placeholders})
            """,
            (*chunk, *degrading_relations),
        ).fetchall()
        for row in rows:
            retained_id = str(row["retained_id"])
            if retained_id in deleted_ids:
                continue
            state = by_retained_id.setdefault(
                retained_id,
                {
                    "rowid": int(row["rowid"]),
                    "issues": _load_lineage_issues(row["lineage_issues_json"]),
                    "missing": {},
                    "superseders": set(),
                },
            )
            missing = state["missing"]
            missing.setdefault(str(row["missing_id"]), set()).add(str(row["relation"]))

    for chunk in _chunks(sorted(deleted_ids)):
        placeholders = ", ".join("?" for _ in chunk)
        rows = conn.execute(
            f"""
            SELECT
                e.target_id AS retained_id,
                e.source_id AS missing_id,
                m.rowid,
                m.lineage_issues_json
            FROM memory_edges e
            JOIN memories m ON m.id = e.target_id
            WHERE e.source_id IN ({placeholders})
              AND e.relation = ?
            """,
            (*chunk, LineageRelation.SUPERSEDES.value),
        ).fetchall()
        for row in rows:
            retained_id = str(row["retained_id"])
            if retained_id in deleted_ids:
                continue
            state = by_retained_id.setdefault(
                retained_id,
                {
                    "rowid": int(row["rowid"]),
                    "issues": _load_lineage_issues(row["lineage_issues_json"]),
                    "missing": {},
                    "superseders": set(),
                },
            )
            state["superseders"].add(str(row["missing_id"]))

    updates: list[tuple[str, str]] = []
    for retained_id, state in sorted(by_retained_id.items(), key=lambda item: item[1]["rowid"]):
        issues = state["issues"]
        for missing_id, relations in sorted(state["missing"].items()):
            issue = {
                "type": "missing_dependency",
                "missing_record_id": missing_id,
                "relations": sorted(relations),
                "root_forget_id": root_forget_id,
            }
            if issue not in issues:
                issues.append(issue)
        for missing_id in sorted(state["superseders"]):
            issue = {
                "type": "forgotten_superseder",
                "missing_record_id": missing_id,
                "root_forget_id": root_forget_id,
            }
            if issue not in issues:
                issues.append(issue)
        updates.append(
            (
                retained_id,
                json.dumps(issues, ensure_ascii=True, sort_keys=True, separators=(",", ":")),
            )
        )
    return updates


def _fetch_rows_by_ids(conn: sqlite3.Connection, memory_ids: list[str]) -> dict[str, sqlite3.Row]:
    rows_by_id: dict[str, sqlite3.Row] = {}
    for chunk in _chunks(memory_ids):
        placeholders = ", ".join("?" for _ in chunk)
        rows = conn.execute(
            f"SELECT {MEMORY_ROW_SELECT} FROM memories WHERE id IN ({placeholders})",
            chunk,
        ).fetchall()
        rows_by_id.update({str(row["id"]): row for row in rows})
    return rows_by_id


def _chunks(values: list[str], size: int = 400) -> list[list[str]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def _load_lineage_issues(raw_value: Any) -> list[dict[str, Any]]:
    try:
        parsed = json.loads(str(raw_value or "[]"))
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict)]


def _load_annotations(raw_value: Any) -> list[dict[str, Any]]:
    annotations = _load_lineage_issues(raw_value)
    for annotation in annotations:
        annotation["added_tags"] = _load_json_value(annotation.pop("added_tags_json", "[]"), default=[])
        annotation["provenance"] = _load_json_value(annotation.pop("provenance_json", "{}"), default={})
    return annotations


def _load_json_value(raw_value: Any, *, default: Any) -> Any:
    try:
        return json.loads(str(raw_value))
    except (TypeError, json.JSONDecodeError):
        return default


def _row_value(row: sqlite3.Row, key: str, default: Any = None) -> Any:
    return row[key] if key in row.keys() else default


def _optional_row_text(row: sqlite3.Row, key: str) -> str | None:
    value = _row_value(row, key)
    return str(value) if value is not None else None


def _optional_row_float(row: sqlite3.Row, key: str) -> float | None:
    value = _row_value(row, key)
    return float(value) if value is not None else None


def _optional_row_int(row: sqlite3.Row, key: str) -> int | None:
    value = _row_value(row, key)
    return int(value) if value is not None else None


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


def content_hash_for_content(content: str) -> str:
    return hashlib.sha256(normalize_content(content).encode("utf-8")).hexdigest()


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
