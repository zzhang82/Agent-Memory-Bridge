"""Role-based access control and namespace boundary enforcement for AMB Enterprise Gateway."""

from __future__ import annotations

import fnmatch
import hashlib
import sqlite3
from pathlib import Path
from typing import Any

# The four standardized enterprise roles
ADMIN = "admin"
WRITER = "writer"
READER = "reader"
ORCHESTRATOR = "orchestrator"

ALL_ROLES: frozenset[str] = frozenset({ADMIN, WRITER, READER, ORCHESTRATOR})

# All 17 frozen public MCP tools
ALL_MCP_TOOLS: frozenset[str] = frozenset(
    {
        "store",
        "recall",
        "browse",
        "stats",
        "forget",
        "feedback",
        "promote",
        "annotate",
        "revise",
        "export",
        "begin_run",
        "record_run_event",
        "get_run",
        "complete_run",
        "claim_signal",
        "extend_signal_lease",
        "ack_signal",
    }
)

# Role permissions matrix
ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    ADMIN: ALL_MCP_TOOLS,
    WRITER: frozenset(
        {
            "store",
            "recall",
            "browse",
            "annotate",
            "revise",
            "feedback",
            "record_run_event",
        }
    ),
    READER: frozenset(
        {
            "recall",
            "browse",
            "stats",
            "get_run",
        }
    ),
    ORCHESTRATOR: frozenset(
        {
            "begin_run",
            "record_run_event",
            "complete_run",
            "get_run",
            "claim_signal",
            "extend_signal_lease",
            "ack_signal",
            "recall",
            "browse",
        }
    ),
}


def match_namespace(namespace: str, allowed_patterns: tuple[str, ...]) -> bool:
    """Check if a namespace matches any of the allowed wildcard or literal patterns."""
    if not allowed_patterns:
        return False
    for pattern in allowed_patterns:
        if pattern == "*":
            return True
        if pattern == namespace:
            return True
        if fnmatch.fnmatchcase(namespace, pattern):
            return True
    return False


def check_tool_permission(role: str, tool_name: str) -> tuple[bool, str | None]:
    """Verify that a role is permitted to call the specified tool."""
    if role not in ROLE_PERMISSIONS:
        return False, f"Unknown role '{role}'"
    allowed_tools = ROLE_PERMISSIONS[role]
    if tool_name not in allowed_tools:
        return False, f"Role '{role}' is not permitted to call tool '{tool_name}'"
    return True, None


def check_namespace_permission(
    allowed_namespaces: tuple[str, ...],
    namespace: str | None,
) -> tuple[bool, str | None]:
    """Verify that the target namespace is within the principal's allowed scope."""
    if namespace is None:
        # Tool does not target an explicit namespace parameter
        return True, None
    if match_namespace(namespace, allowed_namespaces):
        return True, None
    return False, f"Namespace '{namespace}' is not permitted for principal (allowed: {list(allowed_namespaces)})"


NAMESPACE_TOOLS: frozenset[str] = frozenset(
    {
        "store",
        "recall",
        "browse",
        "stats",
        "export",
        "claim_signal",
        "feedback",
    }
)

WORKSPACE_KEY_TOOLS: frozenset[str] = frozenset(
    {
        "begin_run",
        "record_run_event",
        "get_run",
        "complete_run",
    }
)

ID_BASED_TOOLS: frozenset[str] = frozenset(
    {
        "forget",
        "promote",
        "annotate",
        "revise",
        "extend_signal_lease",
        "ack_signal",
    }
)


def extract_namespace_from_arguments(tool_name: str, arguments: dict[str, Any] | None) -> tuple[str | None, str | None]:
    """Extract and validate the authoritative namespace or workspace scope from arguments.

    Returns (scope, error_message).
    Contradictory or rogue scope parameters are rejected immediately (fail-closed).
    """
    if not isinstance(arguments, dict):
        return None, None

    if tool_name in WORKSPACE_KEY_TOOLS:
        if "namespace" in arguments:
            return None, f"Tool '{tool_name}' operates on 'workspace_key'; unexpected 'namespace' parameter provided"
        val = arguments.get("workspace_key")
        if val is not None and (not isinstance(val, str) or not val.strip()):
            return None, f"Tool '{tool_name}' requires a valid non-empty 'workspace_key'"
        return (val.strip() if isinstance(val, str) else None), None

    if tool_name in NAMESPACE_TOOLS:
        if "workspace_key" in arguments:
            return None, f"Tool '{tool_name}' operates on 'namespace'; unexpected 'workspace_key' parameter provided"
        val = arguments.get("namespace")
        if val is not None and (not isinstance(val, str) or not val.strip()):
            return None, f"Tool '{tool_name}' requires a valid non-empty 'namespace'"
        return (val.strip() if isinstance(val, str) else None), None

    if tool_name in ID_BASED_TOOLS:
        if "namespace" in arguments or "workspace_key" in arguments:
            return (
                None,
                f"Tool '{tool_name}' operates by ID; scope parameters 'namespace'/'workspace_key' are not allowed",
            )
        return None, None

    return None, None


RECORD_ID_PARAM_MAP: dict[str, str] = {
    "forget": "id",
    "promote": "id",
    "annotate": "id",
    "revise": "id",
    "extend_signal_lease": "id",
    "ack_signal": "id",
    "feedback": "memory_id",
    "claim_signal": "signal_id",
}

