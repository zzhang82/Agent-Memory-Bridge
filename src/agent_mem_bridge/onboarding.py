from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from typing import Literal

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .paths import resolve_bridge_db_path, resolve_bridge_home, resolve_bridge_log_dir, resolve_config_path


TOOL_NAMES = {
    "store",
    "recall",
    "browse",
    "stats",
    "forget",
    "claim_signal",
    "extend_signal_lease",
    "ack_signal",
    "promote",
    "export",
}


def run_doctor(
    *,
    include_stdio: bool = False,
    project_root: Path | None = None,
) -> dict[str, Any]:
    resolved_project_root = (project_root or Path(__file__).resolve().parents[2]).resolve()
    config_path = resolve_config_path()
    bridge_home = resolve_bridge_home()
    db_path = resolve_bridge_db_path()
    log_dir = resolve_bridge_log_dir()

    checks = [
        _build_check(
            name="python_version",
            ok=sys.version_info >= (3, 11),
            status="pass" if sys.version_info >= (3, 11) else "fail",
            detail=f"Running Python {sys.version.split()[0]}",
        ),
        _sqlite_fts5_check(),
        _config_path_check(config_path),
        _path_writable_check("bridge_home_parent_writable", bridge_home),
        _path_writable_check("db_parent_writable", db_path),
        _path_writable_check("log_dir_parent_writable", log_dir),
        _build_check(
            name="resolved_defaults",
            ok=True,
            status="pass",
            detail="Resolved bridge paths and defaults successfully.",
            paths={
                "project_root": str(resolved_project_root),
                "bridge_home": str(bridge_home),
                "config_path": str(config_path),
                "db_path": str(db_path),
                "log_dir": str(log_dir),
            },
        ),
    ]

    stdio_check: dict[str, Any] | None = None
    if include_stdio:
        stdio_report = run_verify(project_root=resolved_project_root)
        stdio_check = _build_check(
            name="stdio_verify",
            ok=bool(stdio_report["ok"]),
            status="pass" if stdio_report["ok"] else "fail",
            detail="Ran isolated stdio verify." if stdio_report["ok"] else "Isolated stdio verify failed.",
            report=stdio_report,
        )
        checks.append(stdio_check)

    ok = all(check["status"] != "fail" for check in checks)
    return {
        "ok": ok,
        "project_root": str(resolved_project_root),
        "checks": checks,
        "config_path": str(config_path),
        "bridge_home": str(bridge_home),
        "db_path": str(db_path),
        "log_dir": str(log_dir),
        "include_stdio": include_stdio,
        "stdio_check": stdio_check,
    }


def run_verify(
    *,
    project_root: Path | None = None,
    runtime_dir: Path | None = None,
) -> dict[str, Any]:
    resolved_project_root = (project_root or Path(__file__).resolve().parents[2]).resolve()
    if runtime_dir is None:
        with TemporaryDirectory(prefix="amb-verify-") as temp_dir:
            resolved_runtime_dir = Path(temp_dir)
            return asyncio.run(_run_verify_stdio(resolved_project_root, resolved_runtime_dir))
    return asyncio.run(_run_verify_stdio(resolved_project_root, runtime_dir.resolve()))


