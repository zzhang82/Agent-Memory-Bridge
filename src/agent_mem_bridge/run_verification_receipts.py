from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any

from .durable_data_policy import require_durable_structured_data, require_durable_text
from .schema import database_epoch

GOVERNED_V2_PROFILE = "governed-v2"
_HEX_DIGEST_LENGTH = 64
_RECEIPT_RESULTS = frozenset({"verified_success", "failed", "partial_success"})
_CRITERION_RESULTS = frozenset({"passed", "failed", "not_applicable"})
_PREFLIGHT_ARRAY_FIELDS = frozenset(
    {
        "confirmed_facts",
        "reasonable_inferences",
        "unverified_hypotheses",
        "missing_information",
        "alternatives_considered",
        "hidden_risks",
        "maintenance_cost",
        "maintenance_impact",
        "verification_plan",
    }
)


def mint_operator_verification_receipt(
    store: Any,
    *,
    workspace_key: str,
    run_id: str,
    preflight_event_id: str,
    evaluator_digest: str,
    evaluator_version: str,
    criterion_results: Sequence[Mapping[str, Any]],
    result: str,
    evidence: Sequence[Any],
    actor: str,
) -> dict[str, Any]:
    """Mint one human/operator receipt through the non-MCP operator boundary."""

    cleaned_workspace = _required_text("workspace_key", workspace_key, max_chars=512)
    cleaned_run_id = _required_text("run_id", run_id, max_chars=64)
    cleaned_preflight_id = _required_text("preflight_event_id", preflight_event_id, max_chars=64)
    cleaned_evaluator_digest = _required_digest("evaluator_digest", evaluator_digest)
    cleaned_evaluator_version = _required_text("evaluator_version", evaluator_version, max_chars=128)
    cleaned_result = _required_text("result", result, max_chars=32)
    if cleaned_result not in _RECEIPT_RESULTS:
        raise ValueError(f"unsupported verification receipt result: {cleaned_result}")
    cleaned_actor = _required_text("actor", actor, max_chars=128)
    evidence_json = _json_array("evidence", evidence, max_bytes=32768)

    with store._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            run = _require_run(conn, workspace_key=cleaned_workspace, run_id=cleaned_run_id)
            if str(run["evidence_profile"]) != GOVERNED_V2_PROFILE:
                raise ValueError("verification receipts require a governed-v2 run")
            criteria = _json_value(str(run["acceptance_criteria_json"]), expected=list)
            normalized_results = _normalize_criterion_results(
                criterion_results,
                criteria=criteria,
                receipt_result=cleaned_result,
            )
            _require_approved_preflight(conn, run_id=cleaned_run_id, event_id=cleaned_preflight_id)
            artifact_refs, artifact_digest = current_artifact_receipt_refs(conn, run_id=cleaned_run_id)
            current_epoch = database_epoch(conn)
            run_config_digest = calculate_run_config_digest(run)
            criterion_results_json = _canonical_json(normalized_results)
            receipt_id = f"receipt_{secrets.token_hex(16)}"
            created_at = store._utc_now()
            receipt_digest = _digest(
                {
                    "receipt_id": receipt_id,
                    "run_id": cleaned_run_id,
                    "acceptance_criteria_digest": str(run["acceptance_criteria_digest"]),
                    "preflight_event_id": cleaned_preflight_id,
                    "artifact_refs": artifact_refs,
                    "artifact_digest": artifact_digest,
                    "run_config_digest": run_config_digest,
                    "evaluator_type": "human",
                    "evaluator_digest": cleaned_evaluator_digest,
                    "evaluator_version": cleaned_evaluator_version,
                    "database_epoch": current_epoch,
                    "criterion_results": normalized_results,
                    "result": cleaned_result,
                    "evidence": json.loads(evidence_json),
                    "issuer_channel": "operator_cli",
                    "issuer_actor": cleaned_actor,
                    "created_at": created_at,
                }
            )
            conn.execute(
                """
                INSERT INTO run_verification_receipts (
                    receipt_id, run_id, acceptance_criteria_digest, preflight_event_id,
                    artifact_refs_json, artifact_digest, run_config_digest,
                    evaluator_type, evaluator_digest, evaluator_version, database_epoch,
                    criterion_results_json, result, evidence_json, issuer_channel,
                    issuer_actor, receipt_digest, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'human', ?, ?, ?, ?, ?, ?, 'operator_cli', ?, ?, ?)
                """,
                (
                    receipt_id,
                    cleaned_run_id,
                    str(run["acceptance_criteria_digest"]),
                    cleaned_preflight_id,
                    _canonical_json(artifact_refs),
                    artifact_digest,
                    run_config_digest,
                    cleaned_evaluator_digest,
                    cleaned_evaluator_version,
                    current_epoch,
                    criterion_results_json,
                    cleaned_result,
                    evidence_json,
                    cleaned_actor,
                    receipt_digest,
                    created_at,
                ),
            )
            row = conn.execute("SELECT * FROM run_verification_receipts WHERE receipt_id = ?", (receipt_id,)).fetchone()
            if row is None:
                raise RuntimeError("verification receipt insertion is incomplete")
            conn.commit()
            return verification_receipt_payload(row)
        except BaseException:
            conn.rollback()
            raise


