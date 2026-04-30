from __future__ import annotations

import re
from pathlib import Path
from typing import Any


PUBLIC_DOC_PATHS = (
    Path("README.md"),
    Path("README.zh-CN.md"),
    Path("SECURITY.md"),
    Path("CONTRIBUTING.md"),
    Path(".github/ISSUE_TEMPLATE/bug_report.yml"),
    Path(".github/ISSUE_TEMPLATE/client_integration_request.yml"),
    Path(".github/ISSUE_TEMPLATE/config.yml"),
    Path(".github/ISSUE_TEMPLATE/good_first_issue.yml"),
    Path(".github/ISSUE_TEMPLATE/memory_taxonomy_question.yml"),
    Path("benchmark/README.md"),
    Path("docs/CLIENT-PROVENANCE.md"),
    Path("docs/COMPARISON.md"),
    Path("docs/CONFIGURATION.md"),
    Path("docs/CONTEXT-ASSEMBLY.md"),
    Path("docs/INTEGRATIONS.md"),
    Path("docs/MEMORY-TAXONOMY.md"),
    Path("docs/PROMOTION-RULES.md"),
    Path("examples/README.md"),
    Path("examples/demo/README.md"),
    Path("examples/session-notes/demo/01-memory-note.md"),
    Path("examples/session-notes/demo/02-signal-note.md"),
    Path("examples/session-payloads/sample-closeout.json"),
)

PUBLIC_BINARY_ASSETS = (
    Path("examples/diagrams/amb-overview.png"),
)

README_PATHS = (
    Path("README.md"),
    Path("README.zh-CN.md"),
)

BLOCKED_DOC_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"\bcole-(?:core|team|workflows|skills|workspace|reflex|consolidation)\b"),
        "Cole-specific namespace or writer example leaked into a public doc.",
    ),
    (
        re.compile(r"\bproject:mem-store\b"),
        "Repository-specific project namespace leaked into a public doc.",
    ),
    (
        re.compile(r"\bsource:codex\b"),
        "Codex-specific source tag leaked into a public doc where a generic example should be used.",
    ),
    (
        re.compile(r"\[\[Operator Core\]\]"),
        "Operator-specific wiki link leaked into a public doc.",
    ),
    (
        re.compile(r"C:\\Users\\frank", re.IGNORECASE),
        "Maintainer workstation path leaked into a public doc.",
    ),
    (
        re.compile(r"C:/Users/frank", re.IGNORECASE),
        "Maintainer workstation path leaked into a public doc.",
    ),
    (
        re.compile(r"D:\\playground", re.IGNORECASE),
        "Maintainer workspace path leaked into a public doc.",
    ),
    (
        re.compile(r"D:/playground", re.IGNORECASE),
        "Maintainer workspace path leaked into a public doc.",
    ),
    (
        re.compile(r"\bMy-G\b"),
        "Maintainer network path leaked into a public doc.",
    ),
    (
        re.compile(r"\bxwechat\b", re.IGNORECASE),
        "Private app path leaked into a public doc.",
    ),
)

BLOCKED_README_LINKS = (
    "docs/ROADMAP.md",
    "docs/PRODUCTION-STATUS.md",
    "docs/PUBLIC-README-RELEASE-OUTLINE.md",
    "docs/0.7-CUTOVER-READINESS.md",
    "docs/0.7-NARRATIVE.md",
    "docs/0.8-DIRECTION.md",
    "docs/STARTUP-PROTOCOL.md",
    "docs/MODEL-ROUTING.md",
    "docs/CONVERSATION-INGEST.md",
    "docs/PROFILE-CONTROL-ARCHITECTURE.md",
)

PUBLIC_CORE_NOTES = (
    "README / README.zh-CN / CONTRIBUTING",
    "GitHub issue templates for public support intake",
    "benchmark README and checked-in snapshot reports",
    "public docs such as comparison, configuration, provenance, memory taxonomy, and promotion rules",
    "examples and demo assets that are already sanitized",
    "released runtime behind the 10 MCP tools",
)

PRIVATE_LAB_NOTES = (
    "profile migration and cutover helpers",
    "operator-specific startup doctrine and local rollout notes",
    "archived release framing and planning notes",
    "local replay / belief-lab tooling that is not part of the public product story",
)


def run_public_surface_check(root: Path) -> dict[str, Any]:
    project_root = root.resolve()
    violations: list[dict[str, Any]] = []
    checked_files: list[str] = []

    for relative_path in PUBLIC_DOC_PATHS:
        absolute_path = project_root / relative_path
        if not absolute_path.exists():
            continue
        checked_files.append(str(relative_path))
        text = absolute_path.read_text(encoding="utf-8")
        violations.extend(scan_text_for_blocked_patterns(relative_path, text))

    for relative_path in PUBLIC_BINARY_ASSETS:
        absolute_path = project_root / relative_path
        if not absolute_path.exists():
            violations.append(
                {
                    "path": str(relative_path),
                    "kind": "missing-binary-asset",
                    "reason": "Expected public binary asset is missing.",
                }
            )
            continue
        checked_files.append(str(relative_path))

    for relative_path in README_PATHS:
        absolute_path = project_root / relative_path
        if not absolute_path.exists():
            continue
        text = absolute_path.read_text(encoding="utf-8")
        violations.extend(scan_readme_links(relative_path, text))

    return {
        "ok": not violations,
        "root": str(project_root),
        "checked_files": checked_files,
        "violations": violations,
        "public_core_notes": list(PUBLIC_CORE_NOTES),
        "private_lab_notes": list(PRIVATE_LAB_NOTES),
    }


def scan_text_for_blocked_patterns(relative_path: Path, text: str) -> list[dict[str, Any]]:
    violations: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        for pattern, reason in BLOCKED_DOC_PATTERNS:
            match = pattern.search(line)
            if match is None:
                continue
            violations.append(
                {
                    "path": str(relative_path),
                    "line": line_number,
                    "kind": "blocked-pattern",
                    "pattern": pattern.pattern,
                    "reason": reason,
                    "line_text": line.strip(),
                }
            )
    return violations


def scan_readme_links(relative_path: Path, text: str) -> list[dict[str, Any]]:
    violations: list[dict[str, Any]] = []
    for blocked_link in BLOCKED_README_LINKS:
        if blocked_link not in text:
            continue
        violations.append(
            {
                "path": str(relative_path),
                "kind": "blocked-link",
                "target": blocked_link,
                "reason": "Maintainer-only or archive docs should not be linked from the public README surface.",
            }
        )
    return violations
