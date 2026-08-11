from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import time
from collections.abc import Mapping, Sequence
from typing import Any, cast
from urllib.parse import urlsplit

from .durable_data_policy import normalize_durable_key, require_durable_structured_data, require_durable_text
from .provenance import normalize_provenance_mapping
from .retrieval_feedback import recall_receipt_hash, validate_recall_receipt_exposures
from .run_outcome_authority import is_strong_verified_outcome, outcome_authority_class
from .run_projection import (
    WORK_ITEM_STATUSES,
    apply_run_event_projection,
    apply_run_outcome_projection,
    derive_run_authority_state,
    initialize_run_projections,
    initialize_work_item_projection,
    inspect_run_projection,
    validate_work_item_transition,
)
from .run_verification_receipts import (
    GOVERNED_V2_PROFILE,
    calculate_run_config_digest,
    validate_receipt_for_verified_success,
    verification_receipt_payload,
)
from .schema import database_epoch as read_database_epoch

RUN_EVENT_TYPES = frozenset(
    {
        "plan_created",
        "work_item_started",
        "checkpoint",
        "observation",
        "hypothesis",
        "hypothesis_confirmed",
        "hypothesis_rejected",
        "tool_result",
        "test_failure",
        "decision",
        "blocker",
        "memory_recalled",
        "memory_applied",
        "memory_rejected",
        "artifact_created",
        "compaction_boundary",
        "work_item_completed",
        "work_item_failed",
        "work_item_abandoned",
    }
)
GOVERNED_V2_EVENT_TYPES = frozenset(
    {
        "preflight_review",
        "test_result",
        "risk_identified",
        "information_gap",
        "verification_result",
        "work_item_resumed",
        "blocker_resolved",
    }
)
ALL_LOGICAL_EVENT_TYPES = RUN_EVENT_TYPES | GOVERNED_V2_EVENT_TYPES
RUN_EVIDENCE_PROFILES = frozenset({"observational", GOVERNED_V2_PROFILE})
RUN_RISK_LEVELS = frozenset({"low", "medium", "high", "critical"})
RUN_OUTCOME_TYPES = frozenset(
    {
        "verified_success",
        "partial_success",
        "unverified",
        "user_corrected",
        "regression",
        "failed",
        "abandoned",
    }
)
RUN_EVALUATOR_TYPES = frozenset({"agent", "deterministic_verifier", "human", "system"})
MEMORY_ATTRIBUTION_EVENT_TYPES = frozenset({"memory_recalled", "memory_applied", "memory_rejected"})
CALLER_MANAGED_ATTRIBUTION_FIELDS = frozenset({"relation", "receipt_hash", "review_required", "outcome_id"})
LIFECYCLE_PAYLOAD_REJECTED_FIELDS = frozenset(
    {
        "memory_attribution",
        "memory_id",
        "memory_ids",
        "result_id",
        "result_rank",
        "relation",
        "review_required",
        "exact_content_version",
        "feedback_id",
        "outcome_id",
    }
)
LIFECYCLE_PAYLOAD_CONSISTENCY_FIELDS = frozenset(
    {"result_ids", "result_ranks", "receipt_hash", "recall_event_id", "source_recall_event_id"}
)
ARTIFACT_CREATED_EVENT_TYPE = "artifact_created"
CALLER_MANAGED_ARTIFACT_FIELDS = frozenset(
    {
        "artifact_id",
        "artifact_version",
        "run_id",
        "work_item_id",
        "producing_event_id",
        "event_id",
        "created_at",
    }
)
FORBIDDEN_ARTIFACT_CONTENT_FIELDS = frozenset(
    {"content", "contents", "binary", "bytes", "blob", "data", "body", "file_body", "filebody"}
)
_CALLER_MANAGED_ARTIFACT_KEYS = frozenset(normalize_durable_key(field) for field in CALLER_MANAGED_ARTIFACT_FIELDS)
_FORBIDDEN_ARTIFACT_CONTENT_KEYS = frozenset(
    normalize_durable_key(field) for field in FORBIDDEN_ARTIFACT_CONTENT_FIELDS
)
TERMINAL_WORK_ITEM_STATUSES = ("completed", "failed", "abandoned")
ARTIFACT_URI_RE = re.compile(r"(?P<scheme>[A-Za-z][A-Za-z0-9+.-]*):[^\s\x00-\x1f\x7f-\x9f]*")
HEX_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_FORBIDDEN_ARTIFACT_URI_SCHEMES = frozenset({"data", "javascript", "vbscript", "file"})
_HYPOTHESIS_EPISTEMIC_STATUSES = frozenset({"unverified", "tentative", "supported", "refuted"})
_GOVERNED_EXECUTION_EVENT_TYPES = frozenset(
    {
        "work_item_started",
        "work_item_resumed",
        "blocker_resolved",
        "checkpoint",
        "tool_result",
        "test_result",
        "verification_result",
        "artifact_created",
        "work_item_completed",
        "work_item_failed",
        "work_item_abandoned",
    }
)
_GOVERNED_STATE_CHANGING_EVENT_TYPES = frozenset(
    {
        "work_item_started",
        "work_item_resumed",
        "blocker_resolved",
        "blocker",
        "work_item_completed",
        "work_item_failed",
        "work_item_abandoned",
    }
)


def _begin_run_ledger_write_transaction(conn: Any) -> None:
    for attempt in range(4):
        try:
            conn.execute("BEGIN IMMEDIATE")
            return
        except sqlite3.OperationalError as error:
            message = str(error).casefold()
            if attempt == 3 or ("locked" not in message and "busy" not in message):
                raise
            time.sleep(0.05 * (2**attempt))