def validate_receipt_for_verified_success(
    conn: sqlite3.Connection,
    *,
    run: Mapping[str, Any],
    receipt_id: str,
    evaluator_type: str,
    evaluator_digest: str | None,
    evaluator_version: str | None,
) -> Mapping[str, Any]:
    row = conn.execute("SELECT * FROM run_verification_receipts WHERE receipt_id = ?", (receipt_id,)).fetchone()
    if row is None:
        raise ValueError("verification_receipt_id does not identify a server-minted receipt")
    if str(row["run_id"]) != str(run["run_id"]):
        raise ValueError("verification receipt belongs to a different run")
    if str(row["result"]) != "verified_success":
        raise ValueError("verification receipt result is not verified_success")
    if str(row["database_epoch"]) != database_epoch(conn):
        raise ValueError("verification receipt database epoch is stale")
    if str(row["acceptance_criteria_digest"]) != str(run["acceptance_criteria_digest"]):
        raise ValueError("verification receipt acceptance criteria do not match the run")
    if str(row["run_config_digest"]) != calculate_run_config_digest(run):
        raise ValueError("verification receipt run configuration does not match the run")
    artifact_refs, artifact_digest = current_artifact_receipt_refs(conn, run_id=str(run["run_id"]))
    if (
        str(row["artifact_digest"]) != artifact_digest
        or _json_value(str(row["artifact_refs_json"]), expected=list) != artifact_refs
    ):
        raise ValueError("verification receipt artifacts are no longer current")
    _require_approved_preflight(
        conn,
        run_id=str(run["run_id"]),
        event_id=str(row["preflight_event_id"]),
    )
    _normalize_criterion_results(
        _json_value(str(row["criterion_results_json"]), expected=list),
        criteria=_json_value(str(run["acceptance_criteria_json"]), expected=list),
        receipt_result=str(row["result"]),
    )
    if str(row["evaluator_type"]) != evaluator_type:
        raise ValueError("verification receipt evaluator type does not match completion")
    if str(row["evaluator_digest"]) != evaluator_digest:
        raise ValueError("verification receipt evaluator digest does not match completion")
    if str(row["evaluator_version"]) != evaluator_version:
        raise ValueError("verification receipt evaluator version does not match completion")
    return row


def calculate_run_config_digest(run: Mapping[str, Any]) -> str:
    return _digest(
        {
            "evidence_profile": run["evidence_profile"],
            "acceptance_criteria_digest": run["acceptance_criteria_digest"],
            "constraints": _json_value(str(run["constraints_json"]), expected=list),
            "non_goals": _json_value(str(run["non_goals_json"]), expected=list),
            "risk_level": run["risk_level"],
            "model_digest": run["model_digest"],
            "harness_digest": run["harness_digest"],
            "chat_template_digest": run["chat_template_digest"],
            "tool_schema_digest": run["tool_schema_digest"],
            "memory_scopes": _json_value(str(run["memory_scopes_json"]), expected=list),
            "budget": _json_value(str(run["budget_json"]), expected=dict),
        }
    )


