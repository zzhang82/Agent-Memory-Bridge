from __future__ import annotations

import hashlib
import json
from typing import Any

from .record_projection import sync_record_projection
from .repository import (
    HIDDEN_REVIEW_LANE_TAGS,
    MEMORY_ROW_SELECT,
    MemoryRow,
    fetch_row_by_id,
    merge_tags,
    normalize_content,
    normalize_tags,
)
from .structured_record import parse_structured_content

PROVENANCE_FIELDS = (
    "session_id",
    "actor",
    "correlation_id",
    "source_app",
    "source_client",
    "source_model",
    "client_session_id",
    "client_workspace",
    "client_transport",
)
RESERVED_POLICY_TAG_PREFIXES = (
    "candidate_status:",
    "confidence:",
    "control:",
    "kind:",
    "relation:",
    "reviewed:",
    "scope:",
    "source:",
    "status:",
    "validity:",
)
RESERVED_ANNOTATION_TAG_PREFIXES = RESERVED_POLICY_TAG_PREFIXES


def annotate_entry(
    store: Any,
    memory_id: str,
    *,
    tags: list[str] | None = None,
    title: str | None = None,
    provenance: dict[str, str | None] | None = None,
    actor: str | None = None,
) -> dict[str, Any]:
    cleaned_id = memory_id.strip()
    if not cleaned_id:
        raise ValueError("id must not be empty")
    requested_tags = normalize_tags(tags or [])
    reserved_tags = [
        tag for tag in requested_tags if any(tag.startswith(prefix) for prefix in RESERVED_POLICY_TAG_PREFIXES)
    ]
    if reserved_tags:
        raise ValueError(f"annotate cannot add reserved policy tags: {', '.join(reserved_tags)}")
    cleaned_title = title.strip() if title and title.strip() else None
    cleaned_provenance = {
        field: str(value).strip()
        for field, value in (provenance or {}).items()
        if field in PROVENANCE_FIELDS and value is not None and str(value).strip()
    }
    if not requested_tags and cleaned_title is None and not cleaned_provenance:
        raise ValueError("annotate requires tags, title, or provenance")

    with store._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = fetch_row_by_id(conn, cleaned_id)
        if row is None:
            conn.rollback()
            raise ValueError(f"memory id not found: {cleaned_id}")
        source = MemoryRow.from_sqlite(row)
        if source.kind != "memory":
            conn.rollback()
            raise ValueError("only kind=memory entries can be annotated")

        added_tags = [tag for tag in requested_tags if tag not in source.tags]
        title_after = cleaned_title if cleaned_title is not None else source.title
        updated_tags = merge_tags([*source.tags, *added_tags], title=title_after, content=source.content)
        title_changed = title_after != source.title
        tags_changed = updated_tags != source.tags
        changed = bool(title_changed or tags_changed or cleaned_provenance)
        if not changed:
            conn.rollback()
            return {"id": cleaned_id, "changed": False, "item": source.as_dict()}

        is_learning_candidate = int(bool(HIDDEN_REVIEW_LANE_TAGS.intersection(updated_tags)))
        conn.execute(
            """
            UPDATE memories
            SET title = ?, tags_json = ?, is_learning_candidate = ?
            WHERE id = ?
            """,
            (
                title_after,
                json.dumps(updated_tags, ensure_ascii=True),
                is_learning_candidate,
                cleaned_id,
            ),
        )
        sync_record_projection(
            conn,
            memory_id=cleaned_id,
            namespace=source.namespace,
            content=source.content,
            tags=updated_tags,
            kind=source.kind,
            actor=source.actor,
            source_app=source.source_app,
            is_learning_candidate=bool(is_learning_candidate),
        )
        if title_changed:
            conn.execute("DELETE FROM memories_fts WHERE memory_id = ?", (cleaned_id,))
            conn.execute(
                "INSERT INTO memories_fts(memory_id, title, content) VALUES (?, ?, ?)",
                (cleaned_id, title_after or "", source.content),
            )
        conn.execute(
            """
            INSERT INTO memory_annotations (
                memory_id,
                title_before,
                title_after,
                added_tags_json,
                provenance_json,
                actor,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                cleaned_id,
                source.title,
                title_after,
                json.dumps(added_tags, ensure_ascii=True),
                json.dumps(cleaned_provenance, ensure_ascii=True, sort_keys=True),
                actor.strip() if actor and actor.strip() else None,
                store._utc_now(),
            ),
        )
        conn.commit()
        refreshed = fetch_row_by_id(conn, cleaned_id)

    item = MemoryRow.from_sqlite(refreshed).as_dict() if refreshed is not None else None
    store._log(
        "annotate",
        {
            "id": cleaned_id,
            "changed": True,
            "added_tag_count": len(added_tags),
            "title_changed": title_changed,
            "provenance_field_count": len(cleaned_provenance),
        },
    )
    return {
        "id": cleaned_id,
        "changed": True,
        "added_tags": added_tags,
        "title_changed": title_changed,
        "provenance_added": cleaned_provenance,
        "item": item,
    }


def revise_entry(
    store: Any,
    memory_id: str,
    *,
    replacement_content: str,
    title: str | None = None,
    tags: list[str] | None = None,
    actor: str | None = None,
    reason: str | None = None,
    provenance: dict[str, str | None] | None = None,
) -> dict[str, Any]:
    cleaned_id = memory_id.strip()
    cleaned_content = replacement_content.strip()
    if not cleaned_id:
        raise ValueError("id must not be empty")
    if not cleaned_content:
        raise ValueError("replacement_content must not be empty")

    structured = parse_structured_content(cleaned_content)
    if cleaned_id not in structured.values("supersedes"):
        cleaned_content = f"{cleaned_content}\nsupersedes: {cleaned_id}"
    requested_provenance = {
        field: str(value).strip()
        for field, value in (provenance or {}).items()
        if field in PROVENANCE_FIELDS and value is not None and str(value).strip()
    }
    with store._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = fetch_row_by_id(conn, cleaned_id)
            if row is None:
                raise ValueError(f"memory id not found: {cleaned_id}")
            source = MemoryRow.from_sqlite(row)
            if source.kind != "memory":
                raise ValueError("only kind=memory entries can be revised")
            if source.is_learning_candidate or HIDDEN_REVIEW_LANE_TAGS.intersection(source.tags):
                raise ValueError(
                    "learning candidates and reviews must use the governed review workflow before revision"
                )

            if tags is not None:
                explicit_tags = normalize_tags(tags)
                reserved_tags = [
                    tag
                    for tag in explicit_tags
                    if any(tag.startswith(prefix) for prefix in RESERVED_POLICY_TAG_PREFIXES)
                ]
                if reserved_tags:
                    raise ValueError(f"revise cannot add reserved policy tags: {', '.join(reserved_tags)}")
                requested_tags = _revision_tags(explicit_tags, cleaned_content)
            else:
                requested_tags = _revision_tags(source.tags, cleaned_content)
            if HIDDEN_REVIEW_LANE_TAGS.intersection(requested_tags):
                raise ValueError("revise cannot create a hidden learning-candidate or review row")
            title_after = title.strip() if title and title.strip() else source.title
            payload_tags = merge_tags(requested_tags, title=title_after, content=cleaned_content)
            if HIDDEN_REVIEW_LANE_TAGS.intersection(payload_tags):
                raise ValueError("revise cannot create a hidden learning-candidate or review row")
            content_hash = hashlib.sha256(normalize_content(cleaned_content).encode("utf-8")).hexdigest()
            existing = conn.execute(
                f"""
                SELECT {MEMORY_ROW_SELECT}
                FROM memories
                WHERE namespace = ? AND kind != 'signal' AND content_hash = ?
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (source.namespace, content_hash),
            ).fetchone()
            if existing is not None:
                existing_source = MemoryRow.from_sqlite(existing)
                if existing_source.is_learning_candidate or HIDDEN_REVIEW_LANE_TAGS.intersection(existing_source.tags):
                    raise ValueError("revision successor cannot resolve to a hidden learning candidate")
                successor_id = existing_source.id
                result = {
                    "id": successor_id,
                    "stored": False,
                    "duplicate": True,
                    "duplicate_of": successor_id,
                    "write_disposition": "duplicate_content",
                    "metadata_diff": None,
                    "created_at": existing_source.created_at,
                }
            else:
                successor_id = store._new_id()
                created_at = store._utc_now()
                values = {field: requested_provenance.get(field, getattr(source, field)) for field in PROVENANCE_FIELDS}
                if actor is not None and actor.strip():
                    values["actor"] = actor.strip()
                conn.execute(
                    """
                    INSERT INTO memories (
                        id, namespace, kind, title, content, tags_json,
                        session_id, actor, correlation_id, source_app, source_client, source_model,
                        client_session_id, client_workspace, client_transport,
                        signal_status, claimed_by, claimed_at, lease_expires_at, expires_at,
                        acknowledged_at, is_learning_candidate, content_hash, created_at
                    ) VALUES (?, ?, 'memory', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                              NULL, NULL, NULL, NULL, NULL, NULL, 0, ?, ?)
                    """,
                    (
                        successor_id,
                        source.namespace,
                        title_after,
                        cleaned_content,
                        json.dumps(payload_tags, ensure_ascii=True),
                        values["session_id"],
                        values["actor"],
                        values["correlation_id"],
                        values["source_app"],
                        values["source_client"],
                        values["source_model"],
                        values["client_session_id"],
                        values["client_workspace"],
                        values["client_transport"],
                        content_hash,
                        created_at,
                    ),
                )
                conn.execute("INSERT INTO memory_insertions (memory_id) VALUES (?)", (successor_id,))
                sync_record_projection(
                    conn,
                    memory_id=successor_id,
                    namespace=source.namespace,
                    content=cleaned_content,
                    tags=payload_tags,
                    kind="memory",
                    actor=values["actor"],
                    source_app=values["source_app"],
                    is_learning_candidate=False,
                    reject_invalid=True,
                )
                conn.execute(
                    "INSERT INTO memories_fts(memory_id, title, content) VALUES (?, ?, ?)",
                    (successor_id, title_after or "", cleaned_content),
                )
                result = {
                    "id": successor_id,
                    "stored": True,
                    "duplicate": False,
                    "duplicate_of": None,
                    "write_disposition": "stored_new",
                    "metadata_diff": None,
                    "signal_status": None,
                    "expires_at": None,
                    "created_at": created_at,
                }

            conn.execute(
                """
                INSERT OR IGNORE INTO memory_revisions (
                    predecessor_id, successor_id, actor, reason, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    cleaned_id,
                    successor_id,
                    actor.strip() if actor and actor.strip() else None,
                    reason.strip() if reason and reason.strip() else None,
                    store._utc_now(),
                ),
            )
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    store._log(
        "revise",
        {
            "predecessor_id": cleaned_id,
            "successor_id": successor_id,
            "stored": result.get("stored"),
        },
    )
    return {
        "revised": True,
        "predecessor_id": cleaned_id,
        "successor_id": successor_id,
        "result": result,
    }


def _revision_tags(source_tags: list[str], content: str) -> list[str]:
    record_type = parse_structured_content(content).first("record_type")
    tags = [tag for tag in source_tags if not any(tag.startswith(prefix) for prefix in RESERVED_POLICY_TAG_PREFIXES)]
    if record_type:
        tags.append(f"kind:{record_type}")
    return normalize_tags(tags)
