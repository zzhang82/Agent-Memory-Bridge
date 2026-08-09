from __future__ import annotations

import hashlib
import json
import logging
import re
import tomllib
from collections.abc import Mapping
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from mcp.server.caching import CacheableMethod, CacheHint
from mcp.server.context import CallNext, HandlerResult, ServerMiddleware, ServerRequestContext

from .provenance import normalize_provenance_value

SERVER_NAME = "agent-memory-bridge"
SERVER_TITLE = "Agent Memory Bridge"
SERVER_DESCRIPTION = "Persistent engineering memory for coding agents over MCP"
MCP_MODERN_VERSION = "2026-07-28"
MCP_LEGACY_TEST_VERSION = "2025-11-25"
PUBLIC_TOOL_ORDER = (
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
)
PUBLIC_TOOL_NAMES = frozenset(PUBLIC_TOOL_ORDER)
PUBLIC_TOOL_SCHEMA_SHA256 = "a2e3dbbb48c87a7ce23bc4be1c8ea37c8cd176ff8f7fd2318d1374bc9e089e4a"
DISCOVER_CACHE_HINT = CacheHint(ttl_ms=300_000, scope="public")
TOOLS_LIST_CACHE_HINT = CacheHint(ttl_ms=0, scope="private")
MCP_CACHE_HINTS: Mapping[CacheableMethod, CacheHint] = {
    "server/discover": DISCOVER_CACHE_HINT,
    "tools/list": TOOLS_LIST_CACHE_HINT,
}
GENERIC_CLIENT_NAMES = frozenset({"mcp"})

_MAX_OBSERVABILITY_TEXT = 128
_MAX_CAPABILITY_KEYS = 32
_MAX_CAPABILITY_DEPTH = 4
_TRACEPARENT_PATTERN = re.compile(r"^[0-9a-fA-F]{2}-([0-9a-fA-F]{32})-[0-9a-fA-F]{16}-[0-9a-fA-F]{2}$")
_logger = logging.getLogger(__name__)


def package_version() -> str:
    pyproject_path = Path(__file__).resolve().parents[2] / "pyproject.toml"
    if pyproject_path.exists():
        with pyproject_path.open("rb") as handle:
            return str(tomllib.load(handle)["project"]["version"])
    try:
        return version("agent-memory-bridge")
    except PackageNotFoundError:
        return "0.0.0"


def context_source_client(ctx: Any | None) -> str | None:
    if ctx is None:
        return None
    try:
        client_params = ctx.session.client_params
    except (AttributeError, ValueError):
        return None
    client_info = getattr(client_params, "client_info", None)
    client_name = _optional_text(getattr(client_info, "name", None))
    if client_name is None or client_name.casefold() in GENERIC_CLIENT_NAMES:
        return None
    return normalize_provenance_value("source_client", client_name)


def context_observability_fields(ctx: Any | None) -> dict[str, str]:
    """Return bounded, non-authoritative request metadata suitable for debug logs."""

    if ctx is None:
        return {}
    session = getattr(ctx, "session", None)
    client_params = getattr(session, "client_params", None)
    client_info = getattr(client_params, "client_info", None)
    fields: dict[str, str] = {}

    client_name = _bounded_text(getattr(client_info, "name", None))
    if client_name is not None and client_name.casefold() not in GENERIC_CLIENT_NAMES:
        fields["client_name"] = client_name
    client_version = _bounded_text(getattr(client_info, "version", None), limit=64)
    if client_version is not None:
        fields["client_version"] = client_version

    protocol_version = _bounded_text(getattr(ctx, "protocol_version", None), limit=32)
    if protocol_version is None:
        protocol_version = _bounded_text(getattr(session, "protocol_version", None), limit=32)
    if protocol_version is not None:
        fields["protocol_version"] = protocol_version

    request_id = getattr(ctx, "request_id", None)
    if request_id is not None:
        fields["request_id_digest"] = _text_digest(str(request_id))

    capabilities = getattr(session, "client_capabilities", None)
    if capabilities is not None:
        fields["capabilities_digest"] = _bounded_digest(capabilities)

    meta = getattr(ctx, "meta", None)
    traceparent = meta.get("traceparent") if isinstance(meta, Mapping) else None
    if isinstance(traceparent, str):
        match = _TRACEPARENT_PATTERN.fullmatch(traceparent.strip())
        if match is not None:
            fields["trace_id"] = match.group(1).lower()
    return fields


class ProtocolObservabilityMiddleware(ServerMiddleware[Any]):
    """Log only bounded protocol evidence; never raw metadata or capabilities."""

    async def __call__(
        self,
        ctx: ServerRequestContext[Any, Any],
        call_next: CallNext,
    ) -> HandlerResult:
        fields = context_observability_fields(ctx)
        if fields:
            _logger.debug("mcp_request_metadata %s", json.dumps(fields, sort_keys=True, separators=(",", ":")))
        return await call_next(ctx)


def with_context_source_client(
    provenance: dict[str, str | None] | None,
    ctx: Any | None,
) -> dict[str, str | None] | None:
    source_client = context_source_client(ctx)
    if source_client is None:
        return provenance
    if provenance is None:
        return {"source_client": source_client}
    if _optional_text(provenance.get("source_client")) is not None:
        return provenance
    enriched = dict(provenance)
    enriched["source_client"] = source_client
    return enriched


def _optional_text(value: object | None) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _bounded_text(value: object | None, *, limit: int = _MAX_OBSERVABILITY_TEXT) -> str | None:
    cleaned = _optional_text(value)
    if cleaned is None:
        return None
    return cleaned[:limit]


def _text_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:16]


def _bounded_digest(value: object) -> str:
    bounded = _bounded_json_value(value, depth=0)
    encoded = json.dumps(bounded, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _bounded_json_value(value: object, *, depth: int) -> object:
    if depth >= _MAX_CAPABILITY_DEPTH:
        return "<max-depth>"
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            value = model_dump(mode="json", exclude_none=True)
        except (TypeError, ValueError):
            return f"<{type(value).__name__}>"
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return value[:_MAX_OBSERVABILITY_TEXT]
    if isinstance(value, Mapping):
        items = sorted(((str(key)[:64], item) for key, item in value.items()), key=lambda item: item[0])
        return {key: _bounded_json_value(item, depth=depth + 1) for key, item in items[:_MAX_CAPABILITY_KEYS]}
    if isinstance(value, list | tuple):
        return [_bounded_json_value(item, depth=depth + 1) for item in value[:_MAX_CAPABILITY_KEYS]]
    return f"<{type(value).__name__}>"
