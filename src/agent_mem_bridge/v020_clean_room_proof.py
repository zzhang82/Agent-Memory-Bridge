from __future__ import annotations

import asyncio
import gc
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .onboarding import TOOL_NAMES
from .release_contract import load_pyproject_version, load_server_tool_names


ROOT = Path(__file__).resolve().parents[2]
V020_CLEAN_ROOM_PROOF_SCHEMA = "memory.v0_20_clean_room_proof.v1"
DEFAULT_V020_REPORT_PATH = ROOT / "benchmark" / "latest-v0.20-clean-room-proof-report.json"
DEFAULT_V020_TRANSCRIPT_PATH = ROOT / "benchmark" / "latest-v0.20-clean-room-proof-transcript.md"
V020_NAMESPACE = "project:v020-clean-room"
V020_QUERY = "clean room adoption handoff"
V020_TOKEN = "v020-clean-room-stdio-token"
EXPECTED_PUBLIC_TOOL_COUNT = 10
PROOF_KIND = "local_clean_room_adoption_not_vendor_certification"

V020_CASE_MANIFEST: tuple[dict[str, str], ...] = (
    {
        "id": "v020-local-entrypoint-import",
        "category": "install_import",
        "purpose": "Prove the installed/local package entrypoint can import and report a version.",
        "expected_behavior": "`python -m agent_mem_bridge --version` exits 0 with a package version.",
        "non_goal_guard": "Does not claim PyPI, vendor-client, or external clean-room certification.",
    },
    {
        "id": "v020-stdio-tool-surface",
        "category": "stdio_mcp",
        "purpose": "Prove the real stdio MCP server exposes exactly the documented public surface.",
        "expected_behavior": "MCP `list_tools` returns the documented 10 tools and no v0.20 tool.",
        "non_goal_guard": "Does not add startup_packet, task_packet, plugin, watcher, or harness tools.",
    },
    {
        "id": "v020-stdio-store-recall",
        "category": "stdio_mcp",
        "purpose": "Prove a tokened demo memory can round-trip through MCP stdio store and recall.",
        "expected_behavior": "`store` writes one demo memory and `recall` finds the same record with stdio provenance.",
        "non_goal_guard": "Does not count direct MemoryStore calls as MCP round-trip evidence.",
    },
    {
        "id": "v020-first-run-cli",
        "category": "cli_report",
        "purpose": "Prove first-run output is available as a placeholder-safe local CLI report.",
        "expected_behavior": "`first-run --format json --example` parses, stays manual-copy-only, and includes Task Brief.",
        "non_goal_guard": "Does not write client config or claim runtime-specific plugin support.",
    },
    {
        "id": "v020-task-brief-cli",
        "category": "cli_report",
        "purpose": "Prove Task Brief output is available as a read-only local CLI report.",
        "expected_behavior": "`task-brief --format json` parses and includes used/review sections from the temp store.",
        "non_goal_guard": "Does not mutate memory, promote records, or require AMH.",
    },
    {
        "id": "v020-isolation-write-scope",
        "category": "isolation",
        "purpose": "Prove the proof writes only explicit demo data into an isolated temp store.",
        "expected_behavior": "No client config file, no live DB mutation, no non-demo durable writeback.",
        "non_goal_guard": "Does not touch user/client config, live AMB home, watcher, scheduler, or AMH runtime.",
    },
)