async def _run_verify_stdio(project_root: Path, runtime_dir: Path) -> dict[str, Any]:
    runtime_dir.mkdir(parents=True, exist_ok=True)
    db_path = runtime_dir / "verify.db"
    log_dir = runtime_dir / "logs"
    token = datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")
    memory_content = f"Agent Memory Bridge verify round-trip token={token}"
    signal_content = f"Verify signal token={token}"

    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "agent_mem_bridge"],
        cwd=str(project_root),
        env={
            **os.environ,
            "AGENT_MEMORY_BRIDGE_HOME": str(runtime_dir),
            "AGENT_MEMORY_BRIDGE_DB_PATH": str(db_path),
            "AGENT_MEMORY_BRIDGE_LOG_DIR": str(log_dir),
            "AGENT_MEMORY_BRIDGE_DEFAULT_SOURCE_CLIENT": "verify-cli",
            "AGENT_MEMORY_BRIDGE_DEFAULT_CLIENT_TRANSPORT": "stdio",
        },
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools_response = await session.list_tools()
            tool_names = {tool.name for tool in tools_response.tools}

            first = await session.call_tool(
                "store",
                arguments={
                    "namespace": "verify",
                    "content": memory_content,
                    "kind": "memory",
                    "tags": ["check:verify"],
                    "session_id": "verify-session",
                    "actor": "verify-cli",
                    "source_app": "agent-memory-bridge verify",
                },
            )
            duplicate = await session.call_tool(
                "store",
                arguments={
                    "namespace": "verify",
                    "content": memory_content,
                    "kind": "memory",
                    "tags": ["check:verify"],
                    "session_id": "verify-session",
                    "actor": "verify-cli",
                    "source_app": "agent-memory-bridge verify",
                },
            )
            recall = await session.call_tool(
                "recall",
                arguments={
                    "namespace": "verify",
                    "query": token,
                    "kind": "memory",
                    "limit": 5,
                },
            )
            signal = await session.call_tool(
                "store",
                arguments={
                    "namespace": "verify",
                    "content": signal_content,
                    "kind": "signal",
                    "tags": ["check:verify-signal"],
                    "actor": "verify-cli",
                    "ttl_seconds": 120,
                },
            )
            claimed = await session.call_tool(
                "claim_signal",
                arguments={
                    "namespace": "verify",
                    "consumer": "verify-worker",
                    "signal_id": signal.structuredContent["id"],
                    "lease_seconds": 60,
                },
            )
            extended = await session.call_tool(
                "extend_signal_lease",
                arguments={
                    "id": signal.structuredContent["id"],
                    "consumer": "verify-worker",
                    "lease_seconds": 60,
                },
            )
            acked = await session.call_tool(
                "ack_signal",
                arguments={
                    "id": signal.structuredContent["id"],
                    "consumer": "verify-worker",
                },
            )

    first_payload = _structured_payload(first)
    duplicate_payload = _structured_payload(duplicate)
    recall_payload = _structured_payload(recall)
    claim_payload = _structured_payload(claimed)
    extend_payload = _structured_payload(extended)
    ack_payload = _structured_payload(acked)

    checks = [
        _build_check(
            name="tool_surface",
            ok=tool_names == TOOL_NAMES,
            status="pass" if tool_names == TOOL_NAMES else "fail",
            detail=f"Tool count {len(tool_names)}; expected {len(TOOL_NAMES)}.",
            actual_tools=sorted(tool_names),
        ),
        _build_check(
            name="memory_round_trip",
            ok=bool(first_payload.get("stored")) and int(recall_payload.get("count", 0)) >= 1,
            status="pass"
            if bool(first_payload.get("stored")) and int(recall_payload.get("count", 0)) >= 1
            else "fail",
            detail="Stored and recalled one memory record.",
            first_store=first_payload,
            recall=recall_payload,
        ),
        _build_check(
            name="memory_duplicate_detection",
            ok=bool(duplicate_payload.get("duplicate")),
            status="pass" if bool(duplicate_payload.get("duplicate")) else "fail",
            detail="Duplicate durable memory is detected.",
            duplicate_store=duplicate_payload,
        ),
        _build_check(
            name="signal_lifecycle",
            ok=bool(claim_payload.get("claimed"))
            and bool(extend_payload.get("extended"))
            and bool(ack_payload.get("acked")),
            status="pass"
            if bool(claim_payload.get("claimed"))
            and bool(extend_payload.get("extended"))
            and bool(ack_payload.get("acked"))
            else "fail",
            detail="Signal claim, extend, and ack completed.",
            claim=claim_payload,
            extend=extend_payload,
            ack=ack_payload,
        ),
    ]

    return {
        "ok": all(check["status"] == "pass" for check in checks),
        "project_root": str(project_root),
        "runtime_dir": str(runtime_dir),
        "db_path": str(db_path),
        "log_dir": str(log_dir),
        "checks": checks,
        "tool_count": len(tool_names),
        "tool_names": sorted(tool_names),
    }


def render_report(report: dict[str, Any]) -> str:
    lines = [
        f"overall: {'ok' if report['ok'] else 'failed'}",
    ]
    for check in report.get("checks", []):
        lines.append(f"- {check['name']}: {check['status']} - {check['detail']}")
    return "\n".join(lines)


def render_verify_success_message(report: dict[str, Any]) -> str:
    status = "works" if report["ok"] else "failed"
    return f"Your MCP server {status}."


def _config_path_check(config_path: Path) -> dict[str, Any]:
    if not config_path.exists():
        return _build_check(
            name="config_path",
            ok=True,
            status="warn",
            detail="Config file is optional and was not found.",
            path=str(config_path),
        )
    try:
        with config_path.open("rb") as handle:
            tomllib.load(handle)
    except OSError as exc:
        return _build_check(
            name="config_path",
            ok=False,
            status="fail",
            detail=f"Config file could not be read: {exc}",
            path=str(config_path),
        )
    except tomllib.TOMLDecodeError as exc:
        return _build_check(
            name="config_path",
            ok=False,
            status="fail",
            detail=f"Config file could not be parsed: {exc}",
            path=str(config_path),
        )
    return _build_check(
        name="config_path",
        ok=True,
        status="pass",
        detail="Config file exists and is readable.",
        path=str(config_path),
    )


def _sqlite_fts5_check() -> dict[str, Any]:
    try:
        connection = sqlite3.connect(":memory:")
        try:
            connection.execute("CREATE VIRTUAL TABLE verify_fts USING fts5(content)")
        finally:
            connection.close()
    except sqlite3.DatabaseError as exc:
        return _build_check(
            name="sqlite_fts5",
            ok=False,
            status="fail",
            detail=f"SQLite FTS5 unavailable: {exc}",
        )
    return _build_check(
        name="sqlite_fts5",
        ok=True,
        status="pass",
        detail="SQLite FTS5 is available.",
    )


def _path_writable_check(name: str, target: Path) -> dict[str, Any]:
    candidate = _path_check_target(target)
    ok = os.access(candidate, os.W_OK)
    return _build_check(
        name=name,
        ok=ok,
        status="pass" if ok else "fail",
        detail=f"Nearest existing parent is {'writable' if ok else 'not writable'}.",
        target=str(target),
        checked_parent=str(candidate),
    )


def _nearest_existing_parent(path: Path) -> Path:
    candidate = path if path.suffix == "" else path.parent
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate


def _path_check_target(path: Path) -> Path:
    candidate = path if path.is_dir() else path.parent
    if candidate.exists():
        return candidate
    return _nearest_existing_parent(candidate)


def _structured_payload(response: Any) -> dict[str, Any]:
    payload = getattr(response, "structuredContent", None) or {}
    if isinstance(payload, dict):
        return payload
    return {}


def _build_check(
    *,
    name: str,
    ok: bool,
    status: Literal["pass", "warn", "fail"],
    detail: str,
    **extra: Any,
) -> dict[str, Any]:
    payload = {
        "name": name,
        "ok": ok,
        "status": status,
        "detail": detail,
    }
    payload.update(extra)
    return payload
