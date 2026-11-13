from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from .relation_metadata import parse_relation_metadata

RECHECK_STATUSES = ("current", "needs_recheck", "unknown")


def dependency_recheck_status(
    content: str,
    *,
    records_by_id: dict[str, dict[str, Any]],
    repository_root: Path | None = None,
    confirmation_commits: list[str] | None = None,
) -> dict[str, Any]:
    """Return a read-time status without revising or hiding the decision.

    Memory dependencies use existing record lifecycle fields. Repository
    dependencies use ``depends_on_repo: path@commit`` and require a clean Git
    checkout. A recorded confirmation commit replaces the stored dependency
    commit. Missing or dirty Git state is ``unknown``, never ``current``.
    """

    metadata = parse_relation_metadata(content)
    reasons: list[str] = []
    unknown = False
    for target_id in metadata["relations"]["depends_on"]:
        target = records_by_id.get(target_id)
        if target is None or target.get("forgotten") is True:
            reasons.append(f"depends_on:missing:{target_id}")
        elif target.get("superseded") is True or target.get("status") == "superseded":
            reasons.append(f"depends_on:superseded:{target_id}")
        elif target.get("validity_status") == "expired":
            reasons.append(f"depends_on:expired:{target_id}")

    references = _repository_references(content)
    repo_divergences: list[dict[str, Any]] = []
    if references and repository_root is None:
        unknown = True
    elif references:
        root = repository_root or Path()
        toplevel = _git(root, "rev-parse", "--show-toplevel")
        git_root = Path(toplevel.strip()) if toplevel and toplevel.strip() else root
        snapshot = _repository_snapshot(git_root)
        if snapshot is None or _repository_is_dirty(git_root, [path for path, _commit in references]):
            unknown = True
        else:
            confirmed_commit = next(
                (commit for commit in reversed(confirmation_commits or []) if _is_commit(commit)),
                None,
            )
            for path, recorded_commit in references:
                baseline = confirmed_commit or recorded_commit
                if not _path_exists_at(git_root, baseline, path):
                    unknown = True
                    continue
                if _path_changed(git_root, baseline, snapshot["head"], path):
                    reasons.append(f"depends_on_repo:changed:{path}")
                    divergence = _build_repo_divergence(
                        root=git_root,
                        path=path,
                        baseline_commit=baseline,
                        current_commit=snapshot["head"],
                        content=content,
                    )
                    repo_divergences.append(divergence)

    status = "needs_recheck" if reasons else "unknown" if unknown else "current"
    return {
        "recheck_status": status,
        "recheck_reasons": reasons,
        "repo_divergence": repo_divergences,
    }


def _build_repo_divergence(
    root: Path,
    path: str,
    baseline_commit: str,
    current_commit: str,
    content: str,
) -> dict[str, Any]:
    from .structured_record import parse_structured_content

    structured = parse_structured_content(content)
    claim = structured.first("claim")
    if not claim:
        lines = [line.strip() for line in content.splitlines() if line.strip() and not line.startswith("record_type:")]
        claim = lines[0] if lines else ""

    live_text: str | None = None
    target_file = root / path
    if target_file.is_file():
        try:
            live_text = target_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass
    if live_text is None:
        live_text = _git(root, "show", f"{current_commit}:{path}")

    live_preview = ""
    if live_text is not None:
        first_lines = [line.strip() for line in live_text.splitlines() if line.strip()][:3]
        live_preview = " \n ".join(first_lines)[:300]
    else:
        live_preview = "(file deleted or missing in live checkout)"

    diff_stat = _git(root, "diff", "--shortstat", baseline_commit, current_commit, "--", path)
    summary = (
        diff_stat.strip()
        if diff_stat and diff_stat.strip()
        else f"File {path} modified between {baseline_commit[:7]} and {current_commit[:7]}"
    )

    return {
        "path": path,
        "recorded_commit": baseline_commit,
        "current_commit": current_commit,
        "stored_claim": claim,
        "live_content_preview": live_preview,
        "diff_summary": summary,
        "offer_revise": (
            f"Stored claim was recorded against {path}@{baseline_commit[:7]}, "
            f"but the repository is at {current_commit[:7]}. "
            f"Consider using 'revise' to update this memory if current code supersedes the stored claim."
        ),
    }


def _repository_references(content: str) -> list[tuple[str, str]]:
    import re

    metadata = parse_relation_metadata(content)
    raw_values = metadata["relations"].get("depends_on_repo", [])
    if not raw_values:
        from .structured_record import parse_structured_content

        raw_values = list(parse_structured_content(content).values("depends_on_repo"))
    references: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for value in raw_values:
        path, separator, commit = value.rpartition("@")
        if separator and path and _is_commit(commit):
            ref = (path, commit)
            if ref not in seen:
                seen.add(ref)
                references.append(ref)
    if not references:
        for match in re.finditer(r"\b([A-Za-z0-9_./\\-]+\.[A-Za-z0-9_]+)@([0-9a-fA-F]{7,40})\b", content):
            p, c = match.group(1), match.group(2)
            if _is_commit(c):
                ref = (p, c)
                if ref not in seen:
                    seen.add(ref)
                    references.append(ref)
    return references


def _repository_snapshot(root: Path) -> dict[str, str] | None:
    head = _git(root, "rev-parse", "HEAD")
    if head is None:
        return None
    return {"head": head.strip()}


def _repository_is_dirty(root: Path, paths: list[str]) -> bool:
    """True when a declared dependency has uncommitted content or an unmerged stage.

    A clean checkout returns no output. Git failures stay unknown, matching a
    missing repository rather than reporting the recorded commit as current.
    """

    for path in paths:
        unstaged = _git(root, "diff", "--name-only", "--", path)
        staged = _git(root, "diff", "--cached", "--name-only", "--", path)
        if unstaged is None or staged is None or unstaged.strip() or staged.strip():
            return True
    return False


def _path_exists_at(root: Path, commit: str, path: str) -> bool:
    return _git(root, "cat-file", "-e", f"{commit}:{path}") is not None


def _path_changed(root: Path, old_commit: str, new_commit: str, path: str) -> bool:
    if old_commit == new_commit:
        return False
    diff = _git(root, "diff", "--name-only", old_commit, new_commit, "--", path)
    return bool(diff and diff.strip())


def _git(root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def _is_commit(value: str) -> bool:
    return len(value) in {7, 40} and all(character in "0123456789abcdef" for character in value.lower())
