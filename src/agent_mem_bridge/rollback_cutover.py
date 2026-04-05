from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from shutil import move
from typing import Any

from .archive_snapshot import load_manifest


def rollback_live_source_cutover(
    cutover_manifest_path: Path,
    *,
    force: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    manifest = load_manifest(cutover_manifest_path)
    source_root = Path(manifest["source_root"]).resolve()
    cutover_root = Path(manifest["cutover_root"]).resolve()
    retired_root = Path(manifest["retired_root"]).resolve()
    moved_files = [Path(item) for item in manifest.get("moved_files", [])]

    preflight = build_rollback_preflight(cutover_manifest_path)

    restored_files: list[str] = []
    skipped_existing: list[str] = []
    overwritten_existing: list[str] = []

    for relative_path in moved_files:
        source_path = source_root / relative_path
        retired_path = retired_root / relative_path
        if not retired_path.is_file():
            continue
        if source_path.exists() and not force:
            skipped_existing.append(relative_path.as_posix())
            continue
        if dry_run:
            if source_path.exists():
                overwritten_existing.append(relative_path.as_posix())
            restored_files.append(relative_path.as_posix())
            continue
        source_path.parent.mkdir(parents=True, exist_ok=True)
        if source_path.exists():
            source_path.unlink()
            overwritten_existing.append(relative_path.as_posix())
        move(str(retired_path), str(source_path))
        restored_files.append(relative_path.as_posix())

    removed_empty_dirs = [] if dry_run else _remove_empty_directories(retired_root)
    rollback_manifest: dict[str, Any] = {
        "created_at": datetime.now(UTC).isoformat(),
        "cutover_manifest_path": str(Path(cutover_manifest_path).resolve()),
        "source_root": str(source_root),
        "cutover_root": str(cutover_root),
        "retired_root": str(retired_root),
        "dry_run": dry_run,
        "restored_file_count": len(restored_files),
        "restored_files": restored_files,
        "skipped_existing_count": len(skipped_existing),
        "skipped_existing": skipped_existing,
        "overwritten_existing_count": len(overwritten_existing),
        "overwritten_existing": overwritten_existing,
        "overwrite_candidate_count": preflight["overwrite_candidate_count"],
        "overwrite_candidates": preflight["overwrite_candidates"],
        "newer_live_conflict_count": preflight["newer_live_conflict_count"],
        "newer_live_conflicts": preflight["newer_live_conflicts"],
        "removed_empty_dirs": removed_empty_dirs,
    }
    if dry_run:
        rollback_manifest["rollback_manifest_path"] = None
        return rollback_manifest

    rollback_manifest_path = cutover_root / "rollback-manifest.json"
    rollback_manifest_path.write_text(json.dumps(rollback_manifest, indent=2) + "\n", encoding="utf-8")
    rollback_manifest["rollback_manifest_path"] = str(rollback_manifest_path)
    return rollback_manifest


def build_rollback_preflight(cutover_manifest_path: Path) -> dict[str, Any]:
    manifest = load_manifest(cutover_manifest_path)
    source_root = Path(manifest["source_root"]).resolve()
    retired_root = Path(manifest["retired_root"]).resolve()
    moved_files = [Path(item) for item in manifest.get("moved_files", [])]

    missing_retired: list[str] = []
    overwrite_candidates: list[str] = []
    newer_live_conflicts: list[dict[str, str]] = []

    for relative_path in moved_files:
        source_path = source_root / relative_path
        retired_path = retired_root / relative_path
        if not retired_path.is_file():
            missing_retired.append(relative_path.as_posix())
            continue
        if not source_path.exists():
            continue
        overwrite_candidates.append(relative_path.as_posix())
        source_mtime = source_path.stat().st_mtime
        retired_mtime = retired_path.stat().st_mtime
        if source_mtime <= retired_mtime:
            continue
        newer_live_conflicts.append(
            {
                "path": relative_path.as_posix(),
                "live_modified_at": datetime.fromtimestamp(source_mtime, UTC).isoformat(),
                "retired_modified_at": datetime.fromtimestamp(retired_mtime, UTC).isoformat(),
            }
        )

    return {
        "created_at": datetime.now(UTC).isoformat(),
        "cutover_manifest_path": str(Path(cutover_manifest_path).resolve()),
        "overwrite_candidate_count": len(overwrite_candidates),
        "overwrite_candidates": overwrite_candidates,
        "newer_live_conflict_count": len(newer_live_conflicts),
        "newer_live_conflicts": newer_live_conflicts,
        "missing_retired_count": len(missing_retired),
        "missing_retired": missing_retired,
    }


def find_latest_live_cutover_manifest(source_root: Path) -> Path | None:
    archive_root = Path(source_root).resolve() / "archive"
    if not archive_root.is_dir():
        return None
    manifests = sorted(archive_root.glob("*-live-cutover/manifest.json"))
    if not manifests:
        return None
    return manifests[-1]


def _remove_empty_directories(root: Path) -> list[str]:
    base = Path(root).resolve()
    if not base.exists():
        return []
    removed: list[str] = []
    directories = [path for path in base.rglob("*") if path.is_dir()]
    for directory in sorted(directories, key=lambda path: len(path.parts), reverse=True):
        if directory == base:
            continue
        if any(directory.iterdir()):
            continue
        directory.rmdir()
        removed.append(directory.relative_to(base).as_posix())
    if base.exists() and not any(base.iterdir()):
        base.rmdir()
        removed.append(".")
    return removed
