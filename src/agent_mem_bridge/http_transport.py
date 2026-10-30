"""Optional, SDK-native Streamable HTTP transport for one AMB authority."""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import sqlite3
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp.server.auth.middleware.auth_context import AuthContextMiddleware
from mcp.server.auth.middleware.bearer_auth import AccessToken, BearerAuthBackend, RequireAuthMiddleware
from mcp.server.transport_security import TransportSecuritySettings
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .deployment_config import HttpTransportConfig, read_token_file
from .mcp_boundary import package_version
from .paths import resolve_bridge_db_path
from .schema import CURRENT_SCHEMA_VERSION, database_epoch, schema_version
from .server import BridgeFactory, create_mcp_server
from .storage import MemoryStore

_MCP_PATH = "/mcp"
_EXPECTED_TOOL_COUNT = 17
_EXPECTED_TOOL_SCHEMA_SHA256 = "24c5c52321d61b4b6f647c0d74e2d8304ca68716c403e08a274e9badfd8dc9f8"


class _SafeTransportLogFilter(logging.Filter):
    """SDK transport logs may interpolate untrusted headers/session IDs.

    Preserve severity and source location, but not request data or exception
    bodies. This filter is installed only when the HTTP deployment is built.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = "MCP HTTP transport event; request details omitted"
        record.args = ()
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return True


_TRANSPORT_LOG_FILTER = _SafeTransportLogFilter()


def _protect_transport_logs() -> None:
    for name in ("transport_security", "streamable_http", "streamable_http_manager"):
        logging.getLogger(f"mcp.server.{name}").addFilter(_TRANSPORT_LOG_FILTER)


class _StaticTokenVerifier:
    """SDK TokenVerifier for one dedicated, pre-provisioned private token."""

    def __init__(self, token: str) -> None:
        self._token = token.encode("ascii")

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            candidate = token.encode("ascii")
        except UnicodeEncodeError:
            return None
        if not secrets.compare_digest(candidate, self._token):
            return None
        # Do not place the bearer secret in request state; AMB has no token-based
        # identity model and does not use it for authorization decisions.
        return AccessToken(token="", client_id="amb-private-deployment", scopes=[])


class _McpAuthGate:
    """Apply the SDK's RFC 6750 gate only to the MCP route and its redirects."""

    def __init__(self, app: ASGIApp, *, mcp_path: str) -> None:
        self.app = app
        self._mcp_path = mcp_path
        self._require_auth = RequireAuthMiddleware(app, [])

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if scope["type"] == "http" and (path == self._mcp_path or path.startswith(f"{self._mcp_path}/")):
            await self._require_auth(scope, receive, send)
            return
        await self.app(scope, receive, send)


@dataclass(slots=True)
class _ReadinessState:
    tool_contract_ok: bool = False
    tool_count: int = 0
    tool_schema_sha256: str | None = None
    database_epoch: str | None = None


def build_http_app(
    config: HttpTransportConfig,
    *,
    store: MemoryStore | None = None,
    store_factory: BridgeFactory | None = None,
    readiness_db_path: Path | None = None,
    audit_sink: Any | None = None,
) -> ASGIApp:
    """Build the HTTP app over the existing MCP factory and its 17 handlers."""

    config.validate()
    _protect_transport_logs()
    factory = store_factory
    if store is None and factory is None:

        def factory() -> MemoryStore:
            return MemoryStore.from_env(require_local_filesystem=True)

    server = create_mcp_server(store=store, store_factory=factory)
    app = server.streamable_http_app(
        streamable_http_path=_MCP_PATH,
        json_response=True,
        stateless_http=False,
        transport_security=TransportSecuritySettings(
            allowed_hosts=list(config.effective_allowed_hosts),
            allowed_origins=list(config.effective_allowed_origins),
        ),
        host=config.host,
    )
    db_path = readiness_db_path or (store.db_path if store is not None else resolve_bridge_db_path())
    readiness_state = _ReadinessState()
    _install_transport_lifespan(app, server, readiness_state, db_path)
    app.add_route("/healthz", _liveness, methods=["GET"])
    app.add_route("/readyz", _readiness_endpoint(db_path, readiness_state), methods=["GET"])
    if config.gateway_config_file is not None or config.gateway_config_json or config.auth_mode == "rbac":
        from .gateway import (
            AuditEmitter,
            EnterpriseGatewayMiddleware,
            GatewayConfig,
            MultiTokenVerifier,
            Principal,
            hash_token,
            load_gateway_config,
        )

        if config.gateway_config_file is not None:
            gw_config = load_gateway_config(config.gateway_config_file)
        elif config.gateway_config_json:
            gw_config = load_gateway_config(config.gateway_config_json)
        elif config.token_file is not None:
            token = read_token_file(config.token_file)
            gw_config = GatewayConfig(
                principals=(
                    Principal(
                        principal_id="admin:root",
                        token_hash=hash_token(token),
                        role="admin",
                        allowed_namespaces=("*",),
                    ),
                ),
                auth_mode="rbac",
                audit_stream=config.audit_stream,
            )
        else:
            raise ValueError("RBAC mode requires gateway config or token file")

        gw_verifier = MultiTokenVerifier(gw_config)
        audit_target = (
            gw_config.audit_stream
            if (config.audit_stream == "stdout" and gw_config.audit_stream != "stdout")
            else config.audit_stream
        )
        audit_emitter = AuditEmitter(target=audit_target, sink=audit_sink)
        # Starlette applies the most recently registered middleware first.
        # Register in reverse order:
        # 1. AuthenticationMiddleware populates scope["user"] and scope["auth"]
        # 2. AuthContextMiddleware sets SDK contextvars for Session Manager
        # 3. EnterpriseGatewayMiddleware evaluates RBAC, record dereference, and audit
        app.add_middleware(
            EnterpriseGatewayMiddleware,
            config=gw_config,
            verifier=gw_verifier,
            audit_emitter=audit_emitter,
            db_path=db_path,
            mcp_path=_MCP_PATH,
        )
        app.add_middleware(AuthContextMiddleware)
        app.add_middleware(AuthenticationMiddleware, backend=BearerAuthBackend(gw_verifier))
    elif config.token_file is not None:
        static_verifier = _StaticTokenVerifier(read_token_file(config.token_file))
        # Starlette applies the most recently registered middleware first.
        # Register in reverse so authentication populates request state before
        # the SDK context and route gate inspect it.
        app.add_middleware(_McpAuthGate, mcp_path=_MCP_PATH)
        app.add_middleware(AuthContextMiddleware)
        app.add_middleware(AuthenticationMiddleware, backend=BearerAuthBackend(static_verifier))
    return app


