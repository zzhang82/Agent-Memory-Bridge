from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

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


def _safe_remote_identity(remote: str) -> str | None:
    value = remote.strip()
    if not value:
        return None
    if "://" in value:
        parsed = urlsplit(value)
        host = parsed.hostname
        path = parsed.path
        if parsed.scheme == "file" and path:
            return f"file/{path.rstrip('/').removesuffix('.git')}"
        path = path.strip("/")
        if host and path:
            return f"{host.casefold()}/{path.removesuffix('.git')}"
        return None
    if value.startswith(("/", "./", "../")):
        return f"file/{Path(value).expanduser().resolve().as_posix().rstrip('/').removesuffix('.git')}"
    if re.match(r"^[A-Za-z]:[\\\\/]", value):
        normalized_path = value.replace("\\\\", "/").rstrip("/").removesuffix(".git")
        return f"file/{normalized_path.casefold()}"
    if "@" in value and ":" in value:
        user_host, path = value.split(":", 1)
        host = user_host.rsplit("@", 1)[-1].strip()
        path = path.strip("/")
        if host and path:
            return f"{host.casefold()}/{path.removesuffix('.git')}"
    return None


def repository_identity(root: Path) -> dict[str, str]:
    resolved = Path(root).expanduser().resolve()
    git_root = _git(resolved, "rev-parse", "--show-toplevel")
    canonical_root = str(Path(git_root).resolve()) if git_root else str(resolved)
    raw_remote = _git(resolved, "config", "--get", "remote.origin.url") or ""
    logical_repository = _safe_remote_identity(raw_remote)
    logical_basis = logical_repository or f"root:{canonical_root}"
    source_basis = f"{logical_basis}|root:{canonical_root}"
    source_id = _sha256(source_basis)[:32]
    return {
        "repository_id": source_id,
        "local_repository_source_id": source_id,
        "logical_repository_identity": logical_repository or canonical_root,
        "identity_basis": source_basis,
        "git_root": canonical_root,
        "remote_origin": logical_repository or "",
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
            "local_repository_source_id": identity["local_repository_source_id"],
            "logical_repository_identity": identity["logical_repository_identity"],
            "identity_basis": identity["identity_basis"],
            "git_root": identity["git_root"],
            "remote_origin": identity["remote_origin"],
            "snapshot": snapshot,
        }
        _atomic_write_json(self.snapshot_path(identity["repository_id"]), stored)
        return {
            **snapshot,
            "repository_id": identity["repository_id"],
            "local_repository_source_id": identity["local_repository_source_id"],
            "logical_repository_identity": identity["logical_repository_identity"],
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

    @contextmanager
    def _bindings_lock(self):
        self.root.mkdir(parents=True, exist_ok=True)
        lock_path = self.root / "bindings.lock"
        with lock_path.open("a+b") as handle:
            handle.seek(0)
            handle.write(b"0")
            handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)  # type: ignore[attr-defined]
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _read_bindings_unlocked(self) -> dict[str, Any]:
        try:
            with self.bindings_path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return {"store_schema": BINDING_STORE_SCHEMA, "bindings": {}}
        if not isinstance(data, dict) or data.get("store_schema") != BINDING_STORE_SCHEMA:
            return {"store_schema": BINDING_STORE_SCHEMA, "bindings": {}}
        bindings = data.get("bindings")
        return {"store_schema": BINDING_STORE_SCHEMA, "bindings": bindings if isinstance(bindings, dict) else {}}

    def bindings(self) -> dict[str, Any]:
        with self._bindings_lock():
            return self._read_bindings_unlocked()

    def bind_namespace(self, namespace: str, repository_id: str, *, allow_rebind: bool = False) -> dict[str, Any]:
        cleaned = namespace.strip()
        if not cleaned:
            raise ValueError("namespace must not be empty")
        with self._bindings_lock():
            data = self._read_bindings_unlocked()
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
        with self._bindings_lock():
            data = self._read_bindings_unlocked()
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


_SEMANTIC_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")
_TECHNICAL_FACT_KEYS = {"repository_root", "commit_sha", "source_digest"}


def _query_tokens(query: str) -> set[str]:
    return {token.casefold() for token in _SEMANTIC_TOKEN_RE.findall(query) if len(token) >= 2}


def select_repository_facts(
    snapshot: dict[str, Any], query: str, *, limit: int = 8
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    raw_facts = snapshot.get("facts")
    facts: list[Any] = raw_facts if isinstance(raw_facts, list) else []
    query_terms = _query_tokens(query)
    explicit_technical = bool(query_terms & {"root", "path", "commit", "sha", "digest", "source"})
    scored: list[tuple[int, int, dict[str, Any]]] = []
    for index, fact in enumerate(facts):
        if not isinstance(fact, dict):
            continue
        key = str(fact.get("key") or "").casefold()
        if key in _TECHNICAL_FACT_KEYS and not explicit_technical:
            continue
        semantic_parts = [key, str(fact.get("value") or ""), str(fact.get("source") or "")]
        semantic_tokens = set(_SEMANTIC_TOKEN_RE.findall(" ".join(semantic_parts).casefold()))
        score = len(query_terms & semantic_tokens)
        if score:
            scored.append((score, -index, fact))
    scored.sort(reverse=True, key=lambda item: (item[0], item[1]))
    selected: list[dict[str, Any]] = [fact for _, _, fact in scored[: max(0, limit)]]
    selected_ids = {id(fact) for fact in selected}
    excluded: list[dict[str, Any]] = [fact for fact in facts if isinstance(fact, dict) and id(fact) not in selected_ids]
    return selected, excluded


def load_repository_knowledge(*, namespace: str, query: str, limit: int = 8) -> dict[str, Any]:
    from .paths import resolve_repository_snapshot_root

    store = RepositorySnapshotStore(resolve_repository_snapshot_root())
    snapshot = store.load_bound_snapshot(namespace)
    if snapshot is None:
        return {
            "authority": "derived_repository",
            "binding_state": "unbound",
            "selected": [],
            "excluded_count": 0,
        }
    if snapshot.get("binding_state") != "current":
        return {
            "authority": "derived_repository",
            "binding_state": snapshot.get("binding_state"),
            "stale_reason": snapshot.get("stale_reason"),
            "commit": snapshot.get("commit"),
            "current_commit": snapshot.get("current_commit"),
            "selected": [],
            "excluded_count": 0,
        }
    selected, excluded = select_repository_facts(snapshot, query, limit=limit)

    def project(fact: dict[str, Any]) -> dict[str, Any]:
        return {
            "key": fact.get("key"),
            "value": fact.get("value"),
            "source": fact.get("source"),
            "commit": fact.get("commit"),
            "authority": "derived_repository",
        }

    return {
        "authority": "derived_repository",
        "binding_state": "current",
        "repository_id": snapshot.get("repository_id"),
        "local_repository_source_id": snapshot.get("local_repository_source_id", snapshot.get("repository_id")),
        "logical_repository_identity": snapshot.get("logical_repository_identity"),
        "commit": snapshot.get("commit"),
        "current_commit": snapshot.get("current_commit"),
        "selected": [project(fact) for fact in selected],
        "excluded_count": len(excluded),
    }
