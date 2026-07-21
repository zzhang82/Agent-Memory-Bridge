from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, BinaryIO

from .filesystem_safety import ensure_private_directory, ensure_private_file

WINDOWS_LOCK_OFFSET = 1 << 30


class ServiceLockConflict(RuntimeError):
    def __init__(self, path: Path, metadata: dict[str, Any] | None = None) -> None:
        self.path = path
        self.metadata = metadata or {}
        owner = f" pid={self.metadata['pid']}" if self.metadata.get("pid") is not None else ""
        super().__init__(f"service lock is already held: {path}{owner}")


@dataclass(slots=True)
class ServiceFileLock:
    path: Path
    _handle: BinaryIO | None = field(default=None, init=False, repr=False)

    def __enter__(self) -> "ServiceFileLock":
        self.acquire()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        del exc_type, exc, traceback
        self.release()

    def acquire(self) -> None:
        if self._handle is not None:
            return
        ensure_private_directory(self.path.parent)
        file_descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        handle = os.fdopen(file_descriptor, "r+b", buffering=0)
        try:
            _ensure_lockable_file(handle)
        except BaseException:
            handle.close()
            raise
        try:
            _lock_nonblocking(handle)
        except OSError as exc:
            handle.close()
            raise ServiceLockConflict(self.path, _read_lock_metadata(self.path)) from exc

        try:
            payload = json.dumps(_lock_metadata(), sort_keys=True).encode("utf-8") + b"\n"
            handle.seek(0)
            handle.write(payload)
            handle.truncate()
            os.fsync(handle.fileno())
        except BaseException:
            _unlock(handle)
            handle.close()
            raise
        self._handle = handle
        ensure_private_file(self.path)

    def release(self) -> None:
        handle = self._handle
        if handle is None:
            return
        self._handle = None
        try:
            _unlock(handle)
        finally:
            handle.close()


def _ensure_lockable_file(handle: BinaryIO) -> None:
    # Windows byte-range locks block reads through the locked range, including
    # reads from another handle in the same process. The metadata must remain
    # readable by operators and lock contenders, so the actual lock lives at a
    # fixed offset far beyond the small JSON payload instead of byte zero.
    handle.seek(0)


def _lock_nonblocking(handle: BinaryIO) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        handle.seek(WINDOWS_LOCK_OFFSET)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(handle: BinaryIO) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        handle.seek(WINDOWS_LOCK_OFFSET)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _lock_metadata() -> dict[str, Any]:
    try:
        package_version = version("agent-memory-bridge")
    except PackageNotFoundError:
        package_version = "0.0.0"
    return {
        "pid": os.getpid(),
        "started_at": datetime.now(UTC).isoformat(),
        "hostname": socket.gethostname(),
        "version": package_version,
    }


def _read_lock_metadata(path: Path) -> dict[str, Any] | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8").replace("\x00", "").strip())
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def inspect_service_lock(path: Path) -> dict[str, Any]:
    metadata = _read_lock_metadata(path)
    if not path.exists():
        return {"exists": False, "held": False, "metadata": metadata, "error_type": None}
    try:
        handle = path.open("r+b", buffering=0)
    except OSError as exc:
        return {
            "exists": True,
            "held": None,
            "metadata": metadata,
            "error_type": exc.__class__.__name__,
        }
    try:
        try:
            _lock_nonblocking(handle)
        except OSError:
            held = True
        else:
            held = False
            _unlock(handle)
    finally:
        handle.close()
    return {"exists": True, "held": held, "metadata": metadata, "error_type": None}
