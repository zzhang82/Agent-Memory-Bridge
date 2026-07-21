from __future__ import annotations

import json
import math
import re
import sqlite3
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from .lineage import Lineage, parse_lineage
from .structured_record import parse_structured_content

METADATA_SCHEMA_VERSION = 1
RECORD_TYPE_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
STATUS_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
CONFIDENCE_LABEL_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
ALLOWED_RECORD_STATUSES = frozenset(
    {
        "acked",
        "active",
        "approved",
        "archived",
        "candidate",
        "claimed",
        "current",
        "degraded",
        "deleted",
        "draft",
        "expired",
        "go",
        "intact",
        "needs_review",
        "pending",
        "quarantined",
        "rejected",
        "replaced",
        "stale",
        "superseded",
        "unsafe",
        "validated",
    }
)
ALLOWED_CONFIDENCE_LABELS = frozenset(
    {
        "candidate",
        "high",
        "human-reviewed",
        "low",
        "manual",
        "medium",
        "observed",
        "platform-neutral",
        "strong-candidate",
        "tentative",
        "validated",
    }
)


@dataclass(frozen=True, slots=True)
class CanonicalMetadata:
    record_type: str | None
    status: str | None
    confidence: float | None
    confidence_label: str | None
    valid_from: str | None
    valid_until: str | None
    validation_issues: tuple[dict[str, Any], ...]


@dataclass(frozen=True, slots=True)
class RecordProjection:
    metadata: CanonicalMetadata
    tags: tuple[str, ...]
    lineage: Lineage
    machine_owned_target_ids: frozenset[str]


@dataclass(frozen=True, slots=True)
class ResolvedRecordProjection:
    projection: RecordProjection
    edge_rows: tuple[tuple[str, str, str, int, int, str | None, int], ...]


def build_record_projection(
    *,
    content: str,
    tags: list[str],
    kind: str,
    actor: str | None,
    source_app: str | None,
    is_learning_candidate: bool,
) -> RecordProjection:
    structured = parse_structured_content(content)
    record_type = _normalize_optional(structured.first("record_type"))
    status = _normalize_optional(structured.first("status"))
    confidence_raw = _normalize_optional(structured.first("confidence"))
    valid_from = _normalize_optional(structured.first("valid_from"), lowercase=False)
    valid_until = _normalize_optional(structured.first("valid_until"), lowercase=False)
    issues: list[dict[str, Any]] = []

    if record_type is not None and RECORD_TYPE_RE.fullmatch(record_type) is None:
        issues.append({"type": "invalid_record_type", "value": record_type})

    if status is not None:
        if STATUS_RE.fullmatch(status) is None or status not in ALLOWED_RECORD_STATUSES:
            issues.append({"type": "invalid_status", "value": status})

    confidence, confidence_label = _parse_confidence(confidence_raw, issues)
    parsed_valid_from = _parse_timestamp(valid_from)
    parsed_valid_until = _parse_timestamp(valid_until)
    if valid_from is not None and parsed_valid_from is None:
        issues.append({"type": "invalid_valid_from", "value": valid_from})
    if valid_until is not None and parsed_valid_until is None:
        issues.append({"type": "invalid_valid_until", "value": valid_until})
    if parsed_valid_from is not None and parsed_valid_until is not None and parsed_valid_from > parsed_valid_until:
        issues.append(
            {
                "type": "invalid_validity_interval",
                "valid_from": valid_from,
                "valid_until": valid_until,
            }
        )

    normalized_tags = tuple(dict.fromkeys(tag.strip() for tag in tags if tag.strip()))
    kind_tags = [tag for tag in normalized_tags if tag.startswith("kind:")]
    if record_type is not None and kind_tags and f"kind:{record_type}" not in kind_tags:
        issues.append(
            {
                "type": "record_type_tag_mismatch",
                "record_type": record_type,
                "kind_tags": kind_tags,
            }
        )

    lineage = parse_lineage(content)
    machine_owned_target_ids = frozenset(
        _machine_owned_source_ids(
            kind=kind,
            record_type=record_type,
            tags=set(normalized_tags),
            actor=actor,
            source_app=source_app,
            is_learning_candidate=is_learning_candidate,
            lineage=lineage,
        )
    )
    return RecordProjection(
        metadata=CanonicalMetadata(
            record_type=record_type,
            status=status,
            confidence=confidence,
            confidence_label=confidence_label,
            valid_from=valid_from,
            valid_until=valid_until,
            validation_issues=tuple(issues),
        ),
        tags=normalized_tags,
        lineage=lineage,
        machine_owned_target_ids=machine_owned_target_ids,
    )