def current_artifact_receipt_refs(conn: sqlite3.Connection, *, run_id: str) -> tuple[list[dict[str, Any]], str]:
    rows = conn.execute(
        """
        SELECT artifact_id, artifact_version, work_item_id, producing_event_id,
               digest, mime_type, uri, metadata_json
        FROM run_artifacts
        WHERE run_id = ?
        ORDER BY artifact_id, artifact_version
        """,
        (run_id,),
    ).fetchall()
    refs = [
        {
            "artifact_id": str(row["artifact_id"]),
            "artifact_version": int(row["artifact_version"]),
            "work_item_id": str(row["work_item_id"]),
            "producing_event_id": str(row["producing_event_id"]),
            "digest": str(row["digest"]),
            "mime_type": str(row["mime_type"]),
            "uri": str(row["uri"]),
            "metadata": _json_value(str(row["metadata_json"]), expected=dict),
        }
        for row in rows
    ]
    return refs, _digest(refs)


def verification_receipt_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "verification_receipt_id": str(row["receipt_id"]),
        "run_id": str(row["run_id"]),
        "acceptance_criteria_digest": str(row["acceptance_criteria_digest"]),
        "preflight_event_id": str(row["preflight_event_id"]),
        "artifact_refs": _json_value(str(row["artifact_refs_json"]), expected=list),
        "artifact_digest": str(row["artifact_digest"]),
        "run_config_digest": str(row["run_config_digest"]),
        "evaluator_type": str(row["evaluator_type"]),
        "evaluator_digest": str(row["evaluator_digest"]),
        "evaluator_version": str(row["evaluator_version"]),
        "database_epoch": str(row["database_epoch"]),
        "criterion_results": _json_value(str(row["criterion_results_json"]), expected=list),
        "result": str(row["result"]),
        "evidence": _json_value(str(row["evidence_json"]), expected=list),
        "issuer_channel": str(row["issuer_channel"]),
        "issuer_actor": str(row["issuer_actor"]),
        "receipt_digest": str(row["receipt_digest"]),
        "created_at": str(row["created_at"]),
    }


def _require_run(conn: sqlite3.Connection, *, workspace_key: str, run_id: str) -> Mapping[str, Any]:
    row = conn.execute(
        "SELECT * FROM agent_runs WHERE workspace_key = ? AND run_id = ?", (workspace_key, run_id)
    ).fetchone()
    if row is None:
        raise ValueError("run_id does not exist in the declared workspace")
    return row


def _require_approved_preflight(conn: sqlite3.Connection, *, run_id: str, event_id: str) -> None:
    row = conn.execute(
        """
        SELECT event.payload_json, run.risk_level
        FROM run_events event
        JOIN run_event_v2_details detail
          ON detail.run_id = event.run_id AND detail.event_id = event.event_id
        JOIN agent_runs run ON run.run_id = event.run_id
        WHERE event.run_id = ?
          AND event.event_id = ?
          AND detail.logical_event_type = 'preflight_review'
        """,
        (run_id, event_id),
    ).fetchone()
    if row is None:
        raise ValueError("verification receipt requires a same-run governed preflight_review event")
    payload = _json_value(str(row["payload_json"]), expected=dict)
    if payload.get("approved") is not True:
        raise ValueError("verification receipt requires an approved preflight_review event")
    allowed = {"approved", "rollback_plan", *_PREFLIGHT_ARRAY_FIELDS}
    if set(payload) != allowed and set(payload) != allowed - {"rollback_plan"}:
        raise ValueError("verification receipt preflight payload is not closed")
    for field in _PREFLIGHT_ARRAY_FIELDS:
        value = payload.get(field)
        if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence) or len(value) > 32:
            raise ValueError("verification receipt preflight arrays are malformed")
        require_durable_structured_data(list(value), subject="durable verification receipt preflight data")
        _bounded_json(f"preflight.{field}", list(value), max_bytes=8192)
    rollback_plan = payload.get("rollback_plan")
    if str(row["risk_level"]) in {"high", "critical"} and (
        not isinstance(rollback_plan, str) or not rollback_plan.strip()
    ):
        raise ValueError("verification receipt requires the governed preflight rollback plan")
    if rollback_plan is not None:
        _required_text("preflight.rollback_plan", rollback_plan, max_chars=4096)


