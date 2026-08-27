from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

_CURRENT_RELEASE_MARKERS = (
    "PyPI Distribution",
    "pip install agent-memory-bridge==",
    "PyPI Trusted Publishing",
    "GitHub OIDC",
    "schema remains v12",
    "exactly 17 tools",
    "no MCP tool #18",
    "no automatic learning",
)


def run_current_source_release_contract_check(
    root: Path,
    *,
    test_count_provider: Callable[[Path], int] | None = None,
) -> dict[str, Any]:
    """Run the reusable release contract plus current-source release identity checks.

    The long-lived release contract still knows how to validate historical proof
    foundations. This wrapper makes the checked-out source line explicit without
    turning each patch version into another hard-coded branch in that module.
    """

    from . import release_contract

    project_root = root.resolve()
    version = release_contract.load_pyproject_version(project_root / "pyproject.toml")
    report = release_contract.run_release_contract_check(
        project_root,
        test_count_provider=test_count_provider,
        enforce_current_source_identity=False,
    )

    checks = [
        check
        for check in report["checks"]
        if check["name"]
        not in {
            "v020_proof_version_matches_pyproject",
            "v021_governed_change_proof_matches_release_gate",
            "v027_episode_release_contract",
            "historical_v027_episode_contract_retained_for_v028_candidate",
        }
    ]

    proof = release_contract.build_v027_episode_release_check(
        project_root,
        release_contract.V027_EPISODE_RELEASE,
    )
    proof["name"] = "historical_v027_episode_contract_retained_for_current_source"
    proof["current_source_version"] = version
    checks.append(proof)
    checks.append(_current_release_notes_check(project_root, version))

    report["checks"] = checks
    report["ok"] = all(check["ok"] for check in checks)
    return report


def _current_release_notes_check(project_root: Path, version: str) -> dict[str, Any]:
    path = project_root / "docs" / f"v{version}-announcement.md"
    mismatches: list[dict[str, Any]] = []

    if not path.exists():
        mismatches.append({"field": str(path), "expected": "present", "actual": "missing"})
        return {
            "name": "current_source_release_notes_match_package_version",
            "ok": False,
            "version": version,
            "path": str(path),
            "mismatches": mismatches,
        }

    text = path.read_text(encoding="utf-8")
    required_markers = (f"v{version}", f"agent-memory-bridge=={version}", *_CURRENT_RELEASE_MARKERS)
    missing = [marker for marker in required_markers if marker not in text]
    if missing:
        mismatches.append(
            {
                "field": str(path),
                "expected_markers": missing,
                "actual": "missing",
            }
        )

    return {
        "name": "current_source_release_notes_match_package_version",
        "ok": not mismatches,
        "version": version,
        "path": str(path),
        "mismatches": mismatches,
    }
