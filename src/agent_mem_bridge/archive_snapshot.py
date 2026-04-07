from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from shutil import copy2
from typing import Any


ROOT_FILES = (
    "HOW-TO-USE-COLE.md",
    "architecture.md",
)

MEMORY_ROOT_FILES = (
    "memory/.claude-memory-guard.md",
    "memory/MEMORY.md",
    "memory/QUEUE.md",
    "memory/REDLINE.md",
    "memory/status.md",
)

SNAPSHOT_DIRS = (
    "memory/core",
    "memory/team",
    "memory/workflows",
    "memory/workspace",
)

LIVE_SOURCE_FILES = (
    "HOW-TO-USE-COLE.md",
    "memory/.claude-memory-guard.md",
    "memory/MEMORY.md",
    "memory/QUEUE.md",
    "memory/REDLINE.md",
    "memory/core/core.md",
    "memory/core/decision-making.md",
    "memory/core/persona.md",
)


def build_default_snapshot_root(source_root: Path, *, timestamp: datetime | None = None) -> Path:
    moment = timestamp or datetime.now(UTC)
    stamp = moment.strftime("%Y-%m-%d-source-snapshot")
    return Path(source_root).resolve() / "archive" / stamp


def build_default_live_manifest_path(source_root: Path) -> Path:
    return Path(source_root).resolve() / "live-source-manifest.json"


def create_profile_archive_snapshot(
    source_root: Path,
    snapshot_root: Path,
    *,
    compare_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source_root = Path(source_root).resolve()
    snapshot_root = Path(snapshot_root).resolve()
    copied_files: list[str] = []

    for relative_path in iter_snapshot_relative_paths(source_root):
        source_path = source_root / relative_path
        target_path = snapshot_root / relative_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        copy2(source_path, target_path)
        copied_files.append(relative_path.as_posix())

    manifest = {
        "created_at": datetime.now(UTC).isoformat(),
        "source_root": str(source_root),
        "snapshot_root": str(snapshot_root),
        "file_count": len(copied_files),
        "files": copied_files,
        "compare_report": compare_report,
        "migration_safe": bool(
            compare_report
            and compare_report.get("missing_count") == 0
            and compare_report.get("extra_count") == 0
            and compare_report.get("content_mismatch_count") == 0
            and compare_report.get("namespace_mismatch_count") == 0
        ),
    }
    manifest_path = snapshot_root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    manifest["manifest_path"] = str(manifest_path)
    return manifest


# Legacy compatibility alias for older migration helpers.
create_cole_archive_snapshot = create_profile_archive_snapshot


def write_live_source_manifest(
    source_root: Path,
    manifest_path: Path,
    *,
    relative_paths: list[Path] | None = None,
) -> dict[str, Any]:
    source_root = Path(source_root).resolve()
    manifest_path = Path(manifest_path).resolve()
    files = relative_paths or iter_live_source_relative_paths(source_root)
    rendered_files = [path.as_posix() for path in files]
    manifest = {
        "created_at": datetime.now(UTC).isoformat(),
        "source_root": str(source_root),
        "manifest_path": str(manifest_path),
        "file_count": len(rendered_files),
        "files": rendered_files,
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def iter_snapshot_relative_paths(source_root: Path) -> list[Path]:
    root = Path(source_root).resolve()
    files: list[Path] = []

    for relative in ROOT_FILES:
        candidate = root / relative
        if candidate.is_file():
            files.append(Path(relative))

    for relative in MEMORY_ROOT_FILES:
        candidate = root / relative
        if candidate.is_file():
            files.append(Path(relative))

    for relative_dir in SNAPSHOT_DIRS:
        directory = root / relative_dir
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*.md")):
            files.append(path.relative_to(root))

    deduped: list[Path] = []
    seen: set[Path] = set()
    for relative_path in files:
        if relative_path in seen:
            continue
        seen.add(relative_path)
        deduped.append(relative_path)
    return deduped


def iter_live_source_relative_paths(source_root: Path) -> list[Path]:
    root = Path(source_root).resolve()
    files: list[Path] = []
    for relative in LIVE_SOURCE_FILES:
        candidate = root / relative
        if candidate.is_file():
            files.append(Path(relative))
    return files


def load_manifest_relative_paths(manifest_path: Path) -> list[Path]:
    manifest = load_manifest(manifest_path)
    return [Path(item) for item in manifest.get("files", [])]


def load_manifest(manifest_path: Path) -> dict[str, Any]:
    path = Path(manifest_path).resolve()
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return data if isinstance(data, dict) else {}


def find_latest_snapshot_manifest(source_root: Path) -> Path | None:
    archive_root = Path(source_root).resolve() / "archive"
    if not archive_root.is_dir():
        return None
    manifests = sorted(archive_root.glob("*-source-snapshot/manifest.json"))
    if not manifests:
        return None
    return manifests[-1]
