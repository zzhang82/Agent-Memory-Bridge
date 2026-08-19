from pathlib import Path

from agent_mem_bridge.public_surface import (
    PUBLIC_BINARY_ASSETS,
    PUBLIC_DOC_PATHS,
    run_public_surface_check,
    scan_readme_links,
    scan_text_for_blocked_patterns,
)


def test_scan_text_for_blocked_patterns_flags_operator_specific_examples() -> None:
    violations = scan_text_for_blocked_patterns(
        Path("docs/example.md"),
        "namespace = cole-core\nproject = project:mem-store\n",
    )

    assert len(violations) == 2
    assert violations[0]["kind"] == "blocked-pattern"


def test_scan_readme_links_allows_canonical_docs_and_flags_maintainer_docs() -> None:
    violations = scan_readme_links(
        Path("README.md"),
        "- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)\n"
        "- [docs/ROADMAP.md](docs/ROADMAP.md)\n"
        "- [docs/PRODUCTION-STATUS.md](docs/PRODUCTION-STATUS.md)\n"
        "- [docs/STARTUP-PROTOCOL.md](docs/STARTUP-PROTOCOL.md)\n",
    )

    assert [item["target"] for item in violations] == ["docs/STARTUP-PROTOCOL.md"]


def test_public_surface_check_repository_passes() -> None:
    report = run_public_surface_check(Path(__file__).resolve().parents[1])

    assert report["ok"] is True
    assert report["violations"] == []
    assert {
        str(Path("CHANGELOG.md")),
        str(Path("benchmark/latest-v0.21-governed-change-report.json")),
        str(Path("benchmark/v0.21-governed-change-manifest.json")),
        str(Path("docs/ARCHITECTURE.md")),
        str(Path("docs/v0.20-clean-room-proof.md")),
        str(Path("docs/v0.27.2-announcement.md")),
        str(Path("docs/v0.27.3-announcement.md")),
        str(Path("docs/v0.27.4-announcement.md")),
        str(Path("examples/diagrams/amb-overview.svg")),
        str(Path("examples/diagrams/v0.22-shared-memory-hero.png")),
        str(Path("llms-install.md")),
    }.issubset(set(report["checked_files"]))


def test_public_surface_keeps_only_retained_versioned_history() -> None:
    versioned_public_docs = {
        path for path in PUBLIC_DOC_PATHS if path.parent == Path("docs") and path.name.startswith("v")
    }

    assert versioned_public_docs == {
        Path("docs/v0.20-clean-room-proof.md"),
        Path("docs/v0.27.2-announcement.md"),
        Path("docs/v0.27.3-announcement.md"),
        Path("docs/v0.27.4-announcement.md"),
    }


def test_capability_history_is_on_public_surface() -> None:
    assert Path("CHANGELOG.md") in PUBLIC_DOC_PATHS


def test_visual_release_assets_are_on_public_surface_with_png_binary_only() -> None:
    text_scanned_assets = {
        Path("docs/RELEASE-COMMUNICATIONS.md"),
        Path("examples/diagrams/v0.22-cross-client-activation.svg"),
        Path("examples/diagrams/v0.22-receipt-anatomy.svg"),
        Path("examples/diagrams/visual-claims.json"),
    }
    binary_png = Path("examples/diagrams/v0.22-shared-memory-hero.png")

    assert text_scanned_assets.issubset(set(PUBLIC_DOC_PATHS))
    assert binary_png in PUBLIC_BINARY_ASSETS
    assert binary_png not in PUBLIC_DOC_PATHS
