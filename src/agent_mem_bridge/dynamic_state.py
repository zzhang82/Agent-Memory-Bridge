"""Versioned, exact-key release state backed by AMB's SQLite authority store.

This module stays separate from semantic memory retrieval. It owns one narrow
release-state resource type, append-only accepted mutations, terminal request
outcomes for lifecycle idempotency, and a rebuildable current-head projection.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from collections.abc import Mapping
from typing import Any

from .durable_data_policy import require_durable_text
from .provenance import normalize_provenance_mapping
from .schema import database_epoch as read_database_epoch

STATE_TYPE_RELEASE_STATE = "release-state"
STATE_COMMAND_STATUS_TRANSITION = "status_transition"
STATE_COMMAND_OWNER_ASSIGNMENT = "owner_assignment"
STATE_COMMAND_RESTORE = "restore"
_STATE_COMMANDS = frozenset({STATE_COMMAND_STATUS_TRANSITION, STATE_COMMAND_OWNER_ASSIGNMENT, STATE_COMMAND_RESTORE})
_RELEASE_STATUSES = frozenset({"draft", "review", "published", "blocked", "retired"})
_ALLOWED_STATUS_TRANSITIONS = {
    "draft": frozenset({"review", "blocked"}),
    "review": frozenset({"draft", "published", "blocked"}),
    "published": frozenset({"retired"}),
    "blocked": frozenset({"draft", "review"}),
    "retired": frozenset(),
}
_STATE_HEAD_FIELDS = (
    "workspace_key",
    "state_key",
    "current_version",
    "value_json",
    "value_hash",
    "last_mutation_id",
    "updated_at",
)


def _begin_state_write_transaction(conn: sqlite3.Connection) -> None:
    """Use the run-ledger SQLite retry pattern for short state writes."""

    for attempt in range(4):
        try:
            conn.execute("BEGIN IMMEDIATE")
            return
        except sqlite3.OperationalError as error:
            message = str(error).casefold()
            if attempt == 3 or ("locked" not in message and "busy" not in message):
                raise
            time.sleep(0.05 * (2**attempt))


class DynamicStateStore:
    """Internal exact-key release-state boundary over an existing ``MemoryStore``."""

    def __init__(self, store: Any) -> None:
        self._store = store

    def read(self, *, workspace_key: str, state_key: str) -> dict[str, Any]:
        """Return one exact state snapshot; an absent key is version zero with its epoch."""

        cleaned_workspace = _required_text("workspace_key", workspace_key, max_chars=512)
        cleaned_key = _required_text("state_key", state_key, max_chars=512)
        with self._store._connect() as conn:
            conn.execute("BEGIN")
            try:
                epoch = read_database_epoch(conn)
                row = conn.execute(
                    """
                    SELECT resource.workspace_key, resource.state_key, resource.state_type,
                           head.current_version, head.value_json, head.value_hash,
                           head.last_mutation_id, head.updated_at
                    FROM state_resources resource
                    LEFT JOIN state_heads head
                      ON head.workspace_key = resource.workspace_key
                     AND head.state_key = resource.state_key
                    WHERE resource.workspace_key = ? AND resource.state_key = ?
                    """,
                    (cleaned_workspace, cleaned_key),
                ).fetchone()
                if row is None:
                    response = {
                        "workspace_key": cleaned_workspace,
                        "state_key": cleaned_key,
                        "state_type": STATE_TYPE_RELEASE_STATE,
                        "version": 0,
                        "value": None,
                        "value_hash": None,
                        "last_mutation_id": None,
                        "updated_at": None,
                        "database_epoch": epoch,
                        "exists": False,
                    }
                else:
                    if row["current_version"] is None:
                        raise RuntimeError("state resource is missing its current-state projection")
                    response = _head_payload(row, database_epoch=epoch)
                conn.commit()
                return response
            except BaseException:
                conn.rollback()
                raise

    def transition_status(
        self,
        *,
        workspace_key: str,
        state_key: str,
        to_status: str,
        expected_version: int,
        expected_database_epoch: str,
        idempotency_key: str,
        provenance: Mapping[str, object | None] | None = None,
    ) -> dict[str, Any]:
        """Apply one deterministic release-status transition.

        A new resource may only be created at version zero by transitioning to
        ``draft``. Existing transitions are constrained by
        ``_ALLOWED_STATUS_TRANSITIONS`` and preserve an assigned owner.
        """

        return self._execute_command(
            workspace_key=workspace_key,
            state_key=state_key,
            command_type=STATE_COMMAND_STATUS_TRANSITION,
            command_payload={"to_status": _required_text("to_status", to_status, max_chars=32)},
            expected_version=expected_version,
            expected_database_epoch=expected_database_epoch,
            idempotency_key=idempotency_key,
            provenance=provenance,
        )

    def assign_owner(
        self,
        *,
        workspace_key: str,
        state_key: str,
        owner: str,
        expected_version: int,
        expected_database_epoch: str,
        idempotency_key: str,
        provenance: Mapping[str, object | None] | None = None,
    ) -> dict[str, Any]:
        """Assign a non-empty owner to an existing release state without changing status."""

        return self._execute_command(
            workspace_key=workspace_key,
            state_key=state_key,
            command_type=STATE_COMMAND_OWNER_ASSIGNMENT,
            command_payload={"owner": _required_text("owner", owner, max_chars=128)},
            expected_version=expected_version,
            expected_database_epoch=expected_database_epoch,
            idempotency_key=idempotency_key,
            provenance=provenance,
        )

    def restore(
        self,
        *,
        workspace_key: str,
        state_key: str,
        mutation_id: str,
        expected_version: int,
        expected_database_epoch: str,
        idempotency_key: str,
        provenance: Mapping[str, object | None] | None = None,
    ) -> dict[str, Any]:
        """Restore a prior immutable snapshot by appending a new release-state version."""

        return self._execute_command(
            workspace_key=workspace_key,
            state_key=state_key,
            command_type=STATE_COMMAND_RESTORE,
            command_payload={"mutation_id": _required_text("mutation_id", mutation_id, max_chars=96)},
            expected_version=expected_version,
            expected_database_epoch=expected_database_epoch,
            idempotency_key=idempotency_key,
            provenance=provenance,
        )

    def _execute_command(
        self,
        *,
        workspace_key: str,
        state_key: str,
        command_type: str,
        command_payload: Mapping[str, str],
        expected_version: int,
        expected_database_epoch: str,
        idempotency_key: str,
        provenance: Mapping[str, object | None] | None,
    ) -> dict[str, Any]:
        cleaned_workspace = _required_text("workspace_key", workspace_key, max_chars=512)
        cleaned_key = _required_text("state_key", state_key, max_chars=512)
        cleaned_command = str(command_type).strip()
        if cleaned_command not in _STATE_COMMANDS:
            raise ValueError(f"unsupported state command: {cleaned_command or '<empty>'}")
        cleaned_payload = _command_payload(cleaned_command, command_payload)
        cleaned_expected_version = _expected_version(expected_version)
        cleaned_expected_epoch = _required_text("expected_database_epoch", expected_database_epoch, max_chars=128)
        idempotency_digest = _idempotency_digest(idempotency_key)
        cleaned_provenance = normalize_provenance_mapping(provenance) or {}
        request_digest = _request_digest(
            {
                "workspace_key": cleaned_workspace,
                "state_key": cleaned_key,
                "state_type": STATE_TYPE_RELEASE_STATE,
                "command": cleaned_command,
                "command_payload": cleaned_payload,
                "expected_version": cleaned_expected_version,
                "expected_database_epoch": cleaned_expected_epoch,
                "provenance": cleaned_provenance,
            }
        )

        with self._store._connect() as conn:
            _begin_state_write_transaction(conn)
            try:
                existing = _fetch_request_outcome(
                    conn,
                    workspace_key=cleaned_workspace,
                    state_key=cleaned_key,
                    idempotency_key_digest=idempotency_digest,
                )
                if existing is not None:
                    _require_matching_request(existing, request_digest)
                    conn.commit()
                    return _replay_request_outcome(existing)

                actual_epoch = read_database_epoch(conn)
                created_at = self._store._utc_now()
                if cleaned_expected_epoch != actual_epoch:
                    message = f"DATABASE_EPOCH_CONFLICT: expected {cleaned_expected_epoch}, actual {actual_epoch}"
                    _insert_request_outcome(
                        conn,
                        workspace_key=cleaned_workspace,
                        state_key=cleaned_key,
                        idempotency_key_digest=idempotency_digest,
                        request_digest=request_digest,
                        command_type=cleaned_command,
                        outcome_type="conflict",
                        response={
                            "error_code": "DATABASE_EPOCH_CONFLICT",
                            "message": message,
                            "error_type": "ValueError",
                            "expected_database_epoch": cleaned_expected_epoch,
                            "actual_database_epoch": actual_epoch,
                        },
                        created_at=created_at,
                    )
                    conn.commit()
                    raise ValueError(message)

                projection_health = inspect_state_head(
                    conn,
                    workspace_key=cleaned_workspace,
                    state_key=cleaned_key,
                )
                if not bool(projection_health["ok"]):
                    message = _projection_health_error(projection_health)
                    _insert_request_outcome(
                        conn,
                        workspace_key=cleaned_workspace,
                        state_key=cleaned_key,
                        idempotency_key_digest=idempotency_digest,
                        request_digest=request_digest,
                        command_type=cleaned_command,
                        outcome_type="rejected",
                        response={
                            "error_code": "STATE_PROJECTION_DEGRADED",
                            "message": message,
                            "error_type": "RuntimeError",
                        },
                        created_at=created_at,
                    )
                    conn.commit()
                    raise RuntimeError(message)

                head = conn.execute(
                    """
                    SELECT current_version, value_json
                    FROM state_heads
                    WHERE workspace_key = ? AND state_key = ?
                    """,
                    (cleaned_workspace, cleaned_key),
                ).fetchone()
                actual_version = int(head["current_version"]) if head is not None else 0
                if cleaned_expected_version != actual_version:
                    message = f"STATE_VERSION_CONFLICT: expected {cleaned_expected_version}, actual {actual_version}"
                    _insert_request_outcome(
                        conn,
                        workspace_key=cleaned_workspace,
                        state_key=cleaned_key,
                        idempotency_key_digest=idempotency_digest,
                        request_digest=request_digest,
                        command_type=cleaned_command,
                        outcome_type="conflict",
                        response={
                            "error_code": "STATE_VERSION_CONFLICT",
                            "message": message,
                            "error_type": "ValueError",
                            "expected_version": cleaned_expected_version,
                            "actual_version": actual_version,
                        },
                        created_at=created_at,
                    )
                    conn.commit()
                    raise ValueError(message)

                resource = conn.execute(
                    """
                    SELECT state_type
                    FROM state_resources
                    WHERE workspace_key = ? AND state_key = ?
                    """,
                    (cleaned_workspace, cleaned_key),
                ).fetchone()
                if resource is not None and str(resource["state_type"]) != STATE_TYPE_RELEASE_STATE:
                    raise RuntimeError("unsupported persisted state resource type")
                current_value = _decode_json_object(str(head["value_json"])) if head is not None else None
                try:
                    next_value, restore_of_mutation_id = _apply_command(
                        conn,
                        workspace_key=cleaned_workspace,
                        state_key=cleaned_key,
                        command_type=cleaned_command,
                        command_payload=cleaned_payload,
                        actual_version=actual_version,
                        current_value=current_value,
                    )
                except ValueError as error:
                    _insert_request_outcome(
                        conn,
                        workspace_key=cleaned_workspace,
                        state_key=cleaned_key,
                        idempotency_key_digest=idempotency_digest,
                        request_digest=request_digest,
                        command_type=cleaned_command,
                        outcome_type="rejected",
                        response={
                            "error_code": "STATE_COMMAND_REJECTED",
                            "message": str(error),
                            "error_type": "ValueError",
                        },
                        created_at=created_at,
                    )
                    conn.commit()
                    raise

                if resource is None:
                    conn.execute(
                        """
                        INSERT INTO state_resources (
                            workspace_key, state_key, state_type, session_id, correlation_id,
                            actor, source_app, source_client, source_model,
                            client_session_id, client_workspace, client_transport, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            cleaned_workspace,
                            cleaned_key,
                            STATE_TYPE_RELEASE_STATE,
                            cleaned_provenance.get("session_id"),
                            cleaned_provenance.get("correlation_id"),
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

                value_json = _canonical_json(next_value)
                value_hash = _digest_text(value_json)
                new_version = actual_version + 1
                mutation_id = self._store._new_id()
                operation = "restore" if cleaned_command == STATE_COMMAND_RESTORE else "set"
                conn.execute(
                    """
                    INSERT INTO state_mutations (
                        mutation_id, workspace_key, state_key, base_version, new_version,
                        operation, command_type, value_json, value_hash, idempotency_key_digest,
                        request_digest, restore_of_mutation_id, session_id, correlation_id,
                        actor, source_app, source_client, source_model, client_session_id,
                        client_workspace, client_transport, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        mutation_id,
                        cleaned_workspace,
                        cleaned_key,
                        actual_version,
                        new_version,
                        operation,
                        cleaned_command,
                        value_json,
                        value_hash,
                        idempotency_digest,
                        request_digest,
                        restore_of_mutation_id,
                        cleaned_provenance.get("session_id"),
                        cleaned_provenance.get("correlation_id"),
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
                    INSERT INTO state_heads (
                        workspace_key, state_key, current_version, value_json, value_hash,
                        last_mutation_id, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(workspace_key, state_key) DO UPDATE SET
                        current_version = excluded.current_version,
                        value_json = excluded.value_json,
                        value_hash = excluded.value_hash,
                        last_mutation_id = excluded.last_mutation_id,
                        updated_at = excluded.updated_at
                    """,
                    (
                        cleaned_workspace,
                        cleaned_key,
                        new_version,
                        value_json,
                        value_hash,
                        mutation_id,
                        created_at,
                    ),
                )
                response = {
                    "mutation_id": mutation_id,
                    "workspace_key": cleaned_workspace,
                    "state_key": cleaned_key,
                    "base_version": actual_version,
                    "version": new_version,
                    "command": cleaned_command,
                    "operation": operation,
                    "value": next_value,
                    "value_hash": value_hash,
                    "restore_of_mutation_id": restore_of_mutation_id,
                    "created_at": created_at,
                    "database_epoch": actual_epoch,
                    "idempotent_replay": False,
                }
                _insert_request_outcome(
                    conn,
                    workspace_key=cleaned_workspace,
                    state_key=cleaned_key,
                    idempotency_key_digest=idempotency_digest,
                    request_digest=request_digest,
                    command_type=cleaned_command,
                    outcome_type="accepted",
                    response=response,
                    created_at=created_at,
                )
                conn.commit()
                return response
            except BaseException:
                conn.rollback()
                raise

    def history(
        self,
        *,
        workspace_key: str,
        state_key: str,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Return immutable mutations for a single state resource in version order."""

        cleaned_workspace = _required_text("workspace_key", workspace_key, max_chars=512)
        cleaned_key = _required_text("state_key", state_key, max_chars=512)
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 500:
            raise ValueError("limit must be an integer between 1 and 500")
        with self._store._connect() as conn:
            conn.execute("BEGIN")
            try:
                epoch = read_database_epoch(conn)
                rows = conn.execute(
                    """
                    SELECT mutation_id, base_version, new_version, operation, command_type,
                           value_json, value_hash, restore_of_mutation_id, session_id,
                           correlation_id, actor, source_app, source_client, source_model,
                           client_session_id, client_workspace, client_transport, created_at
                    FROM state_mutations
                    WHERE workspace_key = ? AND state_key = ?
                    ORDER BY new_version DESC
                    LIMIT ?
                    """,
                    (cleaned_workspace, cleaned_key, limit + 1),
                ).fetchall()
                has_more = len(rows) > limit
                response = {
                    "workspace_key": cleaned_workspace,
                    "state_key": cleaned_key,
                    "mutations": [_history_payload(row) for row in reversed(rows[:limit])],
                    "has_more": has_more,
                    "database_epoch": epoch,
                }
                conn.commit()
                return response
            except BaseException:
                conn.rollback()
                raise

    def rebuild_heads(self) -> dict[str, int]:
        """Rebuild all current-state heads from immutable mutations."""

        with self._store._connect() as conn:
            _begin_state_write_transaction(conn)
            try:
                counts = rebuild_state_heads(conn)
                conn.commit()
                return counts
            except BaseException:
                conn.rollback()
                raise

    def inspect_heads(self) -> dict[str, Any]:
        """Compare all current heads with independently reconstructed immutable history."""

        with self._store._connect() as conn:
            conn.execute("BEGIN")
            try:
                report = inspect_state_heads(conn)
                conn.commit()
                return report
            except BaseException:
                conn.rollback()
                raise


def rebuild_state_heads(conn: sqlite3.Connection) -> dict[str, int]:
    """Replace materialized heads from authority while preserving mutation timestamps."""

    expected, issues = _expected_head_rows(conn)
    if issues:
        raise RuntimeError(f"state mutation history is invalid; rebuild refused ({', '.join(issues[:3])})")
    conn.execute("DELETE FROM state_heads")
    conn.executemany(
        """
        INSERT INTO state_heads (
            workspace_key, state_key, current_version, value_json, value_hash,
            last_mutation_id, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                row["workspace_key"],
                row["state_key"],
                row["current_version"],
                row["value_json"],
                row["value_hash"],
                row["last_mutation_id"],
                row["updated_at"],
            )
            for row in expected.values()
        ],
    )
    return {"state_head_count": len(expected)}


