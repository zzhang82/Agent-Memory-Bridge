from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import tomllib

from .client_config import render_example_client_configs


PUBLIC_ONBOARDING_FILES = (
    Path("README.md"),
    Path("README.zh-CN.md"),
    Path("CONTRIBUTING.md"),
    Path("config.example.toml"),
    Path("benchmark/README.md"),
    Path("docs/CONFIGURATION.md"),
    Path("docs/INTEGRATIONS.md"),
    Path("examples/README.md"),
)

README_LINKS = (
    "docs/INTEGRATIONS.md",
)

BLOCKED_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"C:\\Users\\frank", re.IGNORECASE), "Maintainer Windows home path leaked."),
    (re.compile(r"C:/Users/frank", re.IGNORECASE), "Maintainer Windows home path leaked."),
    (re.compile(r"D:\\playground", re.IGNORECASE), "Maintainer workspace path leaked."),
    (re.compile(r"D:/playground", re.IGNORECASE), "Maintainer workspace path leaked."),
    (re.compile(r"%(?:USERPROFILE|APPDATA|LOCALAPPDATA)%", re.IGNORECASE), "Windows environment path leaked."),
    (re.compile(r"\.\\(?:\.?venv|scripts|runtime|config)", re.IGNORECASE), "Windows-style relative path leaked."),
    (re.compile(r"\\Scripts\\", re.IGNORECASE), "Windows virtualenv command path leaked."),
    (re.compile(r"\bMy-G\b"), "Maintainer network path leaked."),
    (re.compile(r"\bxwechat\b", re.IGNORECASE), "Private app path leaked."),
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
            else:
                tomllib.loads(rendered.content)
        except (json.JSONDecodeError, tomllib.TOMLDecodeError) as exc:
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
