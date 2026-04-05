import os
from pathlib import Path

from agent_mem_bridge.archive_snapshot import write_live_source_manifest
from agent_mem_bridge.live_cutover import apply_live_source_cutover, build_default_cutover_root
from agent_mem_bridge.rollback_cutover import build_rollback_preflight, rollback_live_source_cutover


def test_rollback_live_source_cutover_restores_retired_files(tmp_path: Path) -> None:
    cole_root = tmp_path / "Cole"
    (cole_root / "memory" / "core").mkdir(parents=True)
    (cole_root / "memory" / "team").mkdir(parents=True)
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
    (cole_root / "memory" / "team" / "README.md").write_text("# Team\n", encoding="utf-8")

    write_live_source_manifest(cole_root, cole_root / "live-source-manifest.json")
    cutover_root = build_default_cutover_root(cole_root)
    cutover_result = apply_live_source_cutover(
        cole_root,
        cutover_root,
        preflight_report={"missing_count": 0, "content_mismatch_count": 0, "namespace_mismatch_count": 0},
    )

    rollback_result = rollback_live_source_cutover(Path(cutover_result["manifest_path"]))

    assert rollback_result["restored_file_count"] == 3
    assert (cole_root / "architecture.md").is_file()
    assert (cole_root / "memory" / "status.md").is_file()
    assert (cole_root / "memory" / "team" / "README.md").is_file()
    assert (cutover_root / "rollback-manifest.json").is_file()


def test_rollback_live_source_cutover_dry_run_does_not_modify_files(tmp_path: Path) -> None:
    cole_root = tmp_path / "Cole"
    (cole_root / "memory").mkdir(parents=True)

    (cole_root / "HOW-TO-USE-COLE.md").write_text("# How to Use Cole\n", encoding="utf-8")
    retired_file = cole_root / "architecture.md"
    retired_file.write_text("# Architecture\n", encoding="utf-8")
    (cole_root / "memory" / ".claude-memory-guard.md").write_text("# Guard\n", encoding="utf-8")
    (cole_root / "memory" / "MEMORY.md").write_text("# Memory\n", encoding="utf-8")
    (cole_root / "memory" / "QUEUE.md").write_text("# Queue\n", encoding="utf-8")
    (cole_root / "memory" / "REDLINE.md").write_text("# Redline\n", encoding="utf-8")

    write_live_source_manifest(cole_root, cole_root / "live-source-manifest.json")
    cutover_result = apply_live_source_cutover(
        cole_root,
        build_default_cutover_root(cole_root),
        preflight_report={"missing_count": 0, "content_mismatch_count": 0, "namespace_mismatch_count": 0},
    )

    manifest_path = Path(cutover_result["manifest_path"])
    retired_path = Path(cutover_result["retired_root"]) / "architecture.md"
    result = rollback_live_source_cutover(manifest_path, dry_run=True)

    assert result["dry_run"] is True
    assert result["restored_file_count"] == 1
    assert result["rollback_manifest_path"] is None
    assert not retired_file.exists()
    assert retired_path.is_file()
    assert not (retired_path.parent.parent / "rollback-manifest.json").exists()


def test_rollback_preflight_detects_newer_live_conflicts(tmp_path: Path) -> None:
    cole_root = tmp_path / "Cole"
    (cole_root / "memory").mkdir(parents=True)

    (cole_root / "HOW-TO-USE-COLE.md").write_text("# How to Use Cole\n", encoding="utf-8")
    target_file = cole_root / "architecture.md"
    target_file.write_text("# Architecture\n", encoding="utf-8")
    (cole_root / "memory" / ".claude-memory-guard.md").write_text("# Guard\n", encoding="utf-8")
    (cole_root / "memory" / "MEMORY.md").write_text("# Memory\n", encoding="utf-8")
    (cole_root / "memory" / "QUEUE.md").write_text("# Queue\n", encoding="utf-8")
    (cole_root / "memory" / "REDLINE.md").write_text("# Redline\n", encoding="utf-8")

    write_live_source_manifest(cole_root, cole_root / "live-source-manifest.json")
    cutover_result = apply_live_source_cutover(
        cole_root,
        build_default_cutover_root(cole_root),
        preflight_report={"missing_count": 0, "content_mismatch_count": 0, "namespace_mismatch_count": 0},
    )

    target_file.write_text("# Architecture changed live\n", encoding="utf-8")
    current_time = target_file.stat().st_mtime
    os.utime(target_file, (current_time + 5, current_time + 5))

    preflight = build_rollback_preflight(Path(cutover_result["manifest_path"]))

    assert preflight["overwrite_candidate_count"] == 1
    assert preflight["overwrite_candidates"] == ["architecture.md"]
    assert preflight["newer_live_conflict_count"] == 1
    assert preflight["newer_live_conflicts"][0]["path"] == "architecture.md"