def run_http_server(config: HttpTransportConfig) -> None:
    """Run one hardened HTTP authority; local filesystem safety is mandatory."""

    config.validate()
    import uvicorn

    app = build_http_app(
        config,
        store_factory=lambda: MemoryStore.from_env(require_local_filesystem=True),
        readiness_db_path=resolve_bridge_db_path(),
    )
    uvicorn.run(app, host=config.host, port=config.port, access_log=False)


async def _liveness(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


def _install_transport_lifespan(
    app: Any,
    server: Any,
    readiness_state: _ReadinessState,
    db_path: Path,
) -> None:
    """Enter the existing MCP lifespan once, then capture its actual tool surface."""

    mcp_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def transport_lifespan(starlette_app: Any) -> Any:
        async with mcp_lifespan(starlette_app):
            tools = await server.list_tools()
            readiness_state.tool_count = len(tools)
            readiness_state.tool_schema_sha256 = _tool_schema_digest(tools)
            readiness_state.tool_contract_ok = (
                readiness_state.tool_count == _EXPECTED_TOOL_COUNT
                and readiness_state.tool_schema_sha256 == _EXPECTED_TOOL_SCHEMA_SHA256
            )
            readiness_state.database_epoch = _inspect_authority(db_path)
            yield
        readiness_state.tool_contract_ok = False
        readiness_state.database_epoch = None

    app.router.lifespan_context = transport_lifespan


def _readiness_endpoint(
    db_path: Path,
    readiness_state: _ReadinessState,
) -> Callable[[Request], Awaitable[JSONResponse]]:
    async def endpoint(_: Request) -> JSONResponse:
        ready = _is_ready(db_path, readiness_state)
        payload: dict[str, Any] = {
            "status": "ready" if ready else "not_ready",
            "ready": ready,
            "version": package_version(),
            "schema_version": CURRENT_SCHEMA_VERSION if ready else None,
            "tool_count": readiness_state.tool_count,
            "tool_schema_sha256": readiness_state.tool_schema_sha256,
        }
        return JSONResponse(payload, status_code=200 if ready else 503)

    return endpoint


def _is_ready(db_path: Path, readiness_state: _ReadinessState) -> bool:
    if not readiness_state.tool_contract_ok or readiness_state.database_epoch is None:
        return False
    current_epoch = _inspect_authority(db_path)
    return current_epoch == readiness_state.database_epoch


def _inspect_authority(db_path: Path) -> str | None:
    """Check the cheap durable authority prerequisites without changing rows."""

    if not db_path.is_file():
        return None
    try:
        database_uri = f"{db_path.resolve().as_uri()}?mode=rw"
        with sqlite3.connect(database_uri, uri=True, timeout=1, isolation_level=None) as connection:
            if schema_version(connection) != CURRENT_SCHEMA_VERSION:
                return None
            for table in ("memories", "agent_runs", "run_events", "bridge_metadata"):
                connection.execute(f"SELECT 1 FROM {table} LIMIT 0")
            epoch = database_epoch(connection)
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("ROLLBACK")
    except (OSError, sqlite3.Error, ValueError):
        return None
    return epoch


def _tool_schema_digest(tools: list[Any]) -> str:
    snapshot = sorted(
        ({"name": tool.name, "inputSchema": tool.input_schema, "outputSchema": tool.output_schema} for tool in tools),
        key=lambda item: item["name"],
    )
    encoded = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
