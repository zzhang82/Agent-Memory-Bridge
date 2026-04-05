from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from shutil import copy2
from typing import Any

from .cole_migration import iter_cole_markdown_files


def build_default_sync_snapshot_root(target_root: Path, *, timestamp: datetime | None = None) -> Path:
    moment = timestamp or datetime.now(UTC)
    stamp = moment.strftime("%Y-%m-%d-source-sync")
    return Path(target_root).resolve() / "archive" / stamp


def plan_source_sync(
    source_root: Path,
    target_root: Path,
    *,
    include_skills: bool = False,
) -> dict[str, Any]:
    source = Path(source_root).resolve()
    target = Path(target_root).resolve()

    source_files = {
        path.relative_to(source).as_posix(): path
        for path in iter_cole_markdown_files(source)
    }
    target_files = {
        path.relative_to(target).as_posix(): path
        for path in iter_cole_markdown_files(target)
    }

    sync_candidates: list[str] = []
    skipped_skill_paths: list[str] = []
    identical_paths: list[str] = []

    for relative_path in sorted(source_files):
        if relative_path.startswith("skills/") and not include_skills:
            skipped_skill_paths.append(relative_path)
            continue
        source_path = source_files[relative_path]
        target_path = target / relative_path
        if not target_path.is_file():
            sync_candidates.append(relative_path)
            continue
        if _sha256(source_path) == _sha256(target_path):
            identical_paths.append(relative_path)
            continue
        sync_candidates.append(relative_path)

    target_only_paths = sorted(path for path in target_files if path not in source_files)

    return {
        "created_at": datetime.now(UTC).isoformat(),
        "source_root": str(source),
        "target_root": str(target),
        "include_skills": include_skills,
        "sync_candidate_count": len(sync_candidates),
        "sync_candidates": sync_candidates,
        "identical_count": len(identical_paths),
        "identical_paths": identical_paths,
        "skipped_skill_count": len(skipped_skill_paths),
        "skipped_skill_paths": skipped_skill_paths,
        "target_only_count": len(target_only_paths),
        "target_only_paths": target_only_paths,
    }


def sync_source_root(
    source_root: Path,
    target_root: Path,
    *,
    snapshot_root: Path,
    include_skills: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    source = Path(source_root).resolve()
    target = Path(target_root).resolve()
    snapshot = Path(snapshot_root).resolve()

    plan = plan_source_sync(source, target, include_skills=include_skills)

    backed_up_files: list[str] = []
    copied_files: list[str] = []

    for relative_path in plan["sync_candidates"]:
        source_path = source / relative_path
        target_path = target / relative_path
        if dry_run:
            copied_files.append(relative_path)
            if target_path.is_file():
                backed_up_files.append(relative_path)
            continue

        if target_path.is_file():
            backup_path = snapshot / "previous" / relative_path
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            copy2(target_path, backup_path)
            backed_up_files.append(relative_path)

        target_path.parent.mkdir(parents=True, exist_ok=True)
        copy2(source_path, target_path)
        copied_files.append(relative_path)

    manifest: dict[str, Any] = {
        "created_at": datetime.now(UTC).isoformat(),
        "source_root": str(source),
        "target_root": str(target),
        "snapshot_root": str(snapshot),
        "include_skills": include_skills,
        "dry_run": dry_run,
        "copied_count": len(copied_files),
        "copied_files": copied_files,
        "backed_up_count": len(backed_up_files),
        "backed_up_files": backed_up_files,
        "plan": plan,
    }

    if dry_run:
        manifest["manifest_path"] = None
        return manifest

    snapshot.mkdir(parents=True, exist_ok=True)
    manifest_path = snapshot / "sync-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    manifest["manifest_path"] = str(manifest_path)
    return manifest


def _sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()