def run_v020_clean_room_proof(
    *,
    project_root: Path | None = None,
    runtime_dir: Path | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Run the v0.20 local clean-room proof without touching the user's configured store."""

    resolved_project_root = (project_root or ROOT).resolve()
    if runtime_dir is None:
        temp_dir = Path(tempfile.mkdtemp(prefix="amb-v020-clean-room-")).resolve()
        try:
            return _run_with_runtime(
                project_root=resolved_project_root,
                runtime_dir=temp_dir,
                ephemeral_runtime=True,
                generated_at=generated_at,
            )
        finally:
            _cleanup_runtime_dir(temp_dir)

    resolved_runtime_dir = runtime_dir.resolve()
    if resolved_runtime_dir.exists() and any(resolved_runtime_dir.iterdir()):
        raise ValueError("runtime_dir must be absent or empty for a clean-room proof")
    return _run_with_runtime(
        project_root=resolved_project_root,
        runtime_dir=resolved_runtime_dir,
        ephemeral_runtime=False,
        generated_at=generated_at,
    )


def render_v020_clean_room_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# v0.20 Clean-Room Adoption Proof",
        "",
        f"- schema: `{report['schema']}`",
        f"- release: `{report['release']}`",
        f"- proof_kind: `{report['proof_kind']}`",
        f"- ok: `{str(summary['v020_ok']).lower()}`",
        f"- case_count: `{summary['v020_case_count']}`",
        f"- pass_count: `{summary['v020_pass_count']}`",
        f"- pass_rate: `{summary['v020_pass_rate']}`",
        f"- public_mcp_tool_count: `{summary['v020_public_mcp_tool_count']}`",
        f"- public_mcp_surface_change: `{str(summary['v020_public_mcp_surface_change']).lower()}`",
        f"- client_config_write_count: `{summary['v020_client_config_write_count']}`",
        f"- explicit_demo_memory_write_count: `{summary['v020_explicit_demo_memory_write_count']}`",
        f"- explicit_demo_signal_write_count: `{summary['v020_explicit_demo_signal_write_count']}`",
        f"- non_demo_durable_writeback_count: `{summary['v020_non_demo_durable_writeback_count']}`",
        f"- amh_required: `{str(summary['v020_amh_required']).lower()}`",
        f"- external_vendor_adoption_claim: `{str(summary['v020_external_vendor_adoption_claim']).lower()}`",
        "",
        "## Cases",
        "",
    ]
    for case in report["cases"]:
        lines.extend(
            [
                f"### {case['id']}",
                "",
                f"- category: `{case['category']}`",
                f"- passed: `{str(case['passed']).lower()}`",
                f"- command_or_query: `{case['query_or_command']}`",
                f"- expected_behavior: {case['expected_behavior']}",
                f"- failure_reason: `{case['failure_reason'] or 'none'}`",
                f"- non_goal_guard: {case['non_goal_guard']}",
                "",
            ]
        )

    lines.extend(
        [
            "## Stdio MCP Evidence",
            "",
            f"- entrypoint: `{report['stdio']['entrypoint']['command']}`",
            f"- tool_count: `{report['stdio']['tool_count']}`",
            f"- store_recall_round_trip: `{str(report['stdio']['store_recall_round_trip']).lower()}`",
            f"- recalled_client_transports: `{', '.join(report['stdio']['recalled_client_transports']) or 'none'}`",
            "",
            "## CLI Report Evidence",
            "",
            f"- first_run_schema: `{report['cli_reports']['first_run']['schema']}`",
            f"- first_run_write_mode: `{report['cli_reports']['first_run']['client_config_write_mode']}`",
            f"- task_brief_schema: `{report['cli_reports']['task_brief']['schema']}`",
            f"- task_brief_used_count: `{report['cli_reports']['task_brief']['used_count']}`",
            f"- task_brief_needs_review_count: `{report['cli_reports']['task_brief']['needs_review_count']}`",
            "",
            "## Boundary",
            "",
            "- Local reproducible proof only.",
            "- No vendor-client certification claim.",
            "- No new public MCP tools.",
            "- No client config writes.",
            "- No AMH dependency.",
            "- No watcher, scheduler, daemon, or runtime loop.",
        ]
    )
    return "\n".join(lines)


