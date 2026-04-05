from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from shutil import move
from typing import Any

from .archive_snapshot import build_default_live_manifest_path, load_manifest_relative_paths


RETIRED_ROOT_FILES = (
    "architecture.md",
    "memory/status.md",
)

RETIRED_CORE_FILES = (
    "memory/core/archive-convention.md",
    "memory/core/evolution.md",
    "memory/core/leadership.md",
    "memory/core/reflections.md",
)

RETIRED_DIRS = (
    "memory/skills",
    "memory/team",
    "memory/workflows",
    "memory/workspace",
)


def build_default_cutover_root(source_root: Path, *, timestamp: datetime | None = None) -> Path:
    moment = timestamp or datetime.now(UTC)
    stamp = moment.strftime("%Y-%m-%d-live-cutover")
    return Path(source_root).resolve() / "archive" / stamp


def iter_live_cutover_relative_paths(
    source_root: Path,
    *,
    live_manifest_path: Path | None = None,
) -> list[Path]:
    root = Path(source_root).resolve()
    live_manifest = (live_manifest_path or build_default_live_manifest_path(root)).resolve()
    live_paths = {path.as_posix() for path in load_manifest_relative_paths(live_manifest)}

    candidates: list[Path] = []
    for relative in RETIRED_ROOT_FILES:
        candidate = root / relative
        if candidate.is_file():
            candidates.append(Path(relative))

    for relative in RETIRED_CORE_FILES:
        candidate = root / relative
        if candidate.is_file():
            candidates.append(Path(relative))

    for relative_dir in RETIRED_DIRS:
        directory = root / relative_dir
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*.md")):
            candidates.append(path.relative_to(root))

    deduped: list[Path] = []
    seen: set[Path] = set()
    for relative_path in candidates:
        if relative_path in seen:
            continue
        if relative_path.as_posix() in live_paths:
            continue
        seen.add(relative_path)
        deduped.append(relative_path)
    return deduped


def apply_live_source_cutover(
    source_root: Path,
    cutover_root: Path,
    *,
    live_manifest_path: Path | None = None,
    preflight_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = Path(source_root).resolve()
    cutover_root = Path(cutover_root).resolve()
    retired_root = cutover_root / "retired"
    relative_paths = iter_live_cutover_relative_paths(root, live_manifest_path=live_manifest_path)
    moved_files: list[str] = []

    for relative_path in relative_paths:
        source_path = root / relative_path
        if not source_path.is_file():
            continue
        target_path = retired_root / relative_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        move(str(source_path), str(target_path))
        moved_files.append(relative_path.as_posix())

    removed_dirs = _remove_empty_directories(root)
    manifest = {
        "created_at": datetime.now(UTC).isoformat(),
        "source_root": str(root),
        "cutover_root": str(cutover_root),
        "retired_root": str(retired_root),
        "moved_file_count": len(moved_files),
        "moved_files": moved_files,
        "removed_empty_dirs": removed_dirs,
        "live_manifest_path": str((live_manifest_path or build_default_live_manifest_path(root)).resolve()),
        "preflight_report": preflight_report,
        "preflight_ok": bool(
            preflight_report
            and preflight_report.get("missing_count") == 0
            and preflight_report.get("content_mismatch_count") == 0
            and preflight_report.get("namespace_mismatch_count") == 0
        ),
    }
    manifest_path = cutover_root / "manifest.json"
    cutover_root.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    manifest["manifest_path"] = str(manifest_path)
    return manifest


def _remove_empty_directories(source_root: Path) -> list[str]:
    root = Path(source_root).resolve()
    removed: list[str] = []
    directories = [path for path in root.rglob("*") if path.is_dir()]
    for directory in sorted(directories, key=lambda path: len(path.parts), reverse=True):
        if directory == root or any(part == "archive" for part in directory.relative_to(root).parts[:1]):
            continue
        if any(directory.iterdir()):
            continue
        directory.rmdir()
        removed.append(directory.relative_to(root).as_posix())
    return removed
