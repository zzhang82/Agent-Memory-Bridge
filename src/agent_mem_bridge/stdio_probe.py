from __future__ import annotations

import importlib.metadata
import os
import sys
from pathlib import Path
from typing import Any, Literal

from mcp import StdioServerParameters
from mcp.client import Client
from mcp.client.stdio import stdio_client
from mcp.types import Implementation

from .mcp_boundary import (
    DISCOVER_CACHE_HINT,
    MCP_LEGACY_TEST_VERSION,
    MCP_MODERN_VERSION,
    PUBLIC_TOOL_ORDER,
    TOOLS_LIST_CACHE_HINT,
    package_version,
)

PROBE_CLIENT = Implementation(name="agent-memory-bridge-operator-proof", version=package_version())


async def run_dual_stdio_probe(project_root: Path, runtime_dir: Path) -> dict[str, Any]:
    """Exercise modern and legacy stdio paths against separate disposable databases."""

    resolved_runtime = runtime_dir.resolve()
    resolved_runtime.mkdir(parents=True, exist_ok=True)
    modern = await _run_era_safely(project_root.resolve(), resolved_runtime / "modern", mode="auto")
    legacy = await _run_era_safely(project_root.resolve(), resolved_runtime / "legacy", mode="legacy")
    checks = [
        _check(
            "mcp_modern_stdio",
            bool(modern.get("ok")),
            "Modern server/discover, list, and 13-tool business flow completed.",
        ),
        _check(
            "mcp_legacy_stdio",
            bool(legacy.get("ok")),
            "Legacy initialize, list, and 13-tool business flow completed.",
        ),
    ]
    return {
        "ok": all(check["status"] == "pass" for check in checks),
        "mcp_sdk_version": importlib.metadata.version("mcp"),
        "project_root": str(project_root.resolve()),
        "runtime_dir": str(resolved_runtime),
        "checks": checks,
        "modern_stdio": modern,
        "legacy_stdio": legacy,
        "tool_count": modern.get("tool_count", 0),
        "tool_names": modern.get("tool_names", []),
        "db_path": modern.get("db_path"),
        "log_dir": modern.get("log_dir"),
    }


async def _run_era_safely(
    project_root: Path,
    runtime_dir: Path,
    *,
    mode: Literal["auto", "legacy"],
) -> dict[str, Any]:
    try:
        return await _run_era_probe(project_root, runtime_dir, mode=mode)
    except Exception as exc:
        return {
            "ok": False,
            "mode": "modern" if mode == "auto" else "legacy",
            "error_type": type(exc).__name__,
            "error": "isolated stdio protocol probe failed",
        }


