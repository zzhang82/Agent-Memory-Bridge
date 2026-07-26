from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .filesystem_safety import ensure_private_directory, ensure_private_file
from .schema import database_epoch as read_database_epoch
from .telemetry import hash_label

RECALL_RECEIPT_SCHEMA = "amb.recall-receipt.v1"
RECALL_RECEIPT_SECRET_SCHEMA = "amb.recall-receipt-secret.v1"
RETRIEVAL_FEEDBACK_SCHEMA = "amb.retrieval-feedback.v1"
RECALL_RECEIPT_TTL_SECONDS = 15 * 60
FEEDBACK_OUTCOMES = {"helpful", "misleading", "outdated", "not_applicable", "not_used"}
FEEDBACK_REASON_REQUIRED = {"misleading", "outdated"}
MAX_FEEDBACK_REASON_CHARS = 280
TOKEN_PREFIX = "v1"


@dataclass(frozen=True, slots=True)
class RecallReceiptSecret:
    bridge_instance_id: str
    hmac_key: bytes


def should_issue_recall_receipt(*, query: str, kind: str | None) -> bool:
    return bool(query.strip()) and kind == "memory"


def issue_recall_receipt(
    store: Any,
    *,
    secret: RecallReceiptSecret,
    namespace: str,
    query: str,
    items: list[dict[str, Any]],
    retrieval_mode: str,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    expires_at = now + timedelta(seconds=RECALL_RECEIPT_TTL_SECONDS)
    with store._connect() as conn:
        database_epoch = read_database_epoch(conn)
    results = [
        {"memory_id": str(item["id"]), "rank": rank}
        for rank, item in enumerate(items, start=1)
        if item.get("kind") == "memory" and item.get("id")
    ]
    payload = {
        "schema": RECALL_RECEIPT_SCHEMA,
        "bridge_instance_id": secret.bridge_instance_id,
        "database_epoch": database_epoch,
        "namespace": namespace,
        "query_hash": hash_label(query),
        "retrieval_mode": retrieval_mode,
        "issued_at": now.isoformat(),
        "expires_at": expires_at.isoformat(),
        "results": results,
    }
    token = encode_recall_receipt(payload, secret=secret)
    return {
        "schema": RECALL_RECEIPT_SCHEMA,
        "token": token,
        "issued_for": "durable_memory_text_recall",
        "expires_at": payload["expires_at"],
        "ttl_seconds": RECALL_RECEIPT_TTL_SECONDS,
        "result_count": len(results),
        "provenance": "server_declared_not_authenticated",
        "authenticated_origin": False,
    }


def record_retrieval_feedback(
    store: Any,
    *,
    secret_path: Path,
    namespace: str,
    recall_receipt: str,
    memory_id: str,
    result_rank: int,
    outcome: str,
    reason: str | None = None,
    source_app: str | None = None,
    source_client: str | None = None,
    source_model: str | None = None,
    client_session_id: str | None = None,
    client_workspace: str | None = None,
    client_transport: str | None = None,
    actor: str | None = None,
) -> dict[str, Any]:
    cleaned_namespace = _required_text(namespace, "namespace")
    cleaned_memory_id = _required_text(memory_id, "memory_id")
    cleaned_outcome = _normalize_outcome(outcome)
    cleaned_reason = _normalize_reason(cleaned_outcome, reason)
    cleaned_source_app = _optional_text(source_app)
    cleaned_source_client = _optional_text(source_client)
    cleaned_source_model = _optional_text(source_model)
    cleaned_client_session_id = _optional_text(client_session_id)
    cleaned_client_workspace = _optional_text(client_workspace)
    cleaned_client_transport = _optional_text(client_transport)
    cleaned_actor = _optional_text(actor)
    if result_rank <= 0:
        raise ValueError("result_rank must be greater than zero")

    validated = validate_recall_receipt(
        store,
        secret=getattr(store, "recall_receipt_secret", None),
        secret_path=secret_path,
        recall_receipt=recall_receipt,
        namespace=cleaned_namespace,
        memory_id=cleaned_memory_id,
        result_rank=result_rank,
    )
    receipt_payload = validated["payload"]
    receipt_hash = validated["receipt_hash"]
    feedback_json = _feedback_json(
        receipt_payload,
        receipt_hash=receipt_hash,
    )
    idempotency_key = _feedback_idempotency_key(
        _feedback_identity_payload(
            receipt_hash=receipt_hash,
            namespace=cleaned_namespace,
            memory_id=cleaned_memory_id,
            result_rank=result_rank,
            source_client=cleaned_source_client,
            client_session_id=cleaned_client_session_id,
        )
    )
    created_at = datetime.now(UTC).isoformat()
    with store._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = _fetch_feedback_by_idempotency_key(conn, idempotency_key)
            if row is not None:
                if row["outcome"] != cleaned_outcome or row["reason"] != cleaned_reason:
                    raise ValueError("conflicting feedback for recall receipt identity")
                stored = False
            else:
                conn.execute(
                    """
                    INSERT INTO retrieval_feedback (
                        idempotency_key,
                        receipt_hash,
                        namespace,
                        memory_id,
                        result_rank,
                        outcome,
                        reason,
                        retrieval_mode,
                        database_epoch,
                        bridge_instance_id,
                        receipt_issued_at,
                        receipt_expires_at,
                        feedback_json,
                        source_app,
                        source_client,
                        source_model,
                        client_session_id,
                        client_workspace,
                        client_transport,
                        actor,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        idempotency_key,
                        receipt_hash,
                        cleaned_namespace,
                        cleaned_memory_id,
                        result_rank,
                        cleaned_outcome,
                        cleaned_reason,
                        _required_text(str(receipt_payload.get("retrieval_mode") or ""), "retrieval_mode"),
                        str(receipt_payload["database_epoch"]),
                        str(receipt_payload["bridge_instance_id"]),
                        str(receipt_payload["issued_at"]),
                        str(receipt_payload["expires_at"]),
                        feedback_json,
                        cleaned_source_app,
                        cleaned_source_client,
                        cleaned_source_model,
                        cleaned_client_session_id,
                        cleaned_client_workspace,
                        cleaned_client_transport,
                        cleaned_actor,
                        created_at,
                    ),
                )
                stored = True
                row = _fetch_feedback_by_idempotency_key(conn, idempotency_key)
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    if row is None:
        raise RuntimeError("retrieval feedback insert did not return a row")
    store._log(
        "feedback",
        {
            "namespace_hash": hash_label(cleaned_namespace),
            "memory_id_hash": hash_label(cleaned_memory_id),
            "result_rank": result_rank,
            "outcome": cleaned_outcome,
            "stored": stored,
            "duplicate": not stored,
            "has_reason": cleaned_reason is not None,
            "receipt_hash": receipt_hash[:12],
            "has_source_client": cleaned_source_client is not None,
            "source_client_hash": hash_label(cleaned_source_client),
            "has_source_model": cleaned_source_model is not None,
            "has_client_session_id": cleaned_client_session_id is not None,
            "client_session_hash": hash_label(cleaned_client_session_id),
            "has_client_workspace": cleaned_client_workspace is not None,
            "client_transport": _safe_transport_category(cleaned_client_transport),
            "provenance": "server_declared_not_authenticated",
            "authenticated_origin": False,
        },
    )
    return _feedback_row_response(row, stored=stored)


def validate_recall_receipt(
    store: Any,
    *,
    secret: RecallReceiptSecret | None = None,
    secret_path: Path | None = None,
    recall_receipt: str,
    namespace: str,
    memory_id: str,
    result_rank: int,
) -> dict[str, Any]:
    resolved_secret = secret or getattr(store, "recall_receipt_secret", None)
    if resolved_secret is None:
        if secret_path is None:
            raise ValueError("recall receipt secret is required")
        resolved_secret = load_or_create_recall_receipt_secret(secret_path)
    payload = decode_recall_receipt(recall_receipt, secret=resolved_secret)
    if payload.get("schema") != RECALL_RECEIPT_SCHEMA:
        raise ValueError("invalid recall receipt: schema mismatch")
    if payload.get("bridge_instance_id") != resolved_secret.bridge_instance_id:
        raise ValueError("invalid recall receipt: bridge instance mismatch")
    with store._connect() as conn:
        active_epoch = read_database_epoch(conn)
    if payload.get("database_epoch") != active_epoch:
        raise ValueError("invalid recall receipt: database epoch mismatch")
    if payload.get("namespace") != namespace:
        raise ValueError("invalid recall receipt: namespace mismatch")

    issued_at = _parse_receipt_time(payload.get("issued_at"), "issued_at")
    expires_at = _parse_receipt_time(payload.get("expires_at"), "expires_at")
    if expires_at <= datetime.now(UTC):
        raise ValueError("invalid recall receipt: expired")
    if expires_at < issued_at:
        raise ValueError("invalid recall receipt: expiry precedes issue time")

    results = payload.get("results")
    if not isinstance(results, list):
        raise ValueError("invalid recall receipt: missing results")
    for result in results:
        if not isinstance(result, dict):
            continue
        if result.get("memory_id") == memory_id and result.get("rank") == result_rank:
            return {"payload": payload, "receipt_hash": recall_receipt_hash(recall_receipt)}
    raise ValueError("invalid recall receipt: memory id and rank mismatch")


def encode_recall_receipt(payload: dict[str, Any], *, secret: RecallReceiptSecret) -> str:
    payload_part = _base64url_encode(_canonical_json(payload))
    signature = hmac.new(secret.hmac_key, payload_part.encode("ascii"), hashlib.sha256).digest()
    return f"{TOKEN_PREFIX}.{payload_part}.{_base64url_encode(signature)}"


def decode_recall_receipt(token: str, *, secret: RecallReceiptSecret) -> dict[str, Any]:
    parts = token.strip().split(".")
    if len(parts) != 3 or parts[0] != TOKEN_PREFIX:
        raise ValueError("invalid recall receipt: malformed token")
    payload_part = parts[1]
    expected_signature = hmac.new(secret.hmac_key, payload_part.encode("ascii"), hashlib.sha256).digest()
    try:
        observed_signature = _base64url_decode(parts[2])
    except ValueError as exc:
        raise ValueError("invalid recall receipt: malformed signature") from exc
    if not hmac.compare_digest(observed_signature, expected_signature):
        raise ValueError("invalid recall receipt: signature mismatch")
    try:
        payload = json.loads(_base64url_decode(payload_part))
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        raise ValueError("invalid recall receipt: malformed payload") from exc
    if not isinstance(payload, dict):
        raise ValueError("invalid recall receipt: malformed payload")
    return payload


def recall_receipt_hash(token: str) -> str:
    return hashlib.sha256(token.strip().encode("utf-8")).hexdigest()


def load_or_create_recall_receipt_secret(secret_path: Path) -> RecallReceiptSecret:
    path = Path(secret_path)
    if path.exists():
        return _read_recall_receipt_secret(path)
    ensure_private_directory(path.parent)
    payload = {
        "schema": RECALL_RECEIPT_SECRET_SCHEMA,
        "bridge_instance_id": secrets.token_hex(16),
        "hmac_key": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii"),
        "created_at": datetime.now(UTC).isoformat(),
    }
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(encoded)
    except FileExistsError:
        return _read_recall_receipt_secret(path)
    ensure_private_file(path)
    return _coerce_secret_payload(payload, path=path)


def _read_recall_receipt_secret(path: Path) -> RecallReceiptSecret:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("recall receipt secret is unreadable or malformed") from exc
    return _coerce_secret_payload(payload, path=path)


def _coerce_secret_payload(payload: Any, *, path: Path) -> RecallReceiptSecret:
    if not isinstance(payload, dict) or payload.get("schema") != RECALL_RECEIPT_SECRET_SCHEMA:
        raise RuntimeError("recall receipt secret schema is invalid")
    bridge_instance_id = _required_text(str(payload.get("bridge_instance_id") or ""), "bridge_instance_id")
    try:
        key = base64.urlsafe_b64decode(str(payload.get("hmac_key") or ""))
    except ValueError as exc:
        raise RuntimeError("recall receipt secret key is invalid") from exc
    if len(key) < 32:
        raise RuntimeError("recall receipt secret key is too short")
    ensure_private_file(path)
    return RecallReceiptSecret(bridge_instance_id=bridge_instance_id, hmac_key=key)


def _normalize_outcome(outcome: str) -> str:
    cleaned = outcome.strip().lower()
    if cleaned not in FEEDBACK_OUTCOMES:
        raise ValueError(f"outcome must be one of {sorted(FEEDBACK_OUTCOMES)}")
    return cleaned


def _normalize_reason(outcome: str, reason: str | None) -> str | None:
    cleaned = " ".join(str(reason or "").split()) or None
    if outcome in FEEDBACK_REASON_REQUIRED and cleaned is None:
        raise ValueError("reason is required for misleading or outdated feedback")
    if cleaned is not None and len(cleaned) > MAX_FEEDBACK_REASON_CHARS:
        raise ValueError(f"reason must be {MAX_FEEDBACK_REASON_CHARS} characters or fewer")
    return cleaned


def _required_text(value: str, name: str) -> str:
    cleaned = str(value).strip()
    if not cleaned:
        raise ValueError(f"{name} must not be empty")
    return cleaned


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _safe_transport_category(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in {"stdio", "http", "sse"}:
        return normalized
    return "other"


def _parse_receipt_time(value: Any, field_name: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"invalid recall receipt: missing {field_name}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid recall receipt: malformed {field_name}") from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _feedback_json(receipt_payload: dict[str, Any], *, receipt_hash: str) -> str:
    payload = {
        "schema": RETRIEVAL_FEEDBACK_SCHEMA,
        "receipt_schema": receipt_payload.get("schema"),
        "receipt_hash": receipt_hash,
        "query_hash": receipt_payload.get("query_hash"),
        "result_count": len(receipt_payload.get("results") or []),
        "feedback_mode": "shadow_only",
        "ordering_effect": "none",
        "provenance": "server_declared_not_authenticated",
        "authenticated_origin": False,
    }
    return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _feedback_identity_payload(
    *,
    receipt_hash: str,
    namespace: str,
    memory_id: str,
    result_rank: int,
    source_client: str | None,
    client_session_id: str | None,
) -> dict[str, Any]:
    return {
        "receipt_hash": receipt_hash,
        "namespace": namespace,
        "memory_id": memory_id,
        "result_rank": result_rank,
        "source_client": source_client,
        "client_session_id": client_session_id,
    }


def _feedback_idempotency_key(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


def _canonical_json(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _base64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _base64url_decode(encoded: str) -> bytes:
    padding = "=" * (-len(encoded) % 4)
    try:
        return base64.urlsafe_b64decode((encoded + padding).encode("ascii"))
    except (binascii.Error, ValueError) as exc:
        raise ValueError("invalid base64url value") from exc


def _fetch_feedback_by_idempotency_key(conn: sqlite3.Connection, idempotency_key: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT
            feedback_id,
            namespace,
            memory_id,
            result_rank,
            outcome,
            reason,
            retrieval_mode,
            receipt_hash,
            source_app,
            source_client,
            source_model,
            client_session_id,
            client_workspace,
            client_transport,
            actor,
            created_at
        FROM retrieval_feedback
        WHERE idempotency_key = ?
        LIMIT 1
        """,
        (idempotency_key,),
    ).fetchone()


def _feedback_row_response(row: sqlite3.Row, *, stored: bool) -> dict[str, Any]:
    return {
        "stored": stored,
        "duplicate": not stored,
        "feedback_id": int(row["feedback_id"]),
        "namespace_hash": hash_label(str(row["namespace"])),
        "memory_id_hash": hash_label(str(row["memory_id"])),
        "result_rank": int(row["result_rank"]),
        "outcome": row["outcome"],
        "retrieval_mode": row["retrieval_mode"],
        "receipt_bound": True,
        "feedback_mode": "shadow_only",
        "ordering": "unchanged",
        "ordering_unchanged": True,
        "provenance": "server_declared_not_authenticated",
        "authenticated_origin": False,
        "diagnostics": {
            "mode": "shadow_only",
            "ordering_effect": "none",
            "ordering": "unchanged",
            "returned_ordering_changed": False,
            "provenance": "server_declared_not_authenticated",
            "authenticated_origin": False,
        },
    }
