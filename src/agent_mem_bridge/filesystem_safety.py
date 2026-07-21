from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

SYNC_PATH_MARKERS = (
    "dropbox",
    "google drive",
    "googledrive",
    "icloud drive",
    "icloud~",
    "onedrive",
)


def ensure_private_directory(path: Path, *, tighten_existing: bool = False) -> None:
    existed = path.exists()
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "posix" and (tighten_existing or not existed):
        path.chmod(0o700)


def ensure_private_file(path: Path) -> None:
    if os.name == "posix" and path.exists():
        path.chmod(0o600)


def permission_report(path: Path, *, directory: bool) -> dict[str, Any]:
    if not path.exists():
        return {
            "path": str(path),
            "exists": False,
            "applicable": os.name == "posix",
            "private": True,
            "mode": None,
        }
    if os.name != "posix":
        return {
            "path": str(path),
            "exists": True,
            "applicable": False,
            "private": None,
            "mode": None,
        }
    mode = stat.S_IMODE(path.stat().st_mode)
    disallowed = mode & 0o077
    expected = 0o700 if directory else 0o600
    return {
        "path": str(path),
        "exists": True,
        "applicable": True,
        "private": disallowed == 0,
        "mode": f"{mode:04o}",
        "expected_mode": f"{expected:04o}",
    }


def path_storage_warnings(path: Path) -> list[str]:
    rendered = str(path.expanduser()).replace("\\", "/")
    lowered = rendered.lower()
    warnings: list[str] = []
    if rendered.startswith("//") or rendered.startswith("\\\\"):
        warnings.append("network-share-path")
    if any(marker in lowered for marker in SYNC_PATH_MARKERS):
        warnings.append("sync-folder-path")
    if lowered.startswith("/mnt/") and any(marker in lowered for marker in SYNC_PATH_MARKERS):
        warnings.append("wsl-mounted-sync-folder")
    return warnings
