from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from .relation_metadata import parse_content_fields
from .repository import MEMORY_ROW_SELECT, MemoryRow
from .storage import MemoryStore

ACTIVATION_RECEIPT_SCHEMA = "amb.cross-client.activation-receipt.v1"
WORKFLOW_TAG = "workflow:cross-client-activation"
WRITER_TAG = "activation-role:writer"
READER_TAG = "activation-role:reader"
REVIEWED_TAG = "reviewed:true"


def build_activation_receipt(
    store: MemoryStore,
    *,
    namespace: str,
    correlation_id: str,
) -> dict[str, Any]:
    return build_activation_receipt_from_db(
        store.db_path,
        namespace=namespace,
        correlation_id=correlation_id,
    )


def build_activation_receipt_from_db(
    db_path: Path,
    *,
    namespace: str,
    correlation_id: str,
) -> dict[str, Any]:
    cleaned_namespace = namespace.strip()
    cleaned_correlation_id = correlation_id.strip()
    if not cleaned_namespace:
        raise ValueError("namespace must not be empty")
    if not cleaned_correlation_id:
        raise ValueError("correlation_id must not be empty")

    try:
        items = _fetch_activation_items_read_only(
            db_path,
            namespace=cleaned_namespace,
            correlation_id=cleaned_correlation_id,
        )
    except (OSError, sqlite3.Error):
        return _assemble_activation_receipt(
            [],
            namespace=cleaned_namespace,
            correlation_id=cleaned_correlation_id,
            store_available=False,
        )
    return _assemble_activation_receipt(
        items,
        namespace=cleaned_namespace,
        correlation_id=cleaned_correlation_id,
        store_available=True,
    )


def _assemble_activation_receipt(
    items: list[dict[str, Any]],
    *,
    namespace: str,
    correlation_id: str,
    store_available: bool,
) -> dict[str, Any]:
    writer_candidates = _matching_records(items, kind="memory", role_tag=WRITER_TAG)
    reader_candidates = _matching_records(items, kind="signal", role_tag=READER_TAG)
    writer = writer_candidates[0] if len(writer_candidates) == 1 else None
    reader = reader_candidates[0] if len(reader_candidates) == 1 else None

    observed_memory_id = _extract_observed_memory_id(str(reader.get("content") or "")) if reader else None
    writer_source_client = _clean_optional(writer.get("source_client") if writer else None)
    reader_source_client = _clean_optional(reader.get("source_client") if reader else None)
    writer_reviewed = bool(writer and REVIEWED_TAG in {str(tag) for tag in writer.get("tags") or []})
    observed_matches_writer = bool(writer and observed_memory_id == writer.get("id"))

    reason_codes: list[str] = []
    if not store_available:
        reason_codes.append("store_unavailable")
    else:
        if len(writer_candidates) != 1:
            reason_codes.append("writer_record_count_not_one")
        if writer is not None and not writer_reviewed:
            reason_codes.append("writer_memory_not_reviewed")
        if len(reader_candidates) != 1:
            reason_codes.append("reader_signal_count_not_one")
        if len(items) != 2:
            reason_codes.append("unexpected_correlation_records")
        if reader is not None and reader.get("signal_status") != "acked":
            reason_codes.append("reader_signal_not_acked")
        if reader is not None and observed_memory_id is None:
            reason_codes.append("reader_observed_memory_id_missing")
        if reader is not None and writer is not None and observed_memory_id is not None and not observed_matches_writer:
            reason_codes.append("reader_observed_memory_id_mismatch")
        if writer is not None and writer_source_client is None:
            reason_codes.append("writer_source_client_missing")
        if reader is not None and reader_source_client is None:
            reason_codes.append("reader_source_client_missing")
        if _same_source_client_identity(writer_source_client, reader_source_client):
            reason_codes.append("source_client_not_cross_client")

    status = "pass" if not reason_codes else "review_required"
    return {
        "schema": ACTIVATION_RECEIPT_SCHEMA,
        "status": status,
        "reason_codes": reason_codes,
        "declared_provenance_only": True,
        "authenticated_origin": False,
        "external_adoption_claim": False,
        "public_mcp_surface_change": False,
        "durable_writeback_count": 0,
        "config_write_count": 0,
        "store_available": store_available,
        "namespace_hash": stable_short_sha256(namespace),
        "correlation_hash": stable_short_sha256(correlation_id),
        "matching_record_count": len(items),
        "required_tags": [WORKFLOW_TAG, WRITER_TAG, REVIEWED_TAG, READER_TAG],
        "writer": {
            "matched_count": len(writer_candidates),
            "record_id_hashes": _record_id_hashes(writer_candidates),
            "reviewed": writer_reviewed,
            "source_client_present": writer_source_client is not None,
            "source_client_hash": stable_short_sha256(writer_source_client) if writer_source_client else None,
        },
        "reader": {
            "matched_count": len(reader_candidates),
            "record_id_hashes": _record_id_hashes(reader_candidates),
            "signal_status": reader.get("signal_status") if reader is not None else None,
            "source_client_present": reader_source_client is not None,
            "source_client_hash": stable_short_sha256(reader_source_client) if reader_source_client else None,
            "observed_memory_id_hash": stable_short_sha256(observed_memory_id) if observed_memory_id else None,
            "observed_memory_matches_writer": observed_matches_writer,
        },
        "source_client_relation": _source_client_relation(writer_source_client, reader_source_client),
    }


