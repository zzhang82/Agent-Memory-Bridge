from __future__ import annotations

import asyncio
import hashlib
import json
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest
from mcp.server import MCPServer

from agent_mem_bridge import mcp_boundary
from agent_mem_bridge.mcp_boundary import (
    DISCOVER_CACHE_HINT,
    GENERIC_CLIENT_NAMES,
    MCP_CACHE_HINTS,
    MCP_LEGACY_TEST_VERSION,
    MCP_MODERN_VERSION,
    PUBLIC_TOOL_NAMES,
    PUBLIC_TOOL_ORDER,
    PUBLIC_TOOL_SCHEMA_SHA256,
    TOOLS_LIST_CACHE_HINT,
    ProtocolObservabilityMiddleware,
    context_observability_fields,
    context_source_client,
    with_context_source_client,
)


def _context(
    *,
    client_name: str | None = "codex",
    client_version: str = "1.2.3",
    protocol_version: str = MCP_MODERN_VERSION,
    request_id: object = "request-secret",
    capabilities: object | None = None,
    meta: dict[str, object] | None = None,
) -> SimpleNamespace:
    client_info = None if client_name is None else SimpleNamespace(name=client_name, version=client_version)
    client_params = None if client_info is None else SimpleNamespace(client_info=client_info)
    session = SimpleNamespace(
        client_params=client_params,
        client_capabilities=capabilities,
        protocol_version=protocol_version,
    )
    return SimpleNamespace(
        session=session,
        protocol_version=protocol_version,
        request_id=request_id,
        meta=meta,
    )


def test_protocol_contract_constants_are_single_source() -> None:
    assert MCP_MODERN_VERSION == "2026-07-28"
    assert MCP_LEGACY_TEST_VERSION == "2025-11-25"
    assert PUBLIC_TOOL_NAMES == frozenset(PUBLIC_TOOL_ORDER)
    assert len(PUBLIC_TOOL_ORDER) == 13
    assert MCP_CACHE_HINTS == {
        "server/discover": DISCOVER_CACHE_HINT,
        "tools/list": TOOLS_LIST_CACHE_HINT,
    }
    assert DISCOVER_CACHE_HINT.ttl_ms == 300_000
    assert DISCOVER_CACHE_HINT.scope == "public"
    assert TOOLS_LIST_CACHE_HINT.ttl_ms == 0
    assert TOOLS_LIST_CACHE_HINT.scope == "private"
    assert GENERIC_CLIENT_NAMES == frozenset({"mcp"})


def test_protocol_documentation_matches_cache_and_tool_order_contract() -> None:
    root = Path(__file__).resolve().parents[1]
    compatibility = (root / "docs" / "MCP-2026-COMPATIBILITY.md").read_text(encoding="utf-8")
    readmes = [
        (root / "README.md").read_text(encoding="utf-8"),
        (root / "README.zh-CN.md").read_text(encoding="utf-8"),
    ]

    positions = [compatibility.index(f"{index}. `{name}`") for index, name in enumerate(PUBLIC_TOOL_ORDER, start=1)]
    assert positions == sorted(positions)
    assert "`ttlMs: 300000`" in compatibility
    assert '`cacheScope: "public"`' in compatibility
    assert "`ttlMs: 0`" in compatibility
    assert '`cacheScope: "private"`' in compatibility
    for readme in readmes:
        assert "300000/public" in readme
        assert "0/private" in readme
    release_text = compatibility + "\n" + "\n".join(readmes)
    release_text += (root / "docs" / "v0.26.1-announcement.md").read_text(encoding="utf-8")
    assert "fully conformant" not in release_text.casefold()


def test_context_source_client_keeps_existing_precedence_rules() -> None:
    assert context_source_client(None) is None
    assert context_source_client(SimpleNamespace()) is None
    assert context_source_client(_context(client_name=None)) is None
    assert context_source_client(_context(client_name="  MCP  ")) is None
    assert context_source_client(_context(client_name="  claude-code  ")) == "claude-code"

    original = {"source_client": "explicit"}
    assert with_context_source_client(original, _context(client_name="codex")) is original
    assert with_context_source_client(None, _context(client_name="codex")) == {"source_client": "codex"}
    assert with_context_source_client({"actor": "reviewer"}, _context(client_name="codex")) == {
        "actor": "reviewer",
        "source_client": "codex",
    }
    assert with_context_source_client({"actor": "reviewer"}, _context(client_name="mcp")) == {"actor": "reviewer"}


def test_context_observability_is_bounded_and_never_returns_raw_metadata() -> None:
    raw_secret = "capability-secret-that-must-not-appear"
    baggage_secret = "private-path=/home/private-user/project"
    context = _context(
        client_name="c" * 300,
        client_version="v" * 300,
        request_id="request-secret-that-must-be-hashed",
        capabilities={
            "extensions": {f"extension-{index}": {"nested": {"deeper": {"secret": raw_secret}}} for index in range(40)}
        },
        meta={
            "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
            "baggage": baggage_secret,
        },
    )

    fields = context_observability_fields(context)
    rendered = json.dumps(fields, sort_keys=True)

    assert fields["client_name"] == "c" * 128
    assert fields["client_version"] == "v" * 64
    assert fields["protocol_version"] == MCP_MODERN_VERSION
    assert len(fields["request_id_digest"]) == 16
    assert len(fields["capabilities_digest"]) == 64
    assert fields["trace_id"] == "4bf92f3577b34da6a3ce929d0e0e4736"
    assert raw_secret not in rendered
    assert baggage_secret not in rendered
    assert "request-secret" not in rendered


