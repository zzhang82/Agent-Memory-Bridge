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

RECALL_RECEIPT_SCHEMA = "amb.recall-receipt.v2"
RECALL_RECEIPT_SECRET_SCHEMA = "amb.recall-receipt-secret.v1"
RETRIEVAL_FEEDBACK_SCHEMA = "amb.retrieval-feedback.v2"
RETRIEVAL_CONTRACT_SCHEMA = "amb.retrieval-contract.v1"
EVIDENCE_CONTEXT_SCHEMA = "amb.evidence-context-digests.v1"
FEEDBACK_IDENTITY_SCHEMA = "amb.feedback-identity.v1"
RECALL_RECEIPT_TTL_SECONDS = 15 * 60
FEEDBACK_TYPES = {"vote", "correction", "retraction"}
FEEDBACK_OUTCOMES = {"helpful", "misleading", "outdated", "not_applicable", "not_used"}
FEEDBACK_REASON_REQUIRED = {"misleading", "outdated"}
MAX_FEEDBACK_REASON_CHARS = 280
EVIDENCE_CONTEXT_FIELDS = ("model", "harness", "chat_template")
MAX_EVIDENCE_CONTEXT_VALUE_CHARS = 256
TOKEN_PREFIX = "v2"


@dataclass(frozen=True, slots=True)
class RecallReceiptSecret:
    bridge_instance_id: str
    hmac_key: bytes


def should_issue_recall_receipt(*, query: str, kind: str | None) -> bool:
    return bool(query.strip()) and kind == "memory"