def begin_run_entry(
    store: Any,
    *,
    workspace_key: str,
    goal: str,
    idempotency_key: str,
    agent_id: str | None = None,
    thread_id: str | None = None,
    model_digest: str | None = None,
    harness_digest: str | None = None,
    chat_template_digest: str | None = None,
    tool_schema_digest: str | None = None,
    memory_scopes: Sequence[str] | None = None,
    budget: Mapping[str, Any] | None = None,
    evidence_profile: str = "observational",
    acceptance_criteria: Sequence[Mapping[str, Any]] | None = None,
    constraints: Sequence[str] | None = None,
    non_goals: Sequence[str] | None = None,
    risk_level: str = "medium",
    continuation_of_run_id: str | None = None,
    provenance: Mapping[str, object | None] | None = None,
) -> dict[str, Any]:
    cleaned_workspace = _bounded_text("workspace_key", workspace_key, max_chars=512)
    cleaned_goal = _bounded_text("goal", goal, max_bytes=8192)
    cleaned_agent_id = _optional_bounded_text("agent_id", agent_id, max_chars=128)
    cleaned_thread_id = _optional_bounded_text("thread_id", thread_id, max_chars=256)
    cleaned_memory_scopes = _normalize_memory_scopes(memory_scopes)
    budget_json = _json_object("budget", budget, max_bytes=8192)
    cleaned_evidence_profile = _normalize_evidence_profile(evidence_profile)
    cleaned_acceptance_criteria = _normalize_acceptance_criteria(acceptance_criteria)
    if cleaned_evidence_profile == GOVERNED_V2_PROFILE and not cleaned_acceptance_criteria:
        raise ValueError("governed-v2 runs require at least one acceptance criterion")
    acceptance_criteria_json = _canonical_json(cleaned_acceptance_criteria)
    acceptance_criteria_digest = _request_digest(cleaned_acceptance_criteria)
    cleaned_constraints = _normalize_run_text_list("constraints", constraints)
    cleaned_non_goals = _normalize_run_text_list("non_goals", non_goals)
    constraints_json = _canonical_json(cleaned_constraints)
    non_goals_json = _canonical_json(cleaned_non_goals)
    cleaned_risk_level = _normalize_risk_level(risk_level)
    cleaned_continuation = _optional_bounded_text("continuation_of_run_id", continuation_of_run_id, max_chars=64)
    cleaned_provenance = normalize_provenance_mapping(provenance) or {}
    digests = {
        "model_digest": _optional_digest("model_digest", model_digest),
        "harness_digest": _optional_digest("harness_digest", harness_digest),
        "chat_template_digest": _optional_digest("chat_template_digest", chat_template_digest),
        "tool_schema_digest": _optional_digest("tool_schema_digest", tool_schema_digest),
    }
    idempotency_digest = _idempotency_digest(idempotency_key)
    request_digest = _request_digest(
        {
            "workspace_key": cleaned_workspace,
            "goal": cleaned_goal,
            "agent_id": cleaned_agent_id,
            "thread_id": cleaned_thread_id,
            "memory_scopes": cleaned_memory_scopes,
            "budget": json.loads(budget_json),
            "evidence_profile": cleaned_evidence_profile,
            "acceptance_criteria": cleaned_acceptance_criteria,
            "constraints": cleaned_constraints,
            "non_goals": cleaned_non_goals,
            "risk_level": cleaned_risk_level,
            "continuation_of_run_id": cleaned_continuation,
            "provenance": cleaned_provenance,
            **digests,
        }
    )
    memory_scopes_json = _canonical_json(cleaned_memory_scopes)
    with store._connect() as conn:
        _begin_run_ledger_write_transaction(conn)
        try:
            existing = conn.execute(
                """
                SELECT run_id, request_digest, created_at
                FROM agent_runs
                WHERE workspace_key = ? AND idempotency_key_digest = ?
                """,
                (cleaned_workspace, idempotency_digest),
            ).fetchone()
            if existing is not None:
                _require_matching_request(existing, request_digest, subject="begin_run")
                payload = _begin_run_response(conn, str(existing["run_id"]), idempotent_replay=True)
                conn.commit()
                return payload

            if cleaned_continuation is not None:
                continuation = conn.execute(
                    "SELECT 1 FROM agent_runs WHERE run_id = ? AND workspace_key = ?",
                    (cleaned_continuation, cleaned_workspace),
                ).fetchone()
                if continuation is None:
                    raise ValueError("continuation_of_run_id does not exist in the declared workspace")

            run_id = _new_id("run_")
            root_work_item_id = _new_id("work_")
            created_at = store._utc_now()
            conn.execute(
                """
                INSERT INTO agent_runs (
                    run_id, workspace_key, root_goal, model_digest,
                    harness_digest, chat_template_digest, tool_schema_digest,
                    agent_id, thread_id, memory_scopes_json, budget_json,
                    evidence_profile, acceptance_criteria_json, acceptance_criteria_digest,
                    constraints_json, non_goals_json, risk_level, continuation_of_run_id,
                    idempotency_key_digest, request_digest, actor, source_app,
                    source_client, source_model, client_session_id,
                    client_workspace, client_transport, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    cleaned_workspace,
                    cleaned_goal,
                    digests["model_digest"],
                    digests["harness_digest"],
                    digests["chat_template_digest"],
                    digests["tool_schema_digest"],
                    cleaned_agent_id,
                    cleaned_thread_id,
                    memory_scopes_json,
                    budget_json,
                    cleaned_evidence_profile,
                    acceptance_criteria_json,
                    acceptance_criteria_digest,
                    constraints_json,
                    non_goals_json,
                    cleaned_risk_level,
                    cleaned_continuation,
                    idempotency_digest,
                    request_digest,
                    cleaned_provenance.get("actor"),
                    cleaned_provenance.get("source_app"),
                    cleaned_provenance.get("source_client"),
                    cleaned_provenance.get("source_model"),
                    cleaned_provenance.get("client_session_id"),
                    cleaned_provenance.get("client_workspace"),
                    cleaned_provenance.get("client_transport"),
                    created_at,
                ),
            )
            conn.execute(
                """
                INSERT INTO run_work_items (
                    work_item_id, run_id, parent_work_item_id, goal,
                    owner_agent_id, created_at
                ) VALUES (?, ?, NULL, ?, ?, ?)
                """,
                (root_work_item_id, run_id, cleaned_goal, cleaned_agent_id, created_at),
            )
            initialize_run_projections(
                conn,
                run_id=run_id,
                root_work_item_id=root_work_item_id,
                created_at=created_at,
            )
            conn.commit()
            return _begin_run_response(conn, run_id, idempotent_replay=False)
        except BaseException:
            conn.rollback()
            raise


def record_run_event_entry(
    store: Any,
    *,
    workspace_key: str,
    run_id: str,
    event_type: str,
    summary: str,
    idempotency_key: str,
    event_schema_version: int = 1,
    expected_database_epoch: str | None = None,
    expected_run_generation: int | None = None,
    expected_last_sequence: int | None = None,
    expected_work_item_status: str | None = None,
    work_item_id: str | None = None,
    parent_work_item_id: str | None = None,
    work_item_goal: str | None = None,
    owner_agent_id: str | None = None,
    payload: Mapping[str, Any] | None = None,
    evidence: Sequence[Any] | None = None,
    memory_attribution: Mapping[str, Any] | None = None,
    agent_id: str | None = None,
    thread_id: str | None = None,
    provenance: Mapping[str, object | None] | None = None,
) -> dict[str, Any]:
    cleaned_workspace = _bounded_text("workspace_key", workspace_key, max_chars=512)
    cleaned_run_id = _bounded_text("run_id", run_id, max_chars=64)
    cleaned_event_type = str(event_type).strip()
    cleaned_event_schema_version = _event_schema_version(event_schema_version)
    if cleaned_event_type not in (ALL_LOGICAL_EVENT_TYPES if cleaned_event_schema_version == 2 else RUN_EVENT_TYPES):
        raise ValueError(f"unsupported run event type: {cleaned_event_type or '<empty>'}")
    if cleaned_event_schema_version == 1 and cleaned_event_type in GOVERNED_V2_EVENT_TYPES:
        raise ValueError(f"{cleaned_event_type} requires event_schema_version=2")
    cleaned_summary = _bounded_text("summary", summary, max_bytes=4096)
    cleaned_expected_epoch = _optional_bounded_text("expected_database_epoch", expected_database_epoch, max_chars=128)
    cleaned_expected_generation = _optional_positive_int("expected_run_generation", expected_run_generation)
    cleaned_expected_sequence = _optional_nonnegative_int("expected_last_sequence", expected_last_sequence)
    cleaned_expected_status = _optional_work_item_status(expected_work_item_status)
    cleaned_work_item_id = _optional_bounded_text("work_item_id", work_item_id, max_chars=64)
    if cleaned_expected_status is not None and cleaned_work_item_id is None:
        raise ValueError("expected_work_item_status requires an existing work_item_id")
    cleaned_parent_id = _optional_bounded_text("parent_work_item_id", parent_work_item_id, max_chars=64)
    cleaned_goal = _optional_bounded_text("work_item_goal", work_item_goal, max_bytes=8192)
    cleaned_owner = _optional_bounded_text("owner_agent_id", owner_agent_id, max_chars=128)
    cleaned_agent_id = _optional_bounded_text("agent_id", agent_id, max_chars=128)
    cleaned_thread_id = _optional_bounded_text("thread_id", thread_id, max_chars=256)
    payload_json = _json_object("payload", payload, max_bytes=32768)
    cleaned_payload = json.loads(payload_json)
    physical_event_type = _physical_event_type(cleaned_event_type, cleaned_event_schema_version)
    cleaned_artifact = _normalize_artifact_payload(cleaned_event_type, cleaned_payload)
    evidence_json = _json_array("evidence", evidence, max_bytes=32768)
    cleaned_memory_attribution = _normalize_memory_attribution(cleaned_event_type, memory_attribution)
    cleaned_provenance = normalize_provenance_mapping(provenance) or {}
    idempotency_digest = _idempotency_digest(idempotency_key)
    request_digest = _request_digest(
        {
            "workspace_key": cleaned_workspace,
            "run_id": cleaned_run_id,
            "event_type": cleaned_event_type,
            "event_schema_version": cleaned_event_schema_version,
            "summary": cleaned_summary,
            "work_item_id": cleaned_work_item_id,
            "parent_work_item_id": cleaned_parent_id,
            "work_item_goal": cleaned_goal,
            "owner_agent_id": cleaned_owner,
            "payload": cleaned_payload,
            "evidence": json.loads(evidence_json),
            "memory_attribution": _memory_attribution_request_identity(cleaned_memory_attribution),
            "agent_id": cleaned_agent_id,
            "thread_id": cleaned_thread_id,
            "provenance": cleaned_provenance,
        }
    )
    with store._connect() as conn:
        _begin_run_ledger_write_transaction(conn)
        try:
            run_row = _require_run(conn, workspace_key=cleaned_workspace, run_id=cleaned_run_id)
            existing = conn.execute(
                """
                SELECT event_id, work_item_id, sequence, event_type, summary,
                       request_digest, created_at
                FROM run_events
                WHERE run_id = ? AND idempotency_key_digest = ?
                """,
                (cleaned_run_id, idempotency_digest),
            ).fetchone()
            if existing is not None:
                _require_matching_request(existing, request_digest, subject="record_run_event")
                conn.commit()
                return _event_response(
                    existing,
                    idempotent_replay=True,
                    created_work_item=cleaned_event_type == "work_item_started" and cleaned_work_item_id is None,
                    artifact=_artifact_for_event(conn, run_id=cleaned_run_id, event_id=str(existing["event_id"])),
                    event_schema_version=cleaned_event_schema_version,
                    logical_event_type=cleaned_event_type,
                    database_epoch=read_database_epoch(conn),
                    run_generation=int(run_row["run_generation"]),
                )
            authority = derive_run_authority_state(conn, run_id=cleaned_run_id)
            projection_health = inspect_run_projection(conn, run_id=cleaned_run_id)
            _require_healthy_run_projection(projection_health)
            run_state = authority["run"]
            is_governed = str(run_row["evidence_profile"]) == GOVERNED_V2_PROFILE
            actual_epoch = read_database_epoch(conn)
            actual_generation = int(run_row["run_generation"])
            if is_governed:
                _require_governed_event_preconditions(
                    expected_database_epoch=cleaned_expected_epoch,
                    expected_run_generation=cleaned_expected_generation,
                    expected_last_sequence=cleaned_expected_sequence,
                    actual_database_epoch=actual_epoch,
                    actual_run_generation=actual_generation,
                    actual_last_sequence=int(run_state["last_sequence"]),
                )
                if cleaned_event_schema_version != 2:
                    raise ValueError("governed-v2 runs require event_schema_version=2")
                _validate_governed_v2_event_payload(
                    event_type=cleaned_event_type,
                    payload=cleaned_payload,
                    evidence=json.loads(evidence_json),
                    conn=conn,
                    run_id=cleaned_run_id,
                    risk_level=str(run_row["risk_level"]),
                )
            if str(run_state["status"]) != "active":
                raise ValueError("completed or abandoned runs cannot accept new events")
            actual_sequence = int(run_state["last_sequence"])
            if cleaned_expected_sequence is not None and cleaned_expected_sequence != actual_sequence:
                raise ValueError(
                    f"run sequence conflict: expected {cleaned_expected_sequence}, actual {actual_sequence}"
                )

            created_work_item = False
            effective_work_item_id = cleaned_work_item_id
            created_at = store._utc_now()
            if cleaned_event_type == "work_item_started" and effective_work_item_id is None:
                if cleaned_parent_id is None or cleaned_goal is None:
                    raise ValueError(
                        "work_item_started without work_item_id requires parent_work_item_id and work_item_goal"
                    )
                _require_work_item(conn, run_id=cleaned_run_id, work_item_id=cleaned_parent_id)
                parent_state = authority["work_items_by_id"].get(cleaned_parent_id)
                if parent_state is None:
                    raise RuntimeError("parent work item is missing from authority state")
                parent_status = str(parent_state["status"])
                if parent_status != "active":
                    raise ValueError(f"new work item requires an active parent; actual status is {parent_status}")
                validate_work_item_transition("pending", physical_event_type)
                effective_work_item_id = _new_id("work_")
                conn.execute(
                    """
                    INSERT INTO run_work_items (
                        work_item_id, run_id, parent_work_item_id, goal,
                        owner_agent_id, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        effective_work_item_id,
                        cleaned_run_id,
                        cleaned_parent_id,
                        cleaned_goal,
                        cleaned_owner or cleaned_agent_id or run_row["agent_id"],
                        created_at,
                    ),
                )
                initialize_work_item_projection(
                    conn,
                    run_id=cleaned_run_id,
                    work_item_id=effective_work_item_id,
                    created_at=created_at,
                )
                created_work_item = True
            else:
                if effective_work_item_id is None:
                    raise ValueError("work_item_id is required for this event type")
                if cleaned_parent_id is not None or cleaned_goal is not None:
                    raise ValueError("parent_work_item_id and work_item_goal are only valid when creating a work item")
                _require_work_item(conn, run_id=cleaned_run_id, work_item_id=effective_work_item_id)
                work_state = authority["work_items_by_id"].get(effective_work_item_id)
                if work_state is None:
                    raise RuntimeError("work item is missing from authority state")
                actual_status = str(work_state["status"])
                if is_governed and cleaned_event_type in _GOVERNED_STATE_CHANGING_EVENT_TYPES:
                    if cleaned_expected_status is None:
                        raise ValueError("governed-v2 state-changing events require expected_work_item_status")
                    if actual_status == "blocked" and cleaned_event_type == "work_item_started":
                        raise ValueError(
                            "a blocked governed-v2 work item may leave blocked only via "
                            "work_item_resumed or blocker_resolved"
                        )
                    if cleaned_event_type in {"work_item_resumed", "blocker_resolved"} and actual_status != "blocked":
                        raise ValueError(
                            f"{cleaned_event_type} requires blocked status; actual status is {actual_status}"
                        )
                if cleaned_expected_status is not None and cleaned_expected_status != actual_status:
                    raise ValueError(
                        f"work-item status conflict: expected {cleaned_expected_status}, actual {actual_status}"
                    )
                validate_work_item_transition(actual_status, physical_event_type)

            if is_governed and cleaned_event_type in _GOVERNED_EXECUTION_EVENT_TYPES:
                _require_prior_approved_preflight(conn, run_id=cleaned_run_id)

            resolved_memory_links = _resolve_memory_attribution(
                conn,
                store=store,
                attribution=cleaned_memory_attribution,
                run_id=cleaned_run_id,
            )
            _validate_memory_lifecycle_payload(
                event_type=cleaned_event_type,
                payload=cleaned_payload,
                attribution=cleaned_memory_attribution,
                links=resolved_memory_links,
            )

            sequence = int(
                conn.execute(
                    "SELECT COALESCE(MAX(sequence), 0) + 1 FROM run_events WHERE run_id = ?",
                    (cleaned_run_id,),
                ).fetchone()[0]
            )
            event_id = _new_id("evt_")
            conn.execute(
                """
                INSERT INTO run_events (
                    event_id, run_id, work_item_id, sequence, event_type,
                    event_schema_version, summary, payload_json, evidence_json,
                    idempotency_key_digest, request_digest, agent_id, thread_id,
                    actor, source_app, source_client, source_model,
                    client_session_id, client_workspace, client_transport,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    cleaned_run_id,
                    effective_work_item_id,
                    sequence,
                    physical_event_type,
                    cleaned_summary,
                    payload_json,
                    evidence_json,
                    idempotency_digest,
                    request_digest,
                    cleaned_agent_id or run_row["agent_id"],
                    cleaned_thread_id or run_row["thread_id"],
                    cleaned_provenance.get("actor"),
                    cleaned_provenance.get("source_app"),
                    cleaned_provenance.get("source_client"),
                    cleaned_provenance.get("source_model"),
                    cleaned_provenance.get("client_session_id"),
                    cleaned_provenance.get("client_workspace"),
                    cleaned_provenance.get("client_transport"),
                    created_at,
                ),
            )
            if cleaned_event_schema_version == 2:
                conn.execute(
                    """
                    INSERT INTO run_event_v2_details (
                        event_id, run_id, logical_event_type, payload_schema_version, created_at
                    ) VALUES (?, ?, ?, 2, ?)
                    """,
                    (event_id, cleaned_run_id, cleaned_event_type, created_at),
                )
            artifact = _insert_run_artifact(
                conn,
                artifact=cleaned_artifact,
                run_id=cleaned_run_id,
                work_item_id=effective_work_item_id,
                event_id=event_id,
                created_at=created_at,
            )
            apply_run_event_projection(
                conn,
                run_id=cleaned_run_id,
                work_item_id=effective_work_item_id,
                sequence=sequence,
                event_type=physical_event_type,
                summary=cleaned_summary,
                created_at=created_at,
            )
            generation_cursor = conn.execute(
                """
                UPDATE agent_runs
                SET run_generation = run_generation + 1
                WHERE run_id = ? AND run_generation = ?
                """,
                (cleaned_run_id, actual_generation),
            )
            if generation_cursor.rowcount != 1:
                raise RuntimeError("run generation changed during event append")
            _insert_run_memory_links(
                conn,
                run_id=cleaned_run_id,
                work_item_id=effective_work_item_id,
                event_id=event_id,
                event_idempotency_digest=idempotency_digest,
                event_request_digest=request_digest,
                links=resolved_memory_links,
                created_at=created_at,
            )
            conn.commit()
            row = conn.execute(
                """
                SELECT event_id, work_item_id, sequence, event_type, summary,
                       request_digest, created_at
                FROM run_events
                WHERE event_id = ?
                """,
                (event_id,),
            ).fetchone()
            return _event_response(
                row,
                idempotent_replay=False,
                created_work_item=created_work_item,
                artifact=artifact,
                event_schema_version=cleaned_event_schema_version,
                logical_event_type=cleaned_event_type,
                database_epoch=actual_epoch,
                run_generation=actual_generation + 1,
            )
        except BaseException:
            conn.rollback()
            raise


def get_run_entry(
    store: Any,
    *,
    workspace_key: str,
    run_id: str,
    since_sequence: int = 0,
    event_limit: int = 100,
) -> dict[str, Any]:
    cleaned_workspace = _bounded_text("workspace_key", workspace_key, max_chars=512)
    cleaned_run_id = _bounded_text("run_id", run_id, max_chars=64)
    if since_sequence < 0:
        raise ValueError("since_sequence must be at least 0")
    if event_limit < 1 or event_limit > 500:
        raise ValueError("event_limit must be between 1 and 500")
    with store._connect() as conn:
        conn.execute("BEGIN")
        try:
            snapshot_epoch = read_database_epoch(conn)
            run_row = _require_run(conn, workspace_key=cleaned_workspace, run_id=cleaned_run_id)
            authority = derive_run_authority_state(conn, run_id=cleaned_run_id)
            projection_health = inspect_run_projection(conn, run_id=cleaned_run_id)
            authority_work_items = authority["work_items_by_id"]
            work_item_rows = conn.execute(
                """
                SELECT *
                FROM run_work_items
                WHERE run_id = ?
                ORDER BY created_at, work_item_id
                """,
                (cleaned_run_id,),
            ).fetchall()
            work_items = [
                _work_item_payload({**dict(row), **authority_work_items[str(row["work_item_id"])]})
                for row in work_item_rows
            ]
            event_rows = conn.execute(
                """
                SELECT event.event_id, event.work_item_id, event.sequence,
                       COALESCE(detail.logical_event_type, event.event_type) AS logical_event_type,
                       CASE WHEN detail.event_id IS NULL THEN event.event_schema_version ELSE 2 END
                           AS logical_event_schema_version,
                       event.summary, event.payload_json, event.evidence_json,
                       event.agent_id, event.thread_id, event.actor, event.source_app, event.source_client,
                       event.source_model, event.client_session_id, event.client_workspace,
                       event.client_transport, event.created_at
                FROM run_events event
                LEFT JOIN run_event_v2_details detail
                  ON detail.run_id = event.run_id AND detail.event_id = event.event_id
                WHERE event.run_id = ? AND event.sequence > ?
                ORDER BY event.sequence
                LIMIT ?
                """,
                (cleaned_run_id, since_sequence, event_limit + 1),
            ).fetchall()
            has_more = len(event_rows) > event_limit
            visible_events = event_rows[:event_limit]
            artifacts = _artifacts_for_events(
                conn,
                run_id=cleaned_run_id,
                event_ids=[str(row["event_id"]) for row in visible_events],
            )
            outcome = conn.execute(
                """
                SELECT outcome.*
                FROM run_outcomes outcome
                LEFT JOIN run_outcomes child ON child.supersedes_outcome_id = outcome.outcome_id
                WHERE outcome.run_id = ? AND child.outcome_id IS NULL
                """,
                (cleaned_run_id,),
            ).fetchone()
            receipt_rows = conn.execute(
                """
                SELECT *
                FROM run_verification_receipts
                WHERE run_id = ?
                ORDER BY created_at, receipt_id
                """,
                (cleaned_run_id,),
            ).fetchall()
            latest_sequence = int(authority["run"]["last_sequence"])
            next_sequence = int(visible_events[-1]["sequence"]) if visible_events else since_sequence
            response = {
                "run": _run_payload(run_row, authority["run"]),
                "work_items": work_items,
                "events": [_event_payload(row) for row in visible_events],
                "artifacts": artifacts,
                "outcome": _outcome_payload(outcome) if outcome is not None else None,
                "verification_receipts": [verification_receipt_payload(row) for row in receipt_rows],
                "since_sequence": since_sequence,
                "next_sequence": next_sequence,
                "latest_sequence": latest_sequence,
                "has_more": has_more,
                "snapshot_epoch": snapshot_epoch,
                "snapshot_last_sequence": latest_sequence,
                "projection_health": projection_health,
                "unresolved_information_gap_ids": _unresolved_information_gap_ids(conn, run_id=cleaned_run_id),
                "degraded": not bool(projection_health["ok"]),
            }
            conn.commit()
            return response
        except BaseException:
            conn.rollback()
            raise


def complete_run_entry(
    store: Any,
    *,
    workspace_key: str,
    run_id: str,
    outcome: str,
    evaluator_type: str,
    idempotency_key: str,
    evidence: Sequence[Any] | None = None,
    metrics: Mapping[str, Any] | None = None,
    evaluator_digest: str | None = None,
    evaluator_version: str | None = None,
    termination_reason: str | None = None,
    supersedes_outcome_id: str | None = None,
    regression_of_run_id: str | None = None,
    verification_receipt_id: str | None = None,
    expected_database_epoch: str | None = None,
    expected_run_generation: int | None = None,
    expected_last_sequence: int | None = None,
    provenance: Mapping[str, object | None] | None = None,
) -> dict[str, Any]:
    cleaned_workspace = _bounded_text("workspace_key", workspace_key, max_chars=512)
    cleaned_run_id = _bounded_text("run_id", run_id, max_chars=64)
    cleaned_outcome = str(outcome).strip()
    if cleaned_outcome not in RUN_OUTCOME_TYPES:
        raise ValueError(f"unsupported run outcome: {cleaned_outcome or '<empty>'}")
    cleaned_evaluator_type = str(evaluator_type).strip()
    if cleaned_evaluator_type not in RUN_EVALUATOR_TYPES:
        raise ValueError(f"unsupported evaluator type: {cleaned_evaluator_type or '<empty>'}")
    evidence_json = _json_array("evidence", evidence, max_bytes=32768)
    metrics_json = _json_object("metrics", metrics, max_bytes=32768)
    cleaned_evaluator_digest = _optional_digest("evaluator_digest", evaluator_digest)
    cleaned_evaluator_version = _optional_bounded_text("evaluator_version", evaluator_version, max_chars=128)
    cleaned_reason = _optional_bounded_text("termination_reason", termination_reason, max_bytes=1024)
    cleaned_supersedes = _optional_bounded_text("supersedes_outcome_id", supersedes_outcome_id, max_chars=64)
    cleaned_regression_run = _optional_bounded_text("regression_of_run_id", regression_of_run_id, max_chars=64)
    cleaned_receipt_id = _optional_bounded_text("verification_receipt_id", verification_receipt_id, max_chars=64)
    cleaned_expected_epoch = _optional_bounded_text("expected_database_epoch", expected_database_epoch, max_chars=128)
    cleaned_expected_generation = _optional_positive_int("expected_run_generation", expected_run_generation)
    cleaned_expected_sequence = _optional_nonnegative_int("expected_last_sequence", expected_last_sequence)
    if cleaned_outcome == "regression" and cleaned_regression_run is None:
        raise ValueError("regression outcome requires regression_of_run_id")
    if cleaned_outcome != "regression" and cleaned_regression_run is not None:
        raise ValueError("regression_of_run_id is only valid for a regression outcome")
    cleaned_provenance = normalize_provenance_mapping(provenance) or {}
    idempotency_digest = _idempotency_digest(idempotency_key)
    request_digest = _request_digest(
        {
            "workspace_key": cleaned_workspace,
            "run_id": cleaned_run_id,
            "outcome": cleaned_outcome,
            "evaluator_type": cleaned_evaluator_type,
            "evidence": json.loads(evidence_json),
            "metrics": json.loads(metrics_json),
            "evaluator_digest": cleaned_evaluator_digest,
            "evaluator_version": cleaned_evaluator_version,
            "termination_reason": cleaned_reason,
            "supersedes_outcome_id": cleaned_supersedes,
            "regression_of_run_id": cleaned_regression_run,
            "verification_receipt_id": cleaned_receipt_id,
            "provenance": cleaned_provenance,
        }
    )
    with store._connect() as conn:
        _begin_run_ledger_write_transaction(conn)
        try:
            run_row = _require_run(conn, workspace_key=cleaned_workspace, run_id=cleaned_run_id)
            existing = conn.execute(
                """
                SELECT *
                FROM run_outcomes
                WHERE run_id = ? AND idempotency_key_digest = ?
                """,
                (cleaned_run_id, idempotency_digest),
            ).fetchone()
            if existing is not None:
                _require_matching_request(existing, request_digest, subject="complete_run")
                unresolved_information_gap_ids = _unresolved_information_gap_ids(conn, run_id=cleaned_run_id)
                conn.commit()
                return {
                    **_outcome_payload(existing),
                    "unresolved_information_gap_ids": unresolved_information_gap_ids,
                    "database_epoch": read_database_epoch(conn),
                    "run_generation": int(run_row["run_generation"]),
                    "idempotent_replay": True,
                }
            authority = derive_run_authority_state(conn, run_id=cleaned_run_id)
            projection_health = inspect_run_projection(conn, run_id=cleaned_run_id)
            _require_healthy_run_projection(projection_health)
            is_governed = str(run_row["evidence_profile"]) == GOVERNED_V2_PROFILE
            actual_epoch = read_database_epoch(conn)
            actual_generation = int(run_row["run_generation"])
            actual_sequence = int(authority["run"]["last_sequence"])
            if is_governed:
                _require_governed_completion_preconditions(
                    expected_database_epoch=cleaned_expected_epoch,
                    expected_run_generation=cleaned_expected_generation,
                    expected_last_sequence=cleaned_expected_sequence,
                    actual_database_epoch=actual_epoch,
                    actual_run_generation=actual_generation,
                    actual_last_sequence=actual_sequence,
                )
            verification_profile = (
                "legacy_declared" if str(run_row["evidence_profile"]) == "legacy-v1" else "observational"
            )
            validated_receipt: Mapping[str, Any] | None = None
            if cleaned_outcome == "verified_success":
                if str(run_row["evidence_profile"]) == "legacy-v1":
                    if cleaned_evaluator_type not in {"deterministic_verifier", "human"} or not json.loads(
                        evidence_json
                    ):
                        raise ValueError("verified_success requires deterministic-verifier or human evidence")
                elif is_governed:
                    if cleaned_receipt_id is None:
                        raise ValueError("verified_success requires a server-minted governed verification receipt")
                    validated_receipt = validate_receipt_for_verified_success(
                        conn,
                        run=cast(Mapping[str, Any], run_row),
                        receipt_id=cleaned_receipt_id,
                        evaluator_type=cleaned_evaluator_type,
                        evaluator_digest=cleaned_evaluator_digest,
                        evaluator_version=cleaned_evaluator_version,
                    )
                    receipt_evidence = str(validated_receipt["evidence_json"])
                    if json.loads(evidence_json) and evidence_json != receipt_evidence:
                        raise ValueError("verified_success evidence must match the governed verification receipt")
                    evidence_json = receipt_evidence
                    verification_profile = GOVERNED_V2_PROFILE
                elif cleaned_receipt_id is not None:
                    raise ValueError("verification_receipt_id is only valid for governed verified_success")
                elif cleaned_evaluator_type not in {"deterministic_verifier", "human"} or not json.loads(evidence_json):
                    raise ValueError("verified_success requires deterministic-verifier or human evidence")
            elif cleaned_receipt_id is not None:
                raise ValueError("verification_receipt_id is only valid for verified_success")
            if is_governed and cleaned_outcome != "verified_success":
                _require_governed_verification_result_coverage(conn, run_id=cleaned_run_id)
            unresolved_information_gap_ids = _unresolved_information_gap_ids(conn, run_id=cleaned_run_id)
            if cleaned_outcome == "verified_success" and unresolved_information_gap_ids:
                raise ValueError("verified_success requires no unresolved information gaps")
            current_outcome_id = authority["run"]["outcome_id"]
            if current_outcome_id is None and cleaned_supersedes is not None:
                raise ValueError("supersedes_outcome_id is invalid because the run has no outcome")
            if current_outcome_id is not None and cleaned_supersedes != str(current_outcome_id):
                raise ValueError("run already has an outcome; corrections must supersede the current head")
            if current_outcome_id is None:
                _require_terminal_work_items(authority["work_items"])
            if cleaned_regression_run is not None:
                regression_row = conn.execute(
                    """
                    SELECT outcome.*
                    FROM agent_runs target
                    JOIN run_outcomes outcome ON outcome.run_id = target.run_id
                    LEFT JOIN run_outcomes correction ON correction.supersedes_outcome_id = outcome.outcome_id
                    WHERE target.run_id = ?
                      AND target.workspace_key = ?
                      AND target.run_id != ?
                      AND correction.outcome_id IS NULL
                    """,
                    (cleaned_regression_run, cleaned_workspace, cleaned_run_id),
                ).fetchone()
                if regression_row is None or not is_strong_verified_outcome(regression_row):
                    raise ValueError(
                        "regression_of_run_id must reference a distinct current strong verified outcome "
                        "in the declared workspace"
                    )
            outcome_id = _new_id("outcome_")
            created_at = store._utc_now()
            conn.execute(
                """
                INSERT INTO run_outcomes (
                    outcome_id, run_id, outcome_type, evaluator_type,
                    evaluator_digest, evaluator_version, evidence_json,
                    metrics_json, supersedes_outcome_id, regression_of_run_id,
                    termination_reason, verification_profile, verification_receipt_id,
                    idempotency_key_digest, request_digest,
                    actor, source_app, source_client, source_model,
                    client_session_id, client_workspace, client_transport,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    outcome_id,
                    cleaned_run_id,
                    cleaned_outcome,
                    cleaned_evaluator_type,
                    cleaned_evaluator_digest,
                    cleaned_evaluator_version,
                    evidence_json,
                    metrics_json,
                    cleaned_supersedes,
                    cleaned_regression_run,
                    cleaned_reason,
                    verification_profile,
                    cleaned_receipt_id,
                    idempotency_digest,
                    request_digest,
                    cleaned_provenance.get("actor"),
                    cleaned_provenance.get("source_app"),
                    cleaned_provenance.get("source_client"),
                    cleaned_provenance.get("source_model"),
                    cleaned_provenance.get("client_session_id"),
                    cleaned_provenance.get("client_workspace"),
                    cleaned_provenance.get("client_transport"),
                    created_at,
                ),
            )
            apply_run_outcome_projection(
                conn,
                run_id=cleaned_run_id,
                outcome_id=outcome_id,
                outcome_type=cleaned_outcome,
                termination_reason=cleaned_reason,
                created_at=created_at,
            )
            generation_cursor = conn.execute(
                """
                UPDATE agent_runs
                SET run_generation = run_generation + 1
                WHERE run_id = ? AND run_generation = ?
                """,
                (cleaned_run_id, actual_generation),
            )
            if generation_cursor.rowcount != 1:
                raise RuntimeError("run generation changed during outcome completion")
            conn.commit()
            row = conn.execute("SELECT * FROM run_outcomes WHERE outcome_id = ?", (outcome_id,)).fetchone()
            return {
                **_outcome_payload(row),
                "unresolved_information_gap_ids": unresolved_information_gap_ids,
                "database_epoch": actual_epoch,
                "run_generation": actual_generation + 1,
                "idempotent_replay": False,
            }
        except BaseException:
            conn.rollback()
            raise