ALL_KNOWN_ID_FIELDS: frozenset[str] = frozenset({"id", "signal_id", "memory_id", "run_id"})


def extract_record_id_for_tool(tool_name: str, arguments: dict[str, Any] | None) -> tuple[str | None, str | None]:
    """Extract and validate the authoritative record ID for a tool call.

    Returns (record_id, error_message).
    Rogue or conflicting ID parameters intended to confuse authorization are rejected (fail-closed).
    """
    if not isinstance(arguments, dict):
        return None, None

    expected_id_param = RECORD_ID_PARAM_MAP.get(tool_name)

    if expected_id_param is not None:
        # Tool expects a specific ID parameter
        for field in ALL_KNOWN_ID_FIELDS:
            if field != expected_id_param and field in arguments:
                return (
                    None,
                    f"Tool '{tool_name}' operates on '{expected_id_param}'; unexpected ID parameter '{field}' provided",
                )
        val = arguments.get(expected_id_param)
        if val is not None and (not isinstance(val, str) or not val.strip()):
            return None, f"Tool '{tool_name}' requires a valid non-empty '{expected_id_param}'"
        return (val.strip() if isinstance(val, str) else None), None

    if tool_name in ("record_run_event", "get_run", "complete_run"):
        for field in ("id", "signal_id", "memory_id"):
            if field in arguments:
                return None, f"Tool '{tool_name}' operates on 'run_id'; unexpected '{field}' parameter provided"
        val = arguments.get("run_id")
        if val is not None and (not isinstance(val, str) or not val.strip()):
            return None, f"Tool '{tool_name}' requires a valid non-empty 'run_id'"
        return None, None

    # Tools without ID parameters must not contain stray record IDs
    for field in ("id", "signal_id", "memory_id", "run_id"):
        if field in arguments:
            return None, f"Tool '{tool_name}' does not accept record ID parameter '{field}'"

    return None, None


def principal_consumer_prefix(principal_id: str) -> str:
    """Generate unambiguous 16-hex digest prefix for consumer framing."""
    digest = hashlib.sha256(principal_id.encode("utf-8")).hexdigest()[:16]
    return f"amb:p:{digest}:"


def encode_consumer(principal_id: str, raw_consumer: str | None) -> str:
    """Encode consumer identifier with unambiguous principal prefix."""
    prefix = principal_consumer_prefix(principal_id)
    cleaned = str(raw_consumer).strip() if raw_consumer else ""
    if cleaned.startswith(prefix):
        return cleaned
    return f"{prefix}{cleaned}" if cleaned else f"{prefix}{principal_id}"


def match_consumer_claim(claimed_by: str | None, principal_id: str, role: str = "") -> bool:
    """Verify if a claimed_by identifier belongs to the specified principal.

    Admins have global management authority over all claims (including legacy/external claims).
    Non-admin principals are strictly constrained to their own digest-prefixed leases ('amb:p:<digest>:').
    External or un-prefixed claims cannot be operated on by non-admin principals (fail-closed).
    """
    if not claimed_by:
        return False
    if role == "admin":
        return True
    prefix = principal_consumer_prefix(principal_id)
    return claimed_by.startswith(prefix)


def resolve_record_claim(db_path: Path | None, record_id: str) -> tuple[str, str | None, str | None]:
    """Resolve the authoritative namespace and claimed_by of a record.

    Returns ('found', namespace, claimed_by), ('not_found', None, None), or ('error', error_details, None).
    """
    if db_path is None or not db_path.is_file() or not record_id:
        return "error", "Database file is missing or invalid", None
    try:
        uri = f"{db_path.resolve().as_uri()}?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=2.0) as conn:
            cursor = conn.execute("SELECT namespace, claimed_by FROM memories WHERE id = ?", (record_id,))
            row = cursor.fetchone()
            if row:
                return "found", str(row[0]), (str(row[1]) if row[1] is not None else None)
            return "not_found", None, None
    except (sqlite3.Error, OSError) as exc:
        return "error", str(exc), None


def resolve_record_namespace(db_path: Path | None, record_id: str) -> tuple[str, str | None]:
    """Resolve the authoritative namespace of a stored record by its ID with explicit status.

    Returns ('found', namespace), ('not_found', None), or ('error', error_details).
    """
    if db_path is None or not db_path.is_file() or not record_id:
        return "error", "Database file is missing or invalid"
    try:
        uri = f"{db_path.resolve().as_uri()}?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=2.0) as conn:
            cursor = conn.execute("SELECT namespace FROM memories WHERE id = ?", (record_id,))
            row = cursor.fetchone()
            if row:
                return "found", str(row[0])
            return "not_found", None
    except (sqlite3.Error, OSError) as exc:
        return "error", str(exc)


def resolve_run_workspace(db_path: Path | None, run_id: str) -> tuple[str, str | None]:
    """Resolve the authoritative workspace_key of an agent run by its run_id with explicit status.

    Returns ('found', workspace_key), ('not_found', None), or ('error', error_details).
    """
    if db_path is None or not db_path.is_file() or not run_id:
        return "error", "Database file is missing or invalid"
    try:
        uri = f"{db_path.resolve().as_uri()}?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=2.0) as conn:
            cursor = conn.execute("SELECT workspace_key FROM agent_runs WHERE run_id = ?", (run_id,))
            row = cursor.fetchone()
            if row:
                return "found", str(row[0])
            return "not_found", None
    except (sqlite3.Error, OSError) as exc:
        return "error", str(exc)
