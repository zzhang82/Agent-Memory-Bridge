from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from . import repository_snapshot_store as snapshots
from .namespaces import canonical_namespace
from .project_init import PROJECT_NAMESPACE_RE

SCHEMA_VERSION = "project.resolution.v1"
NON_AUTHORITY_INPUTS = (
    "caller_tags",
    "client_workspace",
    "prompt_text",
    "source_client",
    "source_model",
)


def resolve_project_context(
    path: Path | str = Path("."),
    *,
    snapshot_root: Path,
    provenance: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve a local checkout to its governed project namespace.

    The result is read-only. It does not create a namespace, rebind an existing
    one, or refresh repository WHAT. Caller provenance cannot select identity.
    """
    ignored = _ignored_provenance_keys(provenance)
    try:
        resolved = Path(path).expanduser().resolve()
    except OSError as exc:
        return _result(
            status="repository_unavailable",
            namespace=None,
            identity=None,
            what=_what("path_unresolved"),
            ambiguity={"error_type": exc.__class__.__name__, "reason": "path_unresolved"},
            ignored_provenance_keys=ignored,
        )
    if not resolved.is_dir():
        return _result(
            status="not_a_repository",
            namespace=None,
            identity=None,
            what=_what("not_a_repository"),
            ambiguity=None,
            ignored_provenance_keys=ignored,
        )

    inside = snapshots._git(resolved, "rev-parse", "--is-inside-work-tree")
    toplevel = snapshots._git(resolved, "rev-parse", "--show-toplevel")
    if inside != "true" or not toplevel:
        if _git_available():
            return _result(
                status="not_a_repository",
                namespace=None,
                identity=None,
                what=_what("not_a_repository"),
                ambiguity=None,
                ignored_provenance_keys=ignored,
            )
        return _result(
            status="repository_unavailable",
            namespace=None,
            identity=None,
            what=_what("git_unavailable"),
            ambiguity={"reason": "git_unavailable"},
            ignored_provenance_keys=ignored,
        )

    identity = dict(snapshots.repository_identity(resolved))
    what = _repository_what(
        Path(str(identity.get("git_root") or resolved)),
        snapshot_root=Path(snapshot_root),
        repository_id=str(identity.get("repository_id") or ""),
    )
    state, bindings, ambiguity = _read_bindings(Path(snapshot_root))
    if state in {"unreadable", "invalid"}:
        return _result(
            status="ambiguous_binding",
            namespace=None,
            identity=identity,
            what=what,
            ambiguity=ambiguity,
            ignored_provenance_keys=ignored,
        )

    repository_id = str(identity.get("repository_id") or "")
    valid: list[str] = []
    invalid: list[str] = []
    for name, binding in bindings.items():
        if not isinstance(binding, dict) or binding.get("repository_id") != repository_id:
            continue
        if isinstance(name, str) and PROJECT_NAMESPACE_RE.fullmatch(name):
            valid.append(name)
        else:
            invalid.append(str(name))
    valid.sort()
    invalid.sort()
    if invalid or len(valid) > 1:
        namespaces = sorted(set(valid).union(invalid))
        reason = "invalid_namespace" if invalid else "multiple_namespaces"
        return _result(
            status="ambiguous_binding",
            namespace=None,
            identity=identity,
            what=what,
            ambiguity={
                "namespaces": namespaces,
                "reason": reason,
                "repository_id": repository_id,
            },
            ignored_provenance_keys=ignored,
        )
    if not valid:
        return _result(
            status="no_binding",
            namespace=None,
            identity=identity,
            what=what,
            ambiguity=None,
            ignored_provenance_keys=ignored,
        )
    return _result(
        status="bound",
        namespace=valid[0],
        identity=identity,
        what=what,
        ambiguity=None,
        ignored_provenance_keys=ignored,
    )


def namespace_for_host_adapter(result: Mapping[str, Any]) -> str | None:
    """Return the governed namespace, or None when the adapter must not guess."""
    if result.get("status") != "bound":
        return None
    namespace = result.get("namespace")
    if not isinstance(namespace, str) or PROJECT_NAMESPACE_RE.fullmatch(namespace) is None:
        return None
    return namespace


def resolution_exit_code(result: Mapping[str, Any]) -> int:
    """Return 0 only when the checkout resolved to exactly one namespace."""
    return 0 if result.get("status") == "bound" else 1


def _ignored_provenance_keys(provenance: Mapping[str, Any] | None) -> list[str]:
    if not isinstance(provenance, Mapping):
        return []
    return sorted(str(key) for key in provenance)


def _result(
    *,
    status: str,
    namespace: str | None,
    identity: dict[str, Any] | None,
    what: dict[str, Any],
    ambiguity: dict[str, Any] | None,
    ignored_provenance_keys: list[str],
) -> dict[str, Any]:
    return {
        "ambiguity": ambiguity,
        "ignored_provenance_keys": ignored_provenance_keys,
        "namespace": namespace,
        "non_authority_inputs": list(NON_AUTHORITY_INPUTS),
        "repository_identity": identity,
        "repository_what": what,
        "schema_version": SCHEMA_VERSION,
        "status": status,
    }


def _what(reason: str) -> dict[str, Any]:
    return {
        "authority": "derived_repository",
        "checkout_clean": None,
        "eligible": False,
        "head": None,
        "reason": reason,
    }


def _repository_what(git_root: Path, *, snapshot_root: Path, repository_id: str) -> dict[str, Any]:
    """Mirror stored WHAT eligibility without refreshing or presenting stale facts."""
    status_ok, clean = snapshots._clean_status(git_root)
    head = snapshots._git(git_root, "rev-parse", "HEAD")
    stored = snapshots.RepositorySnapshotStore(snapshot_root).load_snapshot(repository_id)
    checkout_clean: bool | None
    if not status_ok:
        checkout_clean = None
        reason = "worktree_status_unavailable"
        eligible = False
    elif clean is not True:
        checkout_clean = False
        reason = "dirty_worktree"
        eligible = False
    elif stored is None:
        checkout_clean = True
        reason = "missing_snapshot"
        eligible = False
    elif stored.get("binding") != "git_commit" or not head or stored.get("commit") != head:
        checkout_clean = True
        reason = "head_changed" if stored.get("commit") != head else "not_commit_bound"
        eligible = False
    else:
        checkout_clean = True
        reason = "git_commit"
        eligible = True
    return {
        "authority": "derived_repository",
        "checkout_clean": checkout_clean,
        "eligible": eligible,
        "head": head,
        "reason": reason,
    }


def _read_bindings(snapshot_root: Path) -> tuple[str, dict[str, Any], dict[str, Any] | None]:
    path = snapshot_root / "bindings.json"
    if not path.exists():
        return "absent", {}, None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return "unreadable", {}, {"error_type": exc.__class__.__name__, "reason": "bindings_unreadable"}
    if not isinstance(data, dict) or data.get("store_schema") != snapshots.BINDING_STORE_SCHEMA:
        return "invalid", {}, {"reason": "bindings_invalid"}
    raw_bindings = data.get("bindings")
    if not isinstance(raw_bindings, dict):
        return "invalid", {}, {"reason": "bindings_invalid"}
    bindings: dict[str, Any] = {}
    for k, v in raw_bindings.items():
        norm_k = canonical_namespace(k) if isinstance(k, str) else k
        if norm_k in bindings:
            existing = bindings[norm_k]
            existing_repo = existing.get("repository_id") if isinstance(existing, dict) else None
            incoming_repo = v.get("repository_id") if isinstance(v, dict) else None
            if existing_repo != incoming_repo:
                return "invalid", {}, {"reason": "binding_collision"}
        bindings[norm_k] = v
    return "ok", bindings, None


def _git_available() -> bool:
    try:
        subprocess.run(["git", "--version"], capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return True
