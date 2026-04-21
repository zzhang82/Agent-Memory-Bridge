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

from .archive_snapshot import (
    build_default_live_manifest_path,
    find_latest_snapshot_manifest,
    load_manifest,
    load_manifest_relative_paths,
)
from .profile_migration import build_profile_documents, compare_profile_migration_with_mode
from .paths import resolve_bridge_db_path, resolve_bridge_home, resolve_bridge_log_dir, resolve_sessions_root
from .storage import MemoryStore
from .watcher_health import run_watcher_health_check


RECALL_CHECK_LIMIT = 4


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
    compare = compare_profile_migration_with_mode(store, source_root, mode=resolved_compare_mode)
    recall_checks = _run_recall_checks(store, source_root, resolved_compare_mode)
    watcher_health = run_watcher_health_check(resolve_sessions_root())
    relation_metadata_smoke = run_relation_metadata_smoke(resolve_bridge_home())

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
        and bool(relation_metadata_smoke.get("ok"))
        and bool(watcher_health.get("ok"))
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
        "relation_metadata_smoke": relation_metadata_smoke,
        "watcher_health": watcher_health,
        "stdio_smoke": stdio_smoke,
    }


def _run_recall_checks(
    store: MemoryStore,
    source_root: Path,
    compare_mode: Literal["full", "live", "snapshot-audit"],
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for document in _select_recall_documents(source_root, compare_mode):
        expected_source_path = document.relative_path.as_posix()
        result = store.recall(namespace=document.namespace, query=document.title, limit=3)
        matching_item = next(
            (
                item
                for item in result["items"]
                if _extract_source_path(item["tags"]) == expected_source_path
            ),
            None,
        )
        first_item = matching_item or (result["items"][0] if result["items"] else None)
        checks.append(
            {
                "namespace": document.namespace,
                "query": document.title,
                "expected_source_path": expected_source_path,
                "count": result["count"],
                "ok": matching_item is not None,
                "top_title": first_item["title"] if first_item else None,
                "top_source_path": _extract_source_path(first_item["tags"]) if first_item else None,
            }
        )
    return checks


def _select_recall_documents(
    source_root: Path,
    compare_mode: Literal["full", "live", "snapshot-audit"],
) -> list[Any]:
    root = Path(source_root).resolve()
    if compare_mode == "live":
        manifest_path = build_default_live_manifest_path(root)
        if not manifest_path.is_file():
            return []
        documents = build_profile_documents(root, relative_paths=load_manifest_relative_paths(manifest_path))
    elif compare_mode == "snapshot-audit":
        manifest_path = find_latest_snapshot_manifest(root)
        if manifest_path is None:
            return []
        manifest = load_manifest(manifest_path)
        snapshot_root = Path(manifest.get("snapshot_root", root)).resolve()
        relative_paths = [Path(item) for item in manifest.get("files", [])]
        documents = build_profile_documents(snapshot_root, relative_paths=relative_paths)
    else:
        documents = build_profile_documents(root)

    selected: list[Any] = []
    seen_namespaces: set[str] = set()
    seen_paths: set[str] = set()

    for document in documents:
        if document.namespace in seen_namespaces:
            continue
        selected.append(document)
        seen_namespaces.add(document.namespace)
        seen_paths.add(document.relative_path.as_posix())
        if len(selected) >= RECALL_CHECK_LIMIT:
            return selected

    for document in documents:
        path_key = document.relative_path.as_posix()
        if path_key in seen_paths:
            continue
        selected.append(document)
        seen_paths.add(path_key)
        if len(selected) >= RECALL_CHECK_LIMIT:
            break

    return selected


def run_relation_metadata_smoke(bridge_home: Path) -> dict[str, Any]:
    runtime_dir = bridge_home / "healthcheck-runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    db_path = runtime_dir / "relation-smoke.db"
    log_dir = runtime_dir / "relation-logs"
    store = MemoryStore(db_path=db_path, log_dir=log_dir)
    valid_from = datetime.now(UTC).replace(microsecond=0).isoformat()

    created = store.store(
        namespace="healthcheck:relation",
        kind="memory",
        title="Healthcheck relation smoke",
        tags=["health:relation"],
        content=(
            "claim: Healthcheck should surface relation metadata.\n"
            "supports: smoke-a | smoke-b\n"
            "depends_on: smoke-procedure\n"
            f"valid_from: {valid_from}\n"
            "valid_until: 2099-01-01T00:00:00+00:00\n"
        ),
    )
    recall = store.recall(namespace="healthcheck:relation", tags_any=["relation:supports"], limit=5)
    stats = store.stats(namespace="healthcheck:relation")
    exported = store.export(namespace="healthcheck:relation", format="text")
    item = recall["items"][0] if recall["items"] else None
    ok = (
        bool(created.get("stored"))
        and bool(item)
        and item["relations"]["supports"] == ["smoke-a", "smoke-b"]
        and item["relations"]["depends_on"] == ["smoke-procedure"]
        and item["validity_status"] == "current"
        and stats["relation_counts"]["supports"] == 2
        and stats["relation_counts"]["depends_on"] == 1
        and stats["validity_counts"]["current"] == 1
        and "relations: supports=smoke-a, smoke-b; depends_on=smoke-procedure" in exported["content"]
    )
    return {
        "ok": ok,
        "db_path": str(db_path),
        "created": created,
        "recall": recall,
        "stats": stats,
    }


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
