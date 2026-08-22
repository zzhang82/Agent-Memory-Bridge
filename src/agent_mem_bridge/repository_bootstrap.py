from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

EXTRACTOR_VERSION = "1"
MAX_TEXT_BYTES = 64 * 1024
MAX_STRUCTURE_ENTRIES = 200
SECRET_NAMES = {".env", ".env.local", ".env.production", ".env.development", "credentials", "secrets"}
GENERATED_NAMES = {
    ".git",
    ".hypothesis",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "build",
    "dist",
    "node_modules",
    "__pycache__",
}
MANIFEST_NAMES = {
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "requirements.txt",
    "requirements-dev.txt",
    "Makefile",
    "Taskfile",
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    "compose.yml",
    "compose.yaml",
}
DOC_PREFIXES = ("README", "AGENTS", "CLAUDE", "CONTRIBUTING")


def _under(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


def _git(root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=5, check=True)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None


def _safe_read(
    root: Path, relative: str, excluded: list[dict[str, str]], uncertain: list[dict[str, str]]
) -> str | None:
    path = root / relative
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        uncertain.append({"source": relative, "reason": f"unreadable: {exc.__class__.__name__}"})
        return None
    if not _under(root, resolved):
        excluded.append({"source": relative, "reason": "symlink escapes repository root"})
        return None
    if resolved.name.lower() in SECRET_NAMES or resolved.name.startswith(".") and resolved.name in SECRET_NAMES:
        excluded.append({"source": relative, "reason": "secret-like file excluded"})
        return None
    try:
        size = resolved.stat().st_size
    except OSError as exc:
        uncertain.append({"source": relative, "reason": f"stat failed: {exc.__class__.__name__}"})
        return None
    if size > MAX_TEXT_BYTES:
        excluded.append({"source": relative, "reason": f"file exceeds {MAX_TEXT_BYTES} byte bound"})
        return None
    try:
        data = resolved.read_bytes()
    except OSError as exc:
        uncertain.append({"source": relative, "reason": f"read failed: {exc.__class__.__name__}"})
        return None
    if b"\x00" in data:
        excluded.append({"source": relative, "reason": "binary file excluded"})
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        excluded.append({"source": relative, "reason": "non-UTF-8 file excluded"})
        return None


def _fact(facts: list[dict[str, Any]], key: str, value: Any, source: str, root: Path, commit: str | None) -> None:
    facts.append({"key": key, "value": value, "source": source, "commit": commit, "authority": "derived"})


def compile_repository_snapshot(repo: Path) -> dict[str, Any]:
    root = repo.expanduser().resolve()
    excluded: list[dict[str, str]] = []
    uncertain: list[dict[str, str]] = []
    facts: list[dict[str, Any]] = []
    if not root.exists() or not root.is_dir():
        return {
            "repository": root.name,
            "root": str(root),
            "commit": None,
            "extractor_version": EXTRACTOR_VERSION,
            "binding": "unbound",
            "worktree_clean": None,
            "facts": [],
            "excluded": [{"source": str(repo), "reason": "not a directory"}],
            "uncertain": [],
        }
    commit = _git(root, "rev-parse", "HEAD")
    git_root = _git(root, "rev-parse", "--show-toplevel")
    status = _git(root, "status", "--porcelain", "--untracked-files=all") if git_root else None
    if git_root:
        actual_root = Path(git_root).resolve()
        if actual_root != root:
            uncertain.append(
                {"source": ".git", "reason": "requested path is inside a repository; using requested root only"}
            )
    else:
        uncertain.append({"source": ".git", "reason": "git unavailable or directory is not a git worktree"})
    remote = _git(root, "config", "--get", "remote.origin.url")
    repository_name = root.name
    if remote:
        repository_name = remote.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git") or repository_name
    if git_root and status:
        return {
            "repository": repository_name,
            "root": str(root),
            "commit": commit,
            "extractor_version": EXTRACTOR_VERSION,
            "binding": "unavailable",
            "worktree_clean": False,
            "reason": "dirty_worktree",
            "authority": "derived_repository_knowledge",
            "facts": [],
            "excluded": [],
            "uncertain": [
                {
                    "source": ".",
                    "reason": "dirty_worktree; repository content facts withheld rather than attributed to HEAD",
                }
            ],
        }
    binding = "git_commit" if git_root and commit else "unbound"
    worktree_clean: bool | None = True if git_root and commit else None
    fact_commit = commit if binding == "git_commit" else None
    _fact(facts, "repository_name", repository_name, "remote.origin.url" if remote else ".", root, fact_commit)
    _fact(facts, "repository_root", str(root), ".", root, fact_commit)
    if commit and binding == "git_commit":
        _fact(facts, "commit_sha", commit, ".git/HEAD", root, commit)
    else:
        uncertain.append({"source": ".git/HEAD", "reason": "commit binding unavailable"})
    entries: list[str] = []
    try:
        top_level_entries = sorted(root.iterdir(), key=lambda p: p.name.casefold())
        for entry in top_level_entries:
            if entry.is_symlink():
                try:
                    if not _under(root, entry.resolve(strict=True)):
                        excluded.append({"source": entry.name, "reason": "symlink escapes repository root"})
                except OSError:
                    uncertain.append({"source": entry.name, "reason": "symlink target unreadable"})
            if entry.name in SECRET_NAMES or entry.name.startswith(".env."):
                excluded.append({"source": entry.name, "reason": "secret-like file excluded"})
        for entry in top_level_entries:
            if entry.name in GENERATED_NAMES or entry.name in SECRET_NAMES or entry.name.startswith(".env."):
                continue
            if len(entries) >= MAX_STRUCTURE_ENTRIES:
                excluded.append(
                    {"source": ".", "reason": f"top-level structure capped at {MAX_STRUCTURE_ENTRIES} entries"}
                )
                break
            entries.append(entry.name + ("/" if entry.is_dir() else ""))
    except OSError as exc:
        uncertain.append({"source": ".", "reason": f"directory listing failed: {exc.__class__.__name__}"})
    _fact(facts, "top_level_structure", entries, ".", root, fact_commit)
    names = [p.name for p in top_level_entries] if "top_level_entries" in locals() else []
    for name in names:
        if name in MANIFEST_NAMES or name.startswith(DOC_PREFIXES):
            text = _safe_read(root, name, excluded, uncertain)
            if text is None:
                continue
            digest = hashlib.sha256(text.encode()).hexdigest()
            if name == "pyproject.toml":
                try:
                    import tomllib

                    parsed = tomllib.loads(text)
                    project = parsed.get("project", {})
                    _fact(facts, "python_package", True, name, root, fact_commit)
                    if isinstance(project, dict) and project.get("requires-python"):
                        _fact(facts, "python_requires", project["requires-python"], name, root, fact_commit)
                except (tomllib.TOMLDecodeError, ValueError):
                    excluded.append({"source": name, "reason": "malformed TOML manifest"})
                    continue
            elif name == "package.json":
                try:
                    parsed = json.loads(text)
                    _fact(facts, "node_package", True, name, root, fact_commit)
                    if isinstance(parsed, dict) and parsed.get("packageManager"):
                        _fact(facts, "package_manager", parsed["packageManager"], name, root, fact_commit)
                except json.JSONDecodeError:
                    excluded.append({"source": name, "reason": "malformed JSON manifest"})
                    continue
            elif name == "go.mod":
                _fact(facts, "go_module", True, name, root, fact_commit)
            elif name == "Cargo.toml":
                _fact(facts, "rust_package", True, name, root, fact_commit)
            elif name in {"Makefile", "Taskfile"}:
                _fact(facts, "task_runner", name, name, root, fact_commit)
            elif name.startswith("Dockerfile") or name.startswith("compose") or name.startswith("docker-compose"):
                _fact(facts, "container_config", name, name, root, fact_commit)
            elif name.startswith(DOC_PREFIXES):
                _fact(facts, "project_document", name, name, root, fact_commit)
            if name.endswith(
                (".toml", ".json", ".xml", ".gradle", ".kts", ".txt", ".mod", ".yml", ".yaml", "Makefile", "Taskfile")
            ):
                _fact(facts, "source_digest", {"path": name, "sha256": digest}, name, root, fact_commit)
    workflows = root / ".github" / "workflows"
    if workflows.is_dir() and _under(root, workflows.resolve()):
        files = sorted(p.name for p in workflows.iterdir() if p.is_file() and p.suffix in {".yml", ".yaml"})[
            :MAX_STRUCTURE_ENTRIES
        ]
        _fact(facts, "github_actions_workflows", files, ".github/workflows", root, fact_commit)
    return {
        "repository": repository_name,
        "root": str(root),
        "commit": commit,
        "extractor_version": EXTRACTOR_VERSION,
        "binding": binding,
        "worktree_clean": worktree_clean,
        "authority": "derived_repository_knowledge",
        "facts": facts,
        "excluded": excluded,
        "uncertain": uncertain,
    }


def render_snapshot_markdown(snapshot: dict[str, Any]) -> str:
    lines = [
        f"Repository: {snapshot['repository']}",
        f"Commit: {snapshot['commit'] or 'unavailable'}",
        f"Binding: {snapshot.get('binding', 'unbound')}",
        f"Worktree clean: {snapshot.get('worktree_clean')}",
        f"Extractor: {snapshot['extractor_version']}",
        "",
        "Detected:",
    ]
    for fact in snapshot["facts"]:
        lines.append(
            f"- {fact['key']}: {json.dumps(fact['value'], ensure_ascii=False, sort_keys=True)} (source: {fact['source']}; authority: derived)"
        )
    lines.append("\nExcluded / uncertain:")
    for item in [*snapshot["excluded"], *snapshot["uncertain"]]:
        lines.append(f"- {item['source']}: {item['reason']}")
    if len(lines) == 5:
        lines.append("- none recorded")
    binding_note = (
        "explicitly commit-bound because the worktree is clean"
        if snapshot.get("binding") == "git_commit"
        else "not commit-bound; content attribution is unavailable"
    )
    lines.append(
        f"\nThis snapshot is derived, bounded, rebuildable, and {binding_note}. It is not durable project memory and makes no completeness claim."
    )
    return "\n".join(lines) + "\n"
