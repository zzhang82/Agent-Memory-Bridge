"""Bounded ContextManifest attestations linked through existing run authority.

This adapter deliberately sits outside ``context_manifest``. It translates a
transient manifest into metadata-only artifact evidence through the existing run
ledger and derives read-only evaluation linkage from existing artifacts,
verification receipts, and the current outcome head. It never persists rendered
context, raw task text, or source bodies.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any

from .context_manifest import ContextManifest
from .run_outcome_authority import is_strong_verified_outcome, outcome_authority_class

CONTEXT_ATTESTATION_TYPE = "context_attestation"
CONTEXT_ATTESTATION_VERSION = "context-attestation-v1"
CONTEXT_ATTESTATION_MIME_TYPE = "application/vnd.agent-memory-bridge.context-attestation+json"
_ATTESTATION_DIGEST_FIELDS = frozenset(
    {
        "manifest_fingerprint",
        "input_fingerprint",
        "rendered_context_sha256",
        "task_identifier_sha256",
        "metadata_manifest_sha256",
        "attestation_sha256",
    }
)
_ATTESTATION_FIELDS = frozenset(
    {
        "attestation_type",
        "attestation_version",
        *_ATTESTATION_DIGEST_FIELDS,
        "compiler_version",
        "selection_policy_version",
        "budget_tokens",
        "used_tokens",
        "selected_item_count",
        "omission_count",
    }
)
_HEX_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_VERSION_LABEL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{0,127}$")
_MAX_ATTESTATION_TOKENS = 1_000_000
_MAX_ATTESTATION_COUNT = 100_000


def build_context_attestation(manifest: ContextManifest) -> dict[str, Any]:
    """Build a bounded, metadata-only attestation from a transient manifest.

    The returned payload intentionally contains only manifest-level digests,
    versions, counts, and token accounting. It does not retain the manifest
    serialization itself or any rendered text.
    """
    if not isinstance(manifest, ContextManifest):
        raise TypeError("manifest must be a ContextManifest")
    metadata_manifest_sha256 = _sha256(manifest.serialize())
    attestation = {
        "attestation_type": CONTEXT_ATTESTATION_TYPE,
        "attestation_version": CONTEXT_ATTESTATION_VERSION,
        "manifest_fingerprint": manifest.fingerprint,
        "input_fingerprint": manifest.input_fingerprint,
        "rendered_context_sha256": manifest.rendered_context_sha256,
        "task_identifier_sha256": manifest.task_identifier_sha256,
        "compiler_version": manifest.compiler_version,
        "selection_policy_version": manifest.selection_policy_version,
        "budget_tokens": manifest.budget_tokens,
        "used_tokens": manifest.used_tokens,
        "selected_item_count": len(manifest.items),
        "omission_count": len(manifest.omissions),
        "metadata_manifest_sha256": metadata_manifest_sha256,
    }
    result = {**attestation, "attestation_sha256": _attestation_digest(attestation)}
    if not _is_valid_context_attestation(result):
        raise ValueError("manifest cannot produce a bounded valid context attestation")
    return result


def record_context_attestation(
    store: Any,
    *,
    workspace_key: str,
    run_id: str,
    work_item_id: str,
    manifest: ContextManifest,
    idempotency_key: str,
    event_schema_version: int = 1,
    expected_database_epoch: str | None = None,
    expected_run_generation: int | None = None,
    expected_last_sequence: int | None = None,
    expected_work_item_status: str | None = None,
    provenance: Mapping[str, object | None] | None = None,
) -> dict[str, Any]:
    """Record one context attestation via the existing ``artifact_created`` path.

    Governed-v2 callers must provide the same CAS preconditions required by
    ``MemoryStore.record_run_event``. Idempotency and request-digest conflict
    handling are inherited unchanged from that authority.
    """
    attestation = build_context_attestation(manifest)
    artifact_payload = {
        "artifact": {
            "digest": attestation["attestation_sha256"],
            "mime_type": CONTEXT_ATTESTATION_MIME_TYPE,
            "uri": f"context-attestation://sha256/{attestation['attestation_sha256']}",
            "metadata": attestation,
        }
    }
    event = store.record_run_event(
        workspace_key=workspace_key,
        run_id=run_id,
        work_item_id=work_item_id,
        event_type="artifact_created",
        summary="Recorded bounded compiled-context attestation.",
        payload=artifact_payload,
        idempotency_key=idempotency_key,
        event_schema_version=event_schema_version,
        expected_database_epoch=expected_database_epoch,
        expected_run_generation=expected_run_generation,
        expected_last_sequence=expected_last_sequence,
        expected_work_item_status=expected_work_item_status,
        provenance=provenance,
    )
    return {**event, "context_attestation": attestation}


def get_context_evaluation_linkage(
    store: Any,
    *,
    workspace_key: str,
    run_id: str,
) -> dict[str, Any]:
    """Derive current outcome linkage from existing run artifacts and receipts.

    This read-only view makes no causal claim about whether context caused an
    outcome. It only reports which bounded attestations are present and whether
    they are included in the verification receipt associated with the current
    strong verified outcome.
    """
    with store._connect() as conn:
        conn.execute("BEGIN")
        try:
            run = conn.execute(
                "SELECT run_id FROM agent_runs WHERE run_id = ? AND workspace_key = ?",
                (run_id, workspace_key),
            ).fetchone()
            if run is None:
                raise ValueError("run_id does not exist in the declared workspace")
            artifact_rows = conn.execute(
                """
                SELECT artifact.artifact_id, artifact.artifact_version, artifact.digest,
                       artifact.mime_type, artifact.uri, artifact.metadata_json,
                       artifact.producing_event_id, artifact.created_at, event.sequence
                FROM run_artifacts artifact
                JOIN run_events event
                  ON event.run_id = artifact.run_id AND event.event_id = artifact.producing_event_id
                WHERE artifact.run_id = ?
                ORDER BY event.sequence, artifact.artifact_id, artifact.artifact_version
                """,
                (run_id,),
            ).fetchall()
            attestations = [_attestation_payload(row) for row in artifact_rows if _is_context_attestation_artifact(row)]
            outcome_row = conn.execute(
                """
                SELECT outcome.*
                FROM run_outcomes outcome
                LEFT JOIN run_outcomes child ON child.supersedes_outcome_id = outcome.outcome_id
                WHERE outcome.run_id = ? AND child.outcome_id IS NULL
                """,
                (run_id,),
            ).fetchone()
            outcome = _outcome_payload(outcome_row)
            receipt = _receipt_for_current_outcome(conn, outcome)
            receipt_artifact_refs = _receipt_artifact_refs(receipt)
            receipt_artifact_keys = {
                (str(item["artifact_id"]), int(item["artifact_version"]), str(item["digest"]))
                for item in receipt_artifact_refs
            }
            bound_attestations = [
                item
                for item in attestations
                if (item["artifact_id"], item["artifact_version"], item["artifact_digest"]) in receipt_artifact_keys
            ]
            strong_verified = is_strong_verified_outcome(outcome)
            response = {
                "run_id": run_id,
                "current_outcome_id": outcome.get("outcome_id") if outcome else None,
                "current_outcome_type": outcome.get("outcome_type") if outcome else None,
                "outcome_authority_class": outcome_authority_class(outcome),
                "strong_verified": strong_verified,
                "context_attestations": attestations,
                "latest_context_attestation": attestations[-1] if attestations else None,
                "verification_receipt_id": receipt.get("verification_receipt_id") if receipt else None,
                "context_bound_to_current_verification": bool(strong_verified and bound_attestations),
                "context_attestations_bound_to_current_verification": bound_attestations,
            }
            conn.commit()
            return response
        except BaseException:
            conn.rollback()
            raise


def _is_context_attestation_artifact(row: Mapping[str, Any]) -> bool:
    if str(row["mime_type"]) != CONTEXT_ATTESTATION_MIME_TYPE:
        return False
    try:
        metadata = _json_object(str(row["metadata_json"]))
        artifact_digest = str(row["digest"])
        artifact_version = int(row["artifact_version"])
        uri = str(row["uri"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, RuntimeError):
        return False
    return (
        artifact_version == 1
        and _is_valid_context_attestation(metadata)
        and artifact_digest == metadata["attestation_sha256"]
        and uri == f"context-attestation://sha256/{artifact_digest}"
    )


def _is_valid_context_attestation(attestation: Mapping[str, Any]) -> bool:
    if set(attestation) != _ATTESTATION_FIELDS:
        return False
    if (
        attestation.get("attestation_type") != CONTEXT_ATTESTATION_TYPE
        or attestation.get("attestation_version") != CONTEXT_ATTESTATION_VERSION
    ):
        return False
    if not all(_is_sha256(attestation.get(field)) for field in _ATTESTATION_DIGEST_FIELDS):
        return False
    if not all(
        _is_bounded_version_label(attestation.get(field)) for field in ("compiler_version", "selection_policy_version")
    ):
        return False
    if not all(
        _is_bounded_nonnegative_int(attestation.get(field), maximum=_MAX_ATTESTATION_TOKENS)
        for field in ("budget_tokens", "used_tokens")
    ):
        return False
    if not all(
        _is_bounded_nonnegative_int(attestation.get(field), maximum=_MAX_ATTESTATION_COUNT)
        for field in ("selected_item_count", "omission_count")
    ):
        return False
    if int(attestation["used_tokens"]) > int(attestation["budget_tokens"]):
        return False
    unsigned_attestation = {key: value for key, value in attestation.items() if key != "attestation_sha256"}
    return str(attestation["attestation_sha256"]) == _attestation_digest(unsigned_attestation)


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and _HEX_SHA256_RE.fullmatch(value) is not None


def _is_bounded_version_label(value: Any) -> bool:
    return isinstance(value, str) and _VERSION_LABEL_RE.fullmatch(value) is not None


def _is_bounded_nonnegative_int(value: Any, *, maximum: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= maximum


def _attestation_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    metadata = _json_object(str(row["metadata_json"]))
    return {
        "artifact_id": str(row["artifact_id"]),
        "artifact_version": int(row["artifact_version"]),
        "artifact_digest": str(row["digest"]),
        "producing_event_id": str(row["producing_event_id"]),
        "sequence": int(row["sequence"]),
        "created_at": str(row["created_at"]),
        "attestation": metadata,
    }


def _outcome_payload(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "outcome_id": str(row["outcome_id"]),
        "outcome_type": str(row["outcome_type"]),
        "verification_profile": str(row["verification_profile"]),
        "verification_receipt_id": row["verification_receipt_id"],
    }


def _receipt_for_current_outcome(conn: Any, outcome: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if outcome is None or not outcome.get("verification_receipt_id"):
        return None
    row = conn.execute(
        """
        SELECT receipt_id, artifact_refs_json
        FROM run_verification_receipts
        WHERE receipt_id = ?
        """,
        (str(outcome["verification_receipt_id"]),),
    ).fetchone()
    if row is None:
        return None
    return {
        "verification_receipt_id": str(row["receipt_id"]),
        "artifact_refs": _json_array(str(row["artifact_refs_json"])),
    }


def _receipt_artifact_refs(receipt: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    if receipt is None:
        return []
    return [item for item in receipt["artifact_refs"] if isinstance(item, dict)]


def _json_object(value: str) -> dict[str, Any]:
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise RuntimeError("stored context-attestation metadata is malformed")
    return parsed


def _json_array(value: str) -> list[Any]:
    parsed = json.loads(value)
    if not isinstance(parsed, list):
        raise RuntimeError("stored verification receipt artifact refs are malformed")
    return parsed


def _attestation_digest(attestation: Mapping[str, Any]) -> str:
    return _sha256(_canonical_json(attestation))


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":"), sort_keys=True)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


__all__ = ["build_context_attestation", "get_context_evaluation_linkage", "record_context_attestation"]
