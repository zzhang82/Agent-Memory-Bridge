from __future__ import annotations

import os
from pathlib import Path


def rotate_log_if_needed(
    path: Path,
    *,
    incoming_bytes: int,
    max_bytes: int,
    backup_count: int,
) -> bool:
    if max_bytes <= 0:
        raise ValueError("log max_bytes must be greater than 0")
    if backup_count < 0:
        raise ValueError("log backup_count must not be negative")
    try:
        current_size = path.stat().st_size
    except OSError:
        current_size = 0
    if current_size + max(0, incoming_bytes) <= max_bytes:
        return False
    if backup_count == 0:
        path.unlink(missing_ok=True)
        return True
    oldest = path.with_name(f"{path.name}.{backup_count}")
    oldest.unlink(missing_ok=True)
    for index in range(backup_count - 1, 0, -1):
        source = path.with_name(f"{path.name}.{index}")
        if source.exists():
            os.replace(source, path.with_name(f"{path.name}.{index + 1}"))
    if path.exists():
        os.replace(path, path.with_name(f"{path.name}.1"))
    return True
