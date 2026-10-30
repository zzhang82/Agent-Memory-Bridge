"""AMB Enterprise Gateway package: Multi-token authentication, RBAC, and JSONL audit logging."""

from __future__ import annotations

from .audit import AuditEmitter, AuditRecord, current_iso_timestamp, summarize_arguments, summarize_output
from .auth import GatewayConfig, MultiTokenVerifier, Principal, RateLimiter, hash_token, load_gateway_config
from .middleware import EnterpriseGatewayMiddleware
from .permissions import (
    ADMIN,
    ALL_MCP_TOOLS,
    ALL_ROLES,
    ORCHESTRATOR,
    READER,
    ROLE_PERMISSIONS,
    WRITER,
    check_namespace_permission,
    check_tool_permission,
    extract_namespace_from_arguments,
    match_namespace,
)

__all__ = [
    "ADMIN",
    "ALL_MCP_TOOLS",
    "ALL_ROLES",
    "AuditEmitter",
    "AuditRecord",
    "EnterpriseGatewayMiddleware",
    "GatewayConfig",
    "MultiTokenVerifier",
    "ORCHESTRATOR",
    "Principal",
    "READER",
    "ROLE_PERMISSIONS",
    "RateLimiter",
    "WRITER",
    "check_namespace_permission",
    "check_tool_permission",
    "current_iso_timestamp",
    "extract_namespace_from_arguments",
    "hash_token",
    "load_gateway_config",
    "match_namespace",
    "summarize_arguments",
    "summarize_output",
]