async def _run_era_probe(
    project_root: Path,
    runtime_dir: Path,
    *,
    mode: Literal["auto", "legacy"],
) -> dict[str, Any]:
    runtime_dir.mkdir(parents=True, exist_ok=True)
    db_path = runtime_dir / "verify.db"
    log_dir = runtime_dir / "logs"
    era_name = "modern" if mode == "auto" else "legacy"
    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "agent_mem_bridge"],
        cwd=project_root,
        env={
            **os.environ,
            "AGENT_MEMORY_BRIDGE_HOME": str(runtime_dir),
            "AGENT_MEMORY_BRIDGE_DB_PATH": str(db_path),
            "AGENT_MEMORY_BRIDGE_LOG_DIR": str(log_dir),
            "AGENT_MEMORY_BRIDGE_DEFAULT_SOURCE_CLIENT": "verify-cli",
            "AGENT_MEMORY_BRIDGE_DEFAULT_CLIENT_TRANSPORT": "stdio",
        },
    )

    async with Client(stdio_client(server_params), mode=mode, client_info=PROBE_CLIENT) as client:
        protocol_version = client.protocol_version
        discover_result = client.session.discover_result
        initialize_result = client.session.initialize_result
        tools_result = await client.list_tools(cache_mode="bypass")
        tool_names = [tool.name for tool in tools_result.tools]
        result_types = [tools_result.result_type]
        business = await _exercise_all_tools(client, era_name, result_types)

    expected_version = MCP_MODERN_VERSION if mode == "auto" else MCP_LEGACY_TEST_VERSION
    discovery_ok = (
        discover_result is not None
        and initialize_result is None
        and discover_result.result_type == "complete"
        and discover_result.ttl_ms == DISCOVER_CACHE_HINT.ttl_ms
        and discover_result.cache_scope == DISCOVER_CACHE_HINT.scope
        if mode == "auto"
        else discover_result is None and initialize_result is not None
    )
    list_contract_ok = (
        tool_names == list(PUBLIC_TOOL_ORDER)
        and tools_result.ttl_ms == TOOLS_LIST_CACHE_HINT.ttl_ms
        and tools_result.cache_scope == TOOLS_LIST_CACHE_HINT.scope
    )
    checks = [
        _check("protocol_version", protocol_version == expected_version, f"Negotiated {expected_version}."),
        _check(
            "discovery" if mode == "auto" else "initialize",
            discovery_ok,
            "Used the expected protocol-era entry path.",
        ),
        _check("tool_surface", list_contract_ok, "Listed the canonical 13-tool surface in contract order."),
    ]
    if mode == "auto":
        checks.append(
            _check(
                "complete_results",
                all(result_type == "complete" for result_type in result_types),
                "All modern successful results were complete.",
            )
        )
    checks.append(_check("business_flow", bool(business.get("ok")), "Exercised every public tool over stdio."))
    report: dict[str, Any] = {
        "ok": all(check["status"] == "pass" for check in checks),
        "mode": era_name,
        "protocol_version": protocol_version,
        "discover": discover_result is not None,
        "initialize": initialize_result is not None,
        "tool_count": len(tool_names),
        "tool_names": tool_names,
        "checks": checks,
        "business_flow": business,
        "db_path": str(db_path),
        "log_dir": str(log_dir),
    }
    if mode == "auto" and discover_result is not None:
        report.update(
            {
                "result_type": discover_result.result_type,
                "ttl_ms": discover_result.ttl_ms,
                "cache_scope": discover_result.cache_scope,
                "tools_result_type": tools_result.result_type,
                "tools_ttl_ms": tools_result.ttl_ms,
                "tools_cache_scope": tools_result.cache_scope,
            }
        )
    return report