def _begin_run_response(conn: sqlite3.Connection, run_id: str, *, idempotent_replay: bool) -> dict[str, Any]:
    root = conn.execute(
        """
        SELECT work_item_id
        FROM run_work_items
        WHERE run_id = ? AND parent_work_item_id IS NULL
        """,
        (run_id,),
    ).fetchone()
    authority = derive_run_authority_state(conn, run_id=run_id)
    run = conn.execute("SELECT * FROM agent_runs WHERE run_id = ?", (run_id,)).fetchone()
    if root is None:
        raise RuntimeError("run initialization is incomplete")
    if run is None:
        raise RuntimeError("run initialization is incomplete")
    return {
        "run_id": run_id,
        "root_work_item_id": str(root["work_item_id"]),
        "status": str(authority["run"]["status"]),
        "initial_sequence": int(authority["run"]["last_sequence"]),
        "evidence_profile": str(run["evidence_profile"]),
        "acceptance_criteria_digest": str(run["acceptance_criteria_digest"]),
        "database_epoch": read_database_epoch(conn),
        "run_generation": int(run["run_generation"]),
        "continuation_of_run_id": run["continuation_of_run_id"],
        "idempotent_replay": idempotent_replay,
    }


def _require_run(conn: sqlite3.Connection, *, workspace_key: str, run_id: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM agent_runs WHERE run_id = ? AND workspace_key = ?",
        (run_id, workspace_key),
    ).fetchone()
    if row is None:
        raise ValueError("run_id does not exist in the declared workspace")
    return row


