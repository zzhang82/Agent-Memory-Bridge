from pathlib import Path

from agent_mem_bridge.archive_snapshot import (
    build_default_live_manifest_path,
    build_default_snapshot_root,
    create_cole_archive_snapshot,
    write_live_source_manifest,
)


def test_create_cole_archive_snapshot_copies_markdown_only_targets(tmp_path: Path) -> None:
    cole_root = tmp_path / "Cole"
    (cole_root / "memory" / "core").mkdir(parents=True)
    (cole_root / "memory" / "team").mkdir(parents=True)
    (cole_root / "skills" / "obsidian-markdown").mkdir(parents=True)

    (cole_root / "HOW-TO-USE-COLE.md").write_text("# How to Use Cole\n", encoding="utf-8")
    (cole_root / "memory" / "core" / "persona.md").write_text("# Persona\n", encoding="utf-8")
    (cole_root / "memory" / "team" / "README.md").write_text("# Team\n", encoding="utf-8")
    (cole_root / "skills" / "obsidian-markdown" / "SKILL.md").write_text("# Skill\n", encoding="utf-8")

    snapshot_root = build_default_snapshot_root(cole_root)
    manifest = create_cole_archive_snapshot(
        source_root=cole_root,
        snapshot_root=snapshot_root,
        compare_report={
            "missing_count": 0,
            "extra_count": 0,
            "content_mismatch_count": 0,
            "namespace_mismatch_count": 0,
        },
    )

    assert manifest["migration_safe"] is True
    assert (snapshot_root / "HOW-TO-USE-COLE.md").is_file()
    assert (snapshot_root / "memory" / "core" / "persona.md").is_file()
    assert (snapshot_root / "memory" / "team" / "README.md").is_file()
    assert not (snapshot_root / "skills" / "obsidian-markdown" / "SKILL.md").exists()
    assert (snapshot_root / "manifest.json").is_file()


def test_write_live_source_manifest_keeps_minimal_fallback_set(tmp_path: Path) -> None:
    cole_root = tmp_path / "Cole"
    (cole_root / "memory" / "core").mkdir(parents=True)
    (cole_root / "memory").mkdir(exist_ok=True)

    (cole_root / "HOW-TO-USE-COLE.md").write_text("# How to Use Cole\n", encoding="utf-8")
    (cole_root / "memory" / ".claude-memory-guard.md").write_text("# Guard\n", encoding="utf-8")
    (cole_root / "memory" / "MEMORY.md").write_text("# Memory\n", encoding="utf-8")
    (cole_root / "memory" / "QUEUE.md").write_text("# Queue\n", encoding="utf-8")
    (cole_root / "memory" / "REDLINE.md").write_text("# Redline\n", encoding="utf-8")
    (cole_root / "memory" / "core" / "core.md").write_text("# Core\n", encoding="utf-8")
    (cole_root / "memory" / "core" / "decision-making.md").write_text("# Decisions\n", encoding="utf-8")
    (cole_root / "memory" / "core" / "persona.md").write_text("# Persona\n", encoding="utf-8")

    manifest_path = build_default_live_manifest_path(cole_root)
    manifest = write_live_source_manifest(cole_root, manifest_path)

    assert manifest["file_count"] == 8
    assert manifest_path.is_file()
    assert "memory/core/persona.md" in manifest["files"]