def _run_with_runtime(
    *,
    project_root: Path,
    runtime_dir: Path,
    ephemeral_runtime: bool,
    generated_at: str | None,
) -> dict[str, Any]:
    generated = generated_at or datetime.now(UTC).isoformat()
    bridge_home = runtime_dir / "home"
    db_path = bridge_home / "bridge.db"
    log_dir = bridge_home / "logs"
    config_path = bridge_home / "config.toml"
    config_path_exists_before = config_path.exists()
    runtime_dir.mkdir(parents=True, exist_ok=True)
    replacements = _path_replacements(
        project_root=project_root,
        runtime_dir=runtime_dir,
        bridge_home=bridge_home,
        db_path=db_path,
        log_dir=log_dir,
        config_path=config_path,
    )
    env = _proof_env(
        bridge_home=bridge_home,
        db_path=db_path,
        log_dir=log_dir,
        config_path=config_path,
    )
    release_version = load_pyproject_version(project_root / "pyproject.toml")

    version_command = [sys.executable, "-m", "agent_mem_bridge", "--version"]
    version_result = _run_command(version_command, cwd=project_root, env=env, replacements=replacements)
    stdio = asyncio.run(
        _run_stdio_round_trip(
            project_root=project_root,
            bridge_home=bridge_home,
            db_path=db_path,
            log_dir=log_dir,
            config_path=config_path,
            replacements=replacements,
        )
    )

    before_cli_counts = _table_counts(db_path)
    first_run = _run_first_run_cli(project_root=project_root, env=env, replacements=replacements)
    task_brief = _run_task_brief_cli(project_root=project_root, env=env, replacements=replacements)
    after_cli_counts = _table_counts(db_path)
    namespace_counts = _namespace_counts(db_path, V020_NAMESPACE)
    all_memory_rows = _memory_row_count(db_path)
    config_path_exists_after = config_path.exists()
    public_tools = load_server_tool_names(project_root / "src" / "agent_mem_bridge" / "server.py")

    explicit_demo_memory_write_count = namespace_counts["memory"]
    explicit_demo_signal_write_count = namespace_counts["signal"]
    non_demo_durable_writeback_count = max(
        0,
        all_memory_rows - explicit_demo_memory_write_count - explicit_demo_signal_write_count,
    )
    report_rendering_mutation_count = _count_delta(before_cli_counts, after_cli_counts)

    checks = {
        "v020_import_sanity_pass": version_result["returncode"] == 0
        and version_result["stdout"].strip() == release_version,
        "v020_stdio_tool_surface_pass": stdio["tool_count"] == EXPECTED_PUBLIC_TOOL_COUNT
        and set(stdio["tool_names"]) == TOOL_NAMES,
        "v020_stdio_round_trip_pass": stdio["store_recall_round_trip"]
        and "stdio" in stdio["recalled_client_transports"],
        "v020_first_run_pass": first_run["schema"] == "memory.first_run.v1"
        and first_run["client_config_write_mode"] == "manual_copy_only"
        and first_run["amh_required"] is False
        and not first_run["contains_private_path"],
        "v020_task_brief_pass": task_brief["schema"] == "memory.task_brief.v1"
        and task_brief["used_count"] >= 1
        and task_brief["no_auto_writeback"] is True,
        "v020_isolation_write_scope_pass": not config_path_exists_before
        and not config_path_exists_after
        and report_rendering_mutation_count == 0
        and explicit_demo_memory_write_count == 1
        and explicit_demo_signal_write_count == 0
        and non_demo_durable_writeback_count == 0,
    }
    cases = _build_cases(
        checks=checks,
        version_result=version_result,
        stdio=stdio,
        first_run=first_run,
        task_brief=task_brief,
        explicit_demo_memory_write_count=explicit_demo_memory_write_count,
        explicit_demo_signal_write_count=explicit_demo_signal_write_count,
        non_demo_durable_writeback_count=non_demo_durable_writeback_count,
        config_path_exists_after=config_path_exists_after,
    )
    pass_count = sum(1 for case in cases if case["passed"])
    case_count = len(cases)
    summary = {
        "v020_ok": pass_count == case_count,
        "v020_case_count": case_count,
        "v020_pass_count": pass_count,
        "v020_pass_rate": round(pass_count / case_count, 4) if case_count else 0.0,
        "v020_import_sanity_pass": checks["v020_import_sanity_pass"],
        "v020_stdio_round_trip_pass": checks["v020_stdio_round_trip_pass"],
        "v020_first_run_pass": checks["v020_first_run_pass"],
        "v020_task_brief_pass": checks["v020_task_brief_pass"],
        "v020_public_mcp_tool_count": len(public_tools),
        "v020_public_mcp_surface_change": public_tools != TOOL_NAMES,
        "v020_client_config_write_count": 0 if not config_path_exists_after else 1,
        "v020_explicit_demo_memory_write_count": explicit_demo_memory_write_count,
        "v020_explicit_demo_signal_write_count": explicit_demo_signal_write_count,
        "v020_non_demo_durable_writeback_count": non_demo_durable_writeback_count,
        "v020_amh_required": False,
        "v020_external_vendor_adoption_claim": False,
    }
    return {
        "schema": V020_CLEAN_ROOM_PROOF_SCHEMA,
        "release": release_version,
        "proof_kind": PROOF_KIND,
        "generated_at": generated,
        "scope_boundary": {
            "local_reproducible_proof_only": True,
            "external_vendor_adoption_claim": False,
            "public_mcp_tool_count_must_remain": EXPECTED_PUBLIC_TOOL_COUNT,
            "new_mcp_tools_added": False,
            "client_config_writes_allowed": False,
            "durable_writeback_allowed_outside_temp_store": False,
            "amh_dependency_allowed": False,
            "scheduler_or_runtime_loop_allowed": False,
        },
        "environment": {
            "project_root": "<repo>",
            "runtime_dir": "<temp>",
            "ephemeral_runtime": ephemeral_runtime,
            "python": "python",
            "package_version": release_version,
            "package_version_source": "pyproject.toml",
            "runtime_paths_are_sanitized": True,
        },
        "runtime": {
            "bridge_home": "<temp>/home",
            "db_path": "<temp>/home/bridge.db",
            "log_dir": "<temp>/home/logs",
            "config_path": "<temp>/home/config.toml",
            "config_path_exists_before": config_path_exists_before,
            "config_path_exists_after": config_path_exists_after,
            "namespace_counts": namespace_counts,
            "table_count_delta_from_cli_reports": report_rendering_mutation_count,
        },
        "summary": summary,
        "cases": cases,
        "artifacts": {
            "json_report": "benchmark/latest-v0.20-clean-room-proof-report.json",
            "markdown_transcript": "benchmark/latest-v0.20-clean-room-proof-transcript.md",
        },
        "stdio": stdio,
        "cli_reports": {
            "first_run": first_run,
            "task_brief": task_brief,
        },
    }


