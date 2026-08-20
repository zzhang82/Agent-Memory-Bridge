from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import Any

from .client_config import render_example_client_configs
from .first_run import PINNED_INSTALL_VERSION, RELEASE_INSTALL_GATE_NOTE

PUBLIC_ONBOARDING_FILES = (
    Path("INSTALL_FOR_AGENTS.md"),
    Path("llms-install.md"),
    Path("llms.txt"),
    Path("README.md"),
    Path("README.zh-CN.md"),
    Path("CONTRIBUTING.md"),
    Path("Dockerfile"),
    Path("config.example.toml"),
    Path("benchmark/README.md"),
    Path("docs/CONFIGURATION.md"),
    Path("docs/HARNESS-DESIGN.md"),
    Path("docs/INTEGRATIONS.md"),
    Path("examples/README.md"),
)

README_LINKS = ("docs/INTEGRATIONS.md",)
VERSIONED_INSTALL_GUIDES = (
    Path("INSTALL_FOR_AGENTS.md"),
    Path("llms-install.md"),
    Path("llms.txt"),
)

BLOCKED_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b[A-Za-z]:[\\/][^\s`\"']*", re.IGNORECASE), "Windows absolute path leaked."),
    (re.compile(r"%(?:USERPROFILE|APPDATA|LOCALAPPDATA)%", re.IGNORECASE), "Windows environment path leaked."),
    (re.compile(r"\.\\(?:\.?venv|scripts|runtime|config)", re.IGNORECASE), "Windows-style relative path leaked."),
    (re.compile(r"\\Scripts\\", re.IGNORECASE), "Windows virtualenv command path leaked."),
    (re.compile(r"\bCODEX_HOME\s*=", re.IGNORECASE), "Codex-specific home default leaked."),
    (re.compile(r"/tmp/\.codex", re.IGNORECASE), "Codex-specific temp home leaked."),
    (re.compile(r"\\\\[^\\/\s]+[\\/][^\\/\s]+", re.IGNORECASE), "Private network path leaked."),
    (re.compile(r"\b(?:wechat|temp|private)[\\/]", re.IGNORECASE), "Private app or temp path leaked."),
    (re.compile(r"\bproject:mem-store\b"), "Repository-specific namespace leaked into onboarding surface."),
    (re.compile(r"\bcole-core\b"), "Cole-specific namespace leaked into onboarding surface."),
)


def run_onboarding_contract_check(root: Path) -> dict[str, Any]:
    project_root = root.resolve()
    checks = [
        _required_docs_check(project_root),
        _readme_links_check(project_root),
        _example_configs_check(),
        _onboarding_docs_leak_check(project_root),
        _safe_setup_apply_contract_check(project_root),
        _first_use_memory_loop_contract_check(project_root),
        _versioned_install_tool_surface_check(project_root),
    ]
    return {
        "ok": all(check["ok"] for check in checks),
        "root": str(project_root),
        "checks": checks,
    }


def _required_docs_check(project_root: Path) -> dict[str, Any]:
    missing = [str(path) for path in PUBLIC_ONBOARDING_FILES if not (project_root / path).exists()]
    return {
        "name": "required_onboarding_docs_exist",
        "ok": not missing,
        "missing": missing,
    }


def _readme_links_check(project_root: Path) -> dict[str, Any]:
    readme_path = project_root / "README.md"
    if not readme_path.exists():
        return {
            "name": "readme_links_integrations_doc",
            "ok": False,
            "missing_links": list(README_LINKS),
        }
    text = readme_path.read_text(encoding="utf-8")
    missing_links = [target for target in README_LINKS if target not in text]
    return {
        "name": "readme_links_integrations_doc",
        "ok": not missing_links,
        "missing_links": missing_links,
    }


def _example_configs_check() -> dict[str, Any]:
    failures: list[dict[str, Any]] = []
    for rendered in render_example_client_configs():
        try:
            if rendered.format == "json":
                json.loads(rendered.content)
            elif rendered.format == "toml":
                tomllib.loads(rendered.content)
            elif rendered.format == "yaml":
                _validate_yaml_like_mcp_config(rendered.content)
            else:
                raise ValueError(f"Unsupported rendered config format: {rendered.format}")
        except (json.JSONDecodeError, tomllib.TOMLDecodeError, ValueError) as exc:
            failures.append(
                {
                    "client": rendered.client,
                    "format": rendered.format,
                    "error": str(exc),
                }
            )
        for pattern, reason in BLOCKED_PATTERNS:
            if pattern.search(rendered.content):
                failures.append(
                    {
                        "client": rendered.client,
                        "format": rendered.format,
                        "error": reason,
                        "pattern": pattern.pattern,
                    }
                )
    return {
        "name": "generated_example_configs_parse_and_stay_sanitized",
        "ok": not failures,
        "failures": failures,
    }


def _validate_yaml_like_mcp_config(content: str) -> None:
    required_lines = (
        "mcp_servers:",
        "  agentMemoryBridge:",
        "    command:",
        "    args:",
        "    env:",
    )
    for line in required_lines:
        if line not in content:
            raise ValueError(f"Missing YAML config line: {line}")


def _onboarding_docs_leak_check(project_root: Path) -> dict[str, Any]:
    violations: list[dict[str, Any]] = []
    for relative_path in PUBLIC_ONBOARDING_FILES:
        absolute_path = project_root / relative_path
        if not absolute_path.exists():
            continue
        for line_number, line in enumerate(absolute_path.read_text(encoding="utf-8").splitlines(), start=1):
            for pattern, reason in BLOCKED_PATTERNS:
                if pattern.search(line):
                    violations.append(
                        {
                            "path": str(relative_path),
                            "line": line_number,
                            "reason": reason,
                            "line_text": line.strip(),
                        }
                    )
    return {
        "name": "onboarding_docs_stay_placeholder_safe",
        "ok": not violations,
        "violations": violations,
    }


def _safe_setup_apply_contract_check(project_root: Path) -> dict[str, Any]:
    install_path = project_root / "INSTALL_FOR_AGENTS.md"
    cli_path = project_root / "src" / "agent_mem_bridge" / "cli.py"
    if not install_path.exists() or not cli_path.exists():
        return {
            "name": "safe_setup_apply_contract_is_explicit",
            "ok": False,
            "missing": [str(path) for path in (install_path, cli_path) if not path.exists()],
        }
    install_text = install_path.read_text(encoding="utf-8")
    cli_text = cli_path.read_text(encoding="utf-8")
    required_install_terms = (
        "Changes written: 0",
        "setup --apply",
        "setup --apply --yes --json",
        "setup --rollback",
        "There is no `--force` switch.",
        "manual-review plans are\nnever overwritten",
    )
    required_cli_terms = (
        'setup_parser.add_argument(\n        "--apply"',
        'setup_parser.add_argument(\n        "--yes"',
        'setup_parser.add_argument(\n        "--rollback"',
        "if namespace.yes and not namespace.apply:",
    )
    missing = [term for term in required_install_terms if term not in install_text]
    missing.extend(f"cli:{term}" for term in required_cli_terms if term not in cli_text)
    return {
        "name": "safe_setup_apply_contract_is_explicit",
        "ok": not missing,
        "missing": missing,
    }


def _first_use_memory_loop_contract_check(project_root: Path) -> dict[str, Any]:
    install_path = project_root / "INSTALL_FOR_AGENTS.md"
    first_run_path = project_root / "src" / "agent_mem_bridge" / "first_run.py"
    if not install_path.exists() or not first_run_path.exists():
        return {
            "name": "first_use_memory_loop_contract_is_explicit",
            "ok": False,
            "missing": [str(path) for path in (install_path, first_run_path) if not path.exists()],
        }
    install_text = install_path.read_text(encoding="utf-8")
    first_run_text = first_run_path.read_text(encoding="utf-8")
    required_install_terms = (
        "`setup` owns safe client connection.",
        "`first-run` as a read-only product guide",
        "existing `store` tool",
        "existing `feedback` tool",
        "never seeds\nmemory",
        "does not\nprove that feedback caused a later recall",
    )
    required_first_run_terms = (
        'FIRST_RUN_SCHEMA = "memory.first_run.v2"',
        "guided_existing_store_tool_only",
        "shadow_only_no_memory_or_ranking_change",
        "Feedback is recorded for review and evaluation.",
    )
    missing = [term for term in required_install_terms if term not in install_text]
    missing.extend(f"first_run:{term}" for term in required_first_run_terms if term not in first_run_text)
    return {
        "name": "first_use_memory_loop_contract_is_explicit",
        "ok": not missing,
        "missing": missing,
    }


def release_install_tool_count(version: str) -> int:
    """Return the public MCP surface size for a pinned release-install version."""

    parts = version.split(".")
    if len(parts) != 3 or any(not part.isdigit() for part in parts):
        raise ValueError("release-install version must be a semantic version")
    return 17 if tuple(int(part) for part in parts) >= (0, 27, 0) else 13


def _versioned_install_tool_surface_check(project_root: Path) -> dict[str, Any]:
    pyproject_path = project_root / "pyproject.toml"
    if not pyproject_path.exists():
        return {
            "name": "versioned_install_tool_surface_is_explicit",
            "ok": True,
            "skipped": "pyproject.toml is absent",
        }

    release_install_tool_count_value = release_install_tool_count(PINNED_INSTALL_VERSION)
    package_version = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))["project"]["version"]
    source_tool_count = release_install_tool_count(str(package_version))
    expected_markers = [
        f"The pinned `v{PINNED_INSTALL_VERSION}` release-install route exposes "
        f"`{release_install_tool_count_value}` public MCP tools at client registration.",
        RELEASE_INSTALL_GATE_NOTE,
    ]
    if package_version != PINNED_INSTALL_VERSION:
        expected_markers.append(
            f"Source `{package_version}` differs from pinned release-install `{PINNED_INSTALL_VERSION}`; "
            "use a source checkout until its exact-commit CI gate passes and its tag is created."
        )

    missing: list[dict[str, Any]] = []
    for relative_path in VERSIONED_INSTALL_GUIDES:
        path = project_root / relative_path
        if not path.exists():
            missing.append({"path": str(relative_path), "markers": expected_markers})
            continue
        text = " ".join(path.read_text(encoding="utf-8").split())
        missing_markers = [marker for marker in expected_markers if marker not in text]
        if missing_markers:
            missing.append({"path": str(relative_path), "markers": missing_markers})

    return {
        "name": "versioned_install_tool_surface_is_explicit",
        "ok": not missing,
        "release_install_version": PINNED_INSTALL_VERSION,
        "release_install_tool_count": release_install_tool_count_value,
        "source_version": package_version,
        "source_tool_count": source_tool_count,
        "missing": missing,
    }