def test_context_observability_degrades_safely_for_invalid_values() -> None:
    class BrokenModel:
        def model_dump(self, **_: object) -> object:
            raise TypeError("broken")

    fields = context_observability_fields(
        _context(
            client_name="mcp",
            capabilities=BrokenModel(),
            meta={"traceparent": "not-a-traceparent"},
        )
    )

    assert "client_name" not in fields
    assert "trace_id" not in fields
    assert len(fields["capabilities_digest"]) == 64
    assert context_observability_fields(None) == {}


def test_protocol_observability_middleware_logs_only_bounded_fields(caplog: pytest.LogCaptureFixture) -> None:
    middleware = ProtocolObservabilityMiddleware()
    context = _context(
        capabilities={"experimental": {"raw-secret": {"token": "do-not-log"}}},
        meta={"baggage": "do-not-log-baggage"},
    )

    async def call_next(ctx: object) -> dict[str, bool]:
        assert ctx is context
        return {"ok": True}

    with caplog.at_level("DEBUG", logger="agent_mem_bridge.mcp_boundary"):
        result = asyncio.run(middleware(context, call_next))  # type: ignore[arg-type]

    assert result == {"ok": True}
    assert "mcp_request_metadata" in caplog.text
    assert "do-not-log" not in caplog.text
    assert "capabilities_digest" in caplog.text


def test_protocol_observability_middleware_skips_empty_context(caplog: pytest.LogCaptureFixture) -> None:
    middleware = ProtocolObservabilityMiddleware()

    async def call_next(_: object) -> dict[str, bool]:
        return {"ok": True}

    with caplog.at_level("DEBUG", logger="agent_mem_bridge.mcp_boundary"):
        result = asyncio.run(middleware(SimpleNamespace(), call_next))  # type: ignore[arg-type]

    assert result == {"ok": True}
    assert "mcp_request_metadata" not in caplog.text


def test_bounded_observability_handles_fallback_shapes() -> None:
    class ValueErrorModel:
        def model_dump(self, **_: object) -> object:
            raise ValueError("broken")

    fallback_context = SimpleNamespace(
        session=SimpleNamespace(
            client_params=SimpleNamespace(client_info=SimpleNamespace(name="", version="")),
            client_capabilities=None,
            protocol_version=MCP_MODERN_VERSION,
        ),
        protocol_version=None,
        request_id=None,
        meta=["not", "a", "mapping"],
    )

    assert context_observability_fields(fallback_context) == {"protocol_version": MCP_MODERN_VERSION}
    assert mcp_boundary._bounded_json_value(None, depth=0) is None
    assert mcp_boundary._bounded_json_value(True, depth=0) is True
    assert mcp_boundary._bounded_json_value(("x", 2), depth=0) == ["x", 2]
    assert mcp_boundary._bounded_json_value(ValueErrorModel(), depth=0) == "<ValueErrorModel>"
    assert mcp_boundary._bounded_json_value(object(), depth=0) == "<object>"
    assert mcp_boundary._bounded_json_value({"a": {"b": {"c": {"d": "secret"}}}}, depth=0) == {
        "a": {"b": {"c": {"d": "<max-depth>"}}}
    }
    assert with_context_source_client({"source_client": "   "}, _context(client_name="codex")) == {
        "source_client": "codex"
    }


def test_public_tool_schema_digest_matches_release_snapshot(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_HOME", str(tmp_path))
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_DB_PATH", str(tmp_path / "bridge.db"))
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_LOG_DIR", str(tmp_path / "logs"))

    from agent_mem_bridge.server import mcp

    tools = asyncio.run(mcp.list_tools())
    snapshot = sorted(
        (
            {
                "name": tool.name,
                "inputSchema": tool.input_schema,
                "outputSchema": tool.output_schema,
            }
            for tool in tools
        ),
        key=lambda item: item["name"],
    )
    encoded = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")

    assert hashlib.sha256(encoded).hexdigest() == PUBLIC_TOOL_SCHEMA_SHA256
    assert [tool.name for tool in tools] == list(PUBLIC_TOOL_ORDER)


def test_server_tool_contract_fails_closed_on_registration_mismatch(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_HOME", str(tmp_path))
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_DB_PATH", str(tmp_path / "bridge.db"))
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_LOG_DIR", str(tmp_path / "logs"))

    from agent_mem_bridge.server import mcp

    registered = asyncio.run(MCPServer.list_tools(mcp))

    async def missing_tool(_: MCPServer):
        return registered[:-1]

    monkeypatch.setattr(MCPServer, "list_tools", missing_tool)

    with pytest.raises(RuntimeError, match="public MCP tool contract mismatch"):
        asyncio.run(mcp.list_tools())


def test_package_version_falls_back_to_pyproject(monkeypatch) -> None:
    def missing_distribution(_: str) -> str:
        raise mcp_boundary.PackageNotFoundError

    monkeypatch.setattr(mcp_boundary, "version", missing_distribution)

    root = Path(__file__).resolve().parents[1]
    expected = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    assert mcp_boundary.package_version() == expected


def test_package_version_falls_back_to_zero_without_distribution_or_pyproject(monkeypatch) -> None:
    def missing_distribution(_: str) -> str:
        raise mcp_boundary.PackageNotFoundError

    monkeypatch.setattr(mcp_boundary, "version", missing_distribution)
    monkeypatch.setattr(mcp_boundary.Path, "exists", lambda _: False)

    assert mcp_boundary.package_version() == "0.0.0"
