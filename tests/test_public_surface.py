from pathlib import Path

from agent_mem_bridge.public_surface import run_public_surface_check, scan_readme_links, scan_text_for_blocked_patterns


def test_scan_text_for_blocked_patterns_flags_operator_specific_examples() -> None:
    violations = scan_text_for_blocked_patterns(
        Path("docs/example.md"),
        "namespace = cole-core\nproject = project:mem-store\n",
    )

    assert len(violations) == 2
    assert violations[0]["kind"] == "blocked-pattern"


def test_scan_readme_links_flags_maintainer_docs() -> None:
    violations = scan_readme_links(
        Path("README.md"),
        "- [docs/ROADMAP.md](docs/ROADMAP.md)\n- [docs/PRODUCTION-STATUS.md](docs/PRODUCTION-STATUS.md)\n",
    )

    assert [item["target"] for item in violations] == [
        "docs/ROADMAP.md",
        "docs/PRODUCTION-STATUS.md",
    ]


def test_public_surface_check_repository_passes() -> None:
    report = run_public_surface_check(Path(__file__).resolve().parents[1])

    assert report["ok"] is True
    assert report["violations"] == []
