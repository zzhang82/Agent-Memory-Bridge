"""ASGI Middleware for AMB Enterprise Gateway.

Intercepts requests to the MCP endpoint, enforces multi-token authentication,
RBAC tool permission matrices, namespace tenant boundaries, and emits
structured single-line JSONL audit records with zero SQLite database lock contention.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .audit import AuditEmitter, AuditRecord, current_iso_timestamp, summarize_arguments, summarize_output
from .auth import GatewayConfig, MultiTokenVerifier
from .permissions import check_namespace_permission, check_tool_permission, extract_namespace_from_arguments


class EnterpriseGatewayMiddleware:
    """Enterprise Gateway ASGI middleware for authentication, RBAC, and audit streaming."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        config: GatewayConfig,
        verifier: MultiTokenVerifier,
        audit_emitter: AuditEmitter,
        mcp_path: str = "/mcp",
    ) -> None:
        self.app = app
        self.config = config
        self.verifier = verifier
        self.audit_emitter = audit_emitter
        self.mcp_path = mcp_path

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        # Only gate the MCP path and its subpaths (e.g. /mcp, /mcp/)
        if not (path == self.mcp_path or path.startswith(f"{self.mcp_path}/")):
            await self.app(scope, receive, send)
            return

        start_time = time.perf_counter()
        audit_id = f"aud_{uuid.uuid4().hex}"
        request_id = f"req_{uuid.uuid4().hex[:8]}"
        client_ip = _extract_client_ip(scope)
        transport = "http_mcp"

        # 1. Extract Bearer token
        auth_header_val: str | None = None
        for name, value in scope.get("headers", []):
            if name.lower() == b"authorization":
                auth_header_val = value.decode("latin1", errors="replace").strip()
                break

        if self.config.require_auth:
            if not auth_header_val or not auth_header_val.lower().startswith("bearer "):
                duration_ms = (time.perf_counter() - start_time) * 1000
                record = AuditRecord(
                    audit_id=audit_id,
                    timestamp=current_iso_timestamp(),
                    request_id=request_id,
                    principal_id="anonymous",
                    client_ip=client_ip,
                    transport=transport,
                    tool=None,
                    namespace=None,
                    status="denied_unauthenticated",
                    duration_ms=duration_ms,
                    error_message="Authentication required: missing or invalid Bearer header",
                )
                self.audit_emitter.emit(record)
                await _send_http_json(
                    send,
                    401,
                    {"error": "invalid_token", "error_description": "Authentication required"},
                    www_authenticate=True,
                )
                return

            token = auth_header_val[7:].strip()
            access_token = await self.verifier.verify_token(token)
            if access_token is None:
                duration_ms = (time.perf_counter() - start_time) * 1000
                record = AuditRecord(
                    audit_id=audit_id,
                    timestamp=current_iso_timestamp(),
                    request_id=request_id,
                    principal_id="unknown",
                    client_ip=client_ip,
                    transport=transport,
                    tool=None,
                    namespace=None,
                    status="denied_unauthenticated",
                    duration_ms=duration_ms,
                    error_message="Invalid Bearer token",
                )
                self.audit_emitter.emit(record)
                await _send_http_json(
                    send,
                    401,
                    {"error": "invalid_token", "error_description": "Invalid Bearer token"},
                    www_authenticate=True,
                )
                return

            principal_id = access_token.client_id
            role = access_token.scopes[0] if access_token.scopes else "reader"
            claims = access_token.claims or {}
            allowed_namespaces = tuple(claims.get("allowed_namespaces", ()))
            rate_limit_rpm = int(claims.get("rate_limit_rpm", 0))
        else:
            # Unauthenticated local superadmin mode
            principal_id = "local_root"
            role = "admin"
            allowed_namespaces = ("*",)
            rate_limit_rpm = 0

        # 2. Rate limiting check
        if rate_limit_rpm > 0 and not self.verifier.rate_limiter.is_allowed(principal_id, rate_limit_rpm):
            duration_ms = (time.perf_counter() - start_time) * 1000
            record = AuditRecord(
                audit_id=audit_id,
                timestamp=current_iso_timestamp(),
                request_id=request_id,
                principal_id=principal_id,
                client_ip=client_ip,
                transport=transport,
                tool=None,
                namespace=None,
                status="denied_rate_limited",
                duration_ms=duration_ms,
                error_message=f"Rate limit exceeded ({rate_limit_rpm} rpm)",
            )
            self.audit_emitter.emit(record)
            await _send_http_json(
                send,
                429,
                {"error": "rate_limited", "error_description": "Rate limit exceeded"},
            )
            return

        # 3. Read and inspect incoming body for POST calls
        method = scope.get("method", "GET").upper()
        full_body = b""
        tool_name: str | None = None
        namespace: str | None = None
        arguments_summary: dict[str, Any] | None = None

        if method == "POST":
            body_chunks: list[bytes] = []
            more_body = True
            while more_body:
                msg = await receive()
                if msg["type"] == "http.request":
                    body_chunks.append(msg.get("body", b""))
                    more_body = msg.get("more_body", False)
                elif msg["type"] == "http.disconnect":
                    return
            full_body = b"".join(body_chunks)

            payload: dict[str, Any] | None = None
            if full_body:
                try:
                    payload = json.loads(full_body.decode("utf-8"))
                except ValueError:
                    payload = None

            if isinstance(payload, dict) and payload.get("method") == "tools/call":
                params = payload.get("params", {})
                if isinstance(params, dict):
                    tool_name = params.get("name")
                    arguments = params.get("arguments", {})
                    namespace = extract_namespace_from_arguments(tool_name or "", arguments)
                    arguments_summary = summarize_arguments(arguments)

                    # RBAC Step 1: Tool check
                    tool_ok, tool_err = check_tool_permission(role, tool_name or "")
                    if not tool_ok:
                        duration_ms = (time.perf_counter() - start_time) * 1000
                        record = AuditRecord(
                            audit_id=audit_id,
                            timestamp=current_iso_timestamp(),
                            request_id=request_id,
                            principal_id=principal_id,
                            client_ip=client_ip,
                            transport=transport,
                            tool=tool_name,
                            namespace=namespace,
                            status="denied_forbidden",
                            duration_ms=duration_ms,
                            arguments_summary=arguments_summary,
                            error_message=tool_err,
                        )
                        self.audit_emitter.emit(record)
                        await _send_http_json(
                            send,
                            403,
                            {"error": "forbidden", "error_description": tool_err},
                        )
                        return

                    # RBAC Step 2: Namespace check
                    ns_ok, ns_err = check_namespace_permission(allowed_namespaces, namespace)
                    if not ns_ok:
                        duration_ms = (time.perf_counter() - start_time) * 1000
                        record = AuditRecord(
                            audit_id=audit_id,
                            timestamp=current_iso_timestamp(),
                            request_id=request_id,
                            principal_id=principal_id,
                            client_ip=client_ip,
                            transport=transport,
                            tool=tool_name,
                            namespace=namespace,
                            status="denied_forbidden",
                            duration_ms=duration_ms,
                            arguments_summary=arguments_summary,
                            error_message=ns_err,
                        )
                        self.audit_emitter.emit(record)
                        await _send_http_json(
                            send,
                            403,
                            {"error": "forbidden", "error_description": ns_err},
                        )
                        return

                    # RBAC Step 3: Authoritative Actor Binding
                    # Overwrite or bind the caller-declared actor to prevent agent identity spoofing
                    if isinstance(arguments, dict) and (
                        "actor" in arguments or tool_name in ("store", "annotate", "revise", "feedback")
                    ):
                        arguments["actor"] = principal_id
                        full_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                        _update_content_length(scope, len(full_body))

        sent_body = False

        async def new_receive() -> Message:
            nonlocal sent_body
            if not sent_body:
                sent_body = True
                return {"type": "http.request", "body": full_body, "more_body": False}
            return {"type": "http.request", "body": b"", "more_body": False}

        # 4. Wrap downstream send to capture output and status
        response_status = 200
        response_chunks: list[bytes] = []

        async def wrapped_send(message: Message) -> None:
            nonlocal response_status
            if message["type"] == "http.response.start":
                response_status = message.get("status", 200)
            elif message["type"] == "http.response.body":
                chunk = message.get("body", b"")
                if chunk:
                    response_chunks.append(chunk)
            await send(message)

        try:
            await self.app(scope, new_receive, wrapped_send)
        except Exception as exc:
            duration_ms = (time.perf_counter() - start_time) * 1000
            record = AuditRecord(
                audit_id=audit_id,
                timestamp=current_iso_timestamp(),
                request_id=request_id,
                principal_id=principal_id,
                client_ip=client_ip,
                transport=transport,
                tool=tool_name,
                namespace=namespace,
                status="error",
                duration_ms=duration_ms,
                arguments_summary=arguments_summary,
                error_message=str(exc),
            )
            self.audit_emitter.emit(record)
            raise

        duration_ms = (time.perf_counter() - start_time) * 1000
        output_summary: dict[str, Any] | None = None
        if response_chunks:
            try:
                raw_resp = b"".join(response_chunks).decode("utf-8")
                resp_json = json.loads(raw_resp)
                if isinstance(resp_json, dict) and "result" in resp_json:
                    output_summary = summarize_output(resp_json["result"])
                else:
                    output_summary = summarize_output(resp_json)
            except ValueError:
                pass

        status = "success" if response_status < 400 else "error"
        record = AuditRecord(
            audit_id=audit_id,
            timestamp=current_iso_timestamp(),
            request_id=request_id,
            principal_id=principal_id,
            client_ip=client_ip,
            transport=transport,
            tool=tool_name,
            namespace=namespace,
            status=status,
            duration_ms=duration_ms,
            arguments_summary=arguments_summary,
            output_summary=output_summary,
        )
        self.audit_emitter.emit(record)