def _require_work_item(conn: sqlite3.Connection, *, run_id: str, work_item_id: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM run_work_items WHERE run_id = ? AND work_item_id = ?",
        (run_id, work_item_id),
    ).fetchone()
    if row is None:
        raise ValueError("work_item_id does not belong to the declared run")
    return row


def _require_terminal_work_items(work_items: Sequence[Mapping[str, Any]]) -> None:
    active_rows = [row for row in work_items if str(row["status"]) not in TERMINAL_WORK_ITEM_STATUSES]
    if active_rows:
        states = ", ".join(f"{row['work_item_id']}={row['status']}" for row in active_rows)
        raise ValueError(f"first run outcome requires terminal work items; remaining: {states}")


def _require_healthy_run_projection(projection_health: Mapping[str, Any]) -> None:
    if bool(projection_health.get("ok")):
        return
    counts = projection_health.get("counts")
    details = "unknown"
    if isinstance(counts, Mapping):
        nonzero = [f"{key}={value}" for key, value in counts.items() if int(value) > 0]
        if nonzero:
            details = ", ".join(nonzero)
    raise RuntimeError(f"run projection health is degraded; write refused ({details})")


def _require_matching_request(row: sqlite3.Row, request_digest: str, *, subject: str) -> None:
    if str(row["request_digest"]) != request_digest:
        raise ValueError(f"{subject} idempotency key was already used with a different payload")


def _event_response(
    row: sqlite3.Row,
    *,
    idempotent_replay: bool,
    created_work_item: bool,
    artifact: dict[str, Any] | None = None,
    event_schema_version: int = 1,
    logical_event_type: str | None = None,
    database_epoch: str | None = None,
    run_generation: int | None = None,
) -> dict[str, Any]:
    response = {
        "event_id": str(row["event_id"]),
        "work_item_id": str(row["work_item_id"]),
        "sequence": int(row["sequence"]),
        "event_type": logical_event_type or str(row["event_type"]),
        "event_schema_version": event_schema_version,
        "summary": str(row["summary"]),
        "created_at": str(row["created_at"]),
        "created_work_item": created_work_item,
        "idempotent_replay": idempotent_replay,
    }
    if artifact is not None:
        response["artifact"] = artifact
    if database_epoch is not None:
        response["database_epoch"] = database_epoch
    if run_generation is not None:
        response["run_generation"] = run_generation
    return response


def _run_payload(run: sqlite3.Row, state: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "run_id": str(run["run_id"]),
        "workspace_key": str(run["workspace_key"]),
        "goal": str(run["root_goal"]),
        "status": str(state["status"]),
        "model_digest": run["model_digest"],
        "harness_digest": run["harness_digest"],
        "chat_template_digest": run["chat_template_digest"],
        "tool_schema_digest": run["tool_schema_digest"],
        "agent_id": run["agent_id"],
        "thread_id": run["thread_id"],
        "memory_scopes": json.loads(str(run["memory_scopes_json"])),
        "budget": json.loads(str(run["budget_json"])),
        "evidence_profile": str(run["evidence_profile"]),
        "acceptance_criteria": json.loads(str(run["acceptance_criteria_json"])),
        "acceptance_criteria_digest": str(run["acceptance_criteria_digest"]),
        "constraints": json.loads(str(run["constraints_json"])),
        "non_goals": json.loads(str(run["non_goals_json"])),
        "risk_level": str(run["risk_level"]),
        "continuation_of_run_id": run["continuation_of_run_id"],
        "run_generation": int(run["run_generation"]),
        "run_config_digest": calculate_run_config_digest(cast(Mapping[str, Any], run)),
        "created_at": str(run["created_at"]),
        "ended_at": state["ended_at"],
        "terminal_at": state["terminal_at"],
        "current_outcome_updated_at": state["current_outcome_updated_at"],
        "termination_reason": state["termination_reason"],
        "last_sequence": int(state["last_sequence"]),
        "unresolved_blocker_count": int(state["unresolved_blocker_count"]),
        "active_work_item_count": int(state["active_work_item_count"]),
    }