async def _run_stdio_round_trip(
    *,
    project_root: Path,
    bridge_home: Path,
    db_path: Path,
    log_dir: Path,
    config_path: Path,
    replacements: dict[str, str],
) -> dict[str, Any]:
    env = _proof_env(
        bridge_home=bridge_home,
        db_path=db_path,
        log_dir=log_dir,
        config_path=config_path,
    )
    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "agent_mem_bridge"],
        cwd=str(project_root),
        env=env,
    )
    content = (
        "record_type: procedure\n"
        "goal: Complete clean room adoption handoff.\n"
        f"token: {V020_TOKEN}\n"
        "steps: install/import sanity | store memory via MCP stdio | recall memory via MCP stdio | "
        "render first-run CLI | render task-brief CLI\n"
        "boundary: temp-store proof only; no client config writes; no AMH dependency.\n"
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools_response = await session.list_tools()
            tool_names = sorted(tool.name for tool in tools_response.tools)
            store_response = await session.call_tool(
                "store",
                arguments={
                    "namespace": V020_NAMESPACE,
                    "content": content,
                    "kind": "memory",
                    "tags": ["kind:procedure", "domain:onboarding", "topic:clean-room"],
                    "title": "[[Procedure]] clean room adoption handoff",
                    "session_id": "v020-clean-room-proof",
                    "actor": "v020-proof",
                    "source_app": "agent-memory-bridge v0.20 clean-room proof",
                },
            )
            recall_response = await session.call_tool(
                "recall",
                arguments={
                    "namespace": V020_NAMESPACE,
                    "query": f"{V020_QUERY} {V020_TOKEN}",
                    "kind": "memory",
                    "limit": 5,
                },
            )

    store_payload = _structured_payload(store_response)
    recall_payload = _structured_payload(recall_response)
    recall_items = recall_payload.get("items") or []
    stored_id = str(store_payload.get("id") or "")
    recalled_item_ids = [str(item.get("id") or "") for item in recall_items]
    transports = sorted({str(item.get("client_transport") or "") for item in recall_items if item.get("client_transport")})
    source_clients = sorted({str(item.get("source_client") or "") for item in recall_items if item.get("source_client")})
    token_found = any(V020_TOKEN in str(item.get("content") or "") for item in recall_items)
    return {
        "entrypoint": {
            "command": _public_command([sys.executable, "-m", "agent_mem_bridge"], replacements),
            "cwd": "<repo>",
        },
        "tool_count": len(tool_names),
        "tool_names": tool_names,
        "store_stored": bool(store_payload.get("stored")),
        "recall_count": int(recall_payload.get("count", 0)),
        "stored_id_recalled": bool(stored_id) and stored_id in recalled_item_ids,
        "token_found": token_found,
        "store_recall_round_trip": bool(store_payload.get("stored"))
        and bool(stored_id)
        and stored_id in recalled_item_ids
        and token_found,
        "recalled_client_transports": transports,
        "recalled_source_clients": source_clients,
        "private_ids_redacted": True,
    }


def _run_first_run_cli(
    *,
    project_root: Path,
    env: dict[str, str],
    replacements: dict[str, str],
) -> dict[str, Any]:
    command = [
        sys.executable,
        "-m",
        "agent_mem_bridge",
        "first-run",
        "--client",
        "generic",
        "--namespace",
        V020_NAMESPACE,
        "--query",
        V020_QUERY,
        "--example",
        "--format",
        "json",
    ]
    result = _run_command(command, cwd=project_root, env=env, replacements=replacements)
    payload = _loads_json(result)
    config = payload.get("client_config") or {}
    boundary = payload.get("boundary") or {}
    task_brief = payload.get("first_task_brief") or {}
    task_summary = task_brief.get("summary") or {}
    combined_text = json.dumps(payload, sort_keys=True)
    return {
        "command": result["command"],
        "returncode": result["returncode"],
        "schema": str(payload.get("schema") or ""),
        "client": str(payload.get("client") or ""),
        "client_status": str(payload.get("client_status") or ""),
        "client_config_format": str(payload.get("client_config_format") or ""),
        "client_config_write_mode": str(boundary.get("client_config_write_mode") or ""),
        "mutation_allowed": bool(boundary.get("mutation_allowed")),
        "public_mcp_surface_change": bool(boundary.get("public_mcp_surface_change")),
        "amh_required": bool(boundary.get("amh_required")),
        "task_brief_schema": str(task_brief.get("schema") or ""),
        "task_brief_used_count": int(task_summary.get("used_count", 0)),
        "contains_private_path": _contains_private_path(combined_text),
        "placeholder_safe": "<repo>" not in combined_text and "<temp>" not in combined_text,
        "file_hint": str(config.get("file_hint") or ""),
    }


def _run_task_brief_cli(
    *,
    project_root: Path,
    env: dict[str, str],
    replacements: dict[str, str],
) -> dict[str, Any]:
    command = [
        sys.executable,
        "-m",
        "agent_mem_bridge",
        "task-brief",
        "--namespace",
        V020_NAMESPACE,
        "--query",
        V020_QUERY,
        "--format",
        "json",
    ]
    result = _run_command(command, cwd=project_root, env=env, replacements=replacements)
    payload = _loads_json(result)
    summary = payload.get("summary") or {}
    return {
        "command": result["command"],
        "returncode": result["returncode"],
        "schema": str(payload.get("schema") or ""),
        "mutation_boundary": str(payload.get("mutation_boundary") or ""),
        "writeback_boundary": str(payload.get("writeback_boundary") or ""),
        "public_mcp_surface_change": bool(payload.get("public_mcp_surface_change")),
        "used_count": int(summary.get("used_count", 0)),
        "ignored_count": int(summary.get("ignored_count", 0)),
        "needs_review_count": int(summary.get("needs_review_count", 0)),
        "no_auto_writeback": bool(summary.get("task_brief_no_auto_writeback")),
        "contains_private_path": _contains_private_path(json.dumps(payload, sort_keys=True)),
    }


def _build_cases(
    *,
    checks: dict[str, bool],
    version_result: dict[str, Any],
    stdio: dict[str, Any],
    first_run: dict[str, Any],
    task_brief: dict[str, Any],
    explicit_demo_memory_write_count: int,
    explicit_demo_signal_write_count: int,
    non_demo_durable_writeback_count: int,
    config_path_exists_after: bool,
) -> list[dict[str, Any]]:
    evidence_by_case = {
        "v020-local-entrypoint-import": {
            "command": version_result["command"],
            "returncode": version_result["returncode"],
            "version": version_result["stdout"].strip(),
        },
        "v020-stdio-tool-surface": {
            "tool_count": stdio["tool_count"],
            "tool_names": stdio["tool_names"],
        },
        "v020-stdio-store-recall": {
            "recall_count": stdio["recall_count"],
            "stored_id_recalled": stdio["stored_id_recalled"],
            "token_found": stdio["token_found"],
            "recalled_client_transports": stdio["recalled_client_transports"],
            "recalled_source_clients": stdio["recalled_source_clients"],
        },
        "v020-first-run-cli": {
            "command": first_run["command"],
            "schema": first_run["schema"],
            "client_config_write_mode": first_run["client_config_write_mode"],
            "task_brief_schema": first_run["task_brief_schema"],
            "contains_private_path": first_run["contains_private_path"],
        },
        "v020-task-brief-cli": {
            "command": task_brief["command"],
            "schema": task_brief["schema"],
            "used_count": task_brief["used_count"],
            "needs_review_count": task_brief["needs_review_count"],
            "no_auto_writeback": task_brief["no_auto_writeback"],
        },
        "v020-isolation-write-scope": {
            "config_path_exists_after": config_path_exists_after,
            "explicit_demo_memory_write_count": explicit_demo_memory_write_count,
            "explicit_demo_signal_write_count": explicit_demo_signal_write_count,
            "non_demo_durable_writeback_count": non_demo_durable_writeback_count,
        },
    }
    check_by_case = {
        "v020-local-entrypoint-import": checks["v020_import_sanity_pass"],
        "v020-stdio-tool-surface": checks["v020_stdio_tool_surface_pass"],
        "v020-stdio-store-recall": checks["v020_stdio_round_trip_pass"],
        "v020-first-run-cli": checks["v020_first_run_pass"],
        "v020-task-brief-cli": checks["v020_task_brief_pass"],
        "v020-isolation-write-scope": checks["v020_isolation_write_scope_pass"],
    }
    command_by_case = {
        "v020-local-entrypoint-import": version_result["command"],
        "v020-stdio-tool-surface": "MCP list_tools over python -m agent_mem_bridge",
        "v020-stdio-store-recall": f"MCP store/recall query: {V020_QUERY} {V020_TOKEN}",
        "v020-first-run-cli": first_run["command"],
        "v020-task-brief-cli": task_brief["command"],
        "v020-isolation-write-scope": "inspect temp DB/config boundaries",
    }

    cases: list[dict[str, Any]] = []
    for manifest in V020_CASE_MANIFEST:
        case_id = manifest["id"]
        passed = bool(check_by_case[case_id])
        cases.append(
            {
                **manifest,
                "query_or_command": command_by_case[case_id],
                "passed": passed,
                "failure_reason": "" if passed else _failure_reason(case_id, evidence_by_case[case_id]),
                "evidence": evidence_by_case[case_id],
            }
        )
    return cases


def _failure_reason(case_id: str, evidence: dict[str, Any]) -> str:
    if case_id == "v020-local-entrypoint-import":
        return f"entrypoint returned {evidence.get('returncode')} with version={evidence.get('version')!r}"
    if case_id == "v020-stdio-tool-surface":
        return f"tool_count={evidence.get('tool_count')} expected={EXPECTED_PUBLIC_TOOL_COUNT}"
    if case_id == "v020-stdio-store-recall":
        return "stdio store/recall did not recall the stored tokened record with stdio provenance"
    if case_id == "v020-first-run-cli":
        return "first-run CLI was not parseable, not manual-copy-only, required AMH, or leaked a private path"
    if case_id == "v020-task-brief-cli":
        return "task-brief CLI was not parseable, empty, or not read-only"
    if case_id == "v020-isolation-write-scope":
        return "proof wrote client config, non-demo rows, signals, or report generation mutated the temp store"
    return "unknown failure"


def _proof_env(
    *,
    bridge_home: Path,
    db_path: Path,
    log_dir: Path,
    config_path: Path,
) -> dict[str, str]:
    env = {
        **os.environ,
        "AGENT_MEMORY_BRIDGE_HOME": str(bridge_home),
        "AGENT_MEMORY_BRIDGE_DB_PATH": str(db_path),
        "AGENT_MEMORY_BRIDGE_LOG_DIR": str(log_dir),
        "AGENT_MEMORY_BRIDGE_CONFIG": str(config_path),
        "AGENT_MEMORY_BRIDGE_DEFAULT_SOURCE_CLIENT": "v020-clean-room-proof",
        "AGENT_MEMORY_BRIDGE_DEFAULT_CLIENT_TRANSPORT": "stdio",
        "AGENT_MEMORY_BRIDGE_TELEMETRY_MODE": "off",
        "AGENT_MEMORY_BRIDGE_WATCHER_ENABLED": "0",
        "AGENT_MEMORY_BRIDGE_REFLEX_ENABLED": "0",
        "AGENT_MEMORY_BRIDGE_CONSOLIDATION_ENABLED": "0",
        "AGENT_MEMORY_BRIDGE_EMBEDDING_SCHEDULER_ENABLED": "0",
    }
    env.pop("AGENT_MEMORY_BRIDGE_CLIENT_CONFIG_OUTPUT", None)
    return env


def _run_command(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    replacements: dict[str, str],
) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    return {
        "command": _public_command(command, replacements),
        "returncode": completed.returncode,
        "stdout": _sanitize_text(completed.stdout, replacements),
        "stderr": _sanitize_text(completed.stderr, replacements),
    }


def _loads_json(result: dict[str, Any]) -> dict[str, Any]:
    if result["returncode"] != 0:
        return {}
    try:
        return json.loads(result["stdout"])
    except json.JSONDecodeError:
        return {}


def _structured_payload(response: Any) -> dict[str, Any]:
    payload = getattr(response, "structuredContent", None) or {}
    if isinstance(payload, dict):
        return payload
    return {}


def _table_counts(db_path: Path) -> dict[str, int]:
    with sqlite3.connect(db_path) as conn:
        table_names = [
            row[0]
            for row in conn.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table'
                  AND name NOT LIKE 'sqlite_%'
                ORDER BY name
                """
            )
        ]
        return {name: int(conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]) for name in table_names}


def _namespace_counts(db_path: Path, namespace: str) -> dict[str, int]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT kind, COUNT(*)
            FROM memories
            WHERE namespace = ?
            GROUP BY kind
            """,
            (namespace,),
        ).fetchall()
    counts = {"memory": 0, "signal": 0}
    counts.update({str(kind): int(count) for kind, count in rows})
    return counts


def _memory_row_count(db_path: Path) -> int:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT COUNT(*) FROM memories").fetchone()
    return int(row[0])


def _count_delta(before: dict[str, int], after: dict[str, int]) -> int:
    keys = set(before) | set(after)
    return sum(abs(after.get(key, 0) - before.get(key, 0)) for key in keys)


def _path_replacements(
    *,
    project_root: Path,
    runtime_dir: Path,
    bridge_home: Path,
    db_path: Path,
    log_dir: Path,
    config_path: Path,
) -> dict[str, str]:
    replacements: dict[str, str] = {}
    for path, replacement in (
        (config_path, "<temp>/home/config.toml"),
        (db_path, "<temp>/home/bridge.db"),
        (log_dir, "<temp>/home/logs"),
        (bridge_home, "<temp>/home"),
        (runtime_dir, "<temp>"),
        (project_root, "<repo>"),
        (Path(sys.executable), "python"),
    ):
        for variant in _path_variants(path):
            replacements[variant] = replacement
    return dict(sorted(replacements.items(), key=lambda item: len(item[0]), reverse=True))


def _path_variants(path: Path) -> set[str]:
    raw = str(path)
    variants = {raw, raw.replace("\\", "/")}
    try:
        resolved = str(path.resolve())
        variants.add(resolved)
        variants.add(resolved.replace("\\", "/"))
    except OSError:
        pass
    return variants


def _public_command(command: list[str], replacements: dict[str, str]) -> str:
    sanitized = [_sanitize_text(part, replacements) for part in command]
    if sanitized and sanitized[0] != "python" and sanitized[0].lower().endswith(("python.exe", "python")):
        sanitized[0] = "python"
    return " ".join(sanitized)


def _sanitize_text(value: str, replacements: dict[str, str]) -> str:
    text = str(value)
    for source, target in replacements.items():
        text = text.replace(source, target)
    return text


def _contains_private_path(value: str) -> bool:
    normalized = value.replace("\\\\", "\\").lower()
    if re.search(r"[a-z]:\\", normalized):
        return True
    return any(marker in normalized for marker in ("\\wsl.localhost\\", "/home/", "/mnt/"))


def _cleanup_runtime_dir(path: Path) -> None:
    for _ in range(10):
        gc.collect()
        try:
            shutil.rmtree(path)
            return
        except FileNotFoundError:
            return
        except PermissionError:
            time.sleep(0.1)
    shutil.rmtree(path, ignore_errors=True)


def write_v020_clean_room_outputs(
    report: dict[str, Any],
    *,
    report_path: Path,
    transcript_path: Path,
) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    transcript_path.write_text(render_v020_clean_room_markdown(report) + "\n", encoding="utf-8")
