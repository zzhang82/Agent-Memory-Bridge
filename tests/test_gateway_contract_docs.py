from __future__ import annotations

import re
from pathlib import Path

from agent_mem_bridge.gateway.audit import AuditRecord
from agent_mem_bridge.gateway.permissions import ROLE_PERMISSIONS
from agent_mem_bridge.mcp_boundary import PUBLIC_TOOL_ORDER

ROOT = Path(__file__).resolve().parents[1]
DEPLOYMENT = ROOT / "docs" / "REMOTE-DEPLOYMENT.md"

ALWAYS_PRESENT_AUDIT_FIELDS = (
    "audit_id",
    "timestamp",
    "request_id",
    "principal_id",
    "client_ip",
    "transport",
    "tool",
    "namespace",
    "status",
    "duration_ms",
    "arguments_summary",
    "output_summary",
)
AUDIT_STATUSES = (
    "success",
    "denied_unauthenticated",
    "denied_forbidden",
    "denied_rate_limited",
    "error",
    "tool_error",
    "unknown_truncated",
)


def _tools(cell: str) -> set[str]:
    return set(re.findall(r"`([a-z_]+)`", cell))


def _role_rows() -> dict[str, tuple[set[str], set[str]]]:
    public_tools = set(PUBLIC_TOOL_ORDER)
    rows: dict[str, tuple[set[str], set[str]]] = {}
    for line in DEPLOYMENT.read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"\| \*\*([a-z]+)\*\* \| ([^|]+) \| ([^|]+) \| [^|]+ \|", line.strip())
        if match is None:
            continue
        role, allowed, forbidden = match.groups()
        allowed_tools = public_tools if "All 17 tools" in allowed else _tools(allowed)
        forbidden_tools = set() if forbidden.strip() == "None" else _tools(forbidden)
        rows[role] = (allowed_tools, forbidden_tools)
    return rows


def test_documented_role_allowlists_match_permissions() -> None:
    documented = _role_rows()
    public_tools = set(PUBLIC_TOOL_ORDER)

    assert set(documented) == set(ROLE_PERMISSIONS)
    assert documented["admin"][0] == public_tools
    for role, allowed in ROLE_PERMISSIONS.items():
        assert documented[role][0] == set(allowed)
        if role != "admin":
            assert documented[role][1] == public_tools - set(allowed)


def test_role_doc_guard_rejects_undocumented_permission() -> None:
    documented = _role_rows()
    mutated = {role: set(tools) for role, tools in ROLE_PERMISSIONS.items()}
    mutated["reader"].add("store")

    assert documented["reader"][0] != mutated["reader"]


def test_serialized_audit_contract_matches_remote_deployment_docs() -> None:
    text = DEPLOYMENT.read_text(encoding="utf-8")
    without_error = AuditRecord(
        audit_id="audit-1",
        timestamp="2026-10-30T00:00:00.000Z",
        request_id="request-1",
        principal_id="principal-1",
        client_ip=None,
        transport="http_mcp",
        tool=None,
        namespace=None,
        status="success",
        duration_ms=1.234,
    ).to_dict()
    with_error = AuditRecord(
        audit_id="audit-2",
        timestamp="2026-10-30T00:00:00.000Z",
        request_id="request-2",
        principal_id="principal-2",
        client_ip="127.0.0.1",
        transport="http_mcp",
        tool="store",
        namespace="project:example",
        status="error",
        duration_ms=1,
        error_message="RuntimeError",
    ).to_dict()

    assert tuple(without_error) == ALWAYS_PRESENT_AUDIT_FIELDS
    assert "error_message" not in without_error
    assert tuple(key for key in with_error if key != "error_message") == ALWAYS_PRESENT_AUDIT_FIELDS
    assert with_error["error_message"] == "RuntimeError"
    for field in (*ALWAYS_PRESENT_AUDIT_FIELDS, "error_message"):
        assert f"`{field}`" in text
    for status in AUDIT_STATUSES:
        assert f"`{status}`" in text
    assert "at least 4096 bytes" in text
    assert "first 64 characters" in text
    assert "not redaction" in text


def test_audit_doc_guard_rejects_undocumented_serialized_field() -> None:
    serialized = set(ALWAYS_PRESENT_AUDIT_FIELDS)
    mutated = serialized | {"unexpected_debug"}

    assert mutated - serialized == {"unexpected_debug"}