def _work_item_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "work_item_id": str(row["work_item_id"]),
        "run_id": str(row["run_id"]),
        "parent_work_item_id": row["parent_work_item_id"],
        "goal": str(row["goal"]),
        "owner_agent_id": row["owner_agent_id"],
        "status": str(row["status"]),
        "last_sequence": int(row["last_sequence"]),
        "started_at": row["started_at"],
        "ended_at": row["ended_at"],
        "last_summary": row["last_summary"],
        "created_at": str(row["created_at"]),
    }


def _event_payload(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "event_id": str(row["event_id"]),
        "work_item_id": str(row["work_item_id"]),
        "sequence": int(row["sequence"]),
        "event_type": str(row["logical_event_type"]),
        "event_schema_version": int(row["logical_event_schema_version"]),
        "summary": str(row["summary"]),
        "payload": json.loads(str(row["payload_json"])),
        "evidence": json.loads(str(row["evidence_json"])),
        "agent_id": row["agent_id"],
        "thread_id": row["thread_id"],
        "actor": row["actor"],
        "source_app": row["source_app"],
        "source_client": row["source_client"],
        "source_model": row["source_model"],
        "client_session_id": row["client_session_id"],
        "client_workspace": row["client_workspace"],
        "client_transport": row["client_transport"],
        "created_at": str(row["created_at"]),
    }


def _normalize_artifact_payload(event_type: str, payload: Mapping[str, Any]) -> dict[str, Any] | None:
    artifact_value = payload.get("artifact")
    if event_type != ARTIFACT_CREATED_EVENT_TYPE:
        if "artifact" in payload:
            raise ValueError("payload.artifact is only valid for artifact_created events")
        return None
    if not isinstance(artifact_value, Mapping):
        raise ValueError("artifact_created requires payload.artifact as an object")
    _reject_artifact_managed_or_content_fields(payload)
    allowed = {"digest", "mime_type", "uri", "metadata"}
    _require_exact_artifact_fields(artifact_value, allowed, required={"digest", "mime_type", "uri"})
    metadata = artifact_value.get("metadata", {})
    if not isinstance(metadata, Mapping):
        raise ValueError("artifact.metadata must be an object")
    _reject_artifact_managed_or_content_fields(metadata)
    metadata_json = _json_object("artifact.metadata", metadata, max_bytes=8192)
    return {
        "digest": _required_attribution_digest("artifact.digest", artifact_value["digest"]),
        "mime_type": _required_attribution_text("artifact.mime_type", artifact_value["mime_type"], max_chars=255),
        "uri": _required_artifact_uri(artifact_value["uri"]),
        "metadata_json": metadata_json,
    }


def _require_exact_artifact_fields(
    value: Mapping[str, Any],
    allowed: set[str],
    *,
    required: set[str],
) -> None:
    missing = sorted(required - set(value))
    unexpected = sorted(str(key) for key in value if key not in allowed)
    if missing:
        raise ValueError(f"artifact is missing required fields: {missing}")
    if unexpected:
        raise ValueError(f"artifact has unsupported fields: {unexpected}")


def _reject_artifact_managed_or_content_fields(value: Any) -> None:
    if isinstance(value, Mapping):
        for raw_key, nested in value.items():
            key = normalize_durable_key(str(raw_key))
            if key in _CALLER_MANAGED_ARTIFACT_KEYS:
                raise ValueError(f"artifact field is server-managed: {raw_key}")
            if key in _FORBIDDEN_ARTIFACT_CONTENT_KEYS:
                raise ValueError(f"artifact must not store content or binary data: {raw_key}")
            _reject_artifact_managed_or_content_fields(nested)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            _reject_artifact_managed_or_content_fields(item)
    elif isinstance(value, str):
        match = ARTIFACT_URI_RE.fullmatch(value)
        if match is None:
            return
        scheme = match["scheme"].casefold()
        if scheme in _FORBIDDEN_ARTIFACT_URI_SCHEMES:
            raise ValueError(f"artifact must not use the {scheme} scheme")
        if urlsplit(value).netloc and "@" in urlsplit(value).netloc:
            raise ValueError("artifact must not embed credentials")


def _insert_run_artifact(
    conn: sqlite3.Connection,
    *,
    artifact: Mapping[str, Any] | None,
    run_id: str,
    work_item_id: str,
    event_id: str,
    created_at: str,
) -> dict[str, Any] | None:
    if artifact is None:
        return None
    artifact_id = _new_id("artifact_")
    conn.execute(
        """
        INSERT INTO run_artifacts (
            artifact_id, artifact_version, run_id, work_item_id,
            producing_event_id, digest, mime_type, uri, metadata_json, created_at
        ) VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            artifact_id,
            run_id,
            work_item_id,
            event_id,
            artifact["digest"],
            artifact["mime_type"],
            artifact["uri"],
            artifact["metadata_json"],
            created_at,
        ),
    )
    row = conn.execute(
        """
        SELECT artifact_id, artifact_version, run_id, work_item_id,
               producing_event_id, digest, mime_type, uri, metadata_json, created_at
        FROM run_artifacts
        WHERE artifact_id = ? AND artifact_version = 1
        """,
        (artifact_id,),
    ).fetchone()
    if row is None:
        raise RuntimeError("artifact insertion is incomplete")
    return _artifact_payload(row)


def _artifact_for_event(conn: sqlite3.Connection, *, run_id: str, event_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT artifact_id, artifact_version, run_id, work_item_id,
               producing_event_id, digest, mime_type, uri, metadata_json, created_at
        FROM run_artifacts
        WHERE run_id = ? AND producing_event_id = ?
        ORDER BY artifact_id, artifact_version
        """,
        (run_id, event_id),
    ).fetchone()
    return _artifact_payload(row) if row is not None else None


def _artifacts_for_events(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    event_ids: Sequence[str],
) -> list[dict[str, Any]]:
    if not event_ids:
        return []
    placeholders = ", ".join("?" for _ in event_ids)
    rows = conn.execute(
        f"""
        SELECT artifact.artifact_id, artifact.artifact_version, artifact.run_id,
               artifact.work_item_id, artifact.producing_event_id, artifact.digest,
               artifact.mime_type, artifact.uri, artifact.metadata_json, artifact.created_at
        FROM run_artifacts artifact
        JOIN run_events event
          ON event.run_id = artifact.run_id AND event.event_id = artifact.producing_event_id
        WHERE artifact.run_id = ? AND artifact.producing_event_id IN ({placeholders})
        ORDER BY event.sequence, artifact.artifact_id, artifact.artifact_version
        """,
        (run_id, *event_ids),
    ).fetchall()
    return [_artifact_payload(row) for row in rows]


def _artifact_payload(row: sqlite3.Row) -> dict[str, Any]:
    uri = str(row["uri"])
    match = ARTIFACT_URI_RE.fullmatch(uri)
    if match is None:
        raise RuntimeError("stored artifact URI is malformed")
    uri_scheme = match["scheme"].casefold()
    return {
        "artifact_id": str(row["artifact_id"]),
        "artifact_version": int(row["artifact_version"]),
        "run_id": str(row["run_id"]),
        "work_item_id": str(row["work_item_id"]),
        "producing_event_id": str(row["producing_event_id"]),
        "digest": str(row["digest"]),
        "mime_type": str(row["mime_type"]),
        "uri": uri,
        "uri_scheme": uri_scheme,
        "operator_open_required": uri_scheme in {"http", "https"},
        "metadata": json.loads(str(row["metadata_json"])),
        "created_at": str(row["created_at"]),
    }


