from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

import tomllib


README_NAMES = ("README.md", "README.zh-CN.md")
REQUIRED_BENCHMARK_KEYS = (
    "question_count",
    "memory_expected_top1_accuracy",
    "memory_mrr",
    "file_scan_expected_top1_accuracy",
    "file_scan_mrr",
)
REQUIRED_CALIBRATION_KEYS = (
    "sample_count",
    "classifier_exact_match_rate",
    "fallback_exact_match_rate",
    "classifier_better_count",
    "fallback_better_count",
    "classifier_filtered_low_confidence_count",
)
SEMVER_PATTERN = re.compile(r"(?<![A-Za-z0-9-])v?(\d+\.\d+\.\d+)(?![A-Za-z0-9-])")
KV_PATTERN = re.compile(
    r"(?P<key>[A-Za-z_][A-Za-z0-9_]+)\s*=\s*(?P<value>true|false|\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
PASSED_PATTERN = re.compile(r"(\d+)\s+passed")
PUBLIC_TOOL_COUNT_PATTERN = re.compile(r"`?(\d+)`?\s+public MCP tools", re.IGNORECASE)
TOOL_TOKEN_PATTERN = re.compile(r"`([a-z_][a-z0-9_]*)`")


def run_release_contract_check(
    root: Path,
    *,
    test_count_provider: Callable[[Path], int] | None = None,
) -> dict[str, Any]:
    project_root = root.resolve()
    readme_paths = [project_root / name for name in README_NAMES if (project_root / name).exists()]
    if not readme_paths:
        raise FileNotFoundError("No release README files found.")

    pyproject_version = load_pyproject_version(project_root / "pyproject.toml")
    expected_facts = load_expected_facts(project_root)
    main_readme_path = project_root / "README.md"
    main_readme_text = main_readme_path.read_text(encoding="utf-8")
    server_tools = load_server_tool_names(project_root / "src" / "agent_mem_bridge" / "server.py")
    test_count = (
        test_count_provider(project_root)
        if test_count_provider is not None
        else collect_test_count(project_root)
    )

    checks: list[dict[str, Any]] = []

    checks.append(
        build_version_check(
            pyproject_version=pyproject_version,
            readme_paths=readme_paths,
        )
    )
    checks.append(
        build_fact_check(
            readme_paths=readme_paths,
            expected_facts=expected_facts,
        )
    )
    checks.append(
        build_test_count_check(
            readme_paths=readme_paths,
            expected_test_count=test_count,
        )
    )
    checks.append(
        build_tool_surface_check(
            readme_text=main_readme_text,
            server_tools=server_tools,
        )
    )
    checks.append(
        build_demo_assets_check(
            project_root=project_root,
        )
    )

    return {
        "ok": all(check["ok"] for check in checks),
        "root": str(project_root),
        "pyproject_version": pyproject_version,
        "server_tool_count": len(server_tools),
        "test_count": test_count,
        "checks": checks,
    }


def build_version_check(pyproject_version: str, readme_paths: list[Path]) -> dict[str, Any]:
    mismatches: list[dict[str, Any]] = []
    readmes: list[dict[str, Any]] = []
    ok = True
    for path in readme_paths:
        versions = sorted(set(SEMVER_PATTERN.findall(path.read_text(encoding="utf-8"))))
        readmes.append({"path": str(path), "versions": versions})
        if not versions or versions != [pyproject_version]:
            ok = False
            mismatches.append(
                {
                    "path": str(path),
                    "expected_version": pyproject_version,
                    "actual_versions": versions,
                }
            )
    return {
        "name": "pyproject_version_matches_readmes",
        "ok": ok,
        "pyproject_version": pyproject_version,
        "readmes": readmes,
        "mismatches": mismatches,
    }


def build_fact_check(readme_paths: list[Path], expected_facts: dict[str, int | float]) -> dict[str, Any]:
    required_keys = REQUIRED_BENCHMARK_KEYS + REQUIRED_CALIBRATION_KEYS
    mismatches: list[dict[str, Any]] = []
    ok = True
    for path in readme_paths:
        facts = extract_key_values(path.read_text(encoding="utf-8"))
        for key in required_keys:
            actual_values = facts.get(key, [])
            expected_value = expected_facts[key]
            if not actual_values or any(value != expected_value for value in actual_values):
                ok = False
                mismatches.append(
                    {
                        "path": str(path),
                        "key": key,
                        "expected": expected_value,
                        "actual": actual_values,
                    }
                )
    return {
        "name": "readme_facts_match_snapshot_reports",
        "ok": ok,
        "expected_facts": expected_facts,
        "mismatches": mismatches,
    }


def build_test_count_check(readme_paths: list[Path], expected_test_count: int) -> dict[str, Any]:
    mismatches: list[dict[str, Any]] = []
    ok = True
    for path in readme_paths:
        counts = extract_pass_counts(path.read_text(encoding="utf-8"))
        if not counts or any(count != expected_test_count for count in counts):
            ok = False
            mismatches.append(
                {
                    "path": str(path),
                    "expected": expected_test_count,
                    "actual": counts,
                }
            )
    return {
        "name": "readme_test_count_is_aligned",
        "ok": ok,
        "expected_test_count": expected_test_count,
        "mismatches": mismatches,
    }


def build_tool_surface_check(readme_text: str, server_tools: set[str]) -> dict[str, Any]:
    public_tool_counts = extract_public_tool_counts(readme_text)
    readme_tool_names = extract_readme_tool_names(readme_text)
    expected_count = len(server_tools)
    ok = True
    mismatches: list[dict[str, Any]] = []

    if not public_tool_counts or any(count != expected_count for count in public_tool_counts):
        ok = False
        mismatches.append(
            {
                "kind": "count",
                "expected": expected_count,
                "actual": public_tool_counts,
            }
        )

    if readme_tool_names != server_tools:
        ok = False
        mismatches.append(
            {
                "kind": "tool_names",
                "expected": sorted(server_tools),
                "actual": sorted(readme_tool_names),
            }
        )

    return {
        "name": "public_mcp_tool_count_matches_server_surface",
        "ok": ok,
        "expected_count": expected_count,
        "server_tools": sorted(server_tools),
        "readme_tools": sorted(readme_tool_names),
        "mismatches": mismatches,
    }


def build_demo_assets_check(project_root: Path) -> dict[str, Any]:
    demo_dir = project_root / "examples" / "demo"
    required_assets = {
        demo_dir / "terminal-demo.cast",
        demo_dir / "terminal-demo.gif",
    }

    demo_readme = demo_dir / "README.md"
    if demo_readme.exists():
        text = demo_readme.read_text(encoding="utf-8")
        for asset_name in sorted(set(re.findall(r"terminal-demo\.[A-Za-z0-9]+", text))):
            required_assets.add(demo_dir / asset_name)

    missing = sorted(str(path) for path in required_assets if not path.exists())
    return {
        "name": "current_demo_assets_exist",
        "ok": not missing,
        "required_assets": [str(path) for path in sorted(required_assets)],
        "missing_assets": missing,
    }


def load_pyproject_version(path: Path) -> str:
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    return str(data["project"]["version"])


def load_expected_facts(project_root: Path) -> dict[str, int | float]:
    benchmark_report = json.loads((project_root / "benchmark" / "latest-report.json").read_text(encoding="utf-8"))
    calibration_report = json.loads(
        (project_root / "benchmark" / "latest-calibration-report.json").read_text(encoding="utf-8")
    )
    benchmark_summary = benchmark_report["summary"]
    calibration_summary = calibration_report["summary"]
    expected: dict[str, int | float] = {}
    for key in REQUIRED_BENCHMARK_KEYS:
        expected[key] = benchmark_summary[key]
    for key in REQUIRED_CALIBRATION_KEYS:
        expected[key] = calibration_summary[key]
    return expected


def extract_key_values(text: str) -> dict[str, list[int | float | bool]]:
    values: dict[str, list[int | float | bool]] = {}
    for match in KV_PATTERN.finditer(text):
        key = match.group("key")
        value = coerce_value(match.group("value"))
        values.setdefault(key, []).append(value)
    return values


def extract_pass_counts(text: str) -> list[int]:
    return [int(match) for match in PASSED_PATTERN.findall(text)]


def extract_public_tool_counts(text: str) -> list[int]:
    return [int(match) for match in PUBLIC_TOOL_COUNT_PATTERN.findall(text)]


def extract_readme_tool_names(text: str) -> set[str]:
    section = slice_markdown_section(text, "## MCP Tools")
    tool_names: set[str] = set()
    for line in section.splitlines():
        if not line.lstrip().startswith("-"):
            continue
        for token in TOOL_TOKEN_PATTERN.findall(line):
            if "_" in token or token in {"store", "recall", "browse", "stats", "forget", "promote", "export"}:
                tool_names.add(token)
    return tool_names


def slice_markdown_section(text: str, heading: str) -> str:
    start = text.find(heading)
    if start == -1:
        return ""
    remainder = text[start + len(heading) :]
    next_heading = remainder.find("\n## ")
    if next_heading == -1:
        return remainder
    return remainder[:next_heading]


def load_server_tool_names(path: Path) -> set[str]:
    module = ast.parse(path.read_text(encoding="utf-8"))
    tool_names: set[str] = set()
    for node in module.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        if any(is_mcp_tool_decorator(decorator) for decorator in node.decorator_list):
            tool_names.add(node.name)
    return tool_names


def is_mcp_tool_decorator(node: ast.expr) -> bool:
    target = node.func if isinstance(node, ast.Call) else node
    return isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == "mcp" and target.attr == "tool"


def collect_test_count(project_root: Path) -> int:
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "tests"],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=True,
    )
    match = re.search(r"(\d+)\s+tests collected", completed.stdout)
    if match is None:
        raise ValueError("Could not determine collected test count from pytest output.")
    return int(match.group(1))


def coerce_value(raw: str) -> int | float | bool:
    lowered = raw.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if "." in raw:
        return float(raw)
    return int(raw)
