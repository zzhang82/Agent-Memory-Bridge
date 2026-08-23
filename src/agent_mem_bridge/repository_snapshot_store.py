from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

SNAPSHOT_STORE_SCHEMA = "repository.snapshot.v1"
BINDING_STORE_SCHEMA = "repository.binding.v1"


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _git(root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = result.stdout.strip()
    return value or None


def _clean_status(root: Path) -> tuple[bool, bool | None]:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return False, None
    return True, not bool(result.stdout.strip())


def repository_identity(root: Path) -> dict[str, str]:
    resolved = Path(root).expanduser().resolve()
    git_root = _git(resolved, "rev-parse", "--show-toplevel")
    canonical_root = str(Path(git_root).resolve()) if git_root else str(resolved)
    remote = _git(resolved, "config", "--get", "remote.origin.url")
    identity_basis = f"remote:{remote.strip()}" if remote else f"root:{canonical_root}"
    return {
        "repository_id": _sha256(identity_basis)[:32],
        "identity_basis": identity_basis,
        "git_root": canonical_root,
        "remote_origin": remote or "",
    }


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (_canonical_json(payload) + "\n").encode("utf-8")
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        last_error: OSError | None = None
        for attempt in range(8):
            try:
                os.replace(temporary, path)
                last_error = None
                break
            except PermissionError as error:
                last_error = error
                if attempt == 7:
                    raise
                time.sleep(0.01 * (attempt + 1))
        if last_error is not None:
            raise last_error
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


class RepositorySnapshotStore:
    """Local derived repository snapshots and explicit namespace bindings."""

    def __init__(self, repository_root: Path) -> None:
        self.root = Path(repository_root).expanduser().resolve()
        self.snapshots_root = self.root / "snapshots"
        self.bindings_path = self.root / "bindings.json"

    def snapshot_path(self, repository_id: str) -> Path:
        return self.snapshots_root / repository_id / "current.json"

    def save_snapshot(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        identity = repository_identity(Path(str(snapshot["root"])))
        stored = {
            "store_schema": SNAPSHOT_STORE_SCHEMA,
            "repository_id": identity["repository_id"],
            "identity_basis": identity["identity_basis"],
            "git_root": identity["git_root"],
            "remote_origin": identity["remote_origin"],
            "snapshot": snapshot,
        }
        _atomic_write_json(self.snapshot_path(identity["repository_id"]), stored)
        return {
            **snapshot,
            "repository_id": identity["repository_id"],
            "snapshot_path": str(self.snapshot_path(identity["repository_id"])),
        }

    def load_snapshot(self, repository_id: str) -> dict[str, Any] | None:
        path = self.snapshot_path(repository_id)
        stored: object | None = None
        for attempt in range(8):
            try:
                with path.open("r", encoding="utf-8") as handle:
                    stored = json.load(handle)
                break
            except PermissionError:
                if attempt == 7:
                    return None
                time.sleep(0.01 * (attempt + 1))
            except (OSError, json.JSONDecodeError):
                return None
        if not isinstance(stored, dict) or stored.get("store_schema") != SNAPSHOT_STORE_SCHEMA:
            return None
        snapshot = stored.get("snapshot")
        if not isinstance(snapshot, dict) or stored.get("repository_id") != repository_id:
            return None
        return {**snapshot, "repository_id": repository_id, "snapshot_path": str(path)}

    def bindings(self) -> dict[str, Any]:
        try:
            with self.bindings_path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return {"store_schema": BINDING_STORE_SCHEMA, "bindings": {}}
        if not isinstance(data, dict) or data.get("store_schema") != BINDING_STORE_SCHEMA:
            return {"store_schema": BINDING_STORE_SCHEMA, "bindings": {}}
        bindings = data.get("bindings")
        return {"store_schema": BINDING_STORE_SCHEMA, "bindings": bindings if isinstance(bindings, dict) else {}}

    def bind_namespace(self, namespace: str, repository_id: str, *, allow_rebind: bool = False) -> dict[str, Any]:
        cleaned = namespace.strip()
        if not cleaned:
            raise ValueError("namespace must not be empty")
        data = self.bindings()
        existing = data["bindings"].get(cleaned)
        if isinstance(existing, dict) and existing.get("repository_id") != repository_id and not allow_rebind:
            raise ValueError("namespace is already bound to a different repository; explicit rebind required")
        data["bindings"][cleaned] = {"repository_id": repository_id}
        _atomic_write_json(self.bindings_path, data)
        return {
            "namespace": cleaned,
            "repository_id": repository_id,
            "rebound": bool(existing and existing.get("repository_id") != repository_id),
        }

    def unbind_namespace(self, namespace: str) -> bool:
        cleaned = namespace.strip()
        data = self.bindings()
        removed = data["bindings"].pop(cleaned, None) is not None
        if removed:
            _atomic_write_json(self.bindings_path, data)
        return removed

    def load_bound_snapshot(self, namespace: str) -> dict[str, Any] | None:
        binding = self.bindings()["bindings"].get(namespace.strip())
        if not isinstance(binding, dict):
            return None
        repository_id = binding.get("repository_id")
        if not isinstance(repository_id, str) or not repository_id:
            return None
        snapshot = self.load_snapshot(repository_id)
        if snapshot is None:
            return {"repository_id": repository_id, "binding_state": "missing_snapshot"}
        return self._with_current_status(snapshot)

    def _with_current_status(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        root = Path(str(snapshot.get("root", ""))).expanduser()
        expected_commit = snapshot.get("commit")
        current_commit = _git(root, "rev-parse", "HEAD")
        status_ok, clean = _clean_status(root)
        result = dict(snapshot)
        if not status_ok:
            result["binding_state"] = "stale"
            result["stale_reason"] = "worktree_status_unavailable"
            result["current_commit"] = current_commit
        elif not clean:
            result["binding_state"] = "stale"
            result["stale_reason"] = "dirty_worktree"
            result["current_commit"] = current_commit
        elif expected_commit and current_commit != expected_commit:
            result["binding_state"] = "stale"
            result["stale_reason"] = "head_changed"
            result["current_commit"] = current_commit
        elif snapshot.get("binding") == "git_commit":
            result["binding_state"] = "current"
            result["current_commit"] = current_commit
        else:
            result["binding_state"] = "ineligible"
            result["current_commit"] = current_commit
        return result


def select_repository_facts(
    snapshot: dict[str, Any], query: str, *, limit: int = 8
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    facts = snapshot.get("facts") if isinstance(snapshot.get("facts"), list) else []
    query_terms = {part.casefold() for part in query.split() if len(part.strip()) >= 2}
    scored: list[tuple[int, int, dict[str, Any]]] = []
    for index, fact in enumerate(facts):
        if not isinstance(fact, dict):
            continue
        haystack = _canonical_json(fact).casefold()
        score = sum(1 for term in query_terms if term in haystack)
        if score:
            scored.append((score, -index, fact))
    scored.sort(reverse=True, key=lambda item: (item[0], item[1]))
    selected = [fact for _, _, fact in scored[:limit]]
    selected_ids = {id(fact) for fact in selected}
    excluded = [fact for fact in facts if isinstance(fact, dict) and id(fact) not in selected_ids]
    return selected, excluded