async def _exercise_all_tools(client: Client, era_name: str, result_types: list[str]) -> dict[str, Any]:
    namespace = f"verify:{era_name}"
    token = os.urandom(8).hex()
    memory_content = (
        f"record_type: learn\nclaim: MCP {era_name} stdio proof {token}.\nscope: project\nconfidence: observed\n"
    )

    first = await _call(
        client,
        "store",
        {
            "namespace": namespace,
            "content": memory_content,
            "kind": "memory",
            "tags": ["kind:learn", "check:verify"],
            "session_id": f"verify-{era_name}",
            "actor": "verify-cli",
            "source_app": "agent-memory-bridge verify",
        },
        result_types,
    )
    memory_id = str(first.get("id", ""))
    duplicate = await _call(
        client,
        "store",
        {
            "namespace": namespace,
            "content": memory_content,
            "kind": "memory",
            "tags": ["kind:learn", "check:verify"],
            "session_id": f"verify-{era_name}",
            "actor": "verify-cli",
            "source_app": "agent-memory-bridge verify",
        },
        result_types,
    )
    recall = await _call(
        client,
        "recall",
        {
            "namespace": namespace,
            "query": token,
            "kind": "memory",
            "limit": 5,
        },
        result_types,
    )
    receipt = recall.get("recall_receipt", {})
    feedback = await _call(
        client,
        "feedback",
        {
            "namespace": namespace,
            "recall_receipt": receipt.get("token"),
            "memory_id": memory_id,
            "result_rank": 1,
            "outcome": "helpful",
            "provenance": {"source_app": "agent-memory-bridge verify", "actor": "verify-cli"},
        },
        result_types,
    )
    browse = await _call(client, "browse", {"namespace": namespace, "kind": "memory", "limit": 5}, result_types)
    stats = await _call(client, "stats", {"namespace": namespace}, result_types)
    annotated = await _call(
        client,
        "annotate",
        {
            "id": memory_id,
            "tags": ["topic:protocol-proof"],
            "actor": "verify-cli",
        },
        result_types,
    )
    promoted = await _call(client, "promote", {"id": memory_id, "to_kind": "gotcha"}, result_types)
    revised = await _call(
        client,
        "revise",
        {
            "id": memory_id,
            "replacement_content": (
                "record_type: gotcha\n"
                f"claim: MCP {era_name} revised stdio proof {token}.\n"
                "trigger: protocol verification\n"
                "fix: keep negotiation evidence explicit\n"
                "confidence: observed\n"
            ),
            "actor": "verify-cli",
            "reason": "Exercise explicit revision over stdio.",
        },
        result_types,
    )
    exported = await _call(
        client,
        "export",
        {
            "namespace": namespace,
            "format": "json",
            "kind": "memory",
            "limit": 10,
        },
        result_types,
    )
    signal = await _call(
        client,
        "store",
        {
            "namespace": namespace,
            "content": f"MCP {era_name} signal proof {token}.",
            "kind": "signal",
            "tags": ["check:verify-signal"],
            "actor": "verify-cli",
            "ttl_seconds": 120,
        },
        result_types,
    )
    signal_id = str(signal.get("id", ""))
    claimed = await _call(
        client,
        "claim_signal",
        {
            "namespace": namespace,
            "consumer": "verify-worker",
            "signal_id": signal_id,
            "lease_seconds": 60,
        },
        result_types,
    )
    extended = await _call(
        client,
        "extend_signal_lease",
        {
            "id": signal_id,
            "consumer": "verify-worker",
            "lease_seconds": 60,
        },
        result_types,
    )
    acked = await _call(client, "ack_signal", {"id": signal_id, "consumer": "verify-worker"}, result_types)
    forgotten = await _call(client, "forget", {"id": signal_id}, result_types)

    ok = (
        bool(first.get("stored"))
        and bool(duplicate.get("duplicate"))
        and int(recall.get("count", 0)) >= 1
        and bool(feedback.get("stored"))
        and feedback.get("feedback_mode") == "shadow_only"
        and int(browse.get("count", 0)) >= 1
        and int(stats.get("total_count", 0)) >= 1
        and bool(annotated.get("changed"))
        and bool(promoted.get("changed"))
        and bool(revised.get("successor_id"))
        and isinstance(exported.get("content"), str)
        and bool(exported.get("content"))
        and bool(signal.get("stored"))
        and bool(claimed.get("claimed"))
        and bool(extended.get("extended"))
        and bool(acked.get("acked"))
        and bool(forgotten.get("deleted"))
    )
    return {
        "ok": ok,
        "memory_stored": bool(first.get("stored")),
        "duplicate_detected": bool(duplicate.get("duplicate")),
        "recall_count": int(recall.get("count", 0)),
        "feedback_shadow_only": feedback.get("feedback_mode") == "shadow_only",
        "browse_count": int(browse.get("count", 0)),
        "stats_total_count": int(stats.get("total_count", 0)),
        "annotated": bool(annotated.get("changed")),
        "promoted": bool(promoted.get("changed")),
        "revised": bool(revised.get("successor_id")),
        "export_nonempty": isinstance(exported.get("content"), str) and bool(exported.get("content")),
        "signal_claimed": bool(claimed.get("claimed")),
        "signal_extended": bool(extended.get("extended")),
        "signal_acked": bool(acked.get("acked")),
        "signal_forgotten": bool(forgotten.get("deleted")),
    }


async def _call(client: Client, name: str, arguments: dict[str, Any], result_types: list[str]) -> dict[str, Any]:
    result = await client.call_tool(name, arguments)
    result_types.append(result.result_type)
    payload = result.structured_content
    if result.is_error or not isinstance(payload, dict):
        raise RuntimeError(f"public tool probe failed: {name}")
    return payload


def _check(name: str, ok: bool, detail: str) -> dict[str, Any]:
    return {
        "name": name,
        "ok": ok,
        "status": "pass" if ok else "fail",
        "detail": detail,
    }
