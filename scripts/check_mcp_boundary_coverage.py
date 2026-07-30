from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

SERVER_START_MARKER = "# MCP_BOUNDARY_COVERAGE_START"
SERVER_END_MARKER = "# MCP_BOUNDARY_COVERAGE_END"


def _coverage_file(files: dict[str, Any], suffix: str) -> dict[str, Any]:
    matches = [value for path, value in files.items() if path.replace("\\", "/").endswith(suffix)]
    if len(matches) != 1:
        raise RuntimeError(f"expected one coverage entry ending in {suffix!r}, found {len(matches)}")
    return matches[0]


def _branch_percent(entry: dict[str, Any]) -> float:
    summary = entry["summary"]
    total = int(summary["num_branches"])
    covered = int(summary["covered_branches"])
    return 100.0 if total == 0 else covered * 100.0 / total


def _marked_branch_percent(entry: dict[str, Any], source_path: Path) -> float:
    lines = source_path.read_text(encoding="utf-8").splitlines()
    start = next(index for index, line in enumerate(lines, start=1) if line.strip() == SERVER_START_MARKER)
    end = next(index for index, line in enumerate(lines, start=1) if line.strip() == SERVER_END_MARKER)
    executed = [branch for branch in entry.get("executed_branches", []) if start < int(branch[0]) < end]
    missing = [branch for branch in entry.get("missing_branches", []) if start < int(branch[0]) < end]
    total = len(executed) + len(missing)
    if total == 0:
        raise RuntimeError("server MCP boundary region contains no measured branches")
    return len(executed) * 100.0 / total


def check_coverage(report_path: Path, root: Path) -> dict[str, Any]:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    files = report["files"]
    boundary_entry = _coverage_file(files, "src/agent_mem_bridge/mcp_boundary.py")
    server_entry = _coverage_file(files, "src/agent_mem_bridge/server.py")
    boundary_percent = _branch_percent(boundary_entry)
    server_boundary_percent = _marked_branch_percent(
        server_entry,
        root / "src" / "agent_mem_bridge" / "server.py",
    )
    checks = {
        "mcp_boundary_branch_coverage": boundary_percent >= 95.0,
        "server_mcp_boundary_branch_coverage": server_boundary_percent >= 90.0,
    }
    return {
        "ok": all(checks.values()),
        "mcp_boundary_branch_percent": round(boundary_percent, 2),
        "server_mcp_boundary_branch_percent": round(server_boundary_percent, 2),
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Enforce branch coverage for the MCP protocol boundary.")
    parser.add_argument("report", type=Path)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    result = check_coverage(args.report.resolve(), args.root.resolve())
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
