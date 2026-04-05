from pathlib import Path

from agent_mem_bridge.archive_snapshot import write_live_source_manifest
from agent_mem_bridge.live_cutover import apply_live_source_cutover, build_default_cutover_root


def test_apply_live_source_cutover_moves_archive_first_markdown_only(tmp_path: Path) -> None:
    cole_root = tmp_path / "Cole"
    (cole_root / "memory" / "core").mkdir(parents=True)
    (cole_root / "memory" / "team").mkdir(parents=True)
    (cole_root / "memory" / "workspace" / "active_projects" / "scraper_sandbox").mkdir(parents=True)
    (cole_root / "memory").mkdir(exist_ok=True)

    (cole_root / "HOW-TO-USE-COLE.md").write_text("# How to Use Cole\n", encoding="utf-8")
    (cole_root / "architecture.md").write_text("# Architecture\n", encoding="utf-8")
    (cole_root / "memory" / ".claude-memory-guard.md").write_text("# Guard\n", encoding="utf-8")
    (cole_root / "memory" / "MEMORY.md").write_text("# Memory\n", encoding="utf-8")
    (cole_root / "memory" / "QUEUE.md").write_text("# Queue\n", encoding="utf-8")
    (cole_root / "memory" / "REDLINE.md").write_text("# Redline\n", encoding="utf-8")
    (cole_root / "memory" / "status.md").write_text("# Status\n", encoding="utf-8")
    (cole_root / "memory" / "core" / "core.md").write_text("# Core\n", encoding="utf-8")
    (cole_root / "memory" / "core" / "decision-making.md").write_text("# Decisions\n", encoding="utf-8")
    (cole_root / "memory" / "core" / "persona.md").write_text("# Persona\n", encoding="utf-8")
    (cole_root / "memory" / "core" / "reflections.md").write_text("# Reflections\n", encoding="utf-8")
    (cole_root / "memory" / "team" / "README.md").write_text("# Team\n", encoding="utf-8")
    (cole_root / "memory" / "workspace" / "active_projects" / "daily-briefing.md").write_text(
        "# Daily Briefing\n",
        encoding="utf-8",
    )
    code_file = cole_root / "memory" / "workspace" / "active_projects" / "scraper_sandbox" / "worker.py"
    code_file.write_text("print('keep')\n", encoding="utf-8")

    write_live_source_manifest(cole_root, cole_root / "live-source-manifest.json")
    cutover_root = build_default_cutover_root(cole_root)
    result = apply_live_source_cutover(cole_root, cutover_root, preflight_report={"missing_count": 0, "content_mismatch_count": 0, "namespace_mismatch_count": 0})

    assert result["moved_file_count"] == 5
    assert not (cole_root / "architecture.md").exists()
    assert not (cole_root / "memory" / "status.md").exists()
    assert not (cole_root / "memory" / "core" / "reflections.md").exists()
    assert not (cole_root / "memory" / "team" / "README.md").exists()
    assert not (cole_root / "memory" / "workspace" / "active_projects" / "daily-briefing.md").exists()
    assert (cutover_root / "retired" / "architecture.md").is_file()
    assert (cutover_root / "retired" / "memory" / "status.md").is_file()
    assert (cutover_root / "retired" / "memory" / "core" / "reflections.md").is_file()
    assert code_file.is_file()
    assert (cutover_root / "manifest.json").is_file()