def inspect_state_heads(conn: sqlite3.Connection) -> dict[str, Any]:
    """Compare all materialized heads against immutable mutation history."""

    return _inspect_state_heads(conn, workspace_key=None, state_key=None)


def inspect_state_head(
    conn: sqlite3.Connection,
    *,
    workspace_key: str,
    state_key: str,
) -> dict[str, Any]:
    """Targeted health check for one state resource used by the write path."""

    return _inspect_state_heads(conn, workspace_key=workspace_key, state_key=state_key)


def _inspect_state_heads(
    conn: sqlite3.Connection,
    *,
    workspace_key: str | None,
    state_key: str | None,
) -> dict[str, Any]:
    counts = {
        "missing_state_head_count": 0,
        "stale_state_head_count": 0,
        "orphan_state_head_count": 0,
        "invalid_state_history_count": 0,
    }
    samples: dict[str, list[str]] = {key.removesuffix("_count"): [] for key in counts}
    tables = {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()}
    required = {"state_resources", "state_mutations", "state_heads"}
    if not required.issubset(tables):
        missing = ",".join(sorted(required - tables))
        counts["missing_state_head_count"] = 1
        samples["missing_state_head"].append(f"required-tables-missing:{missing}")
        return {"ok": False, "counts": counts, "samples": samples}

    expected, issues = _expected_head_rows(conn, workspace_key=workspace_key, state_key=state_key)
    for issue in issues:
        counts["invalid_state_history_count"] += 1
        _append_sample(samples["invalid_state_history"], issue)
    where_sql, params = _state_filter(workspace_key=workspace_key, state_key=state_key)
    actual = {
        _state_identity(str(row["workspace_key"]), str(row["state_key"])): _row_values(row, _STATE_HEAD_FIELDS)
        for row in conn.execute(
            f"""
            SELECT workspace_key, state_key, current_version, value_json, value_hash,
                   last_mutation_id, updated_at
            FROM state_heads
            {where_sql}
            """,
            params,
        ).fetchall()
    }
    for key, expected_row in expected.items():
        actual_row = actual.pop(key, None)
        if actual_row is None:
            counts["missing_state_head_count"] += 1
            _append_sample(samples["missing_state_head"], key)
        elif actual_row != expected_row:
            counts["stale_state_head_count"] += 1
            _append_sample(samples["stale_state_head"], key)
    for key in actual:
        counts["orphan_state_head_count"] += 1
        _append_sample(samples["orphan_state_head"], key)
    return {"ok": sum(counts.values()) == 0, "counts": counts, "samples": samples}