def _normalize_criterion_results(
    value: Sequence[Mapping[str, Any]],
    *,
    criteria: Sequence[Any],
    receipt_result: str,
) -> list[dict[str, Any]]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise ValueError("criterion_results must be an array")
    criterion_ids = _criteria_ids(criteria)
    if not criterion_ids:
        raise ValueError("verified receipt requires at least one acceptance criterion")
    if len(value) != len(criterion_ids):
        raise ValueError("criterion_results must cover each acceptance criterion exactly once")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in value:
        if not isinstance(raw, Mapping):
            raise ValueError("criterion_results entries must be objects")
        allowed = {"criterion_id", "result", "evidence_refs"}
        if set(raw) != allowed:
            raise ValueError("criterion_results entries require criterion_id, result, and evidence_refs only")
        criterion_id = _required_text("criterion_result.criterion_id", raw["criterion_id"], max_chars=128)
        result = _required_text("criterion_result.result", raw["result"], max_chars=32)
        if result not in _CRITERION_RESULTS:
            raise ValueError(f"unsupported criterion result: {result}")
        evidence_refs = _normalize_evidence_refs(raw["evidence_refs"])
        if criterion_id not in criterion_ids or criterion_id in seen:
            raise ValueError("criterion_results must use each declared criterion_id exactly once")
        seen.add(criterion_id)
        normalized.append({"criterion_id": criterion_id, "result": result, "evidence_refs": evidence_refs})
    if receipt_result == "verified_success" and any(item["result"] != "passed" for item in normalized):
        raise ValueError("verified_success receipt cannot contain failed or not_applicable criterion results")
    return normalized


def _criteria_ids(criteria: Sequence[Any]) -> set[str]:
    if isinstance(criteria, (str, bytes, bytearray)) or not isinstance(criteria, Sequence):
        raise ValueError("acceptance criteria are malformed")
    identifiers: set[str] = set()
    for criterion in criteria:
        if not isinstance(criterion, Mapping):
            raise ValueError("acceptance criteria are malformed")
        identifier = criterion.get("criterion_id")
        if not isinstance(identifier, str) or not identifier.strip() or identifier in identifiers:
            raise ValueError("acceptance criteria are malformed")
        identifiers.add(identifier)
    return identifiers


def _normalize_evidence_refs(value: Any) -> list[Any]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise ValueError("criterion_result.evidence_refs must be an array")
    refs = list(value)
    if not refs:
        raise ValueError("criterion_result.evidence_refs must not be empty")
    require_durable_structured_data(refs, subject="durable verification receipt structured data")
    _bounded_json("criterion_result.evidence_refs", refs, max_bytes=8192)
    return refs


def _json_array(name: str, value: Sequence[Any], *, max_bytes: int) -> str:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise ValueError(f"{name} must be an array")
    require_durable_structured_data(list(value), subject="durable verification receipt structured data")
    return _bounded_json(name, list(value), max_bytes=max_bytes)


def _required_digest(name: str, value: object) -> str:
    digest = _required_text(name, value, max_chars=_HEX_DIGEST_LENGTH)
    if len(digest) != _HEX_DIGEST_LENGTH or any(character not in "0123456789abcdef" for character in digest):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return digest


def _required_text(name: str, value: object, *, max_chars: int) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError(f"{name} must not be empty")
    if len(text) > max_chars:
        raise ValueError(f"{name} must be at most {max_chars} characters")
    require_durable_text(text, subject="durable verification receipt text")
    return text


def _bounded_json(name: str, value: Any, *, max_bytes: int) -> str:
    try:
        encoded = _canonical_json(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must contain JSON-compatible finite values") from exc
    if len(encoded.encode("utf-8")) > max_bytes:
        raise ValueError(f"{name} must be at most {max_bytes} bytes")
    return encoded


def _json_value(value: str, *, expected: type[list[Any]] | type[dict[str, Any]]) -> Any:
    parsed = json.loads(value)
    if not isinstance(parsed, expected):
        raise RuntimeError("stored verification receipt JSON has an invalid shape")
    return parsed


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()