def sync_record_projection(
    conn: sqlite3.Connection,
    *,
    memory_id: str,
    namespace: str,
    content: str,
    tags: list[str],
    kind: str,
    actor: str | None,
    source_app: str | None,
    is_learning_candidate: bool,
    reject_invalid: bool = False,
) -> RecordProjection:
    resolved = resolve_record_projection(
        conn,
        memory_id=memory_id,
        namespace=namespace,
        content=content,
        tags=tags,
        kind=kind,
        actor=actor,
        source_app=source_app,
        is_learning_candidate=is_learning_candidate,
    )
    projection = resolved.projection
    if reject_invalid and projection.metadata.validation_issues:
        issue_types = ", ".join(issue["type"] for issue in projection.metadata.validation_issues)
        raise ValueError(f"invalid structured memory metadata: {issue_types}")

    conn.execute("DELETE FROM memory_metadata WHERE memory_id = ?", (memory_id,))
    conn.execute("DELETE FROM memory_tags WHERE memory_id = ?", (memory_id,))
    conn.execute("DELETE FROM memory_edges WHERE source_id = ?", (memory_id,))
    metadata = projection.metadata
    conn.execute(
        """
        INSERT INTO memory_metadata (
            memory_id,
            record_type,
            status,
            confidence,
            confidence_label,
            valid_from,
            valid_until,
            metadata_schema_version,
            validation_issues_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            memory_id,
            metadata.record_type,
            metadata.status,
            metadata.confidence,
            metadata.confidence_label,
            metadata.valid_from,
            metadata.valid_until,
            METADATA_SCHEMA_VERSION,
            json.dumps(metadata.validation_issues, ensure_ascii=True, sort_keys=True, separators=(",", ":")),
        ),
    )
    conn.executemany(
        "INSERT INTO memory_tags (memory_id, tag, prefix) VALUES (?, ?, ?)",
        ((memory_id, tag, _tag_prefix(tag)) for tag in projection.tags),
    )
    conn.executemany(
        """
        INSERT INTO memory_edges (
            source_id,
            target_id,
            relation,
            position,
            machine_owned,
            target_namespace,
            target_exists
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        resolved.edge_rows,
    )
    conn.execute(
        """
        UPDATE memory_edges
        SET target_namespace = ?, target_exists = 1
        WHERE target_id = ?
        """,
        (namespace, memory_id),
    )
    return projection


def resolve_record_projection(
    conn: sqlite3.Connection,
    *,
    memory_id: str,
    namespace: str,
    content: str,
    tags: list[str],
    kind: str,
    actor: str | None,
    source_app: str | None,
    is_learning_candidate: bool,
) -> ResolvedRecordProjection:
    projection = build_record_projection(
        content=content,
        tags=tags,
        kind=kind,
        actor=actor,
        source_app=source_app,
        is_learning_candidate=is_learning_candidate,
    )
    edge_rows: list[tuple[str, str, str, int, int, str | None, int]] = []
    projection_issues = list(projection.metadata.validation_issues)
    for position, reference in enumerate(projection.lineage.references):
        target = conn.execute(
            "SELECT namespace FROM memories WHERE id = ? LIMIT 1",
            (reference.target_id,),
        ).fetchone()
        target_namespace = str(target["namespace"]) if target is not None else None
        if (
            target_namespace is not None
            and target_namespace != namespace
            and reference.relation.value
            in {
                "candidate_id",
                "derived_from_belief_id",
                "derived_from_candidate_id",
                "evidence_refs",
                "source_candidate_id",
                "target_record_id",
            }
        ):
            projection_issues.append(
                {
                    "type": "cross_namespace_lineage_target",
                    "relation": reference.relation.value,
                    "target_id": reference.target_id,
                    "source_namespace": namespace,
                    "target_namespace": target_namespace,
                }
            )
        edge_rows.append(
            (
                memory_id,
                reference.target_id,
                reference.relation.value,
                position,
                int(reference.target_id in projection.machine_owned_target_ids),
                target_namespace,
                int(target is not None),
            )
        )
    if tuple(projection_issues) != projection.metadata.validation_issues:
        projection = replace(
            projection,
            metadata=replace(projection.metadata, validation_issues=tuple(projection_issues)),
        )
    return ResolvedRecordProjection(projection=projection, edge_rows=tuple(edge_rows))


def backfill_record_projections(conn: sqlite3.Connection, *, only_missing: bool = False) -> int:
    where_sql = ""
    if only_missing:
        where_sql = "WHERE NOT EXISTS (SELECT 1 FROM memory_metadata mm WHERE mm.memory_id = m.id)"
    rows = conn.execute(
        f"""
        SELECT
            m.id,
            m.namespace,
            m.kind,
            m.content,
            m.tags_json,
            m.actor,
            m.source_app,
            m.is_learning_candidate
        FROM memories m
        {where_sql}
        ORDER BY m.rowid ASC
        """
    ).fetchall()
    for row in rows:
        try:
            tags = json.loads(row["tags_json"] or "[]")
        except (TypeError, json.JSONDecodeError):
            tags = []
        sync_record_projection(
            conn,
            memory_id=str(row["id"]),
            namespace=str(row["namespace"]),
            content=str(row["content"]),
            tags=[str(tag) for tag in tags if isinstance(tag, str)],
            kind=str(row["kind"]),
            actor=str(row["actor"]) if row["actor"] is not None else None,
            source_app=str(row["source_app"]) if row["source_app"] is not None else None,
            is_learning_candidate=bool(row["is_learning_candidate"]),
        )
    return len(rows)


def _parse_confidence(
    raw_value: str | None,
    issues: list[dict[str, Any]],
) -> tuple[float | None, str | None]:
    if raw_value is None:
        return None, None
    try:
        numeric = float(raw_value)
    except ValueError:
        label = raw_value.lower()
        if CONFIDENCE_LABEL_RE.fullmatch(label) is None or label not in ALLOWED_CONFIDENCE_LABELS:
            issues.append({"type": "invalid_confidence", "value": raw_value})
        return None, label
    if not math.isfinite(numeric) or not 0.0 <= numeric <= 1.0:
        issues.append({"type": "invalid_confidence", "value": raw_value})
        return None, None
    return numeric, None


def _parse_timestamp(value: str | None) -> datetime | None:
    if value is None:
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _machine_owned_source_ids(
    *,
    kind: str,
    record_type: str | None,
    tags: set[str],
    actor: str | None,
    source_app: str | None,
    is_learning_candidate: bool,
    lineage: Lineage,
) -> set[str]:
    generated_by_consolidation = (
        "source:consolidation" in tags
        or actor == "bridge-consolidation"
        or source_app == "agent-memory-bridge-consolidation"
    )
    if generated_by_consolidation and record_type == "belief" and "kind:belief" in tags:
        return _optional_id_set(lineage.derived_from_candidate_id)
    if generated_by_consolidation and record_type == "concept-note" and "kind:concept-note" in tags:
        return _optional_id_set(lineage.derived_from_belief_id)
    if generated_by_consolidation and record_type == "belief-candidate" and "kind:belief-candidate" in tags:
        return set(lineage.evidence_refs)
    if is_learning_candidate and tags.intersection({"kind:learning-candidate", "kind:learning-review"}):
        if "kind:learning-review" in tags or record_type == "learning-review":
            return _optional_id_set(lineage.source_candidate_id)
        return set(lineage.evidence_refs)
    if kind == "signal" and "kind:governance-trigger" in tags and record_type == "governance-trigger":
        return _optional_id_set(lineage.candidate_id)
    return set()


def _tag_prefix(tag: str) -> str:
    prefix, separator, _ = tag.partition(":")
    return f"{prefix}:" if separator else ""


def _normalize_optional(value: str | None, *, lowercase: bool = True) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.split()).strip()
    if not normalized:
        return None
    return normalized.lower() if lowercase else normalized


def _optional_id_set(value: str | None) -> set[str]:
    return {value} if value is not None else set()