def render_activation_receipt_markdown(receipt: dict[str, Any]) -> str:
    reason_codes = receipt.get("reason_codes") or []
    writer = receipt.get("writer") or {}
    reader = receipt.get("reader") or {}
    lines = [
        "# Cross-Client Activation Receipt",
        "",
        "**Declared provenance only: this receipt does not authenticate client identity.**",
        "**This receipt is not proof of external adoption or vendor certification.**",
        "",
        f"- Schema: `{receipt['schema']}`",
        f"- Status: `{receipt['status']}`",
        f"- Reason codes: `{', '.join(reason_codes) if reason_codes else 'none'}`",
        f"- Namespace hash: `{receipt['namespace_hash']}`",
        f"- Correlation hash: `{receipt['correlation_hash']}`",
        f"- Matching record count: `{receipt['matching_record_count']}`",
        f"- Store available: `{str(receipt['store_available']).lower()}`",
        f"- Public MCP surface change: `{str(receipt['public_mcp_surface_change']).lower()}`",
        f"- Durable writeback count: `{receipt['durable_writeback_count']}`",
        f"- Config write count: `{receipt['config_write_count']}`",
        "",
        "## Writer",
        "",
        f"- Matched count: `{writer.get('matched_count')}`",
        f"- Record ID hashes: `{_format_hashes(writer.get('record_id_hashes') or [])}`",
        f"- Reviewed: `{str(writer.get('reviewed')).lower()}`",
        f"- Source client present: `{str(writer.get('source_client_present')).lower()}`",
        f"- Source client hash: `{writer.get('source_client_hash') or 'none'}`",
        "",
        "## Reader",
        "",
        f"- Matched count: `{reader.get('matched_count')}`",
        f"- Record ID hashes: `{_format_hashes(reader.get('record_id_hashes') or [])}`",
        f"- Signal status: `{reader.get('signal_status') or 'none'}`",
        f"- Source client present: `{str(reader.get('source_client_present')).lower()}`",
        f"- Source client hash: `{reader.get('source_client_hash') or 'none'}`",
        f"- Observed memory ID hash: `{reader.get('observed_memory_id_hash') or 'none'}`",
        f"- Observed memory matches writer: `{str(reader.get('observed_memory_matches_writer')).lower()}`",
        f"- Source client relation: `{receipt.get('source_client_relation')}`",
    ]
    return "\n".join(lines)


def stable_short_sha256(value: str, *, length: int = 12) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return f"sha256:{digest[:length]}"


def _fetch_activation_items_read_only(
    db_path: Path,
    *,
    namespace: str,
    correlation_id: str,
) -> list[dict[str, Any]]:
    database_uri = f"{Path(db_path).resolve().as_uri()}?mode=ro"
    with sqlite3.connect(database_uri, uri=True, timeout=5.0) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"""
            SELECT
                {MEMORY_ROW_SELECT}
            FROM memories
            WHERE namespace = ?
              AND correlation_id = ?
            ORDER BY created_at ASC, rowid ASC
            """,
            (namespace, correlation_id),
        ).fetchall()
    return [MemoryRow.from_sqlite(row).as_dict() for row in rows]


def _matching_records(items: list[dict[str, Any]], *, kind: str, role_tag: str) -> list[dict[str, Any]]:
    return [
        item
        for item in items
        if item.get("kind") == kind
        and {str(tag) for tag in item.get("tags") or []}.issuperset({WORKFLOW_TAG, role_tag})
    ]


def _record_id_hashes(items: list[dict[str, Any]]) -> list[str]:
    return [stable_short_sha256(str(item["id"])) for item in sorted(items, key=lambda item: str(item["id"]))]


def _extract_observed_memory_id(content: str) -> str | None:
    stripped = content.strip()
    if not stripped:
        return None
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict):
        value = payload.get("observed_memory_id")
        cleaned = _clean_optional(value)
        if cleaned is not None:
            return cleaned

    return _clean_optional(parse_content_fields(content).get("observed_memory_id"))


def _source_client_relation(writer_source_client: str | None, reader_source_client: str | None) -> str:
    if writer_source_client is None or reader_source_client is None:
        return "incomplete_declared_values"
    if _same_source_client_identity(writer_source_client, reader_source_client):
        return "same_declared_value"
    return "distinct_declared_values"


def _same_source_client_identity(writer_source_client: str | None, reader_source_client: str | None) -> bool:
    if writer_source_client is None or reader_source_client is None:
        return False
    return writer_source_client.casefold() == reader_source_client.casefold()


def _clean_optional(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _format_hashes(values: list[str]) -> str:
    if not values:
        return "none"
    return ", ".join(values)
