from __future__ import annotations

import json
from typing import Any

from .record_projection import sync_record_projection
from .repository import (
    HIDDEN_REVIEW_LANE_TAGS,
    LEARNING_CANDIDATE_TAG,
    LEARNING_REVIEW_TAG,
    MemoryRow,
    content_hash_for_content,
    exact_content_hash_for_content,
    fetch_row_by_id,
    merge_tags,
)
from .structured_record import (
    LINEAGE_LIST_FIELDS,
    LINEAGE_SINGLETON_FIELDS,
    RELATION_FIELDS,
    build_structured_content,
    parse_structured_content,
)

PROMOTABLE_RECORD_TYPES = {"learn", "gotcha", "domain-note"}
PRESERVED_PROMOTION_FIELDS = (
    *RELATION_FIELDS,
    "valid_from",
    "valid_until",
    *tuple(field for field in LINEAGE_SINGLETON_FIELDS if field not in RELATION_FIELDS),
    *tuple(field for field in LINEAGE_LIST_FIELDS if field not in RELATION_FIELDS),
)


def promote_entry(store: Any, memory_id: str, to_kind: str) -> dict[str, Any]:
    cleaned_id = memory_id.strip()
    target_kind = to_kind.strip().lower()
    if not cleaned_id:
        raise ValueError("id must not be empty")
    if target_kind not in PROMOTABLE_RECORD_TYPES:
        raise ValueError(f"to_kind must be one of {sorted(PROMOTABLE_RECORD_TYPES)}")

    with store._connect() as conn:
        row = fetch_row_by_id(conn, cleaned_id)
        if row is None:
            raise ValueError(f"memory id not found: {cleaned_id}")
        if row["kind"] != "memory":
            raise ValueError("only kind=memory entries can be promoted")

        source = MemoryRow.from_sqlite(row)
        if is_learning_candidate_record(source):
            raise ValueError(
                "learning candidates cannot be promoted directly; review and store durable memory explicitly"
            )
        current_record_type = record_type_for_row(source)
        if current_record_type == target_kind:
            store._log("promote", {"id": cleaned_id, "changed": False, "reason": "already-target-kind"})
            return {
                "id": cleaned_id,
                "changed": False,
                "record_type": target_kind,
                "previous_record_type": current_record_type,
                "item": source.as_dict(),
            }

        updated_item = build_promoted_item(source, target_kind=target_kind, current_record_type=current_record_type)
        content_hash = content_hash_for_content(updated_item["content"])
        exact_hash = exact_content_hash_for_content(updated_item["content"])

        duplicate = conn.execute(
            """
            SELECT id
            FROM memories
            WHERE namespace = ? AND kind = 'memory' AND exact_content_hash = ? AND id != ?
            LIMIT 1
            """,
            (source.namespace, exact_hash, cleaned_id),
        ).fetchone()
        if duplicate is not None:
            store._log(
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
            SET title = ?, content = ?, tags_json = ?, is_learning_candidate = 0,
                content_hash = ?, exact_content_hash = ?
            WHERE id = ?
            """,
            (
                updated_item["title"],
                updated_item["content"],
                json.dumps(updated_item["tags"]),
                content_hash,
                exact_hash,
                cleaned_id,
            ),
        )
        sync_record_projection(
            conn,
            memory_id=cleaned_id,
            namespace=source.namespace,
            content=updated_item["content"],
            tags=updated_item["tags"],
            kind=source.kind,
            actor=source.actor,
            source_app=source.source_app,
            is_learning_candidate=False,
            reject_invalid=True,
        )
        conn.execute("DELETE FROM memories_fts WHERE memory_id = ?", (cleaned_id,))
        conn.execute(
            "INSERT INTO memories_fts(memory_id, title, content) VALUES (?, ?, ?)",
            (cleaned_id, updated_item["title"] or "", updated_item["content"]),
        )
        conn.commit()

        refreshed = fetch_row_by_id(conn, cleaned_id)

    promoted = MemoryRow.from_sqlite(refreshed).as_dict() if refreshed is not None else updated_item
    store._log(
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


def parse_structured_record(content: str) -> dict[str, str]:
    return parse_structured_content(content).as_compat_dict()


def build_structured_record(fields: dict[str, str]) -> str:
    return build_structured_content(fields)


def truncate_title(text: str, limit: int = 72) -> str:
    compact = " ".join(text.split()).strip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rstrip() + "..."


def record_type_for_row(row: MemoryRow) -> str:
    for tag in row.tags:
        if tag in {"kind:learn", "kind:gotcha", "kind:domain-note"}:
            return tag.split(":", 1)[1]
    return parse_structured_record(row.content).get("record_type", "memory")


def is_learning_candidate_record(row: MemoryRow) -> bool:
    fields = parse_structured_record(row.content)
    return bool(
        row.is_learning_candidate
        or HIDDEN_REVIEW_LANE_TAGS.intersection(row.tags)
        or fields.get("record_type") in {"learning-candidate", "learning-review"}
        or LEARNING_CANDIDATE_TAG in row.tags
        or LEARNING_REVIEW_TAG in row.tags
    )


def build_promoted_item(row: MemoryRow, *, target_kind: str, current_record_type: str) -> dict[str, Any]:
    fields = parse_structured_record(row.content)
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
        title = f"[[Learn]] {truncate_title(claim)}"
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
        title = f"[[Gotcha]] {truncate_title(claim)}"
    else:
        primary_domain = base_fields.get("domain") or (domain_tags[0] if domain_tags else "domain:general")
        promoted_fields = {
            "record_type": "domain-note",
            "domain": primary_domain,
            "claim": claim,
            "scope": base_fields.get("scope", "global"),
            "signals": base_fields.get("signals", ""),
        }
        title = f"[[Domain Note]] {truncate_title(claim)}"

    for key in PRESERVED_PROMOTION_FIELDS:
        value = fields.get(key)
        if value:
            promoted_fields[key] = value

    explicit_tags = [
        tag
        for tag in row.tags
        if not tag.startswith("kind:")
        and not tag.startswith("confidence:")
        and not tag.startswith("promoted-from:")
        and not tag.startswith("relation:")
        and not tag.startswith("validity:")
    ]
    explicit_tags.extend([f"kind:{target_kind}", "confidence:manual"])
    if current_record_type in PROMOTABLE_RECORD_TYPES:
        explicit_tags.append(f"promoted-from:{current_record_type}")
    promoted_content = build_structured_record(promoted_fields)
    return {
        "id": row.id,
        "namespace": row.namespace,
        "kind": row.kind,
        "title": title,
        "content": promoted_content,
        "tags": merge_tags(explicit_tags, title=title, content=promoted_content),
        "session_id": row.session_id,
        "actor": row.actor,
        "correlation_id": row.correlation_id,
        "source_app": row.source_app,
        "created_at": row.created_at,
    }
