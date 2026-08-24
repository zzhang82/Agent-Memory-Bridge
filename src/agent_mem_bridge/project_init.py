from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .knowledge_explorer import (
    EMPTY_WHY_GUIDANCE,
    _build_explorer,
    _presentation_from_build,
    render_explorer_human_markdown,
)
from .repository_bootstrap import compile_repository_snapshot
from .repository_snapshot_store import RepositorySnapshotStore, repository_identity

PROJECT_NAMESPACE_RE = re.compile(r"^project:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
NAMESPACE_TOKEN_RE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True, slots=True)
class ProjectInitPlan:
    path: Path
    repository_name: str
    suggested_namespace: str
    chosen_namespace: str
    identity: dict[str, str]
    snapshot: dict[str, Any]
    existing_binding: dict[str, Any] | None
    repository_bound_namespaces: tuple[str, ...]
    action: str
    blocking_error: str | None = None


def propose_project_namespace(repository_name: str) -> str:
    token = NAMESPACE_TOKEN_RE.sub("-", repository_name.casefold()).strip("-")
    token = token[:63].strip("-") or "project"
    if token[0].isdigit():
        token = f"repo-{token}"[:63].strip("-")
    return f"project:{token}"


def validate_project_namespace(namespace: str) -> str:
    cleaned = namespace.strip()
    if not PROJECT_NAMESPACE_RE.fullmatch(cleaned):
        raise ValueError("namespace must be `project:` plus a lowercase slug of letters, digits, and hyphens")
    return cleaned


def plan_project_init(
    path: Path,
    *,
    namespace: str | None,
    snapshot_root: Path,
) -> ProjectInitPlan:
    snapshot = compile_repository_snapshot(path)
    repository_name = str(snapshot.get("repository") or path.expanduser().resolve().name)
    suggested = propose_project_namespace(repository_name)
    chosen = validate_project_namespace(namespace) if namespace else suggested
    git_root = snapshot.get("root")
    identity = (
        repository_identity(Path(str(git_root)))
        if git_root
        else {
            "repository_id": "",
            "local_repository_source_id": "",
            "logical_repository_identity": "",
            "git_root": str(path),
            "remote_origin": "",
        }
    )
    store = RepositorySnapshotStore(snapshot_root)
    bindings = store.peek_bindings()["bindings"]
    existing = bindings.get(chosen)
    existing_binding = existing if isinstance(existing, dict) else None
    repository_id = identity.get("repository_id") or ""
    bound_here = tuple(
        sorted(
            name
            for name, binding in bindings.items()
            if isinstance(binding, dict) and binding.get("repository_id") == repository_id
        )
    )

    blocking_error: str | None = None
    action = "bootstrap"
    git_binding = str(snapshot.get("binding") or "unbound")
    uncertain_reasons = [
        str(item.get("reason") or "") for item in snapshot.get("uncertain") or [] if isinstance(item, dict)
    ]
    excluded_reasons = [
        str(item.get("reason") or "") for item in snapshot.get("excluded") or [] if isinstance(item, dict)
    ]
    if git_binding != "git_commit":
        reason = str(snapshot.get("reason") or git_binding)
        if reason == "dirty_worktree":
            blocking_error = (
                "Repository WHAT cannot be initialized because the worktree is dirty. "
                "Commit, stash, or restore, then rerun Project Init. Dirty content is not attributed to HEAD."
            )
        elif reason == "worktree_status_unavailable":
            blocking_error = "Repository status could not be verified, so Project Init cannot bind WHAT to HEAD."
        elif any("not a directory" in item for item in excluded_reasons):
            blocking_error = "This path is not a directory. Choose a Git checkout, then rerun Project Init."
        elif any("not a git worktree" in item for item in uncertain_reasons) or git_binding in {
            "unbound",
            "unavailable",
        }:
            blocking_error = (
                "This path is not a Git repository. Initialize or choose a Git checkout, then rerun Project Init."
            )
        else:
            blocking_error = "Repository WHAT cannot be initialized from the current checkout."
    elif existing_binding and existing_binding.get("repository_id") != repository_id:
        blocking_error = (
            f"Namespace `{chosen}` is already bound to a different repository. "
            "Choose another namespace or unbind the existing binding first. Project Init does not rebind silently."
        )
        action = "conflict"
    elif bound_here and chosen not in bound_here:
        current = ", ".join(f"`{name}`" for name in bound_here)
        blocking_error = (
            f"This repository is already bound as {current}. "
            "Use that namespace, or unbind it before choosing a different one."
        )
        action = "conflict"
    elif existing_binding and existing_binding.get("repository_id") == repository_id:
        action = "refresh"

    return ProjectInitPlan(
        path=path.expanduser().resolve(),
        repository_name=repository_name,
        suggested_namespace=suggested,
        chosen_namespace=chosen,
        identity=identity,
        snapshot=snapshot,
        existing_binding=existing_binding,
        repository_bound_namespaces=bound_here,
        action=action,
        blocking_error=blocking_error,
    )