def _event_schema_version(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value not in {1, 2}:
        raise ValueError("event_schema_version must be 1 or 2")
    return value


def _physical_event_type(logical_event_type: str, event_schema_version: int) -> str:
    if event_schema_version != 2:
        return logical_event_type
    if logical_event_type in {"work_item_resumed", "blocker_resolved"}:
        return "work_item_started"
    if logical_event_type in GOVERNED_V2_EVENT_TYPES:
        return "checkpoint"
    return logical_event_type


def _require_governed_event_preconditions(
    *,
    expected_database_epoch: str | None,
    expected_run_generation: int | None,
    expected_last_sequence: int | None,
    actual_database_epoch: str,
    actual_run_generation: int,
    actual_last_sequence: int,
) -> None:
    if expected_database_epoch is None:
        raise ValueError("governed-v2 events require expected_database_epoch")
    if expected_run_generation is None:
        raise ValueError("governed-v2 events require expected_run_generation")
    if expected_last_sequence is None:
        raise ValueError("governed-v2 events require expected_last_sequence")
    if expected_database_epoch != actual_database_epoch:
        raise ValueError(f"database epoch conflict: expected {expected_database_epoch}, actual {actual_database_epoch}")
    if expected_run_generation != actual_run_generation:
        raise ValueError(f"run generation conflict: expected {expected_run_generation}, actual {actual_run_generation}")
    if expected_last_sequence != actual_last_sequence:
        raise ValueError(f"run sequence conflict: expected {expected_last_sequence}, actual {actual_last_sequence}")


def _require_governed_completion_preconditions(
    *,
    expected_database_epoch: str | None,
    expected_run_generation: int | None,
    expected_last_sequence: int | None,
    actual_database_epoch: str,
    actual_run_generation: int,
    actual_last_sequence: int,
) -> None:
    _require_governed_event_preconditions(
        expected_database_epoch=expected_database_epoch,
        expected_run_generation=expected_run_generation,
        expected_last_sequence=expected_last_sequence,
        actual_database_epoch=actual_database_epoch,
        actual_run_generation=actual_run_generation,
        actual_last_sequence=actual_last_sequence,
    )


def _validate_governed_v2_event_payload(
    *,
    event_type: str,
    payload: Mapping[str, Any],
    evidence: Sequence[Any],
    conn: sqlite3.Connection,
    run_id: str,
    risk_level: str,
) -> None:
    if event_type in {"hypothesis_confirmed", "hypothesis_rejected"}:
        _require_payload_fields(payload, required={"hypothesis_event_id", "evidence_refs"})
        hypothesis_event_id = _bounded_text("payload.hypothesis_event_id", payload["hypothesis_event_id"], max_chars=64)
        _require_nonempty_evidence_refs(payload["evidence_refs"])
        _require_same_run_logical_event(
            conn,
            run_id=run_id,
            event_id=hypothesis_event_id,
            logical_event_type="hypothesis",
        )
        return
    if event_type == "hypothesis":
        _require_payload_fields(
            payload,
            required={"claim", "falsifier", "epistemic_status", "confidence"},
        )
        _bounded_text("payload.claim", payload["claim"], max_bytes=4096)
        _bounded_text("payload.falsifier", payload["falsifier"], max_bytes=4096)
        _require_enum_payload_value(
            "payload.epistemic_status",
            payload["epistemic_status"],
            _HYPOTHESIS_EPISTEMIC_STATUSES,
        )
        confidence = payload["confidence"]
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0.0 <= confidence <= 1.0:
            raise ValueError("payload.confidence must be a number between 0.0 and 1.0")
        return
    if event_type == "preflight_review":
        required = {
            "approved",
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
        _require_payload_fields(payload, required=required, optional={"rollback_plan"})
        if not isinstance(payload["approved"], bool):
            raise ValueError("preflight_review payload.approved must be a boolean")
        for field in sorted(required - {"approved"}):
            _require_bounded_payload_array(f"payload.{field}", payload[field])
        rollback_plan = payload.get("rollback_plan")
        if risk_level in {"high", "critical"}:
            if not isinstance(rollback_plan, str) or not rollback_plan.strip():
                raise ValueError("high or critical preflight_review requires payload.rollback_plan")
            _bounded_text("payload.rollback_plan", rollback_plan, max_bytes=4096)
        elif rollback_plan is not None:
            _bounded_text("payload.rollback_plan", rollback_plan, max_bytes=4096)
        if evidence:
            raise ValueError("preflight_review evidence must be carried in payload")
        return
    if event_type == "decision":
        _require_payload_fields(
            payload,
            required={"selected_decision", "alternatives_considered", "evidence_refs", "maintenance_summary"},
        )
        _bounded_text("payload.selected_decision", payload["selected_decision"], max_bytes=4096)
        _require_bounded_payload_array("payload.alternatives_considered", payload["alternatives_considered"])
        _require_nonempty_evidence_refs(payload["evidence_refs"])
        _require_maintenance_summary(payload["maintenance_summary"])
        return
    if event_type == "test_failure":
        raise ValueError("governed-v2 test evidence must use test_result")
    if event_type == "test_result":
        _require_payload_fields(payload, required={"test_id", "result", "evidence_refs"})
        _bounded_text("payload.test_id", payload["test_id"], max_chars=256)
        _require_enum_payload_value("payload.result", payload["result"], {"passed", "failed", "skipped"})
        _require_nonempty_evidence_refs(payload["evidence_refs"])
        return
    if event_type == "risk_identified":
        _require_payload_fields(payload, required={"risk_id", "severity"}, optional={"mitigation"})
        _bounded_text("payload.risk_id", payload["risk_id"], max_chars=128)
        _require_enum_payload_value("payload.severity", payload["severity"], RUN_RISK_LEVELS)
        if "mitigation" in payload:
            _bounded_text("payload.mitigation", payload["mitigation"], max_bytes=4096)
        return
    if event_type == "information_gap":
        _require_payload_fields(payload, required={"gap_id", "question"})
        _bounded_text("payload.gap_id", payload["gap_id"], max_chars=128)
        _bounded_text("payload.question", payload["question"], max_bytes=4096)
        return
    if event_type == "verification_result":
        _require_payload_fields(payload, required={"criterion_id", "result", "evidence_refs"})
        criterion_id = _bounded_text("payload.criterion_id", payload["criterion_id"], max_chars=128)
        if criterion_id not in _governed_criterion_ids(conn, run_id=run_id):
            raise ValueError("payload.criterion_id is not declared by the governed run")
        _require_enum_payload_value("payload.result", payload["result"], {"passed", "failed", "not_applicable"})
        _require_nonempty_evidence_refs(payload["evidence_refs"])
        return
    if event_type == "work_item_resumed":
        _require_payload_fields(payload, required={"reason"})
        _bounded_text("payload.reason", payload["reason"], max_bytes=4096)
        return
    if event_type == "blocker_resolved":
        _require_payload_fields(payload, required={"blocker_event_id", "resolution"})
        blocker_event_id = _bounded_text("payload.blocker_event_id", payload["blocker_event_id"], max_chars=64)
        _bounded_text("payload.resolution", payload["resolution"], max_bytes=4096)
        _require_same_run_logical_event(
            conn,
            run_id=run_id,
            event_id=blocker_event_id,
            logical_event_type="blocker",
        )
        return


def _require_payload_fields(
    payload: Mapping[str, Any],
    *,
    required: set[str],
    optional: set[str] | None = None,
) -> None:
    optional = optional or set()
    missing = sorted(required - set(payload))
    unexpected = sorted(str(key) for key in payload if key not in required | optional)
    if missing:
        raise ValueError(f"governed-v2 payload is missing required fields: {missing}")
    if unexpected:
        raise ValueError(f"governed-v2 payload has unsupported fields: {unexpected}")


def _require_enum_payload_value(name: str, value: object, allowed: Sequence[str] | set[str] | frozenset[str]) -> None:
    cleaned = _bounded_text(name, value, max_chars=64)
    if cleaned not in allowed:
        raise ValueError(f"{name} has unsupported value: {cleaned}")


def _require_nonempty_evidence_refs(value: Any) -> None:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise ValueError("payload.evidence_refs must be an array")
    if not value:
        raise ValueError("payload.evidence_refs must not be empty")
    require_durable_structured_data(list(value), subject="durable run structured data")
    _bounded_json("payload.evidence_refs", list(value), max_bytes=8192)


def _require_bounded_payload_array(name: str, value: Any) -> None:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise ValueError(f"{name} must be an array")
    items = list(value)
    if len(items) > 32:
        raise ValueError(f"{name} must contain at most 32 entries")
    require_durable_structured_data(items, subject="durable governed run structured data")
    _bounded_json(name, items, max_bytes=8192)


def _require_maintenance_summary(value: Any) -> None:
    if not isinstance(value, Mapping):
        raise ValueError("payload.maintenance_summary must be an object")
    _require_payload_fields(value, required={"cost", "impact"})
    _bounded_text("payload.maintenance_summary.cost", value["cost"], max_bytes=2048)
    _bounded_text("payload.maintenance_summary.impact", value["impact"], max_bytes=2048)


def _require_same_run_logical_event(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    event_id: str,
    logical_event_type: str,
) -> None:
    row = conn.execute(
        """
        SELECT 1
        FROM run_events event
        LEFT JOIN run_event_v2_details detail
          ON detail.run_id = event.run_id AND detail.event_id = event.event_id
        WHERE event.run_id = ?
          AND event.event_id = ?
          AND COALESCE(detail.logical_event_type, event.event_type) = ?
        """,
        (run_id, event_id, logical_event_type),
    ).fetchone()
    if row is None:
        raise ValueError(f"payload reference must identify a same-run {logical_event_type} event")


def _governed_criterion_ids(conn: sqlite3.Connection, *, run_id: str) -> set[str]:
    row = conn.execute(
        "SELECT acceptance_criteria_json FROM agent_runs WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    if row is None:
        raise RuntimeError("governed run is missing")
    try:
        criteria = json.loads(str(row["acceptance_criteria_json"]))
    except json.JSONDecodeError as exc:
        raise RuntimeError("governed acceptance criteria are malformed") from exc
    if not isinstance(criteria, list):
        raise RuntimeError("governed acceptance criteria are malformed")
    criterion_ids: set[str] = set()
    for criterion in criteria:
        if not isinstance(criterion, Mapping):
            raise RuntimeError("governed acceptance criteria are malformed")
        criterion_id = criterion.get("criterion_id")
        if not isinstance(criterion_id, str) or not criterion_id or criterion_id in criterion_ids:
            raise RuntimeError("governed acceptance criteria are malformed")
        criterion_ids.add(criterion_id)
    if not criterion_ids:
        raise RuntimeError("governed acceptance criteria are missing")
    return criterion_ids


def _require_governed_verification_result_coverage(conn: sqlite3.Connection, *, run_id: str) -> None:
    criterion_ids = _governed_criterion_ids(conn, run_id=run_id)
    rows = conn.execute(
        """
        SELECT event.payload_json
        FROM run_events event
        JOIN run_event_v2_details detail
          ON detail.run_id = event.run_id AND detail.event_id = event.event_id
        WHERE event.run_id = ? AND detail.logical_event_type = 'verification_result'
        ORDER BY event.sequence DESC
        """,
        (run_id,),
    ).fetchall()
    latest_result_ids: set[str] = set()
    for row in rows:
        try:
            payload = json.loads(str(row["payload_json"]))
        except json.JSONDecodeError as exc:
            raise RuntimeError("governed verification result is malformed") from exc
        if not isinstance(payload, Mapping) or not isinstance(payload.get("criterion_id"), str):
            raise RuntimeError("governed verification result is malformed")
        criterion_id = payload["criterion_id"]
        if criterion_id in criterion_ids:
            latest_result_ids.add(criterion_id)
    missing = sorted(criterion_ids - latest_result_ids)
    if missing:
        raise ValueError(f"governed completion requires verification_result coverage for: {missing}")


def _unresolved_information_gap_ids(conn: sqlite3.Connection, *, run_id: str) -> list[str]:
    rows = conn.execute(
        """
        SELECT event.payload_json
        FROM run_events event
        JOIN run_event_v2_details detail
          ON detail.run_id = event.run_id AND detail.event_id = event.event_id
        WHERE event.run_id = ? AND detail.logical_event_type = 'information_gap'
        ORDER BY event.sequence
        """,
        (run_id,),
    ).fetchall()
    gap_ids: list[str] = []
    for row in rows:
        try:
            payload = json.loads(str(row["payload_json"]))
        except json.JSONDecodeError as exc:
            raise RuntimeError("governed information gap is malformed") from exc
        gap_id = payload.get("gap_id") if isinstance(payload, Mapping) else None
        if not isinstance(gap_id, str) or not gap_id.strip():
            raise RuntimeError("governed information gap is malformed")
        if gap_id not in gap_ids:
            gap_ids.append(gap_id)
    return gap_ids


def _require_prior_approved_preflight(conn: sqlite3.Connection, *, run_id: str) -> None:
    rows = conn.execute(
        """
        SELECT event.payload_json
        FROM run_events event
        JOIN run_event_v2_details detail
          ON detail.run_id = event.run_id AND detail.event_id = event.event_id
        WHERE event.run_id = ? AND detail.logical_event_type = 'preflight_review'
        ORDER BY event.sequence
        """,
        (run_id,),
    ).fetchall()
    for row in rows:
        payload = json.loads(str(row["payload_json"]))
        if payload.get("approved") is True:
            return
    raise ValueError("governed-v2 execution events require a prior approved preflight_review")


def _outcome_payload(row: sqlite3.Row) -> dict[str, Any]:
    payload = {
        "outcome_id": str(row["outcome_id"]),
        "run_id": str(row["run_id"]),
        "outcome": str(row["outcome_type"]),
        "evaluator_type": str(row["evaluator_type"]),
        "evaluator_digest": row["evaluator_digest"],
        "evaluator_version": row["evaluator_version"],
        "verification_profile": str(row["verification_profile"]),
        "verification_receipt_id": row["verification_receipt_id"],
        "evidence": json.loads(str(row["evidence_json"])),
        "metrics": json.loads(str(row["metrics_json"])),
        "supersedes_outcome_id": row["supersedes_outcome_id"],
        "regression_of_run_id": row["regression_of_run_id"],
        "termination_reason": row["termination_reason"],
        "created_at": str(row["created_at"]),
    }
    payload["authority_class"] = outcome_authority_class(row)
    payload["strong_verified"] = is_strong_verified_outcome(row)
    return payload


def _normalize_memory_attribution(
    event_type: str,
    value: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if event_type not in MEMORY_ATTRIBUTION_EVENT_TYPES:
        if value is not None:
            raise ValueError("memory_attribution is only valid for memory lifecycle events")
        return None
    if not isinstance(value, Mapping):
        raise ValueError(f"memory_attribution is required for {event_type}")
    _reject_caller_managed_attribution_fields(value)
    if event_type == "memory_recalled":
        _require_exact_attribution_fields(value, {"namespace", "recall_receipt", "items"})
        return {
            "mode": "receipt",
            "namespace": _required_attribution_text("memory_attribution.namespace", value["namespace"], max_chars=512),
            "recall_receipt": _required_attribution_text(
                "memory_attribution.recall_receipt", value["recall_receipt"], max_bytes=65536
            ),
            "items": _normalize_receipt_attribution_items(
                value["items"],
                allow_feedback=False,
                allow_empty=True,
            ),
        }
    if "source_recall_event_id" in value:
        _require_exact_attribution_fields(value, {"source_recall_event_id", "items"})
        return {
            "mode": "source",
            "event_type": event_type,
            "source_recall_event_id": _required_attribution_text(
                "memory_attribution.source_recall_event_id", value["source_recall_event_id"], max_chars=64
            ),
            "items": _normalize_receipt_attribution_items(
                value["items"],
                allow_feedback=True,
                allow_empty=False,
            ),
        }
    _require_exact_attribution_fields(value, {"items"})
    return {
        "mode": "manual",
        "event_type": event_type,
        "items": _normalize_manual_attribution_items(value["items"]),
    }


def _require_exact_attribution_fields(value: Mapping[str, Any], allowed: set[str]) -> None:
    unexpected = sorted(str(key) for key in value if key not in allowed)
    missing = sorted(field for field in allowed if field not in value)
    if missing:
        raise ValueError(f"memory_attribution is missing required fields: {missing}")
    if unexpected:
        raise ValueError(f"memory_attribution has unsupported fields: {unexpected}")


def _reject_caller_managed_attribution_fields(value: Any) -> None:
    if isinstance(value, Mapping):
        for raw_key, nested in value.items():
            normalized = str(raw_key).strip().lower().replace("-", "_")
            if normalized in CALLER_MANAGED_ATTRIBUTION_FIELDS:
                raise ValueError(f"memory_attribution field is server-managed: {raw_key}")
            _reject_caller_managed_attribution_fields(nested)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            _reject_caller_managed_attribution_fields(item)


def _normalize_receipt_attribution_items(
    value: Any,
    *,
    allow_feedback: bool,
    allow_empty: bool,
) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > 32 or (not allow_empty and not value):
        lower_bound = 0 if allow_empty else 1
        raise ValueError(f"memory_attribution.items must contain between {lower_bound} and 32 items")
    allowed = {"memory_id", "result_rank"}
    if allow_feedback:
        allowed.add("feedback_id")
    items: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for item in value:
        if not isinstance(item, Mapping):
            raise ValueError("memory_attribution.items entries must be objects")
        _require_exact_attribution_fields(item, allowed if "feedback_id" in item else allowed - {"feedback_id"})
        memory_id = _required_attribution_text("memory_attribution.items.memory_id", item["memory_id"], max_chars=256)
        result_rank = _positive_attribution_int("memory_attribution.items.result_rank", item["result_rank"])
        identity = (memory_id, result_rank)
        if identity in seen:
            raise ValueError("memory_attribution.items must not contain duplicate memory_id/result_rank pairs")
        seen.add(identity)
        normalized: dict[str, Any] = {"memory_id": memory_id, "result_rank": result_rank}
        if "feedback_id" in item:
            if not allow_feedback:
                raise ValueError("memory_attribution feedback_id is only valid for source-linked use")
            normalized["feedback_id"] = _positive_attribution_int(
                "memory_attribution.items.feedback_id", item["feedback_id"]
            )
        items.append(normalized)
    return items


def _normalize_manual_attribution_items(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list) or not 1 <= len(value) <= 32:
        raise ValueError("memory_attribution.items must contain between 1 and 32 items")
    items: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in value:
        if not isinstance(item, Mapping):
            raise ValueError("memory_attribution.items entries must be objects")
        _require_exact_attribution_fields(item, {"memory_id", "exact_content_version"})
        memory_id = _required_attribution_text("memory_attribution.items.memory_id", item["memory_id"], max_chars=256)
        exact_content_version = _required_attribution_digest(
            "memory_attribution.items.exact_content_version", item["exact_content_version"]
        )
        identity = (memory_id, exact_content_version)
        if identity in seen:
            raise ValueError("memory_attribution.items must not contain duplicate memory versions")
        seen.add(identity)
        items.append({"memory_id": memory_id, "exact_content_version": exact_content_version})
    return items


def _required_attribution_text(
    name: str, value: Any, *, max_chars: int | None = None, max_bytes: int | None = None
) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    return _bounded_text(
        name,
        value,
        max_chars=max_chars,
        max_bytes=max_bytes,
        allow_receipt_token=name == "memory_attribution.recall_receipt",
    )


def _required_artifact_uri(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("artifact.uri must be a string")
    if not value:
        raise ValueError("artifact.uri must not be empty")
    if len(value.encode("utf-8")) > 2048:
        raise ValueError("artifact.uri must be at most 2048 bytes")
    match = ARTIFACT_URI_RE.fullmatch(value)
    if match is None:
        raise ValueError(
            "artifact.uri must be a reference-like URI with a scheme and no whitespace or control characters"
        )
    scheme = match["scheme"].casefold()
    if scheme in _FORBIDDEN_ARTIFACT_URI_SCHEMES:
        raise ValueError(f"artifact.uri must not use the {scheme} scheme")
    parsed = urlsplit(value)
    if parsed.netloc and "@" in parsed.netloc:
        raise ValueError("artifact.uri must not embed credentials")
    return value


def _required_attribution_digest(name: str, value: Any) -> str:
    cleaned = _required_attribution_text(name, value, max_chars=64)
    if not HEX_DIGEST_RE.fullmatch(cleaned):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return cleaned


def _positive_attribution_int(name: str, value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _memory_attribution_request_identity(attribution: dict[str, Any] | None) -> dict[str, Any] | None:
    if attribution is None:
        return None
    mode = str(attribution["mode"])
    if mode == "receipt":
        return {
            "mode": mode,
            "namespace": attribution["namespace"],
            "receipt_hash": recall_receipt_hash(str(attribution["recall_receipt"])),
            "items": attribution["items"],
        }
    if mode == "source":
        return {
            "mode": mode,
            "event_type": attribution["event_type"],
            "source_recall_event_id": attribution["source_recall_event_id"],
            "items": attribution["items"],
        }
    return {"mode": mode, "event_type": attribution["event_type"], "items": attribution["items"]}


def _validate_memory_lifecycle_payload(
    *,
    event_type: str,
    payload: Mapping[str, Any],
    attribution: dict[str, Any] | None,
    links: Sequence[Mapping[str, Any]],
) -> None:
    if event_type not in MEMORY_ATTRIBUTION_EVENT_TYPES:
        return
    if attribution is None:
        raise RuntimeError("memory lifecycle attribution was not normalized")
    mode = str(attribution["mode"])
    result_ids = [str(link["memory_id"]) for link in links]
    result_ranks = [int(link["exposure_rank"]) for link in links if link["exposure_rank"] is not None]
    receipt_hashes = {str(link["receipt_hash"]) for link in links if link["receipt_hash"] is not None}
    if len(receipt_hashes) > 1:
        raise RuntimeError("memory lifecycle links have inconsistent receipt hashes")
    receipt_hash = next(iter(receipt_hashes), None)
    if receipt_hash is None and mode == "receipt":
        receipt_hash = recall_receipt_hash(str(attribution["recall_receipt"]))
    source_event_id = attribution.get("source_recall_event_id")

    for raw_key, value in _iter_payload_fields(payload):
        key = raw_key.strip().lower().replace("-", "_")
        if key in LIFECYCLE_PAYLOAD_REJECTED_FIELDS:
            raise ValueError(f"memory lifecycle payload field is server-managed: {raw_key}")
        if key not in LIFECYCLE_PAYLOAD_CONSISTENCY_FIELDS:
            continue
        if mode == "manual":
            raise ValueError(f"manual memory_attribution must not claim {raw_key} in payload")
        if key == "result_ids":
            if value != result_ids:
                raise ValueError("memory lifecycle payload result_ids do not match resolved memory links")
        elif key == "result_ranks":
            if value != result_ranks:
                raise ValueError("memory lifecycle payload result_ranks do not match resolved memory links")
        elif key == "receipt_hash":
            if receipt_hash is None or value != receipt_hash:
                raise ValueError("memory lifecycle payload receipt_hash does not match resolved memory links")
        elif key in {"recall_event_id", "source_recall_event_id"}:
            if mode != "source" or value != source_event_id:
                raise ValueError("memory lifecycle payload recall event does not match source memory attribution")


def _iter_payload_fields(value: Any) -> Sequence[tuple[str, Any]]:
    fields: list[tuple[str, Any]] = []
    if isinstance(value, Mapping):
        for raw_key, nested in value.items():
            fields.append((str(raw_key), nested))
            fields.extend(_iter_payload_fields(nested))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            fields.extend(_iter_payload_fields(item))
    return fields


def _resolve_memory_attribution(
    conn: sqlite3.Connection,
    *,
    store: Any,
    attribution: dict[str, Any] | None,
    run_id: str,
) -> list[dict[str, Any]]:
    if attribution is None:
        return []
    mode = str(attribution["mode"])
    if mode == "receipt":
        validated = validate_recall_receipt_exposures(
            store,
            conn=conn,
            recall_receipt=str(attribution["recall_receipt"]),
            namespace=str(attribution["namespace"]),
            selections=[(str(item["memory_id"]), int(item["result_rank"])) for item in attribution["items"]],
        )
        if not attribution["items"] and (
            validated["payload"].get("results") != [] or validated["payload"].get("exposure_set") != []
        ):
            raise ValueError("empty memory_attribution.items requires a zero-result recall receipt")
        return [
            {
                "memory_id": item["memory_id"],
                "exact_content_version": item["exact_content_version"],
                "receipt_hash": validated["receipt_hash"],
                "exposure_rank": item["result_rank"],
                "feedback_id": None,
                "relation": "recalled",
                "review_required": 0,
            }
            for item in validated["items"]
        ]
    if mode == "source":
        return _resolve_source_memory_attribution(conn, attribution=attribution, run_id=run_id)
    return _resolve_manual_memory_attribution(conn, attribution=attribution)


def _resolve_source_memory_attribution(
    conn: sqlite3.Connection,
    *,
    attribution: dict[str, Any],
    run_id: str,
) -> list[dict[str, Any]]:
    source_event_id = str(attribution["source_recall_event_id"])
    source_event = conn.execute(
        "SELECT event_type FROM run_events WHERE run_id = ? AND event_id = ?",
        (run_id, source_event_id),
    ).fetchone()
    if source_event is None or str(source_event["event_type"]) != "memory_recalled":
        raise ValueError("source_recall_event_id must reference a prior memory_recalled event in this run")
    source_links = {
        (str(row["memory_id"]), int(row["exposure_rank"])): row
        for row in conn.execute(
            """
            SELECT memory_id, exact_content_version, receipt_hash, exposure_rank
            FROM run_memory_links
            WHERE run_id = ? AND event_id = ? AND relation = 'recalled'
            """,
            (run_id, source_event_id),
        ).fetchall()
    }
    if not source_links:
        raise ValueError("source_recall_event_id has no recalled memory links")
    relation = "applied" if str(attribution["event_type"]) == "memory_applied" else "rejected"
    resolved: list[dict[str, Any]] = []
    for selected in attribution["items"]:
        key = (str(selected["memory_id"]), int(selected["result_rank"]))
        source = source_links.get(key)
        if source is None:
            raise ValueError("memory_attribution item was not exposed by source_recall_event_id")
        feedback_id = selected.get("feedback_id")
        if feedback_id is not None:
            _validate_source_feedback_link(
                conn,
                feedback_id=int(feedback_id),
                source=source,
                relation=relation,
            )
        resolved.append(
            {
                "memory_id": str(source["memory_id"]),
                "exact_content_version": str(source["exact_content_version"]),
                "receipt_hash": str(source["receipt_hash"]),
                "exposure_rank": int(source["exposure_rank"]),
                "feedback_id": feedback_id,
                "relation": relation,
                "review_required": 0,
            }
        )
    return resolved


def _validate_source_feedback_link(
    conn: sqlite3.Connection,
    *,
    feedback_id: int,
    source: sqlite3.Row,
    relation: str,
) -> None:
    row = conn.execute(
        """
        SELECT feedback.*, memory.namespace AS memory_namespace, memory.exact_content_hash AS current_exact_content_version
        FROM retrieval_feedback feedback
        JOIN memories memory ON memory.id = feedback.memory_id AND memory.kind = 'memory'
        WHERE feedback.feedback_id = ?
          AND NOT EXISTS (
              SELECT 1 FROM retrieval_feedback child WHERE child.supersedes_feedback_id = feedback.feedback_id
          )
        LIMIT 1
        """,
        (feedback_id,),
    ).fetchone()
    if row is None or str(row["feedback_type"]) == "retraction":
        raise ValueError("feedback_id must be the current effective feedback head")
    if (
        str(row["memory_id"]) != str(source["memory_id"])
        or int(row["result_rank"]) != int(source["exposure_rank"])
        or not hmac.compare_digest(str(row["receipt_hash"]), str(source["receipt_hash"]))
        or not hmac.compare_digest(str(row["current_exact_content_version"]), str(source["exact_content_version"]))
    ):
        raise ValueError("feedback_id does not match the recalled memory exposure")
    try:
        feedback_payload = json.loads(str(row["feedback_json"]))
    except json.JSONDecodeError as exc:
        raise ValueError("feedback_id has malformed exact content identity") from exc
    feedback_exact_content_version = (
        feedback_payload.get("exact_content_hash") if isinstance(feedback_payload, Mapping) else None
    )
    if not isinstance(feedback_exact_content_version, str) or not hmac.compare_digest(
        feedback_exact_content_version, str(source["exact_content_version"])
    ):
        raise ValueError("feedback_id does not match the recalled exact content version")
    if str(row["namespace"]) != str(row["memory_namespace"]):
        raise ValueError("feedback_id does not match the recalled memory namespace")
    allowed_outcomes = {
        "applied": {"helpful"},
        "rejected": {"misleading", "outdated", "not_applicable"},
    }
    feedback_outcome = str(row["outcome"])
    reviewed_neutral_supported = feedback_outcome == "not_used" and _has_existing_helpful_support(
        conn,
        memory_id=str(source["memory_id"]),
        exact_content_version=str(source["exact_content_version"]),
    )
    if feedback_outcome not in allowed_outcomes[relation] and not (
        relation == "applied" and reviewed_neutral_supported
    ):
        raise ValueError(f"feedback outcome cannot support source-linked memory_{relation}")


def _has_existing_helpful_support(
    conn: sqlite3.Connection,
    *,
    memory_id: str,
    exact_content_version: str,
) -> bool:
    rows = conn.execute(
        """
        SELECT outcome.*
        FROM run_memory_links link
        JOIN retrieval_feedback feedback ON feedback.feedback_id = link.feedback_id
        JOIN run_outcomes outcome ON outcome.run_id = link.run_id
        LEFT JOIN retrieval_feedback feedback_child ON feedback_child.supersedes_feedback_id = feedback.feedback_id
        LEFT JOIN run_outcomes outcome_child ON outcome_child.supersedes_outcome_id = outcome.outcome_id
        WHERE link.memory_id = ?
          AND link.exact_content_version = ?
          AND link.relation = 'applied'
          AND link.review_required = 0
          AND feedback_child.feedback_id IS NULL
          AND feedback.feedback_type != 'retraction'
          AND feedback.outcome = 'helpful'
          AND outcome_child.outcome_id IS NULL
        """,
        (memory_id, exact_content_version),
    ).fetchall()
    return any(is_strong_verified_outcome(row) for row in rows)


def _resolve_manual_memory_attribution(
    conn: sqlite3.Connection, *, attribution: dict[str, Any]
) -> list[dict[str, Any]]:
    relation = "applied" if str(attribution["event_type"]) == "memory_applied" else "rejected"
    resolved: list[dict[str, Any]] = []
    for selected in attribution["items"]:
        row = conn.execute(
            """
            SELECT id, exact_content_hash
            FROM memories
            WHERE id = ? AND exact_content_hash = ? AND kind = 'memory'
            LIMIT 1
            """,
            (selected["memory_id"], selected["exact_content_version"]),
        ).fetchone()
        if row is None:
            raise ValueError("manual memory_attribution must match an existing exact memory version")
        resolved.append(
            {
                "memory_id": str(row["id"]),
                "exact_content_version": str(row["exact_content_hash"]),
                "receipt_hash": None,
                "exposure_rank": None,
                "feedback_id": None,
                "relation": relation,
                "review_required": 1,
            }
        )
    return resolved


def _insert_run_memory_links(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    work_item_id: str,
    event_id: str,
    event_idempotency_digest: str,
    event_request_digest: str,
    links: Sequence[Mapping[str, Any]],
    created_at: str,
) -> None:
    for index, link in enumerate(links, start=1):
        link_identity = {
            "event_request_digest": event_request_digest,
            "memory_id": link["memory_id"],
            "exact_content_version": link["exact_content_version"],
            "receipt_hash": link["receipt_hash"],
            "exposure_rank": link["exposure_rank"],
            "feedback_id": link["feedback_id"],
            "relation": link["relation"],
            "review_required": link["review_required"],
        }
        conn.execute(
            """
            INSERT INTO run_memory_links (
                link_id, run_id, work_item_id, event_id, outcome_id, memory_id,
                exact_content_version, receipt_hash, exposure_rank, feedback_id,
                relation, review_required, idempotency_key_digest, request_digest, created_at
            ) VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _new_id("link_"),
                run_id,
                work_item_id,
                event_id,
                link["memory_id"],
                link["exact_content_version"],
                link["receipt_hash"],
                link["exposure_rank"],
                link["feedback_id"],
                link["relation"],
                link["review_required"],
                _idempotency_digest(f"{event_idempotency_digest}:memory-link:{index}"),
                _request_digest(link_identity),
                created_at,
            ),
        )


def _normalize_memory_scopes(memory_scopes: Sequence[str] | None) -> list[str]:
    if memory_scopes is None:
        return []
    if isinstance(memory_scopes, str):
        raise ValueError("memory_scopes must be a list of strings")
    if len(memory_scopes) > 32:
        raise ValueError("memory_scopes must contain at most 32 entries")
    cleaned: list[str] = []
    for scope in memory_scopes:
        value = _bounded_text("memory_scope", str(scope), max_chars=128)
        if value not in cleaned:
            cleaned.append(value)
    return cleaned


def _normalize_evidence_profile(value: str) -> str:
    cleaned = _bounded_text("evidence_profile", value, max_chars=32)
    if cleaned not in RUN_EVIDENCE_PROFILES:
        raise ValueError(f"unsupported evidence_profile: {cleaned}")
    return cleaned


def _normalize_risk_level(value: str) -> str:
    cleaned = _bounded_text("risk_level", value, max_chars=32)
    if cleaned not in RUN_RISK_LEVELS:
        raise ValueError(f"unsupported risk_level: {cleaned}")
    return cleaned


def _normalize_acceptance_criteria(value: Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise ValueError("acceptance_criteria must be an array")
    if len(value) > 64:
        raise ValueError("acceptance_criteria must contain at most 64 entries")
    normalized: list[dict[str, Any]] = []
    identifiers: set[str] = set()
    for entry in value:
        if not isinstance(entry, Mapping):
            raise ValueError("acceptance_criteria entries must be objects")
        keys = set(entry)
        if "id" in keys and "criterion_id" in keys:
            raise ValueError("acceptance_criteria entries must use either id or criterion_id, not both")
        allowed = {"id", "criterion_id", "description"}
        unexpected = sorted(str(key) for key in keys - allowed)
        if unexpected:
            raise ValueError(f"acceptance_criteria entries have unsupported fields: {unexpected}")
        raw_identifier = entry.get("criterion_id", entry.get("id"))
        identifier = _bounded_text("acceptance_criteria.criterion_id", raw_identifier, max_chars=128)
        if identifier in identifiers:
            raise ValueError("acceptance_criteria criterion_id values must be unique")
        identifiers.add(identifier)
        normalized_entry: dict[str, Any] = {"criterion_id": identifier}
        if "description" in entry:
            normalized_entry["description"] = _bounded_text(
                "acceptance_criteria.description", entry["description"], max_bytes=4096
            )
        normalized.append(normalized_entry)
    require_durable_structured_data(normalized, subject="durable run structured data")
    _bounded_json("acceptance_criteria", normalized, max_bytes=16384)
    return normalized


def _normalize_run_text_list(name: str, value: Sequence[str] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise ValueError(f"{name} must be an array")
    if len(value) > 64:
        raise ValueError(f"{name} must contain at most 64 entries")
    normalized: list[str] = []
    for raw_item in value:
        item = _bounded_text(name, raw_item, max_bytes=2048)
        if item not in normalized:
            normalized.append(item)
    _bounded_json(name, normalized, max_bytes=8192)
    return normalized


def _json_object(name: str, value: Mapping[str, Any] | None, *, max_bytes: int) -> str:
    if value is None:
        value = {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    require_durable_structured_data(value, subject="durable run structured data")
    return _bounded_json(name, dict(value), max_bytes=max_bytes)


def _json_array(name: str, value: Sequence[Any] | None, *, max_bytes: int) -> str:
    if value is None:
        value = []
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise ValueError(f"{name} must be an array")
    items = list(value)
    require_durable_structured_data(items, subject="durable run structured data")
    return _bounded_json(name, items, max_bytes=max_bytes)


def _bounded_json(name: str, value: Any, *, max_bytes: int) -> str:
    try:
        encoded = _canonical_json(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must contain JSON-compatible finite values") from exc
    if len(encoded.encode("utf-8")) > max_bytes:
        raise ValueError(f"{name} must be at most {max_bytes} bytes")
    return encoded


def _idempotency_digest(value: str) -> str:
    cleaned = _bounded_text("idempotency_key", value, max_chars=512, allow_receipt_token=True)
    return hashlib.sha256(cleaned.encode("utf-8")).hexdigest()


def _request_digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True)


def _optional_digest(name: str, value: str | None) -> str | None:
    cleaned = _optional_bounded_text(name, value, max_chars=64)
    if cleaned is None:
        return None
    if not HEX_DIGEST_RE.fullmatch(cleaned):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return cleaned


def _optional_nonnegative_int(name: str, value: int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _optional_positive_int(name: str, value: int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _optional_work_item_status(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    if cleaned not in WORK_ITEM_STATUSES:
        raise ValueError(f"unsupported expected work-item status: {cleaned or '<empty>'}")
    return cleaned


def _bounded_text(
    name: str,
    value: object,
    *,
    max_chars: int | None = None,
    max_bytes: int | None = None,
    allow_receipt_token: bool = False,
) -> str:
    cleaned = str(value).strip()
    if not cleaned:
        raise ValueError(f"{name} must not be empty")
    if max_chars is not None and len(cleaned) > max_chars:
        raise ValueError(f"{name} must be at most {max_chars} characters")
    if max_bytes is not None and len(cleaned.encode("utf-8")) > max_bytes:
        raise ValueError(f"{name} must be at most {max_bytes} bytes")
    if not allow_receipt_token:
        require_durable_text(cleaned, subject="durable run text")
    return cleaned


def _optional_bounded_text(
    name: str,
    value: object | None,
    *,
    max_chars: int | None = None,
    max_bytes: int | None = None,
    allow_receipt_token: bool = False,
) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    if not cleaned:
        return None
    return _bounded_text(
        name,
        cleaned,
        max_chars=max_chars,
        max_bytes=max_bytes,
        allow_receipt_token=allow_receipt_token,
    )


def _new_id(prefix: str) -> str:
    return f"{prefix}{secrets.token_hex(16)}"