def _extract_client_ip(scope: Scope) -> str | None:
    """Extract client IP address respecting reverse proxies."""
    for name, value in scope.get("headers", []):
        if name.lower() == b"x-forwarded-for":
            raw = value.decode("latin1", errors="replace")
            first_ip = raw.split(",")[0].strip()
            if first_ip:
                return first_ip
        if name.lower() == b"x-real-ip":
            raw = value.decode("latin1", errors="replace").strip()
            if raw:
                return raw

    client = scope.get("client")
    if client and isinstance(client, (list, tuple)) and client[0]:
        return str(client[0])
    return None


def _update_content_length(scope: Scope, length: int) -> None:
    """Update or append content-length header in ASGI scope."""
    headers: list[tuple[bytes, bytes]] = []
    for name, value in scope.get("headers", []):
        if name.lower() != b"content-length":
            headers.append((name, value))
    headers.append((b"content-length", str(length).encode("ascii")))
    scope["headers"] = headers


async def _send_http_json(
    send: Send,
    status_code: int,
    data: dict[str, Any],
    *,
    www_authenticate: bool = False,
) -> None:
    """Send a structured JSON error response."""
    body_bytes = json.dumps(data, separators=(",", ":")).encode("utf-8")
    headers: list[tuple[bytes, bytes]] = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body_bytes)).encode("ascii")),
    ]
    if www_authenticate:
        headers.append((b"www-authenticate", b'Bearer error="invalid_token"'))

    await send(
        {
            "type": "http.response.start",
            "status": status_code,
            "headers": headers,
        }
    )
    await send(
        {
            "type": "http.response.body",
            "body": body_bytes,
            "more_body": False,
        }
    )