def render_project_init_detection(plan: ProjectInitPlan) -> str:
    lines = [
        f"Project detected: {plan.repository_name}",
        f"Suggested namespace: {plan.suggested_namespace}",
        f"Chosen namespace: {plan.chosen_namespace}",
        "",
    ]
    if plan.blocking_error:
        lines.append(plan.blocking_error)
    return "\n".join(lines) + "\n"


def render_project_init_preview(plan: ProjectInitPlan) -> str:
    lines = [render_project_init_detection(plan).rstrip(), ""]
    if plan.blocking_error:
        return "\n".join(lines).rstrip() + "\n"
    if plan.action == "refresh":
        lines.append(
            "This namespace is already bound to this repository. Confirmed init would refresh repository WHAT."
        )
    else:
        lines.append("Confirmed init would bootstrap repository WHAT and then show Human-first Explore.")
    lines.append("No changes have been made.")
    return "\n".join(lines) + "\n"


def _same_repository_identity(left: dict[str, str], right: dict[str, str]) -> bool:
    return (left.get("repository_id") or "", left.get("git_root") or "") == (
        right.get("repository_id") or "",
        right.get("git_root") or "",
    )


def apply_project_init(plan: ProjectInitPlan, *, snapshot_root: Path) -> dict[str, Any]:
    if plan.blocking_error:
        raise ValueError(plan.blocking_error)
    # Confirmation covers repository identity + namespace, not a specific commit.
    # Recompile after confirm and persist that snapshot; never persist plan.snapshot.
    current = plan_project_init(plan.path, namespace=plan.chosen_namespace, snapshot_root=snapshot_root)
    if not _same_repository_identity(plan.identity, current.identity):
        raise ValueError("Repository identity changed after confirmation. Rerun Project Init.")
    if current.blocking_error:
        raise ValueError(current.blocking_error)
    store = RepositorySnapshotStore(snapshot_root)
    stored = store.save_snapshot(current.snapshot)
    binding = store.bind_namespace(current.chosen_namespace, str(stored["repository_id"]))
    return {"snapshot": stored, "binding": binding, "action": current.action}


def project_init_success_what_line(action: str) -> str:
    if action == "refresh":
        return "Repository WHAT refreshed; existing project WHY is unchanged."
    return "Repository WHAT initialized."


def render_project_init_success(
    plan: ProjectInitPlan,
    *,
    snapshot_root: Path,
    memory_store: Any | None = None,
    action: str | None = None,
) -> str:
    applied = action or plan.action
    heading = (
        f"Refreshed project: {plan.repository_name}"
        if applied == "refresh"
        else f"Initialized project: {plan.repository_name}"
    )
    build = _build_explorer(
        namespace=plan.chosen_namespace,
        snapshot_root=snapshot_root,
        memory_store=memory_store,
    )
    explore = render_explorer_human_markdown(build)
    presentation = _presentation_from_build(build)
    lines = [
        heading,
        f"Namespace: {plan.chosen_namespace}",
        "",
        project_init_success_what_line(applied),
        "",
        explore.rstrip(),
    ]
    if not presentation.decisions and not presentation.constraints and EMPTY_WHY_GUIDANCE not in explore:
        lines.extend(["", EMPTY_WHY_GUIDANCE])
    return "\n".join(lines) + "\n"
