from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Literal

from mcp import StdioServerParameters
from mcp.client import Client
from mcp.client.stdio import stdio_client
from mcp.types import Implementation

from agent_mem_bridge.mcp_boundary import MCP_LEGACY_TEST_VERSION, MCP_MODERN_VERSION, PUBLIC_TOOL_ORDER
from agent_mem_bridge.storage import MemoryStore

PROOF_CLIENT = Implementation(name="amb-mcp-reliability-proof", version="0.26.1")


def _direct_child_process_ids() -> list[int] | None:
    # Linux-only proof: unsupported platforms return None and fail closed
    # instead of claiming child-process cleanup without evidence.
    children_path = Path("/proc") / str(os.getpid()) / "task" / str(os.getpid()) / "children"
    try:
        raw = children_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return [int(value) for value in raw.split() if value.isdigit()]


def _server_parameters(project_root: Path, runtime_dir: Path) -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "agent_mem_bridge"],
        cwd=project_root,
        env={
            **os.environ,
            "AGENT_MEMORY_BRIDGE_HOME": str(runtime_dir),
            "AGENT_MEMORY_BRIDGE_DB_PATH": str(runtime_dir / "shared.db"),
            "AGENT_MEMORY_BRIDGE_LOG_DIR": str(runtime_dir / "logs"),
        },
    )


async def _writer(project_root: Path, runtime_dir: Path, index: int) -> dict[str, Any]:
    mode: Literal["auto", "legacy"] = "auto" if index % 2 == 0 else "legacy"
    async with Client(
        stdio_client(_server_parameters(project_root, runtime_dir)), mode=mode, client_info=PROOF_CLIENT
    ) as client:
        result = await client.call_tool(
            "store",
            {
                "namespace": "project:mcp-concurrency-proof",
                "content": f"Concurrent MCP writer proof record {index:03d}.",
                "kind": "memory",
                "tags": ["proof:mcp-concurrency"],
            },
        )
        payload = result.structured_content or {}
        return {
            "ok": result.is_error is False and bool(payload.get("stored")),
            "mode": mode,
            "protocol_version": client.protocol_version,
        }


async def _connection_cycle(project_root: Path, runtime_dir: Path, index: int) -> dict[str, Any]:
    mode: Literal["auto", "legacy"] = "auto" if index % 2 == 0 else "legacy"
    async with Client(
        stdio_client(_server_parameters(project_root, runtime_dir)), mode=mode, client_info=PROOF_CLIENT
    ) as client:
        listed = await client.list_tools(cache_mode="bypass")
        expected_version = MCP_MODERN_VERSION if mode == "auto" else MCP_LEGACY_TEST_VERSION
        return {
            "ok": client.protocol_version == expected_version
            and [tool.name for tool in listed.tools] == list(PUBLIC_TOOL_ORDER),
            "mode": mode,
        }


async def run_proof(project_root: Path, runtime_dir: Path, *, writers: int, cycles: int) -> dict[str, Any]:
    runtime_dir.mkdir(parents=True, exist_ok=True)
    MemoryStore(runtime_dir / "shared.db", log_dir=runtime_dir / "logs")
    writer_results = await asyncio.gather(
        *(_writer(project_root, runtime_dir, index) for index in range(writers)),
        return_exceptions=True,
    )
    writer_errors = [type(item).__name__ for item in writer_results if isinstance(item, BaseException)]
    writer_reports = [item for item in writer_results if isinstance(item, dict)]
    stored_count = MemoryStore(runtime_dir / "shared.db", log_dir=runtime_dir / "logs").stats(
        "project:mcp-concurrency-proof"
    )["total_count"]

    cycle_reports: list[dict[str, Any]] = []
    cycle_errors: list[str] = []
    for index in range(cycles):
        try:
            cycle_reports.append(await _connection_cycle(project_root, runtime_dir, index))
        except Exception as exc:
            cycle_errors.append(type(exc).__name__)

    temp_artifacts = sorted(
        path.name
        for path in runtime_dir.iterdir()
        if path.name.startswith(("tmp", ".tmp")) or path.suffix in {".tmp", ".temp"}
    )
    remaining_child_processes = _direct_child_process_ids()
    checks = {
        "concurrent_processes": len(writer_reports) == writers and not writer_errors,
        "concurrent_writes": all(item["ok"] for item in writer_reports) and stored_count == writers,
        "connect_disconnect": len(cycle_reports) == cycles
        and not cycle_errors
        and all(item["ok"] for item in cycle_reports),
        "process_cleanup": remaining_child_processes == [],
        "temporary_artifacts": not temp_artifacts,
    }
    return {
        "ok": all(checks.values()),
        "writers": writers,
        "cycles": cycles,
        "stored_count": stored_count,
        "writer_errors": writer_errors,
        "cycle_errors": cycle_errors,
        "process_cleanup_supported": remaining_child_processes is not None,
        "remaining_child_processes": remaining_child_processes,
        "temporary_artifacts": temp_artifacts,
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Stress dual-era stdio concurrency and connection cleanup.")
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--runtime-dir", type=Path)
    parser.add_argument("--writers", type=int, default=20)
    parser.add_argument("--cycles", type=int, default=100)
    args = parser.parse_args()

    if args.runtime_dir is None:
        with TemporaryDirectory(prefix="amb-mcp-reliability-") as temp_dir:
            report = asyncio.run(
                run_proof(args.project_root.resolve(), Path(temp_dir), writers=args.writers, cycles=args.cycles)
            )
    else:
        report = asyncio.run(
            run_proof(
                args.project_root.resolve(),
                args.runtime_dir.resolve(),
                writers=args.writers,
                cycles=args.cycles,
            )
        )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