def _expected_head_rows(
    conn: sqlite3.Connection,
    *,
    workspace_key: str | None = None,
    state_key: str | None = None,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    where_sql, params = _state_filter(workspace_key=workspace_key, state_key=state_key)
    resources = {
        _state_identity(str(row["workspace_key"]), str(row["state_key"]))
        for row in conn.execute(f"SELECT workspace_key, state_key FROM state_resources {where_sql}", params).fetchall()
    }
    expected: dict[str, dict[str, Any]] = {}
    issues: list[str] = []
    rows = conn.execute(
        f"""
        SELECT mutation_id, workspace_key, state_key, base_version, new_version,
               value_json, value_hash, created_at
        FROM state_mutations
        {where_sql}
        ORDER BY workspace_key, state_key, new_version
        """,
        params,
    ).fetchall()
    last_versions: dict[str, int] = {}
    for row in rows:
        current_workspace = str(row["workspace_key"])
        current_key = str(row["state_key"])
        identity = _state_identity(current_workspace, current_key)
        prior_version = last_versions.get(identity, 0)
        base_version = int(row["base_version"])
        new_version = int(row["new_version"])
        value_json = str(row["value_json"])
        try:
            _decode_json_object(value_json)
        except (TypeError, ValueError, json.JSONDecodeError):
            _append_unique(issues, f"invalid-value-json:{identity}")
        computed_hash = _digest_text(value_json)
        if str(row["value_hash"]) != computed_hash:
            _append_unique(issues, f"invalid-value-hash:{identity}")
        if identity not in resources:
            _append_unique(issues, f"mutation-without-resource:{identity}")
        if base_version != prior_version or new_version != prior_version + 1:
            _append_unique(issues, f"non-contiguous-history:{identity}")
        last_versions[identity] = new_version
        expected[identity] = {
            "workspace_key": current_workspace,
            "state_key": current_key,
            "current_version": new_version,
            "value_json": value_json,
            "value_hash": computed_hash,
            "last_mutation_id": str(row["mutation_id"]),
            "updated_at": str(row["created_at"]),
        }
    for identity in resources:
        if identity not in expected:
            _append_unique(issues, f"resource-without-mutation:{identity}")
    return expected, issues


def _state_filter(*, workspace_key: str | None, state_key: str | None) -> tuple[str, tuple[str, ...]]:
    if workspace_key is None and state_key is None:
        return "", ()
    if workspace_key is None or state_key is None:
        raise ValueError("workspace_key and state_key must be supplied together")
    return "WHERE workspace_key = ? AND state_key = ?", (workspace_key, state_key)


def _apply_command(
    conn: sqlite3.Connection,
    *,
    workspace_key: str,
    state_key: str,
    command_type: str,
    command_payload: Mapping[str, str],
    actual_version: int,
    current_value: dict[str, Any] | None,
) -> tuple[dict[str, str], str | None]:
    if command_type == STATE_COMMAND_STATUS_TRANSITION:
        target = str(command_payload["to_status"])
        if target not in _RELEASE_STATUSES:
            raise ValueError(f"release state status must be one of {sorted(_RELEASE_STATUSES)}")
        if current_value is None:
            if actual_version != 0 or target != "draft":
                raise ValueError("new release state must transition to draft at version zero")
            return {"status": "draft"}, None
        current_status = _release_status(current_value)
        if target not in _ALLOWED_STATUS_TRANSITIONS[current_status]:
            raise ValueError(f"release state transition {current_status}->{target} is not allowed")
        next_value = {"status": target}
        if "owner" in current_value:
            next_value["owner"] = _release_owner(current_value)
        return next_value, None
    if command_type == STATE_COMMAND_OWNER_ASSIGNMENT:
        if current_value is None or actual_version == 0:
            raise ValueError("owner assignment requires an existing release state")
        owner = str(command_payload["owner"])
        if current_value.get("owner") == owner:
            raise ValueError("owner assignment must change the current owner")
        return {"status": _release_status(current_value), "owner": owner}, None

    target_mutation_id = str(command_payload["mutation_id"])
    row = conn.execute(
        """
        SELECT value_json
        FROM state_mutations
        WHERE workspace_key = ? AND state_key = ? AND mutation_id = ?
        """,
        (workspace_key, state_key, target_mutation_id),
    ).fetchone()
    if row is None:
        raise ValueError("state mutation not found for restore")
    return _release_state_value(_decode_json_object(str(row["value_json"]))), target_mutation_id


def _release_status(value: Mapping[str, Any]) -> str:
    status = value.get("status")
    if not isinstance(status, str) or status not in _RELEASE_STATUSES:
        raise RuntimeError("persisted release state has invalid status")
    return status


def _release_owner(value: Mapping[str, Any]) -> str:
    owner = value.get("owner")
    if not isinstance(owner, str) or not owner:
        raise RuntimeError("persisted release state has invalid owner")
    return owner


def _fetch_request_outcome(
    conn: sqlite3.Connection,
    *,
    workspace_key: str,
    state_key: str,
    idempotency_key_digest: str,
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT request_digest, outcome_type, response_json
        FROM state_request_outcomes
        WHERE workspace_key = ? AND state_key = ? AND idempotency_key_digest = ?
        """,
        (workspace_key, state_key, idempotency_key_digest),
    ).fetchone()


def _insert_request_outcome(
    conn: sqlite3.Connection,
    *,
    workspace_key: str,
    state_key: str,
    idempotency_key_digest: str,
    request_digest: str,
    command_type: str,
    outcome_type: str,
    response: Mapping[str, Any],
    created_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO state_request_outcomes (
            workspace_key, state_key, idempotency_key_digest, request_digest,
            command_type, outcome_type, response_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            workspace_key,
            state_key,
            idempotency_key_digest,
            request_digest,
            command_type,
            outcome_type,
            _canonical_json(response),
            created_at,
        ),
    )


def _replay_request_outcome(row: sqlite3.Row) -> dict[str, Any]:
    response = _decode_json_object(str(row["response_json"]))
    if str(row["outcome_type"]) == "accepted":
        return {**response, "idempotent_replay": True}
    message = response.get("message")
    if not isinstance(message, str) or not message:
        raise RuntimeError("persisted state request outcome is malformed")
    if response.get("error_type") == "RuntimeError":
        raise RuntimeError(message)
    raise ValueError(message)


def _require_matching_request(row: sqlite3.Row, request_digest: str) -> None:
    if str(row["request_digest"]) != request_digest:
        raise ValueError("dynamic state idempotency key was already used with a different request")


def _projection_health_error(report: Mapping[str, Any]) -> str:
    counts = report.get("counts")
    details = "unknown"
    if isinstance(counts, Mapping):
        nonzero = [f"{key}={value}" for key, value in counts.items() if int(value) > 0]
        if nonzero:
            details = ", ".join(nonzero)
    return f"state head projection health is degraded; write refused ({details})"


def _command_payload(command_type: str, payload: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(payload, Mapping):
        raise ValueError("state command payload must be an object")
    normalized = {str(key): str(value) for key, value in payload.items()}
    required_fields = {
        STATE_COMMAND_STATUS_TRANSITION: {"to_status"},
        STATE_COMMAND_OWNER_ASSIGNMENT: {"owner"},
        STATE_COMMAND_RESTORE: {"mutation_id"},
    }[command_type]
    if set(normalized) != required_fields:
        raise ValueError(f"{command_type} payload fields must be {sorted(required_fields)}")
    return normalized


def _release_state_value(value: Mapping[str, Any]) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise RuntimeError("persisted release state is not an object")
    raw = dict(value)
    unknown = sorted(str(key) for key in raw if key not in {"status", "owner"})
    if unknown:
        raise RuntimeError(f"persisted release state has unsupported fields: {unknown}")
    status = _release_status(raw)
    normalized = {"status": status}
    if "owner" in raw:
        normalized["owner"] = _release_owner(raw)
    return normalized


def _head_payload(row: sqlite3.Row, *, database_epoch: str) -> dict[str, Any]:
    return {
        "workspace_key": str(row["workspace_key"]),
        "state_key": str(row["state_key"]),
        "state_type": str(row["state_type"]),
        "version": int(row["current_version"]),
        "value": _decode_json_object(str(row["value_json"])),
        "value_hash": str(row["value_hash"]),
        "last_mutation_id": str(row["last_mutation_id"]),
        "updated_at": str(row["updated_at"]),
        "database_epoch": database_epoch,
        "exists": True,
    }


def _history_payload(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "mutation_id": str(row["mutation_id"]),
        "base_version": int(row["base_version"]),
        "version": int(row["new_version"]),
        "command": str(row["command_type"]),
        "operation": str(row["operation"]),
        "value": _decode_json_object(str(row["value_json"])),
        "value_hash": str(row["value_hash"]),
        "restore_of_mutation_id": row["restore_of_mutation_id"],
        "provenance": {
            key: row[key]
            for key in (
                "session_id",
                "correlation_id",
                "actor",
                "source_app",
                "source_client",
                "source_model",
                "client_session_id",
                "client_workspace",
                "client_transport",
            )
            if row[key] is not None
        },
        "created_at": str(row["created_at"]),
    }


def _required_text(name: str, value: object, *, max_chars: int) -> str:
    cleaned = str(value).strip()
    if not cleaned:
        raise ValueError(f"{name} must not be empty")
    if len(cleaned) > max_chars:
        raise ValueError(f"{name} must be at most {max_chars} characters")
    require_durable_text(cleaned, subject=f"dynamic state {name}")
    return cleaned


def _expected_version(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError("expected_version must be a non-negative integer")
    return value


def _decode_json_object(value_json: str) -> dict[str, Any]:
    value = json.loads(value_json)
    if not isinstance(value, dict):
        raise ValueError("persisted state value is not a JSON object")
    return value


def _idempotency_digest(value: object) -> str:
    cleaned = _required_text("idempotency_key", value, max_chars=512)
    return _digest_text(cleaned)


def _request_digest(value: Mapping[str, Any]) -> str:
    return _digest_text(_canonical_json(value))


def _digest_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _state_identity(workspace_key: str, state_key: str) -> str:
    return f"{workspace_key}\x1f{state_key}"


def _row_values(row: sqlite3.Row, fields: tuple[str, ...]) -> dict[str, Any]:
    return {field: row[field] for field in fields}


def _append_sample(samples: list[str], value: str) -> None:
    if len(samples) < 5:
        samples.append(value)


def _append_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)
