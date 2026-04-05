from __future__ import annotations

import asyncio
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .archive_snapshot import build_default_live_manifest_path
from .cole_migration import compare_cole_migration_with_mode
from .paths import resolve_bridge_db_path, resolve_bridge_home, resolve_bridge_log_dir
from .storage import MemoryStore


DEFAULT_RECALL_CHECKS = (
    ("cole-core", "simplicity over feature count reliability over cleverness"),
    ("cole-core", "If in doubt, stop and ask"),
    ("cole-workflows", "Subagent Orchestration Patterns"),
    ("cole-skills", "Obsidian Flavored Markdown Skill"),
)


def run_health_check(
    source_root: Path,
    *,
    check_stdio: bool = True,
    project_root: Path | None = None,
    compare_mode: Literal["auto", "full", "live", "snapshot-audit"] = "auto",
) -> dict[str, Any]:
    store = MemoryStore(
        db_path=resolve_bridge_db_path(),
        log_dir=resolve_bridge_log_dir(),
    )
    resolved_compare_mode = _resolve_compare_mode(source_root, compare_mode)
    compare = compare_cole_migration_with_mode(store, source_root, mode=resolved_compare_mode)
    recall_checks = _run_recall_checks(store)

    stdio_smoke: dict[str, Any] | None = None
    if check_stdio:
        stdio_smoke = asyncio.run(
            run_stdio_smoke(
                project_root=project_root or Path(__file__).resolve().parents[2],
                bridge_home=resolve_bridge_home(),
            )
        )

    ok = (
        compare["missing_count"] == 0
        and compare["extra_count"] == 0
        and compare["content_mismatch_count"] == 0
        and compare["namespace_mismatch_count"] == 0
        and all(item["ok"] for item in recall_checks)
        and (stdio_smoke is None or bool(stdio_smoke.get("ok")))
    )
    return {
        "ok": ok,
        "source_root": str(Path(source_root).resolve()),
        "bridge_home": str(resolve_bridge_home()),
        "db_path": str(resolve_bridge_db_path()),
        "db_exists": resolve_bridge_db_path().is_file(),
        "resolved_compare_mode": resolved_compare_mode,
        "compare": compare,
        "recall_checks": recall_checks,
        "stdio_smoke": stdio_smoke,
    }


def _run_recall_checks(store: MemoryStore) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for namespace, query in DEFAULT_RECALL_CHECKS:
        result = store.recall(namespace=namespace, query=query, limit=3)
        first_item = result["items"][0] if result["items"] else None
        checks.append(
            {
                "namespace": namespace,
                "query": query,
                "count": result["count"],
                "ok": result["count"] > 0,
                "top_title": first_item["title"] if first_item else None,
                "top_source_path": _extract_source_path(first_item["tags"]) if first_item else None,
            }
        )
    return checks


async def run_stdio_smoke(project_root: Path, bridge_home: Path) -> dict[str, Any]:
    runtime_dir = bridge_home / "healthcheck-runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    db_path = runtime_dir / "healthcheck-stdio.db"
    log_dir = runtime_dir / "logs"
    namespace = "healthcheck-stdio"
    token = datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")
    content = f"Healthcheck stdio transport proof is working. token={token}"

    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "agent_mem_bridge"],
        cwd=str(project_root),
        env={
            **os.environ,
            "AGENT_MEMORY_BRIDGE_DB_PATH": str(db_path),
            "AGENT_MEMORY_BRIDGE_LOG_DIR": str(log_dir),
        },
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools_response = await session.list_tools()

            first = await session.call_tool(
                "store",
                arguments={
                    "namespace": namespace,
                    "content": content,
                    "kind": "memory",
                    "tags": ["health:stdio", "check:transport"],
                    "session_id": "healthcheck-stdio",
                    "actor": "healthcheck",
                    "source_app": "healthcheck.py",
                },
            )
            second = await session.call_tool(
                "store",
                arguments={
                    "namespace": namespace,
                    "content": content,
                    "kind": "memory",
                    "tags": ["health:stdio", "check:transport"],
                    "session_id": "healthcheck-stdio",
                    "actor": "healthcheck",
                    "source_app": "healthcheck.py",
                },
            )
            recall = await session.call_tool(
                "recall",
                arguments={
                    "namespace": namespace,
                    "query": "transport proof",
                    "limit": 5,
                },
            )

    tool_names = [tool.name for tool in tools_response.tools]
    first_payload = getattr(first, "structuredContent", None) or {}
    second_payload = getattr(second, "structuredContent", None) or {}
    recall_payload = getattr(recall, "structuredContent", None) or {}
    return {
        "ok": (
            "store" in tool_names
            and "recall" in tool_names
            and bool(first_payload.get("stored"))
            and bool(second_payload.get("duplicate"))
            and int(recall_payload.get("count", 0)) >= 1
        ),
        "tools": tool_names,
        "first_store": first_payload,
        "duplicate_store": second_payload,
        "recall": recall_payload,
        "db_path": str(db_path),
    }


def _extract_source_path(tags: list[str]) -> str | None:
    for tag in tags:
        if tag.startswith("source-path:"):
            return tag.removeprefix("source-path:")
    return None


def _resolve_compare_mode(
    source_root: Path,
    compare_mode: Literal["auto", "full", "live", "snapshot-audit"],
) -> Literal["full", "live", "snapshot-audit"]:
    if compare_mode != "auto":
        return compare_mode
    live_manifest_path = build_default_live_manifest_path(source_root)
    if live_manifest_path.is_file():
        return "live"
    return "full"
