"""Versioned, exact-key mutable state backed by AMB's SQLite authority store.

This module intentionally stays separate from semantic memory retrieval.  It owns
one small release-state resource type with an append-only mutation ledger
and a rebuildable current-head projection.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from collections.abc import Mapping
from typing import Any

from .durable_data_policy import require_durable_structured_data, require_durable_text
from .provenance import normalize_provenance_mapping
from .schema import database_epoch as read_database_epoch

STATE_TYPE_RELEASE_STATE = "release-state"
_RELEASE_STATUSES = frozenset({"draft", "review", "published", "blocked", "retired"})
STATE_OPERATION_SET = "set"
STATE_OPERATION_RESTORE = "restore"
_STATE_OPERATIONS = frozenset({STATE_OPERATION_SET, STATE_OPERATION_RESTORE})
_STATE_HEAD_FIELDS = (
    "workspace_key",
    "state_key",
    "current_version",
    "value_json",
    "value_hash",
    "last_mutation_id",
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
    """Internal exact-key mutable-state boundary over a :class:`MemoryStore`.

    The caller supplies the MemoryStore only for its established SQLite
    connection, timestamp, and ID conventions.  No memory retrieval, FTS, or
    embedding behavior crosses this boundary.
    """

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
                    conn.commit()
                    return response
                if row["current_version"] is None:
                    raise RuntimeError("state resource is missing its current-state projection")
                response = _head_payload(row, database_epoch=epoch)
                conn.commit()
                return response
            except BaseException:
                conn.rollback()
                raise

    def commit(
        self,
        *,
        workspace_key: str,
        state_key: str,
        value: Mapping[str, Any],
        expected_version: int,
        expected_database_epoch: str,
        idempotency_key: str,
        provenance: Mapping[str, object | None] | None = None,
        operation: str = STATE_OPERATION_SET,
        restore_of_mutation_id: str | None = None,
    ) -> dict[str, Any]:
        """Append a new full-snapshot mutation after epoch and version CAS checks.

        The only normal command for this MVP is ``set``.  ``restore`` is reserved
        for :meth:`restore`, which obtains its value from a prior immutable
        mutation.  Replaying a successful request with the same key and semantic
        request digest returns the original mutation even after later versions.
        """

        cleaned_workspace = _required_text("workspace_key", workspace_key, max_chars=512)
        cleaned_key = _required_text("state_key", state_key, max_chars=512)
        cleaned_value = _release_state_value(value)
        cleaned_expected_version = _expected_version(expected_version)
        cleaned_expected_epoch = _required_text("expected_database_epoch", expected_database_epoch, max_chars=128)
        idempotency_digest = _idempotency_digest(idempotency_key)
        cleaned_provenance = normalize_provenance_mapping(provenance) or {}
        cleaned_operation = str(operation).strip()
        if cleaned_operation not in _STATE_OPERATIONS:
            raise ValueError(f"unsupported state operation: {cleaned_operation or '<empty>'}")
        if cleaned_operation == STATE_OPERATION_SET and restore_of_mutation_id is not None:
            raise ValueError("restore_of_mutation_id is only valid for restore")
        cleaned_restore_id = (
            _required_text("restore_of_mutation_id", restore_of_mutation_id, max_chars=96)
            if restore_of_mutation_id is not None
            else None
        )
        if cleaned_operation == STATE_OPERATION_RESTORE and cleaned_restore_id is None:
            raise ValueError("restore requires restore_of_mutation_id")
        value_json = _canonical_json(cleaned_value)
        value_hash = _digest_text(value_json)
        request_digest = _request_digest(
            {
                "workspace_key": cleaned_workspace,
                "state_key": cleaned_key,
                "state_type": STATE_TYPE_RELEASE_STATE,
                "operation": cleaned_operation,
                "value": cleaned_value,
                "restore_of_mutation_id": cleaned_restore_id,
                "provenance": cleaned_provenance,
            }
        )

        with self._store._connect() as conn:
            _begin_state_write_transaction(conn)
            try:
                actual_epoch = read_database_epoch(conn)
                if cleaned_expected_epoch != actual_epoch:
                    raise ValueError(
                        f"DATABASE_EPOCH_CONFLICT: expected {cleaned_expected_epoch}, actual {actual_epoch}"
                    )
                existing = conn.execute(
                    """
                    SELECT mutation_id, base_version, new_version, operation, value_json,
                           value_hash, restore_of_mutation_id, request_digest, created_at
                    FROM state_mutations
                    WHERE workspace_key = ? AND state_key = ? AND idempotency_key_digest = ?
                    """,
                    (cleaned_workspace, cleaned_key, idempotency_digest),
                ).fetchone()
                if existing is not None:
                    _require_matching_request(existing, request_digest)
                    conn.commit()
                    return _mutation_payload(existing, database_epoch=actual_epoch, idempotent_replay=True)

                projection_health = inspect_state_heads(conn)
                _require_healthy_state_heads(projection_health)
                head = conn.execute(
                    """
                    SELECT current_version
                    FROM state_heads
                    WHERE workspace_key = ? AND state_key = ?
                    """,
                    (cleaned_workspace, cleaned_key),
                ).fetchone()
                actual_version = int(head["current_version"]) if head is not None else 0
                if cleaned_expected_version != actual_version:
                    raise ValueError(
                        f"STATE_VERSION_CONFLICT: expected {cleaned_expected_version}, actual {actual_version}"
                    )
                resource = conn.execute(
                    """
                    SELECT state_type
                    FROM state_resources
                    WHERE workspace_key = ? AND state_key = ?
                    """,
                    (cleaned_workspace, cleaned_key),
                ).fetchone()
                created_at = self._store._utc_now()
                if resource is None:
                    if actual_version != 0:
                        raise RuntimeError("state head exists without its state resource")
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
                elif str(resource["state_type"]) != STATE_TYPE_RELEASE_STATE:
                    raise RuntimeError("unsupported persisted state resource type")

                new_version = actual_version + 1
                mutation_id = self._store._new_id()
                conn.execute(
                    """
                    INSERT INTO state_mutations (
                        mutation_id, workspace_key, state_key, base_version, new_version,
                        operation, value_json, value_hash, idempotency_key_digest,
                        request_digest, restore_of_mutation_id, session_id, correlation_id,
                        actor, source_app, source_client, source_model, client_session_id,
                        client_workspace, client_transport, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        mutation_id,
                        cleaned_workspace,
                        cleaned_key,
                        actual_version,
                        new_version,
                        cleaned_operation,
                        value_json,
                        value_hash,
                        idempotency_digest,
                        request_digest,
                        cleaned_restore_id,
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
                conn.commit()
                return {
                    "mutation_id": mutation_id,
                    "workspace_key": cleaned_workspace,
                    "state_key": cleaned_key,
                    "base_version": actual_version,
                    "version": new_version,
                    "operation": cleaned_operation,
                    "value": cleaned_value,
                    "value_hash": value_hash,
                    "restore_of_mutation_id": cleaned_restore_id,
                    "created_at": created_at,
                    "database_epoch": actual_epoch,
                    "idempotent_replay": False,
                }
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
                    SELECT mutation_id, base_version, new_version, operation, value_json,
                           value_hash, restore_of_mutation_id, session_id, correlation_id,
                           actor, source_app, source_client, source_model, client_session_id,
                           client_workspace, client_transport, created_at
                    FROM state_mutations
                    WHERE workspace_key = ? AND state_key = ?
                    ORDER BY new_version DESC
                    LIMIT ?
                    """,
                    (cleaned_workspace, cleaned_key, limit + 1),
                ).fetchall()
                has_more = len(rows) > limit
                visible_rows = rows[:limit]
                response = {
                    "workspace_key": cleaned_workspace,
                    "state_key": cleaned_key,
                    "mutations": [_history_payload(row) for row in reversed(visible_rows)],
                    "has_more": has_more,
                    "database_epoch": epoch,
                }
                conn.commit()
                return response
            except BaseException:
                conn.rollback()
                raise

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
        """Restore a prior mutation snapshot by appending a new version."""

        cleaned_workspace = _required_text("workspace_key", workspace_key, max_chars=512)
        cleaned_key = _required_text("state_key", state_key, max_chars=512)
        cleaned_mutation_id = _required_text("mutation_id", mutation_id, max_chars=96)
        with self._store._connect() as conn:
            row = conn.execute(
                """
                SELECT value_json
                FROM state_mutations
                WHERE workspace_key = ? AND state_key = ? AND mutation_id = ?
                """,
                (cleaned_workspace, cleaned_key, cleaned_mutation_id),
            ).fetchone()
        if row is None:
            raise ValueError("state mutation not found for restore")
        value = _decode_json_object(str(row["value_json"]))
        return self.commit(
            workspace_key=cleaned_workspace,
            state_key=cleaned_key,
            value=value,
            expected_version=expected_version,
            expected_database_epoch=expected_database_epoch,
            idempotency_key=idempotency_key,
            provenance=provenance,
            operation=STATE_OPERATION_RESTORE,
            restore_of_mutation_id=cleaned_mutation_id,
        )

    def rebuild_heads(self) -> dict[str, int]:
        """Rebuild all current-state heads from append-only state mutations."""

        with self._store._connect() as conn:
            _begin_state_write_transaction(conn)
            try:
                counts = rebuild_state_heads(conn, rebuilt_at=self._store._utc_now())
                conn.commit()
                return counts
            except BaseException:
                conn.rollback()
                raise

    def inspect_heads(self) -> dict[str, Any]:
        """Compare current heads with independently reconstructed immutable history."""

        with self._store._connect() as conn:
            conn.execute("BEGIN")
            try:
                report = inspect_state_heads(conn)
                conn.commit()
                return report
            except BaseException:
                conn.rollback()
                raise


def rebuild_state_heads(conn: sqlite3.Connection, *, rebuilt_at: str) -> dict[str, int]:
    """Replace the materialized state-head projection from immutable mutations."""

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
                rebuilt_at,
            )
            for row in expected.values()
        ],
    )
    return {"state_head_count": len(expected)}


def inspect_state_heads(conn: sqlite3.Connection) -> dict[str, Any]:
    """Compare materialized heads against the mutation ledger in one snapshot."""

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

    expected, issues = _expected_head_rows(conn)
    for issue in issues:
        counts["invalid_state_history_count"] += 1
        _append_sample(samples["invalid_state_history"], issue)
    actual = {
        _state_identity(str(row["workspace_key"]), str(row["state_key"])): _row_values(row, _STATE_HEAD_FIELDS)
        for row in conn.execute(
            """
            SELECT workspace_key, state_key, current_version, value_json, value_hash,
                   last_mutation_id
            FROM state_heads
            """
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


def _expected_head_rows(conn: sqlite3.Connection) -> tuple[dict[str, dict[str, Any]], list[str]]:
    resources = {
        _state_identity(str(row["workspace_key"]), str(row["state_key"]))
        for row in conn.execute("SELECT workspace_key, state_key FROM state_resources").fetchall()
    }
    expected: dict[str, dict[str, Any]] = {}
    issues: list[str] = []
    rows = conn.execute(
        """
        SELECT mutation_id, workspace_key, state_key, base_version, new_version,
               value_json, value_hash
        FROM state_mutations
        ORDER BY workspace_key, state_key, new_version
        """
    ).fetchall()
    last_versions: dict[str, int] = {}
    for row in rows:
        workspace_key = str(row["workspace_key"])
        state_key = str(row["state_key"])
        identity = _state_identity(workspace_key, state_key)
        prior_version = last_versions.get(identity, 0)
        base_version = int(row["base_version"])
        new_version = int(row["new_version"])
        if identity not in resources:
            _append_unique(issues, f"mutation-without-resource:{identity}")
        if base_version != prior_version or new_version != prior_version + 1:
            _append_unique(issues, f"non-contiguous-history:{identity}")
        last_versions[identity] = new_version
        expected[identity] = {
            "workspace_key": workspace_key,
            "state_key": state_key,
            "current_version": new_version,
            "value_json": str(row["value_json"]),
            "value_hash": str(row["value_hash"]),
            "last_mutation_id": str(row["mutation_id"]),
        }
    for identity in resources:
        if identity not in expected:
            _append_unique(issues, f"resource-without-mutation:{identity}")
    return expected, issues


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


def _mutation_payload(
    row: sqlite3.Row,
    *,
    database_epoch: str,
    idempotent_replay: bool,
) -> dict[str, Any]:
    return {
        "mutation_id": str(row["mutation_id"]),
        "base_version": int(row["base_version"]),
        "version": int(row["new_version"]),
        "operation": str(row["operation"]),
        "value": _decode_json_object(str(row["value_json"])),
        "value_hash": str(row["value_hash"]),
        "restore_of_mutation_id": row["restore_of_mutation_id"],
        "created_at": str(row["created_at"]),
        "database_epoch": database_epoch,
        "idempotent_replay": idempotent_replay,
    }


def _history_payload(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "mutation_id": str(row["mutation_id"]),
        "base_version": int(row["base_version"]),
        "version": int(row["new_version"]),
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


def _require_healthy_state_heads(report: Mapping[str, Any]) -> None:
    if bool(report.get("ok")):
        return
    counts = report.get("counts")
    details = "unknown"
    if isinstance(counts, Mapping):
        nonzero = [f"{key}={value}" for key, value in counts.items() if int(value) > 0]
        if nonzero:
            details = ", ".join(nonzero)
    raise RuntimeError(f"state head projection health is degraded; write refused ({details})")


def _require_matching_request(row: sqlite3.Row, request_digest: str) -> None:
    if str(row["request_digest"]) != request_digest:
        raise ValueError("dynamic state idempotency key was already used with a different payload")


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


def _release_state_value(value: Mapping[str, Any]) -> dict[str, str]:
    """Validate the MVP's only state resource: status plus an optional owner."""

    if not isinstance(value, Mapping):
        raise ValueError("value must be an object")
    raw = dict(value)
    require_durable_structured_data(raw, subject="release state value")
    unknown = sorted(str(key) for key in raw if key not in {"status", "owner"})
    if unknown:
        raise ValueError(f"release state value has unsupported fields: {unknown}")
    status = raw.get("status")
    if not isinstance(status, str) or status.strip() not in _RELEASE_STATUSES:
        raise ValueError(f"release state status must be one of {sorted(_RELEASE_STATUSES)}")
    normalized = {"status": status.strip()}
    if "owner" in raw:
        owner = raw["owner"]
        if not isinstance(owner, str) or not owner.strip():
            raise ValueError("release state owner must be a non-empty string")
        cleaned_owner = owner.strip()
        if len(cleaned_owner) > 128:
            raise ValueError("release state owner must be at most 128 characters")
        require_durable_text(cleaned_owner, subject="release state owner")
        normalized["owner"] = cleaned_owner
    encoded = _canonical_json(normalized)
    if len(encoded.encode("utf-8")) > 32768:
        raise ValueError("value must be at most 32768 bytes")
    return normalized


def _decode_json_object(value_json: str) -> dict[str, Any]:
    value = json.loads(value_json)
    if not isinstance(value, dict):
        raise RuntimeError("persisted state value is not a JSON object")
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
