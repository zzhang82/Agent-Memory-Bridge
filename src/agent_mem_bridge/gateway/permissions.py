"""Role-based access control and namespace boundary enforcement for AMB Enterprise Gateway."""

from __future__ import annotations

import fnmatch
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


def extract_namespace_from_arguments(tool_name: str, arguments: dict[str, Any] | None) -> str | None:
    """Extract the target namespace or workspace scope from tool call arguments."""
    if not isinstance(arguments, dict):
        return None
    if "namespace" in arguments and isinstance(arguments["namespace"], str):
        return arguments["namespace"]
    if "workspace_key" in arguments and isinstance(arguments["workspace_key"], str):
        return arguments["workspace_key"]
    return None
