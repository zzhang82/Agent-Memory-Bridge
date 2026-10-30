"""Comprehensive test suite for AMB Enterprise Gateway: Multi-Token Auth, RBAC, and Audit Logging."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from agent_mem_bridge.deployment_config import HttpTransportConfig
from agent_mem_bridge.gateway import (
    ADMIN,
    ALL_MCP_TOOLS,
    AuditRecord,
    GatewayConfig,
    Principal,
    hash_token,
    load_gateway_config,
)
from agent_mem_bridge.http_transport import build_http_app
from agent_mem_bridge.storage import MemoryStore


def _isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> MemoryStore:
    bridge_home = tmp_path / "bridge-home"
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_HOME", str(bridge_home))
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_DB_PATH", str(bridge_home / "bridge.db"))
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_LOG_DIR", str(bridge_home / "logs"))
    return MemoryStore.from_env()


def _jsonrpc_call(tool_name: str, arguments: dict[str, Any], req_id: int = 1) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments,
        },
    }


def _jsonrpc_list_tools(req_id: int = 1) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "method": "tools/list",
        "params": {},
    }


def _initialize_session(client: TestClient, token: str) -> dict[str, str]:
    headers = {
        "accept": "application/json, text/event-stream",
        "content-type": "application/json",
        "Authorization": f"Bearer {token}",
    }
    resp = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "1"},
            },
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    session_id = resp.headers.get("mcp-session-id")
    if session_id:
        headers["mcp-session-id"] = session_id
    return headers


def _create_sample_gateway_config() -> tuple[GatewayConfig, dict[str, str]]:
    tokens = {
        "admin": "admin-secret-token-12345678",
        "writer": "writer-secret-token-12345678",
        "reader": "reader-secret-token-12345678",
        "orchestrator": "orchestrator-secret-token-12345678",
    }
    principals = (
        Principal(
            principal_id="agent:admin-operator",
            token_hash=hash_token(tokens["admin"]),
            role=ADMIN,
            allowed_namespaces=("*",),
            rate_limit_rpm=0,
        ),
        Principal(
            principal_id="agent:writer-bob",
            token_hash=hash_token(tokens["writer"]),
            role="writer",
            allowed_namespaces=("project:allowed-proj", "domain:*"),
            rate_limit_rpm=10,
        ),
        Principal(
            principal_id="service:reader-auditor",
            token_hash=hash_token(tokens["reader"]),
            role="reader",
            allowed_namespaces=("project:*", "domain:*"),
            rate_limit_rpm=0,
        ),
        Principal(
            principal_id="system:orca-orchestrator",
            token_hash=hash_token(tokens["orchestrator"]),
            role="orchestrator",
            allowed_namespaces=("project:orchestrated-app",),
            rate_limit_rpm=0,
        ),
    )
    return GatewayConfig(principals=principals, auth_mode="rbac"), tokens


def test_gateway_config_loading_and_validation(tmp_path: Path) -> None:
    valid_raw = {
        "principals": [
            {
                "principal_id": "test:user",
                "token": "my-secret-token",
                "role": "writer",
                "allowed_namespaces": ["project:test"],
                "rate_limit_rpm": 60,
            }
        ]
    }
    cfg = load_gateway_config(valid_raw)
    assert len(cfg.principals) == 1
    assert cfg.principals[0].principal_id == "test:user"
    assert cfg.principals[0].token_hash == hash_token("my-secret-token")
    assert cfg.principals[0].role == "writer"
    assert cfg.principals[0].allowed_namespaces == ("project:test",)
    assert cfg.principals[0].rate_limit_rpm == 60

    # Test file loading
    cfg_file = tmp_path / "amb_tokens.json"
    cfg_file.write_text(json.dumps(valid_raw), encoding="utf-8")
    loaded = load_gateway_config(cfg_file)
    assert loaded.principals[0].principal_id == "test:user"

    # Validation errors
    with pytest.raises(ValueError, match="Duplicate principal_id"):
        load_gateway_config(
            {
                "principals": [
                    {"principal_id": "dup", "token": "t1", "role": "admin"},
                    {"principal_id": "dup", "token": "t2", "role": "admin"},
                ]
            }
        )

    with pytest.raises(ValueError, match="invalid; must be one of"):
        load_gateway_config(
            {
                "principals": [
                    {"principal_id": "p1", "token": "t1", "role": "super-hacker"},
                ]
            }
        )

    with pytest.raises(ValueError, match="Duplicate token_hash"):
        load_gateway_config(
            {
                "principals": [
                    {"principal_id": "p1", "token": "same-token", "role": "admin"},
                    {
                        "principal_id": "p2",
                        "token": "same-token",
                        "role": "reader",
                        "allowed_namespaces": ["project:*"],
                    },
                ]
            }
        )

    with pytest.raises(ValueError, match="must explicitly declare 'allowed_namespaces'"):
        load_gateway_config(
            {
                "principals": [
                    {"principal_id": "p1", "token": "t1", "role": "writer"},
                ]
            }
        )

    with pytest.raises(ValueError, match="must be a JSON array of strings"):
        load_gateway_config(
            {
                "principals": [
                    {"principal_id": "p1", "token": "t1", "role": "writer", "allowed_namespaces": "project:invalid"},
                ]
            }
        )

    with pytest.raises(ValueError, match="declared unconstrained or invalid pattern"):
        load_gateway_config(
            {
                "principals": [
                    {"principal_id": "p1", "token": "t1", "role": "writer", "allowed_namespaces": ["*"]},
                ]
            }
        )

    with pytest.raises(ValueError, match="Gateway authentication cannot be disabled"):
        GatewayConfig(
            principals=(
                Principal(
                    principal_id="p1",
                    token_hash=hash_token("t"),
                    role="admin",
                    allowed_namespaces=("*",),
                ),
            ),
            require_auth=False,
        ).validate()


def test_gateway_unauthenticated_and_invalid_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _isolated_store(tmp_path, monkeypatch)
    gw_config, _ = _create_sample_gateway_config()
    emitted_records: list[AuditRecord] = []

    app = build_http_app(
        HttpTransportConfig(
            allowed_hosts=("testserver",),
            allowed_origins=("http://testserver",),
            auth_mode="rbac",
            gateway_config_json=json.dumps(
                {
                    "principals": [
                        {
                            "principal_id": p.principal_id,
                            "token_hash": p.token_hash,
                            "role": p.role,
                            "allowed_namespaces": list(p.allowed_namespaces),
                        }
                        for p in gw_config.principals
                    ]
                }
            ),
        ),
        store=store,
        audit_sink=emitted_records.append,
    )

    headers = {"accept": "application/json", "content-type": "application/json"}

    with TestClient(app) as client:
        # 1. Missing Authorization header
        resp_absent = client.post(
            "/mcp",
            json=_jsonrpc_call("store", {"namespace": "project:allowed-proj", "content": "hi"}),
            headers=headers,
        )
        assert resp_absent.status_code == 401
        assert "Authentication required" in resp_absent.text
        assert emitted_records[-1].status == "denied_unauthenticated"
        assert emitted_records[-1].principal_id == "anonymous"

        # 2. Invalid Bearer token
        resp_invalid = client.post(
            "/mcp",
            json=_jsonrpc_call("store", {"namespace": "project:allowed-proj", "content": "hi"}),
            headers={**headers, "Authorization": "Bearer bad-token-xxx"},
        )
        assert resp_invalid.status_code == 401
        assert "Invalid Bearer token" in resp_invalid.text
        assert emitted_records[-1].status == "denied_unauthenticated"
        assert emitted_records[-1].principal_id == "unknown"


def test_writer_role_permissions_and_namespace_isolation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _isolated_store(tmp_path, monkeypatch)
    gw_config, tokens = _create_sample_gateway_config()
    emitted_records: list[AuditRecord] = []

    app = build_http_app(
        HttpTransportConfig(
            allowed_hosts=("testserver",),
            allowed_origins=("http://testserver",),
            auth_mode="rbac",
            gateway_config_json=json.dumps(
                {
                    "principals": [
                        {
                            "principal_id": p.principal_id,
                            "token_hash": p.token_hash,
                            "role": p.role,
                            "allowed_namespaces": list(p.allowed_namespaces),
                        }
                        for p in gw_config.principals
                    ]
                }
            ),
        ),
        store=store,
        audit_sink=emitted_records.append,
    )

    with TestClient(app) as client:
        writer_headers = _initialize_session(client, tokens["writer"])

        # Writer calling 'store' in allowed namespace -> 200 Success
        resp_store = client.post(
            "/mcp",
            json=_jsonrpc_call(
                "store",
                {"namespace": "project:allowed-proj", "content": "record 1", "actor": "fake-actor-must-be-overridden"},
            ),
            headers=writer_headers,
        )
        assert resp_store.status_code == 200
        assert resp_store.json()["result"] is not None
        last_audit = emitted_records[-1]
        assert last_audit.status == "success"
        assert last_audit.principal_id == "agent:writer-bob"
        assert last_audit.tool == "store"
        assert last_audit.namespace == "project:allowed-proj"

        # Verify that authoritative actor binding occurred
        recalled = store.recall("project:allowed-proj", query="")
        assert recalled["count"] == 1
        assert recalled["items"][0]["actor"] == "agent:writer-bob"

        # Writer calling 'store' in forbidden namespace -> 403 Forbidden
        resp_cross_tenant = client.post(
            "/mcp",
            json=_jsonrpc_call("store", {"namespace": "project:forbidden-corp", "content": "intrusion"}),
            headers=writer_headers,
        )
        assert resp_cross_tenant.status_code == 403
        assert "not permitted for principal" in resp_cross_tenant.text
        assert emitted_records[-1].status == "denied_forbidden"
        assert emitted_records[-1].namespace == "project:forbidden-corp"

        # Writer calling forbidden tool 'forget' -> 403 Forbidden
        resp_forbidden_tool = client.post(
            "/mcp",
            json=_jsonrpc_call("forget", {"id": recalled["items"][0]["id"]}),
            headers=writer_headers,
        )
        assert resp_forbidden_tool.status_code == 403
        assert "not permitted to call tool 'forget'" in resp_forbidden_tool.text
        assert emitted_records[-1].status == "denied_forbidden"
        assert emitted_records[-1].tool == "forget"

        # Writer calling wildcard matched namespace domain:test -> 200 Success
        resp_domain = client.post(
            "/mcp",
            json=_jsonrpc_call("store", {"namespace": "domain:mcp-gateway", "content": "domain knowledge"}),
            headers=writer_headers,
        )
        assert resp_domain.status_code == 200
        assert emitted_records[-1].status == "success"


def test_reader_role_blocked_from_mutations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _isolated_store(tmp_path, monkeypatch)
    store.store("project:demo", "existing memory", actor="admin")
    gw_config, tokens = _create_sample_gateway_config()
    emitted_records: list[AuditRecord] = []

    app = build_http_app(
        HttpTransportConfig(
            allowed_hosts=("testserver",),
            allowed_origins=("http://testserver",),
            auth_mode="rbac",
            gateway_config_json=json.dumps(
                {
                    "principals": [
                        {
                            "principal_id": p.principal_id,
                            "token_hash": p.token_hash,
                            "role": p.role,
                            "allowed_namespaces": list(p.allowed_namespaces),
                        }
                        for p in gw_config.principals
                    ]
                }
            ),
        ),
        store=store,
        audit_sink=emitted_records.append,
    )

    with TestClient(app) as client:
        reader_headers = _initialize_session(client, tokens["reader"])

        # Reader calling 'recall' -> 200 Success
        resp_recall = client.post(
            "/mcp",
            json=_jsonrpc_call("recall", {"namespace": "project:demo", "query": ""}),
            headers=reader_headers,
        )
        assert resp_recall.status_code == 200
        assert emitted_records[-1].status == "success"
        assert emitted_records[-1].tool == "recall"

        # Reader calling 'store' -> 403 Forbidden
        resp_store = client.post(
            "/mcp",
            json=_jsonrpc_call("store", {"namespace": "project:demo", "content": "write attempt"}),
            headers=reader_headers,
        )
        assert resp_store.status_code == 403
        assert "not permitted to call tool 'store'" in resp_store.text
        assert emitted_records[-1].status == "denied_forbidden"

        # Reader calling 'stats' -> 200 Success
        resp_stats = client.post(
            "/mcp",
            json=_jsonrpc_call("stats", {"namespace": "project:demo"}),
            headers=reader_headers,
        )
        assert resp_stats.status_code == 200
        assert emitted_records[-1].status == "success"
        assert emitted_records[-1].tool == "stats"


def test_orchestrator_role_lifecycle_and_signal_coordination(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _isolated_store(tmp_path, monkeypatch)
    gw_config, tokens = _create_sample_gateway_config()
    emitted_records: list[AuditRecord] = []

    app = build_http_app(
        HttpTransportConfig(
            allowed_hosts=("testserver",),
            allowed_origins=("http://testserver",),
            auth_mode="rbac",
            gateway_config_json=json.dumps(
                {
                    "principals": [
                        {
                            "principal_id": p.principal_id,
                            "token_hash": p.token_hash,
                            "role": p.role,
                            "allowed_namespaces": list(p.allowed_namespaces),
                        }
                        for p in gw_config.principals
                    ]
                }
            ),
        ),
        store=store,
        audit_sink=emitted_records.append,
    )

    with TestClient(app) as client:
        orch_headers = _initialize_session(client, tokens["orchestrator"])

        # Orchestrator calling 'begin_run' in allowed workspace -> 200 Success
        resp_begin = client.post(
            "/mcp",
            json=_jsonrpc_call(
                "begin_run",
                {
                    "workspace_key": "project:orchestrated-app",
                    "goal": "Verify payment gateway",
                    "idempotency_key": "orch-run-001",
                },
            ),
            headers=orch_headers,
        )
        assert resp_begin.status_code == 200
        assert emitted_records[-1].status == "success"
        assert emitted_records[-1].tool == "begin_run"
        assert emitted_records[-1].namespace == "project:orchestrated-app"

        # Orchestrator calling 'store' -> 403 Forbidden
        resp_store = client.post(
            "/mcp",
            json=_jsonrpc_call("store", {"namespace": "project:orchestrated-app", "content": "should be blocked"}),
            headers=orch_headers,
        )
        assert resp_store.status_code == 403
        assert "not permitted to call tool 'store'" in resp_store.text


def test_admin_role_can_execute_all_tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _isolated_store(tmp_path, monkeypatch)
    gw_config, tokens = _create_sample_gateway_config()
    emitted_records: list[AuditRecord] = []

    app = build_http_app(
        HttpTransportConfig(
            allowed_hosts=("testserver",),
            allowed_origins=("http://testserver",),
            auth_mode="rbac",
            gateway_config_json=json.dumps(
                {
                    "principals": [
                        {
                            "principal_id": p.principal_id,
                            "token_hash": p.token_hash,
                            "role": p.role,
                            "allowed_namespaces": list(p.allowed_namespaces),
                        }
                        for p in gw_config.principals
                    ]
                }
            ),
        ),
        store=store,
        audit_sink=emitted_records.append,
    )

    with TestClient(app) as client:
        admin_headers = _initialize_session(client, tokens["admin"])

        # Admin lists tools
        resp_list = client.post("/mcp", json=_jsonrpc_list_tools(), headers=admin_headers)
        assert resp_list.status_code == 200
        tools = resp_list.json()["result"]["tools"]
        assert len(tools) == 17
        tool_names = {t["name"] for t in tools}
        assert tool_names == ALL_MCP_TOOLS

        # Admin stores and then forgets
        resp_store = client.post(
            "/mcp",
            json=_jsonrpc_call("store", {"namespace": "global", "content": "temporary secret"}),
            headers=admin_headers,
        )
        assert resp_store.status_code == 200
        recalled = store.recall("global", query="")
        assert recalled["count"] == 1

        resp_forget = client.post(
            "/mcp",
            json=_jsonrpc_call("forget", {"id": recalled["items"][0]["id"]}),
            headers=admin_headers,
        )
        assert resp_forget.status_code == 200
        assert store.stats("global")["total_count"] == 0


def test_rate_limiting_enforcement(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _isolated_store(tmp_path, monkeypatch)
    emitted_records: list[AuditRecord] = []

    token = "rate-limited-token"
    config = GatewayConfig(
        principals=(
            Principal(
                principal_id="service:throttled-bot",
                token_hash=hash_token(token),
                role="reader",
                allowed_namespaces=("global", "project:*"),
                rate_limit_rpm=2,  # strictly 2 requests per minute
            ),
        ),
        auth_mode="rbac",
    )

    app = build_http_app(
        HttpTransportConfig(
            allowed_hosts=("testserver",),
            allowed_origins=("http://testserver",),
            auth_mode="rbac",
            gateway_config_json=json.dumps(
                {
                    "principals": [
                        {
                            "principal_id": p.principal_id,
                            "token_hash": p.token_hash,
                            "role": p.role,
                            "allowed_namespaces": list(p.allowed_namespaces),
                            "rate_limit_rpm": p.rate_limit_rpm,
                        }
                        for p in config.principals
                    ]
                }
            ),
        ),
        store=store,
        audit_sink=emitted_records.append,
    )

    with TestClient(app) as client:
        # Request 1: initialize (counts against 2 rpm)
        headers = _initialize_session(client, token)

        # Request 2: OK
        r2 = client.post("/mcp", json=_jsonrpc_call("stats", {"namespace": "global"}), headers=headers)
        assert r2.status_code == 200

        # Request 3: Exceeds 2 rpm -> 429
        r3 = client.post("/mcp", json=_jsonrpc_call("stats", {"namespace": "global"}), headers=headers)
        assert r3.status_code == 429
        assert "Rate limit exceeded" in r3.text
        assert emitted_records[-1].status == "denied_rate_limited"


def test_audit_record_serialization_and_timing() -> None:
    record = AuditRecord(
        audit_id="aud_123456",
        timestamp="2026-10-30T01:15:00.123Z",
        request_id="req_abcdef",
        principal_id="agent:cursor",
        client_ip="192.168.1.100",
        transport="http_mcp",
        tool="store",
        namespace="project:test",
        status="success",
        duration_ms=1.2345,
        arguments_summary={"kind": "memory", "content_bytes": 100},
        output_summary={"stored_id": "mem_1"},
    )
    d = record.to_dict()
    assert d["duration_ms"] == 1.23
    assert d["status"] == "success"
    json_str = record.to_json()
    assert '"audit_id":"aud_123456"' in json_str


def test_adversarial_id_mutation_cross_tenant_blocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _isolated_store(tmp_path, monkeypatch)
    # Target victim record created in forbidden-tenant
    victim = store.store("project:forbidden-tenant", "confidential victim data", actor="admin")
    gw_config, tokens = _create_sample_gateway_config()
    emitted_records: list[AuditRecord] = []

    app = build_http_app(
        HttpTransportConfig(
            allowed_hosts=("testserver",),
            allowed_origins=("http://testserver",),
            auth_mode="rbac",
            gateway_config_json=json.dumps(
                {
                    "principals": [
                        {
                            "principal_id": p.principal_id,
                            "token_hash": p.token_hash,
                            "role": p.role,
                            "allowed_namespaces": list(p.allowed_namespaces),
                        }
                        for p in gw_config.principals
                    ]
                }
            ),
        ),
        store=store,
        audit_sink=emitted_records.append,
    )

    with TestClient(app) as client:
        writer_headers = _initialize_session(client, tokens["writer"])

        # 1. Attacker attempts cross-tenant annotate on victim record
        resp_annotate = client.post(
            "/mcp",
            json=_jsonrpc_call("annotate", {"id": victim["id"], "title": "malicious annotation"}),
            headers=writer_headers,
        )
        assert resp_annotate.status_code == 403
        assert "outside principal's allowed scope" in resp_annotate.text
        assert emitted_records[-1].status == "denied_forbidden"

        # 2. Attacker attempts cross-tenant revise on victim record
        resp_revise = client.post(
            "/mcp",
            json=_jsonrpc_call("revise", {"id": victim["id"], "replacement_content": "tampered content"}),
            headers=writer_headers,
        )
        assert resp_revise.status_code == 403
        assert "outside principal's allowed scope" in resp_revise.text
        assert emitted_records[-1].status == "denied_forbidden"

        # Verify victim data in SQLite remains completely untampered
        recalled = store.recall("project:forbidden-tenant", query="")
        assert recalled["count"] == 1
        assert recalled["items"][0]["content"] == "confidential victim data"


def test_adversarial_signal_cross_tenant_blocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _isolated_store(tmp_path, monkeypatch)
    victim_signal = store.store("project:forbidden-tenant", "victim signal", kind="signal", actor="admin")
    gw_config, tokens = _create_sample_gateway_config()
    emitted_records: list[AuditRecord] = []

    app = build_http_app(
        HttpTransportConfig(
            allowed_hosts=("testserver",),
            allowed_origins=("http://testserver",),
            auth_mode="rbac",
            gateway_config_json=json.dumps(
                {
                    "principals": [
                        {
                            "principal_id": p.principal_id,
                            "token_hash": p.token_hash,
                            "role": p.role,
                            "allowed_namespaces": list(p.allowed_namespaces),
                        }
                        for p in gw_config.principals
                    ]
                }
            ),
        ),
        store=store,
        audit_sink=emitted_records.append,
    )

    with TestClient(app) as client:
        orch_headers = _initialize_session(client, tokens["orchestrator"])

        # 1. Orchestrator attempts cross-tenant ack_signal
        resp_ack = client.post(
            "/mcp",
            json=_jsonrpc_call("ack_signal", {"id": victim_signal["id"], "consumer": "agent:attacker"}),
            headers=orch_headers,
        )
        assert resp_ack.status_code == 403
        assert "outside principal's allowed scope" in resp_ack.text
        assert emitted_records[-1].status == "denied_forbidden"

        # 2. Orchestrator attempts cross-tenant extend_signal_lease
        resp_extend = client.post(
            "/mcp",
            json=_jsonrpc_call(
                "extend_signal_lease",
                {"id": victim_signal["id"], "consumer": "agent:attacker", "lease_seconds": 60},
            ),
            headers=orch_headers,
        )
        assert resp_extend.status_code == 403
        assert "outside principal's allowed scope" in resp_extend.text
        assert emitted_records[-1].status == "denied_forbidden"


def test_adversarial_session_hijacking_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _isolated_store(tmp_path, monkeypatch)
    gw_config, tokens = _create_sample_gateway_config()

    app = build_http_app(
        HttpTransportConfig(
            allowed_hosts=("testserver",),
            allowed_origins=("http://testserver",),
            auth_mode="rbac",
            gateway_config_json=json.dumps(
                {
                    "principals": [
                        {
                            "principal_id": p.principal_id,
                            "token_hash": p.token_hash,
                            "role": p.role,
                            "allowed_namespaces": list(p.allowed_namespaces),
                        }
                        for p in gw_config.principals
                    ]
                }
            ),
        ),
        store=store,
    )

    with TestClient(app) as client:
        # Client 1 initializes session as Writer
        writer_headers = _initialize_session(client, tokens["writer"])
        writer_session_id = writer_headers["mcp-session-id"]

        # Attacker with Reader token attempts to issue tools/call using Writer's session-id
        attacker_headers = {
            "accept": "application/json, text/event-stream",
            "content-type": "application/json",
            "Authorization": f"Bearer {tokens['reader']}",
            "mcp-session-id": writer_session_id,
        }
        resp = client.post(
            "/mcp",
            json=_jsonrpc_call("recall", {"namespace": "project:demo", "query": ""}),
            headers=attacker_headers,
        )
        # Upstream SDK Session Manager detects that session owner (Writer) != current user (Reader)
        assert resp.status_code in (400, 403, 404)


def test_adversarial_actor_and_provenance_tamper_sanitized(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _isolated_store(tmp_path, monkeypatch)
    gw_config, tokens = _create_sample_gateway_config()

    app = build_http_app(
        HttpTransportConfig(
            allowed_hosts=("testserver",),
            allowed_origins=("http://testserver",),
            auth_mode="rbac",
            gateway_config_json=json.dumps(
                {
                    "principals": [
                        {
                            "principal_id": p.principal_id,
                            "token_hash": p.token_hash,
                            "role": p.role,
                            "allowed_namespaces": list(p.allowed_namespaces),
                        }
                        for p in gw_config.principals
                    ]
                }
            ),
        ),
        store=store,
    )

    with TestClient(app) as client:
        writer_headers = _initialize_session(client, tokens["writer"])

        # Writer attempts to spoof actor and provenance
        resp = client.post(
            "/mcp",
            json=_jsonrpc_call(
                "store",
                {
                    "namespace": "project:allowed-proj",
                    "content": "spoof test",
                    "actor": "super-admin-root",
                    "provenance": {"actor": "super-admin-root"},
                },
            ),
            headers=writer_headers,
        )
        assert resp.status_code == 200

        recalled = store.recall("project:allowed-proj", query="")
        assert recalled["count"] == 1
        # Authoritative actor binding must overwrite the spoofed value
        assert recalled["items"][0]["actor"] == "agent:writer-bob"


def test_adversarial_tool_error_audit_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _isolated_store(tmp_path, monkeypatch)
    gw_config, tokens = _create_sample_gateway_config()
    emitted_records: list[AuditRecord] = []

    app = build_http_app(
        HttpTransportConfig(
            allowed_hosts=("testserver",),
            allowed_origins=("http://testserver",),
            auth_mode="rbac",
            gateway_config_json=json.dumps(
                {
                    "principals": [
                        {
                            "principal_id": p.principal_id,
                            "token_hash": p.token_hash,
                            "role": p.role,
                            "allowed_namespaces": list(p.allowed_namespaces),
                        }
                        for p in gw_config.principals
                    ]
                }
            ),
        ),
        store=store,
        audit_sink=emitted_records.append,
    )

    with TestClient(app) as client:
        admin_headers = _initialize_session(client, tokens["admin"])

        # Admin calls a tool with invalid limit (-1) to cause a tool-level validation error
        resp = client.post(
            "/mcp",
            json=_jsonrpc_call("recall", {"namespace": "global", "limit": -1}),
            headers=admin_headers,
        )
        assert resp.status_code == 200
        # Audit record must correctly capture tool_error rather than false success
        assert emitted_records[-1].status == "tool_error"


def test_adversarial_conflicting_scope_arguments_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _isolated_store(tmp_path, monkeypatch)
    gw_config, tokens = _create_sample_gateway_config()

    app = build_http_app(
        HttpTransportConfig(
            allowed_hosts=("testserver",),
            allowed_origins=("http://testserver",),
            auth_mode="rbac",
            gateway_config_json=json.dumps(
                {
                    "principals": [
                        {
                            "principal_id": p.principal_id,
                            "token_hash": p.token_hash,
                            "role": p.role,
                            "allowed_namespaces": list(p.allowed_namespaces),
                        }
                        for p in gw_config.principals
                    ]
                }
            ),
        ),
        store=store,
    )

    with TestClient(app) as client:
        orch_headers = _initialize_session(client, tokens["orchestrator"])

        # Orchestrator tries to create run in forbidden workspace by injecting a conflicting "namespace" parameter
        resp = client.post(
            "/mcp",
            json=_jsonrpc_call(
                "begin_run",
                {
                    "workspace_key": "project:forbidden-tenant",
                    "namespace": "project:orchestrated-app",
                    "goal": "Exploit run",
                    "idempotency_key": "exploit-run-001",
                },
            ),
            headers=orch_headers,
        )
        assert resp.status_code == 403
        assert "unexpected 'namespace' parameter" in resp.text

        # Verify no run was created in SQLite
        with sqlite3.connect(store.db_path) as conn:
            runs = conn.execute("SELECT count(*) FROM agent_runs").fetchone()[0]
            assert runs == 0


def test_legitimate_writer_annotate_and_revise_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _isolated_store(tmp_path, monkeypatch)
    gw_config, tokens = _create_sample_gateway_config()

    app = build_http_app(
        HttpTransportConfig(
            allowed_hosts=("testserver",),
            allowed_origins=("http://testserver",),
            auth_mode="rbac",
            gateway_config_json=json.dumps(
                {
                    "principals": [
                        {
                            "principal_id": p.principal_id,
                            "token_hash": p.token_hash,
                            "role": p.role,
                            "allowed_namespaces": list(p.allowed_namespaces),
                        }
                        for p in gw_config.principals
                    ]
                }
            ),
        ),
        store=store,
    )

    with TestClient(app) as client:
        writer_headers = _initialize_session(client, tokens["writer"])

        # 1. Writer stores initial memory in allowed namespace
        resp_store = client.post(
            "/mcp",
            json=_jsonrpc_call(
                "store",
                {
                    "namespace": "project:allowed-proj",
                    "content": "Initial fact",
                    "title": "Initial Title",
                },
            ),
            headers=writer_headers,
        )
        assert resp_store.status_code == 200
        mem_id = store.recall("project:allowed-proj", query="")["items"][0]["id"]

        # 2. Writer legally annotates with valid standard provenance
        resp_annotate = client.post(
            "/mcp",
            json=_jsonrpc_call(
                "annotate",
                {
                    "id": mem_id,
                    "title": "Enriched Title",
                    "provenance": {
                        "session_id": "sess-writer-1",
                        "correlation_id": "corr-writer-1",
                    },
                },
            ),
            headers=writer_headers,
        )
        assert resp_annotate.status_code == 200
        assert resp_annotate.json()["result"]["isError"] is False

        # 3. Writer legally revises with valid replacement content and standard provenance
        resp_revise = client.post(
            "/mcp",
            json=_jsonrpc_call(
                "revise",
                {
                    "id": mem_id,
                    "replacement_content": "Superseding durable fact",
                    "reason": "Updated decision",
                    "provenance": {
                        "session_id": "sess-writer-2",
                    },
                },
            ),
            headers=writer_headers,
        )
        assert resp_revise.status_code == 200
        assert resp_revise.json()["result"]["isError"] is False

        recalled = store.recall("project:allowed-proj", query="")
        assert recalled["count"] == 1
        assert "Superseding durable fact" in recalled["items"][0]["content"]
        assert recalled["items"][0]["actor"] == "agent:writer-bob"


def test_legitimate_orchestrator_run_lifecycle_and_provenance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _isolated_store(tmp_path, monkeypatch)
    gw_config, tokens = _create_sample_gateway_config()

    app = build_http_app(
        HttpTransportConfig(
            allowed_hosts=("testserver",),
            allowed_origins=("http://testserver",),
            auth_mode="rbac",
            gateway_config_json=json.dumps(
                {
                    "principals": [
                        {
                            "principal_id": p.principal_id,
                            "token_hash": p.token_hash,
                            "role": p.role,
                            "allowed_namespaces": list(p.allowed_namespaces),
                        }
                        for p in gw_config.principals
                    ]
                }
            ),
        ),
        store=store,
    )

    with TestClient(app) as client:
        orch_headers = _initialize_session(client, tokens["orchestrator"])

        # 1. begin_run with declared agent_id and standard provenance
        resp_begin = client.post(
            "/mcp",
            json=_jsonrpc_call(
                "begin_run",
                {
                    "workspace_key": "project:orchestrated-app",
                    "goal": "Autonomous deployment validation",
                    "idempotency_key": "legit-orch-001",
                    "agent_id": "worker:eli-implementer",
                    "provenance": {
                        "session_id": "run-sess-001",
                        "correlation_id": "run-corr-001",
                    },
                },
            ),
            headers=orch_headers,
        )
        assert resp_begin.status_code == 200
        run_data = json.loads(resp_begin.json()["result"]["content"][0]["text"])
        run_id = run_data["run_id"]

        # 2. record_run_event: complete the root work item to allow run completion
        resp_event = client.post(
            "/mcp",
            json=_jsonrpc_call(
                "record_run_event",
                {
                    "workspace_key": "project:orchestrated-app",
                    "run_id": run_id,
                    "work_item_id": run_data["root_work_item_id"],
                    "event_type": "work_item_completed",
                    "summary": "Stage 1 tests passed",
                    "idempotency_key": "event-key-001",
                    "agent_id": "worker:eli-implementer",
                    "provenance": {
                        "session_id": "run-sess-001",
                    },
                },
            ),
            headers=orch_headers,
        )
        assert resp_event.status_code == 200
        assert resp_event.json()["result"]["isError"] is False

        # 3. complete_run
        resp_complete = client.post(
            "/mcp",
            json=_jsonrpc_call(
                "complete_run",
                {
                    "workspace_key": "project:orchestrated-app",
                    "run_id": run_id,
                    "outcome": "unverified",
                    "evaluator_type": "agent",
                    "idempotency_key": "outcome-key-001",
                    "provenance": {
                        "source_app": "orchestrator-test",
                    },
                },
            ),
            headers=orch_headers,
        )
        assert resp_complete.status_code == 200
        assert resp_complete.json()["result"]["isError"] is False

        # 4. Verify durable rows exist in SQLite authority tables
        with sqlite3.connect(store.db_path) as conn:
            run_row = conn.execute(
                "SELECT run_id, workspace_key FROM agent_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            assert run_row is not None
            assert run_row[1] == "project:orchestrated-app"

            event_row = conn.execute(
                "SELECT event_type, summary FROM run_events WHERE run_id = ?", (run_id,)
            ).fetchone()
            assert event_row is not None
            assert event_row[0] == "work_item_completed"
            assert event_row[1] == "Stage 1 tests passed"

            outcome_row = conn.execute(
                "SELECT outcome_type, evaluator_type FROM run_outcomes WHERE run_id = ?", (run_id,)
            ).fetchone()
            assert outcome_row is not None
            assert outcome_row[0] == "unverified"
            assert outcome_row[1] == "agent"


def test_database_unavailable_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _isolated_store(tmp_path, monkeypatch)
    gw_config, tokens = _create_sample_gateway_config()

    # Point db_path to a non-existent file
    bogus_db = tmp_path / "non-existent" / "missing.db"
    app = build_http_app(
        HttpTransportConfig(
            allowed_hosts=("testserver",),
            allowed_origins=("http://testserver",),
            auth_mode="rbac",
            gateway_config_json=json.dumps(
                {
                    "principals": [
                        {
                            "principal_id": p.principal_id,
                            "token_hash": p.token_hash,
                            "role": p.role,
                            "allowed_namespaces": list(p.allowed_namespaces),
                        }
                        for p in gw_config.principals
                    ]
                }
            ),
        ),
        store=store,
        readiness_db_path=bogus_db,
    )

    with TestClient(app) as client:
        writer_headers = _initialize_session(client, tokens["writer"])

        # When authority database is unavailable for ID dereferencing, fail closed
        resp = client.post(
            "/mcp",
            json=_jsonrpc_call("annotate", {"id": "any-id", "title": "fail closed"}),
            headers=writer_headers,
        )
        assert resp.status_code == 403
        assert "Authorization verification failed" in resp.text


def test_non_admin_wildcard_combinations_rejected() -> None:
    # Reject wildcard patterns that bypass namespace scoping
    for pattern in ("*", "**", "*:*", "*.*", "?*", "[!x]*", "project:"):
        with pytest.raises(ValueError, match="declared unconstrained or invalid pattern|missing target subpattern"):
            Principal(
                principal_id="test:writer",
                token_hash=hash_token("token-secret"),
                role="writer",
                allowed_namespaces=(pattern,),
            ).validate()

    # Valid prefixed patterns validate cleanly
    for valid_pattern in ("project:*", "project:app-*", "domain:auth", "domain:*", "global"):
        Principal(
            principal_id="test:writer",
            token_hash=hash_token("token-secret"),
            role="writer",
            allowed_namespaces=(valid_pattern,),
        ).validate()


def test_signal_lease_consumer_isolation_and_impersonation_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _isolated_store(tmp_path, monkeypatch)
    # Provision two orchestrators where Principal A's ID is an exact prefix of Principal B's ID
    tokens = {
        "orch_a": "orch-a-token-12345678",
        "orch_b": "orch-b-token-12345678",
    }
    gw_config = GatewayConfig(
        principals=(
            Principal(
                principal_id="system:orchestrator",
                token_hash=hash_token(tokens["orch_a"]),
                role="orchestrator",
                allowed_namespaces=("project:orchestrated-app",),
            ),
            Principal(
                principal_id="system:orchestrator:child",
                token_hash=hash_token(tokens["orch_b"]),
                role="orchestrator",
                allowed_namespaces=("project:orchestrated-app",),
            ),
        ),
        auth_mode="rbac",
    )

    app = build_http_app(
        HttpTransportConfig(
            allowed_hosts=("testserver",),
            allowed_origins=("http://testserver",),
            auth_mode="rbac",
            gateway_config_json=json.dumps(
                {
                    "principals": [
                        {
                            "principal_id": p.principal_id,
                            "token_hash": p.token_hash,
                            "role": p.role,
                            "allowed_namespaces": list(p.allowed_namespaces),
                        }
                        for p in gw_config.principals
                    ]
                }
            ),
        ),
        store=store,
    )

    # Insert a signal in project:orchestrated-app
    sig = store.store("project:orchestrated-app", "coordination task", kind="signal", actor="admin")
    sig_id = sig["id"]

    with TestClient(app) as client:
        headers_a = _initialize_session(client, tokens["orch_a"])
        headers_b = _initialize_session(client, tokens["orch_b"])

        # 1. Orchestrator B ("system:orchestrator:child") claims the signal
        resp_claim_b = client.post(
            "/mcp",
            json=_jsonrpc_call(
                "claim_signal",
                {
                    "namespace": "project:orchestrated-app",
                    "signal_id": sig_id,
                    "consumer": "worker-1",
                },
            ),
            headers=headers_b,
        )
        assert resp_claim_b.status_code == 200
        assert resp_claim_b.json()["result"]["isError"] is False

        # Verify SQLite has collision-proof prefix bound to Orchestrator B's digest
        with sqlite3.connect(store.db_path) as conn:
            claimed_by_b = conn.execute("SELECT claimed_by FROM memories WHERE id = ?", (sig_id,)).fetchone()[0]
            assert claimed_by_b.startswith("amb:p:")

        # 2. Orchestrator A ("system:orchestrator", whose ID is a prefix of B's) attempts to extend lease on B's signal
        resp_extend_a = client.post(
            "/mcp",
            json=_jsonrpc_call(
                "extend_signal_lease",
                {
                    "id": sig_id,
                    "consumer": "worker-1",
                    "lease_seconds": 60,
                },
            ),
            headers=headers_a,
        )
        assert resp_extend_a.status_code == 403
        assert "claimed by another consumer" in resp_extend_a.text

        # 3. Orchestrator A attempts to ACK B's signal (even passing B's exact claimed_by string)
        resp_ack_a = client.post(
            "/mcp",
            json=_jsonrpc_call(
                "ack_signal",
                {
                    "id": sig_id,
                    "consumer": claimed_by_b,
                },
            ),
            headers=headers_a,
        )
        assert resp_ack_a.status_code == 403
        assert "claimed by another consumer" in resp_ack_a.text

        # 4. Orchestrator B extends lease legitimately
        resp_extend_b = client.post(
            "/mcp",
            json=_jsonrpc_call(
                "extend_signal_lease",
                {
                    "id": sig_id,
                    "consumer": "worker-1",
                    "lease_seconds": 60,
                },
            ),
            headers=headers_b,
        )
        assert resp_extend_b.status_code == 200
        assert resp_extend_b.json()["result"]["isError"] is False

        # 5. Orchestrator B ACKs legitimately
        resp_ack_b = client.post(
            "/mcp",
            json=_jsonrpc_call(
                "ack_signal",
                {
                    "id": sig_id,
                    "consumer": "worker-1",
                },
            ),
            headers=headers_b,
        )
        assert resp_ack_b.status_code == 200
        assert resp_ack_b.json()["result"]["isError"] is False

        # Verify SQLite has signal_status='acked'
        with sqlite3.connect(store.db_path) as conn:
            sig_status = conn.execute("SELECT signal_status FROM memories WHERE id = ?", (sig_id,)).fetchone()[0]
            assert sig_status == "acked"


def test_legacy_unprefixed_signal_lease_compatibility(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _isolated_store(tmp_path, monkeypatch)
    gw_config, tokens = _create_sample_gateway_config()

    # Create pre-existing legacy claimed signal using store then updating SQLite directly
    legacy_sig = store.store("project:orchestrated-app", "legacy task content", kind="signal", actor="admin")
    legacy_sig_id = legacy_sig["id"]
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            """
            UPDATE memories
            SET signal_status = 'claimed',
                claimed_by = 'legacy-worker-1',
                claimed_at = datetime('now'),
                lease_expires_at = datetime('now', '+300 seconds')
            WHERE id = ?
            """,
            (legacy_sig_id,),
        )
        conn.commit()

    app = build_http_app(
        HttpTransportConfig(
            allowed_hosts=("testserver",),
            allowed_origins=("http://testserver",),
            auth_mode="rbac",
            gateway_config_json=json.dumps(
                {
                    "principals": [
                        {
                            "principal_id": p.principal_id,
                            "token_hash": p.token_hash,
                            "role": p.role,
                            "allowed_namespaces": list(p.allowed_namespaces),
                        }
                        for p in gw_config.principals
                    ]
                }
            ),
        ),
        store=store,
    )

    with TestClient(app) as client:
        orch_headers = _initialize_session(client, tokens["orchestrator"])
        admin_headers = _initialize_session(client, tokens["admin"])

        # 1. Non-admin orchestrator is blocked from hijacking unauthenticated/legacy claims
        resp_extend_orch = client.post(
            "/mcp",
            json=_jsonrpc_call(
                "extend_signal_lease",
                {
                    "id": legacy_sig_id,
                    "consumer": "legacy-worker-1",
                    "lease_seconds": 60,
                },
            ),
            headers=orch_headers,
        )
        assert resp_extend_orch.status_code == 403
        assert "unauthenticated or external lease" in resp_extend_orch.text

        resp_ack_orch = client.post(
            "/mcp",
            json=_jsonrpc_call(
                "ack_signal",
                {
                    "id": legacy_sig_id,
                },
            ),
            headers=orch_headers,
        )
        assert resp_ack_orch.status_code == 403
        assert "unauthenticated or external lease" in resp_ack_orch.text

        # 2. Administrator has global authority to safely complete legacy claims
        resp_ack_admin = client.post(
            "/mcp",
            json=_jsonrpc_call(
                "ack_signal",
                {
                    "id": legacy_sig_id,
                    "consumer": "legacy-worker-1",
                },
            ),
            headers=admin_headers,
        )
        assert resp_ack_admin.status_code == 200
        assert resp_ack_admin.json()["result"]["isError"] is False

        with sqlite3.connect(store.db_path) as conn:
            sig_status = conn.execute("SELECT signal_status FROM memories WHERE id = ?", (legacy_sig_id,)).fetchone()[0]
            assert sig_status == "acked"


def test_feedback_provenance_actor_binding(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _isolated_store(tmp_path, monkeypatch)
    gw_config, tokens = _create_sample_gateway_config()

    app = build_http_app(
        HttpTransportConfig(
            allowed_hosts=("testserver",),
            allowed_origins=("http://testserver",),
            auth_mode="rbac",
            gateway_config_json=json.dumps(
                {
                    "principals": [
                        {
                            "principal_id": p.principal_id,
                            "token_hash": p.token_hash,
                            "role": p.role,
                            "allowed_namespaces": list(p.allowed_namespaces),
                        }
                        for p in gw_config.principals
                    ]
                }
            ),
        ),
        store=store,
    )

    with TestClient(app) as client:
        writer_headers = _initialize_session(client, tokens["writer"])

        # 1. Store a memory
        resp_store = client.post(
            "/mcp",
            json=_jsonrpc_call(
                "store",
                {
                    "namespace": "project:allowed-proj",
                    "content": "Recallable target memory for feedback evaluation",
                    "title": "Feedback Eval Target",
                },
            ),
            headers=writer_headers,
        )
        assert resp_store.status_code == 200
        mem_id = store.recall("project:allowed-proj", query="")["items"][0]["id"]

        # 2. Recall memory with kind='memory' to obtain signed recall receipt token
        resp_recall = client.post(
            "/mcp",
            json=_jsonrpc_call(
                "recall",
                {
                    "namespace": "project:allowed-proj",
                    "query": "Recallable target memory",
                    "kind": "memory",
                },
            ),
            headers=writer_headers,
        )
        assert resp_recall.status_code == 200
        recall_json = json.loads(resp_recall.json()["result"]["content"][0]["text"])
        receipt_token = recall_json["recall_receipt"]["token"]

        # 3. Call feedback without provenance
        resp_feedback_1 = client.post(
            "/mcp",
            json=_jsonrpc_call(
                "feedback",
                {
                    "namespace": "project:allowed-proj",
                    "recall_receipt": receipt_token,
                    "memory_id": mem_id,
                    "result_rank": 1,
                    "outcome": "helpful",
                },
            ),
            headers=writer_headers,
        )
        assert resp_feedback_1.status_code == 200
        assert resp_feedback_1.json()["result"]["isError"] is False

        # Verify in SQLite retrieval_feedback table that actor was bound to writer principal
        with sqlite3.connect(store.db_path) as conn:
            fb_actor = conn.execute(
                "SELECT actor FROM retrieval_feedback WHERE memory_id = ? ORDER BY feedback_id DESC",
                (mem_id,),
            ).fetchone()[0]
            assert fb_actor == "agent:writer-bob"


def test_claim_signal_extra_id_parameter_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _isolated_store(tmp_path, monkeypatch)
    gw_config, tokens = _create_sample_gateway_config()

    app = build_http_app(
        HttpTransportConfig(
            allowed_hosts=("testserver",),
            allowed_origins=("http://testserver",),
            auth_mode="rbac",
            gateway_config_json=json.dumps(
                {
                    "principals": [
                        {
                            "principal_id": p.principal_id,
                            "token_hash": p.token_hash,
                            "role": p.role,
                            "allowed_namespaces": list(p.allowed_namespaces),
                        }
                        for p in gw_config.principals
                    ]
                }
            ),
        ),
        store=store,
    )

    with TestClient(app) as client:
        orch_headers = _initialize_session(client, tokens["orchestrator"])

        # Caller provides signal_id PLUS rogue "id" parameter to confuse authorization
        resp = client.post(
            "/mcp",
            json=_jsonrpc_call(
                "claim_signal",
                {
                    "namespace": "project:orchestrated-app",
                    "signal_id": "sig-001",
                    "id": "conflicting-record-id",
                    "consumer": "worker-1",
                },
            ),
            headers=orch_headers,
        )
        assert resp.status_code == 403
        assert "unexpected ID parameter 'id'" in resp.text


def test_recall_actor_query_filter_preserved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _isolated_store(tmp_path, monkeypatch)
    # Store records with different authors
    store.store("project:allowed-proj", "Bob's thought", actor="agent:writer-bob")
    store.store("project:allowed-proj", "Alice's observation", actor="agent:researcher-alice")

    gw_config, tokens = _create_sample_gateway_config()

    app = build_http_app(
        HttpTransportConfig(
            allowed_hosts=("testserver",),
            allowed_origins=("http://testserver",),
            auth_mode="rbac",
            gateway_config_json=json.dumps(
                {
                    "principals": [
                        {
                            "principal_id": p.principal_id,
                            "token_hash": p.token_hash,
                            "role": p.role,
                            "allowed_namespaces": list(p.allowed_namespaces),
                        }
                        for p in gw_config.principals
                    ]
                }
            ),
        ),
        store=store,
    )

    with TestClient(app) as client:
        writer_headers = _initialize_session(client, tokens["writer"])

        # Writer Bob queries for records written by Alice
        resp = client.post(
            "/mcp",
            json=_jsonrpc_call(
                "recall",
                {
                    "namespace": "project:allowed-proj",
                    "actor": "agent:researcher-alice",
                    "query": "",
                },
            ),
            headers=writer_headers,
        )
        assert resp.status_code == 200
        result_text = resp.json()["result"]["content"][0]["text"]
        result_data = json.loads(result_text)

        # Actor filter must NOT have been overwritten to agent:writer-bob!
        assert result_data["count"] == 1
        assert result_data["items"][0]["content"] == "Alice's observation"
        assert result_data["items"][0]["actor"] == "agent:researcher-alice"


def test_large_response_truncated_audit_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _isolated_store(tmp_path, monkeypatch)
    # Store multiple memories to create a response that exceeds 4096 bytes
    for i in range(30):
        store.store("global", f"Large memory content row {i:03d} " + ("x" * 200), actor="admin")

    gw_config, tokens = _create_sample_gateway_config()
    emitted_records: list[AuditRecord] = []

    app = build_http_app(
        HttpTransportConfig(
            allowed_hosts=("testserver",),
            allowed_origins=("http://testserver",),
            auth_mode="rbac",
            gateway_config_json=json.dumps(
                {
                    "principals": [
                        {
                            "principal_id": p.principal_id,
                            "token_hash": p.token_hash,
                            "role": p.role,
                            "allowed_namespaces": list(p.allowed_namespaces),
                        }
                        for p in gw_config.principals
                    ]
                }
            ),
        ),
        store=store,
        audit_sink=emitted_records.append,
    )

    with TestClient(app) as client:
        admin_headers = _initialize_session(client, tokens["admin"])

        # Recall all 30 memories -> response exceeds 4KB cap
        resp = client.post(
            "/mcp",
            json=_jsonrpc_call("recall", {"namespace": "global", "limit": 30}),
            headers=admin_headers,
        )
        assert resp.status_code == 200
        last_audit = emitted_records[-1]
        assert last_audit.output_summary is not None
        assert last_audit.output_summary.get("truncated") is True
        # Since response was truncated at 4KB and not an error, status is unknown_truncated
        assert last_audit.status == "unknown_truncated"


def test_admin_can_manage_other_principal_gateway_leases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _isolated_store(tmp_path, monkeypatch)
    gw_config, tokens = _create_sample_gateway_config()

    app = build_http_app(
        HttpTransportConfig(
            allowed_hosts=("testserver",),
            allowed_origins=("http://testserver",),
            auth_mode="rbac",
            gateway_config_json=json.dumps(
                {
                    "principals": [
                        {
                            "principal_id": p.principal_id,
                            "token_hash": p.token_hash,
                            "role": p.role,
                            "allowed_namespaces": list(p.allowed_namespaces),
                        }
                        for p in gw_config.principals
                    ]
                }
            ),
        ),
        store=store,
    )

    sig = store.store("project:orchestrated-app", "task for admin override", kind="signal", actor="admin")
    sig_id = sig["id"]

    with TestClient(app) as client:
        orch_headers = _initialize_session(client, tokens["orchestrator"])
        admin_headers = _initialize_session(client, tokens["admin"])

        # 1. Orchestrator claims the signal with gateway digest prefix
        resp_claim = client.post(
            "/mcp",
            json=_jsonrpc_call(
                "claim_signal",
                {
                    "namespace": "project:orchestrated-app",
                    "signal_id": sig_id,
                    "consumer": "worker-1",
                },
            ),
            headers=orch_headers,
        )
        assert resp_claim.status_code == 200
        assert resp_claim.json()["result"]["isError"] is False

        # 2. Administrator extends lease on orchestrator's gateway-prefixed claim
        resp_extend_admin = client.post(
            "/mcp",
            json=_jsonrpc_call(
                "extend_signal_lease",
                {
                    "id": sig_id,
                    "lease_seconds": 60,
                },
            ),
            headers=admin_headers,
        )
        assert resp_extend_admin.status_code == 200
        assert resp_extend_admin.json()["result"]["isError"] is False

        # 3. Administrator ACKs orchestrator's gateway-prefixed claim
        resp_ack_admin = client.post(
            "/mcp",
            json=_jsonrpc_call(
                "ack_signal",
                {
                    "id": sig_id,
                },
            ),
            headers=admin_headers,
        )
        assert resp_ack_admin.status_code == 200
        assert resp_ack_admin.json()["result"]["isError"] is False

        # Verify SQLite has signal_status='acked'
        with sqlite3.connect(store.db_path) as conn:
            sig_status = conn.execute("SELECT signal_status FROM memories WHERE id = ?", (sig_id,)).fetchone()[0]
            assert sig_status == "acked"


def test_middleware_initialization_requires_db_path() -> None:
    from agent_mem_bridge.gateway import (
        AuditEmitter,
        EnterpriseGatewayMiddleware,
        GatewayConfig,
        MultiTokenVerifier,
        Principal,
    )

    config = GatewayConfig(
        principals=(
            Principal(
                principal_id="admin:root",
                token_hash="sha256:" + "0" * 64,
                role="admin",
                allowed_namespaces=("*",),
            ),
        ),
        auth_mode="rbac",
    )
    with pytest.raises(ValueError, match="EnterpriseGatewayMiddleware requires db_path"):
        EnterpriseGatewayMiddleware(
            app=lambda *args: None,
            config=config,
            verifier=MultiTokenVerifier(config),
            audit_emitter=AuditEmitter(sink=lambda r: None),
            db_path=None,  # type: ignore[arg-type]
        )