def issue_recall_receipt(
    *,
    secret: RecallReceiptSecret,
    database_epoch: str,
    namespace: str,
    query: str,
    items: list[dict[str, Any]],
    retrieval_mode: str,
    limit: int,
    kind: str,
    signal_status: str | None,
    tags_any: list[str] | None,
    session_id: str | None,
    actor: str | None,
    correlation_id: str | None,
    since: str | None,
    evidence_context: dict[str, str] | None = None,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    expires_at = now + timedelta(seconds=RECALL_RECEIPT_TTL_SECONDS)
    results = [
        {"memory_id": str(item["id"]), "rank": rank}
        for rank, item in enumerate(items, start=1)
        if item.get("kind") == "memory" and item.get("id")
    ]
    exposure_set = _recall_exposure_set(items)
    retrieval_contract = build_retrieval_contract(
        namespace=namespace,
        query=query,
        retrieval_mode=retrieval_mode,
        limit=limit,
        kind=kind,
        signal_status=signal_status,
        tags_any=tags_any,
        session_id=session_id,
        actor=actor,
        correlation_id=correlation_id,
        since=since,
    )
    payload = {
        "schema": RECALL_RECEIPT_SCHEMA,
        "bridge_instance_id": secret.bridge_instance_id,
        "database_epoch": database_epoch,
        "namespace": namespace,
        "query_hash": hash_label(query),
        "retrieval_mode": retrieval_mode,
        "retrieval_contract": retrieval_contract,
        "retrieval_contract_digest": canonical_retrieval_contract_digest(retrieval_contract),
        "retrieval_contract_attestation": "server",
        "issued_at": now.isoformat(),
        "expires_at": expires_at.isoformat(),
        "results": results,
        "exposure_set": exposure_set,
    }
    receipt_evidence_context = _receipt_evidence_context(evidence_context)
    if receipt_evidence_context is not None:
        payload["evidence_context"] = receipt_evidence_context
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
    outcome: str | None = None,
    reason: str | None = None,
    feedback_type: str = "vote",
    supersedes_feedback_id: int | None = None,
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
    cleaned_feedback_type = _normalize_feedback_type(feedback_type)
    cleaned_supersedes_feedback_id = _normalize_supersedes_feedback_id(supersedes_feedback_id)
    if cleaned_feedback_type == "vote" and cleaned_supersedes_feedback_id is not None:
        raise ValueError("vote feedback must not supersede another feedback event")
    if cleaned_feedback_type != "vote" and cleaned_supersedes_feedback_id is None:
        raise ValueError(f"{cleaned_feedback_type} feedback must supersede the current feedback head")
    if cleaned_feedback_type == "retraction":
        cleaned_outcome = _normalize_outcome(outcome) if outcome is not None else None
        cleaned_reason = _normalize_optional_reason(reason)
    else:
        if outcome is None:
            raise ValueError(f"outcome is required for {cleaned_feedback_type} feedback")
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
    receipt_audit_hash = validated["receipt_hash"]
    feedback_identity_digest = validated["feedback_identity_digest"]
    feedback_json = _feedback_json(
        receipt_payload,
        receipt_hash=receipt_audit_hash,
        feedback_identity_digest=feedback_identity_digest,
        memory_id=cleaned_memory_id,
        result_rank=result_rank,
    )
    subject = _feedback_identity_payload(
        feedback_identity_digest=feedback_identity_digest,
        namespace=cleaned_namespace,
        memory_id=cleaned_memory_id,
        result_rank=result_rank,
    )
    idempotency_key = _feedback_event_idempotency_key(
        subject,
        feedback_type=cleaned_feedback_type,
        supersedes_feedback_id=cleaned_supersedes_feedback_id,
    )
    created_at = datetime.now(UTC).isoformat()
    with store._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = _fetch_feedback_by_idempotency_key(conn, idempotency_key)
            if row is not None:
                if not _feedback_request_matches(
                    row,
                    feedback_type=cleaned_feedback_type,
                    supersedes_feedback_id=cleaned_supersedes_feedback_id,
                    outcome=cleaned_outcome,
                    reason=cleaned_reason,
                ):
                    if cleaned_feedback_type == "vote":
                        raise ValueError("conflicting plain feedback vote; submit a correction")
                    raise ValueError("conflicting feedback for recall receipt identity")
                stored = False
            else:
                head = _fetch_feedback_head(
                    conn,
                    feedback_identity_digest=feedback_identity_digest,
                    namespace=cleaned_namespace,
                    memory_id=cleaned_memory_id,
                    result_rank=result_rank,
                )
                if cleaned_feedback_type == "vote" and head is not None:
                    if _feedback_request_matches(
                        head,
                        feedback_type="vote",
                        supersedes_feedback_id=None,
                        outcome=cleaned_outcome,
                        reason=cleaned_reason,
                    ):
                        row = head
                        stored = False
                    else:
                        raise ValueError("conflicting plain feedback vote; submit a correction")
                else:
                    if cleaned_feedback_type != "vote":
                        if head is None or int(head["feedback_id"]) != cleaned_supersedes_feedback_id:
                            raise ValueError(
                                f"{cleaned_feedback_type} feedback must supersede the current feedback head"
                            )
                    stored_outcome = cleaned_outcome
                    if cleaned_feedback_type == "retraction" and stored_outcome is None:
                        if head is None:
                            raise ValueError("retraction feedback must supersede the current feedback head")
                        stored_outcome = str(head["outcome"])
                    conn.execute(
                        """
                        INSERT INTO retrieval_feedback (
                            idempotency_key,
                            receipt_hash,
                            feedback_identity_digest,
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
                            created_at,
                            feedback_type,
                            supersedes_feedback_id
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            idempotency_key,
                            receipt_audit_hash,
                            feedback_identity_digest,
                            cleaned_namespace,
                            cleaned_memory_id,
                            result_rank,
                            _required_text(str(stored_outcome or ""), "outcome"),
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
                            cleaned_feedback_type,
                            cleaned_supersedes_feedback_id,
                        ),
                    )
                    stored = True
                    row = _fetch_feedback_by_idempotency_key(conn, idempotency_key)
            if row is None:
                raise RuntimeError("retrieval feedback insert did not return a row")
            current_head = _fetch_feedback_head(
                conn,
                feedback_identity_digest=feedback_identity_digest,
                namespace=cleaned_namespace,
                memory_id=cleaned_memory_id,
                result_rank=result_rank,
            )
            effective_vote = (
                current_head is not None
                and int(current_head["feedback_id"]) == int(row["feedback_id"])
                and str(current_head["feedback_type"]) != "retraction"
            )
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
            "outcome": row["outcome"],
            "feedback_type": cleaned_feedback_type,
            "stored": stored,
            "duplicate": not stored,
            "has_reason": cleaned_reason is not None,
            "receipt_hash": receipt_audit_hash[:12],
            "has_source_client": cleaned_source_client is not None,
            "source_client_hash": hash_label(cleaned_source_client),
            "has_source_model": cleaned_source_model is not None,
            "has_client_session_id": cleaned_client_session_id is not None,
            "client_session_hash": hash_label(cleaned_client_session_id),
            "has_client_workspace": cleaned_client_workspace is not None,
            "client_transport": _safe_transport_category(cleaned_client_transport),
            "provenance": "caller_declared_not_authenticated",
            "authenticated_origin": False,
        },
    )
    return _feedback_row_response(row, stored=stored, effective_vote=effective_vote)


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
    if payload.get("namespace") != namespace:
        raise ValueError("invalid recall receipt: namespace mismatch")
    _validate_retrieval_contract(payload)
    _validate_evidence_context(payload)

    issued_at = _parse_receipt_time(payload.get("issued_at"), "issued_at")
    expires_at = _parse_receipt_time(payload.get("expires_at"), "expires_at")
    if expires_at <= datetime.now(UTC):
        raise ValueError("invalid recall receipt: expired")
    if expires_at < issued_at:
        raise ValueError("invalid recall receipt: expiry precedes issue time")

    results = payload.get("results")
    if not isinstance(results, list):
        raise ValueError("invalid recall receipt: missing results")
    if not any(
        isinstance(result, dict) and result.get("memory_id") == memory_id and result.get("rank") == result_rank
        for result in results
    ):
        raise ValueError("invalid recall receipt: memory id and rank mismatch")

    exposure_set = payload.get("exposure_set")
    if not isinstance(exposure_set, list) or len(exposure_set) != len(results):
        raise ValueError("invalid recall receipt: incomplete exposure set")
    _validate_exposure_set(results, exposure_set)
    exposure = next(
        (
            member
            for member in exposure_set
            if isinstance(member, dict) and member.get("memory_id") == memory_id and member.get("rank") == result_rank
        ),
        None,
    )
    if exposure is None:
        raise ValueError("invalid recall receipt: memory id and rank mismatch")
    exact_content_hash = _required_exact_content_hash(exposure.get("exact_content_hash"))
    with store._connect() as conn:
        conn.execute("BEGIN")
        active_epoch = read_database_epoch(conn)
        row = conn.execute(
            """
            SELECT exact_content_hash
            FROM memories
            WHERE id = ? AND namespace = ? AND kind = 'memory'
            LIMIT 1
            """,
            (memory_id, namespace),
        ).fetchone()
    if payload.get("database_epoch") != active_epoch:
        raise ValueError("invalid recall receipt: database epoch mismatch")
    if row is None or not hmac.compare_digest(str(row["exact_content_hash"]), exact_content_hash):
        raise ValueError("invalid recall receipt: memory content hash mismatch")
    return {
        "payload": payload,
        "receipt_hash": recall_receipt_hash(recall_receipt),
        "feedback_identity_digest": canonical_feedback_identity_digest(payload),
    }


def build_retrieval_contract(
    *,
    namespace: str,
    query: str,
    retrieval_mode: str,
    limit: int,
    kind: str,
    signal_status: str | None,
    tags_any: list[str] | None,
    session_id: str | None,
    actor: str | None,
    correlation_id: str | None,
    since: str | None,
) -> dict[str, Any]:
    normalized_tags = sorted({str(tag).strip() for tag in tags_any or [] if str(tag).strip()})
    return {
        "schema": RETRIEVAL_CONTRACT_SCHEMA,
        "namespace": namespace,
        "query_hash": hash_label(query),
        "retrieval_mode": retrieval_mode,
        "limit": limit,
        "kind": kind,
        "signal_status": signal_status,
        "tags_any_digest": _canonical_collection_digest(normalized_tags),
        "session_id_digest": _private_value_digest(session_id),
        "actor_digest": _private_value_digest(actor),
        "correlation_id_digest": _private_value_digest(correlation_id),
        "since_digest": _private_value_digest(since),
    }


def canonical_retrieval_contract_digest(contract: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(contract)).hexdigest()


def canonical_feedback_identity_digest(payload: dict[str, Any]) -> str:
    identity = {
        "schema": FEEDBACK_IDENTITY_SCHEMA,
        "bridge_instance_id": payload.get("bridge_instance_id"),
        "database_epoch": payload.get("database_epoch"),
        "retrieval_contract_digest": payload.get("retrieval_contract_digest"),
        "exposure_set": payload.get("exposure_set"),
    }
    return hashlib.sha256(_canonical_json(identity)).hexdigest()


def _receipt_evidence_context(evidence_context: dict[str, str] | None) -> dict[str, Any] | None:
    if evidence_context is None:
        return None
    if not isinstance(evidence_context, dict):
        raise ValueError("evidence_context must be a mapping")
    unknown_fields = sorted(
        (field for field in evidence_context if field not in EVIDENCE_CONTEXT_FIELDS),
        key=str,
    )
    if unknown_fields:
        raise ValueError(f"evidence_context only supports {list(EVIDENCE_CONTEXT_FIELDS)}")
    digests: dict[str, str] = {}
    for field in EVIDENCE_CONTEXT_FIELDS:
        if field not in evidence_context:
            continue
        value = evidence_context[field]
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"evidence_context.{field} must be a non-empty string")
        if len(value) > MAX_EVIDENCE_CONTEXT_VALUE_CHARS:
            raise ValueError(f"evidence_context.{field} must be {MAX_EVIDENCE_CONTEXT_VALUE_CHARS} characters or fewer")
        digests[field] = _canonical_text_digest(value)
    if not digests:
        return None
    return {
        "schema": EVIDENCE_CONTEXT_SCHEMA,
        "digest_algorithm": "sha256",
        "digest_canonicalization": "json-string-v1",
        "digests": digests,
        "provenance": "caller_declared_not_authenticated",
        "authenticated_origin": False,
    }


def _recall_exposure_set(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    exposures: list[dict[str, Any]] = []
    for rank, item in enumerate(items, start=1):
        if item.get("kind") != "memory" or not item.get("id"):
            continue
        exposures.append(
            {
                "memory_id": str(item["id"]),
                "rank": rank,
                "exact_content_hash": _required_exact_content_hash(item.get("_exact_content_hash")),
                "content_version": _required_exact_content_hash(item.get("_exact_content_hash")),
            }
        )
    return exposures


def _validate_retrieval_contract(payload: dict[str, Any]) -> None:
    contract = payload.get("retrieval_contract")
    if not isinstance(contract, dict) or contract.get("schema") != RETRIEVAL_CONTRACT_SCHEMA:
        raise ValueError("invalid recall receipt: retrieval contract mismatch")
    if payload.get("retrieval_contract_attestation") != "server":
        raise ValueError("invalid recall receipt: retrieval contract attestation mismatch")
    observed_digest = payload.get("retrieval_contract_digest")
    expected_digest = canonical_retrieval_contract_digest(contract)
    if not isinstance(observed_digest, str) or not hmac.compare_digest(observed_digest, expected_digest):
        raise ValueError("invalid recall receipt: retrieval contract digest mismatch")
    for field in ("namespace", "query_hash", "retrieval_mode"):
        if contract.get(field) != payload.get(field):
            raise ValueError("invalid recall receipt: retrieval contract mismatch")


def _validate_evidence_context(payload: dict[str, Any]) -> None:
    context = payload.get("evidence_context")
    if context is None:
        return
    if (
        not isinstance(context, dict)
        or context.get("schema") != EVIDENCE_CONTEXT_SCHEMA
        or context.get("digest_algorithm") != "sha256"
        or context.get("digest_canonicalization") != "json-string-v1"
        or context.get("provenance") != "caller_declared_not_authenticated"
        or context.get("authenticated_origin") is not False
    ):
        raise ValueError("invalid recall receipt: evidence context metadata mismatch")
    digests = context.get("digests")
    if not isinstance(digests, dict) or not digests:
        raise ValueError("invalid recall receipt: evidence context digests missing")
    if set(digests) - set(EVIDENCE_CONTEXT_FIELDS):
        raise ValueError("invalid recall receipt: evidence context field mismatch")
    for digest in digests.values():
        _required_sha256_digest(digest, field_name="evidence context")


def _validate_exposure_set(results: list[Any], exposure_set: list[Any]) -> None:
    expected_members = [(result.get("memory_id"), result.get("rank")) for result in results if isinstance(result, dict)]
    observed_members = [
        (exposure.get("memory_id"), exposure.get("rank")) for exposure in exposure_set if isinstance(exposure, dict)
    ]
    if len(expected_members) != len(results) or observed_members != expected_members:
        raise ValueError("invalid recall receipt: incomplete exposure set")
    for exposure in exposure_set:
        exact_content_hash = _required_exact_content_hash(exposure.get("exact_content_hash"))
        content_version = _required_exact_content_hash(exposure.get("content_version"))
        if not hmac.compare_digest(exact_content_hash, content_version):
            raise ValueError("invalid recall receipt: content version mismatch")


def _required_exact_content_hash(value: Any) -> str:
    exact_content_hash = str(value or "")
    if (
        len(exact_content_hash) != 64
        or exact_content_hash != exact_content_hash.lower()
        or any(character not in "0123456789abcdef" for character in exact_content_hash)
    ):
        raise ValueError("invalid recall receipt: malformed exact content hash")
    return exact_content_hash


def _canonical_collection_digest(values: list[str]) -> str:
    return hashlib.sha256(_canonical_json({"values": values})).hexdigest()


def _private_value_digest(value: str | None) -> str | None:
    if value is None:
        return None
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _canonical_text_digest(value: str) -> str:
    canonical_value = json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical_value).hexdigest()


def _required_sha256_digest(value: Any, *, field_name: str) -> str:
    digest = str(value or "")
    if (
        len(digest) != 64
        or digest != digest.lower()
        or any(character not in "0123456789abcdef" for character in digest)
    ):
        raise ValueError(f"invalid recall receipt: malformed {field_name} digest")
    return digest


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


def _normalize_feedback_type(feedback_type: str) -> str:
    cleaned = feedback_type.strip().lower()
    if cleaned not in FEEDBACK_TYPES:
        raise ValueError(f"feedback_type must be one of {sorted(FEEDBACK_TYPES)}")
    return cleaned


def _normalize_supersedes_feedback_id(value: int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("supersedes_feedback_id must be a positive integer")
    return value


def _normalize_reason(outcome: str, reason: str | None) -> str | None:
    cleaned = _normalize_optional_reason(reason)
    if outcome in FEEDBACK_REASON_REQUIRED and cleaned is None:
        raise ValueError("reason is required for misleading or outdated feedback")
    return cleaned


def _normalize_optional_reason(reason: str | None) -> str | None:
    cleaned = " ".join(str(reason or "").split()) or None
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


def _feedback_json(
    receipt_payload: dict[str, Any],
    *,
    receipt_hash: str,
    feedback_identity_digest: str,
    memory_id: str,
    result_rank: int,
) -> str:
    exposure_set = receipt_payload.get("exposure_set")
    if not isinstance(exposure_set, list):
        raise ValueError("invalid recall receipt: incomplete exposure set")
    exposure = next(
        (
            member
            for member in exposure_set
            if isinstance(member, dict) and member.get("memory_id") == memory_id and member.get("rank") == result_rank
        ),
        None,
    )
    if exposure is None:
        raise ValueError("invalid recall receipt: memory id and rank mismatch")
    payload = {
        "schema": RETRIEVAL_FEEDBACK_SCHEMA,
        "receipt_schema": receipt_payload.get("schema"),
        "receipt_hash": receipt_hash,
        "feedback_identity_digest": feedback_identity_digest,
        "query_hash": receipt_payload.get("query_hash"),
        "result_count": len(receipt_payload.get("results") or []),
        "exact_content_hash": exposure.get("exact_content_hash"),
        "content_version": exposure.get("content_version"),
        "retrieval_contract_digest": receipt_payload.get("retrieval_contract_digest"),
        "exposure_set": exposure_set,
        "feedback_mode": "shadow_only",
        "ordering_effect": "none",
        "provenance": "caller_declared_not_authenticated",
        "authenticated_origin": False,
    }
    return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _feedback_identity_payload(
    *,
    feedback_identity_digest: str,
    namespace: str,
    memory_id: str,
    result_rank: int,
) -> dict[str, Any]:
    return {
        "feedback_identity_digest": feedback_identity_digest,
        "namespace": namespace,
        "memory_id": memory_id,
        "result_rank": result_rank,
    }


def _feedback_event_idempotency_key(
    subject: dict[str, Any],
    *,
    feedback_type: str,
    supersedes_feedback_id: int | None,
) -> str:
    payload = {**subject, "feedback_type": feedback_type}
    if supersedes_feedback_id is not None:
        payload["supersedes_feedback_id"] = supersedes_feedback_id
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


def _canonical_json(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _base64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _base64url_decode(encoded: str) -> bytes:
    if not encoded:
        raise ValueError("invalid base64url value")
    try:
        encoded_bytes = encoded.encode("ascii")
        padding = b"=" * (-len(encoded_bytes) % 4)
        decoded = base64.b64decode(encoded_bytes + padding, altchars=b"-_", validate=True)
    except (binascii.Error, UnicodeEncodeError, ValueError) as exc:
        raise ValueError("invalid base64url value") from exc
    if _base64url_encode(decoded) != encoded:
        raise ValueError("invalid base64url value")
    return decoded


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
            feedback_identity_digest,
            source_app,
            source_client,
            source_model,
            client_session_id,
            client_workspace,
            client_transport,
            actor,
            feedback_type,
            supersedes_feedback_id,
            created_at
        FROM retrieval_feedback
        WHERE idempotency_key = ?
        LIMIT 1
        """,
        (idempotency_key,),
    ).fetchone()


def _fetch_feedback_head(
    conn: sqlite3.Connection,
    *,
    feedback_identity_digest: str,
    namespace: str,
    memory_id: str,
    result_rank: int,
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT
            head.feedback_id,
            head.namespace,
            head.memory_id,
            head.result_rank,
            head.outcome,
            head.reason,
            head.retrieval_mode,
            head.receipt_hash,
            head.feedback_identity_digest,
            head.source_app,
            head.source_client,
            head.source_model,
            head.client_session_id,
            head.client_workspace,
            head.client_transport,
            head.actor,
            head.feedback_type,
            head.supersedes_feedback_id,
            head.created_at
        FROM retrieval_feedback head
        WHERE head.feedback_identity_digest = ?
          AND head.namespace = ?
          AND head.memory_id = ?
          AND head.result_rank = ?
          AND NOT EXISTS (
              SELECT 1
              FROM retrieval_feedback child
              WHERE child.supersedes_feedback_id = head.feedback_id
          )
        ORDER BY head.feedback_id DESC
        LIMIT 1
        """,
        (feedback_identity_digest, namespace, memory_id, result_rank),
    ).fetchone()


def _feedback_request_matches(
    row: sqlite3.Row,
    *,
    feedback_type: str,
    supersedes_feedback_id: int | None,
    outcome: str | None,
    reason: str | None,
) -> bool:
    observed_supersedes = row["supersedes_feedback_id"]
    return (
        row["feedback_type"] == feedback_type
        and (int(observed_supersedes) if observed_supersedes is not None else None) == supersedes_feedback_id
        and (outcome is None or row["outcome"] == outcome)
        and row["reason"] == reason
    )


def _feedback_row_response(
    row: sqlite3.Row,
    *,
    stored: bool,
    effective_vote: bool,
) -> dict[str, Any]:
    feedback_type = str(row["feedback_type"])
    return {
        "stored": stored,
        "duplicate": not stored,
        "feedback_id": int(row["feedback_id"]),
        "feedback_type": feedback_type,
        "supersedes_feedback_id": (
            int(row["supersedes_feedback_id"]) if row["supersedes_feedback_id"] is not None else None
        ),
        "effective_vote": effective_vote,
        "namespace_hash": hash_label(str(row["namespace"])),
        "memory_id_hash": hash_label(str(row["memory_id"])),
        "result_rank": int(row["result_rank"]),
        "outcome": row["outcome"],
        "retrieval_mode": row["retrieval_mode"],
        "receipt_bound": True,
        "feedback_mode": "shadow_only",
        "ordering": "unchanged",
        "ordering_unchanged": True,
        "provenance": "caller_declared_not_authenticated",
        "authenticated_origin": False,
        "diagnostics": {
            "mode": "shadow_only",
            "ordering_effect": "none",
            "ordering": "unchanged",
            "returned_ordering_changed": False,
            "provenance": "caller_declared_not_authenticated",
            "authenticated_origin": False,
        },
    }
