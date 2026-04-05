from pathlib import Path

from agent_mem_bridge.source_sync import build_default_sync_snapshot_root, plan_source_sync, sync_source_root


def test_plan_source_sync_skips_skills_by_default(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    (source_root / "memory" / "core").mkdir(parents=True)
    (source_root / "skills" / "obsidian-markdown").mkdir(parents=True)
    (target_root / "memory" / "core").mkdir(parents=True)
    (target_root / "skills" / "obsidian-markdown").mkdir(parents=True)

    (source_root / "memory" / "core" / "core.md").write_text("# Core\nnew\n", encoding="utf-8")
    (target_root / "memory" / "core" / "core.md").write_text("# Core\nold\n", encoding="utf-8")
    (source_root / "skills" / "obsidian-markdown" / "SKILL.md").write_text("# Skill\n", encoding="utf-8")
    (target_root / "skills" / "obsidian-markdown" / "SKILL.md").write_text("# Old Skill\n", encoding="utf-8")

    plan = plan_source_sync(source_root, target_root)

    assert plan["sync_candidates"] == ["memory/core/core.md"]
    assert plan["skipped_skill_paths"] == ["skills/obsidian-markdown/SKILL.md"]


def test_sync_source_root_copies_and_backs_up_target_files(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    snapshot_root = build_default_sync_snapshot_root(target_root)
    (source_root / "memory" / "core").mkdir(parents=True)
    (target_root / "memory" / "core").mkdir(parents=True)

    source_file = source_root / "memory" / "core" / "core.md"
    target_file = target_root / "memory" / "core" / "core.md"
    source_file.write_text("# Core\nnew\n", encoding="utf-8")
    target_file.write_text("# Core\nold\n", encoding="utf-8")

    result = sync_source_root(source_root, target_root, snapshot_root=snapshot_root)

    assert result["copied_count"] == 1
    assert result["backed_up_count"] == 1
    assert target_file.read_text(encoding="utf-8") == "# Core\nnew\n"
    assert (snapshot_root / "previous" / "memory" / "core" / "core.md").read_text(encoding="utf-8") == "# Core\nold\n"
    assert (snapshot_root / "sync-manifest.json").is_file()

