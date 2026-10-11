from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from agent_mem_bridge.cli import _build_parser, main
from agent_mem_bridge.mcp_boundary import PUBLIC_TOOL_ORDER
from agent_mem_bridge.project_init import plan_project_init, propose_project_namespace
from agent_mem_bridge.project_resolution import (
    namespace_for_host_adapter,
    resolve_project_context,
)
from agent_mem_bridge.repository_bootstrap import compile_repository_snapshot
from agent_mem_bridge.repository_snapshot_store import (
    RepositorySnapshotStore,
    _safe_remote_identity,
    repository_identity,
)

ROOT = Path(__file__).resolve().parents[1]


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


def run_git(*args: str) -> None:
    subprocess.run(["git", *args], check=True, capture_output=True, text=True)


def make_repo(parent: Path, name: str = "Agent-Memory-Bridge") -> Path:
    repo = parent / name
    repo.mkdir()
    (repo / "README.md").write_text("Fixture project\n", encoding="utf-8")
    (repo / "pkg").mkdir()
    (repo / "pkg" / "keep.txt").write_text("tracked\n", encoding="utf-8")
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "fixture@example.invalid")
    git(repo, "config", "user.name", "Fixture")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "initial")
    return repo


def head(repo: Path) -> str:
    return subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()


def isolate_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "amb-home"
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_HOME", str(home))
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_DB_PATH", str(home / "bridge.db"))
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_LOG_DIR", str(home / "logs"))
    return home


def tree_bytes(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    payload: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            payload[str(path.relative_to(root))] = path.read_bytes()
    return payload


def bind_current(repo: Path, snapshot_root: Path, namespace: str) -> dict[str, str]:
    store = RepositorySnapshotStore(snapshot_root)
    saved = store.save_snapshot(compile_repository_snapshot(repo))
    store.bind_namespace(namespace, str(saved["repository_id"]))
    return repository_identity(repo)


def test_public_mcp_surface_does_not_grow() -> None:
    assert len(PUBLIC_TOOL_ORDER) == 17
    assert "resolve_project" not in PUBLIC_TOOL_ORDER
    assert "project_resolve" not in PUBLIC_TOOL_ORDER


def test_clean_bound_repository_resolves_without_reconstructing_slug(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    snapshot_root = tmp_path / "repository"
    identity = bind_current(repo, snapshot_root, "project:custom-name")
    before = tree_bytes(snapshot_root)
    result = resolve_project_context(repo, snapshot_root=snapshot_root)
    assert result["status"] == "bound"
    assert result["namespace"] == "project:custom-name"
    assert result["namespace"] != propose_project_namespace(repo.name)
    assert result["repository_identity"] == identity
    assert result["repository_what"]["eligible"] is True
    assert result["repository_what"]["reason"] == "git_commit"
    assert result["repository_what"]["head"] == head(repo)
    assert namespace_for_host_adapter(result) == "project:custom-name"
    assert tree_bytes(snapshot_root) == before


def test_unbound_repository_returns_no_binding_and_writes_nothing(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    snapshot_root = tmp_path / "repository"
    result = resolve_project_context(repo, snapshot_root=snapshot_root)
    assert result["status"] == "no_binding"
    assert result["namespace"] is None
    assert result["repository_identity"]["repository_id"] == repository_identity(repo)["repository_id"]
    assert result["repository_what"]["eligible"] is False
    assert namespace_for_host_adapter(result) is None
    assert snapshot_root.exists() is False


def test_conflicting_bindings_fail_closed(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    snapshot_root = tmp_path / "repository"
    identity = bind_current(repo, snapshot_root, "project:one")
    RepositorySnapshotStore(snapshot_root).bind_namespace("project:two", identity["repository_id"])
    before = tree_bytes(snapshot_root)
    result = resolve_project_context(repo, snapshot_root=snapshot_root)
    assert result["status"] == "ambiguous_binding"
    assert result["namespace"] is None
    assert result["ambiguity"]["reason"] == "multiple_namespaces"
    assert result["ambiguity"]["namespaces"] == ["project:one", "project:two"]
    assert namespace_for_host_adapter(result) is None
    assert tree_bytes(snapshot_root) == before


def test_invalid_namespace_and_unreadable_bindings_fail_closed(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    snapshot_root = tmp_path / "repository"
    identity = repository_identity(repo)
    snapshot_root.mkdir()
    bindings = snapshot_root / "bindings.json"
    bindings.write_text(
        json.dumps(
            {
                "store_schema": "repository.binding.v1",
                "bindings": {"Not A Namespace": {"repository_id": identity["repository_id"]}},
            }
        ),
        encoding="utf-8",
    )
    invalid = resolve_project_context(repo, snapshot_root=snapshot_root)
    assert invalid["status"] == "ambiguous_binding"
    assert invalid["namespace"] is None
    assert invalid["ambiguity"]["reason"] == "invalid_namespace"
    bindings.write_text("{", encoding="utf-8")
    unreadable = resolve_project_context(repo, snapshot_root=snapshot_root)
    assert unreadable["status"] == "ambiguous_binding"
    assert unreadable["ambiguity"]["reason"] == "bindings_unreadable"
    assert unreadable["namespace"] is None
    bindings.write_text('{"store_schema":"other","bindings":{}}', encoding="utf-8")
    invalid_schema = resolve_project_context(repo, snapshot_root=snapshot_root)
    assert invalid_schema["status"] == "ambiguous_binding"
    assert invalid_schema["ambiguity"]["reason"] == "bindings_invalid"
    assert invalid_schema["namespace"] is None


def test_dirty_worktree_keeps_identity_but_not_what_authority(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    snapshot_root = tmp_path / "repository"
    bind_current(repo, snapshot_root, "project:custom-name")
    (repo / "README.md").write_text("secret-dirty-token\n", encoding="utf-8")
    before = tree_bytes(snapshot_root)
    result = resolve_project_context(repo, snapshot_root=snapshot_root)
    plan = plan_project_init(repo, namespace="project:custom-name", snapshot_root=snapshot_root)
    assert result["status"] == "bound"
    assert result["namespace"] == "project:custom-name"
    assert result["repository_what"]["eligible"] is False
    assert result["repository_what"]["reason"] == "dirty_worktree"
    assert result["repository_what"]["checkout_clean"] is False
    assert result["repository_what"]["head"] == head(repo)
    assert "secret-dirty-token" not in json.dumps(result)
    assert plan.blocking_error is not None
    assert "worktree is dirty" in plan.blocking_error
    assert tree_bytes(snapshot_root) == before


def test_changed_head_remains_bound_and_stored_what_stays_ineligible(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    snapshot_root = tmp_path / "repository"
    bind_current(repo, snapshot_root, "project:custom-name")
    (repo / "README.md").write_text("second\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-qm", "second")
    result = resolve_project_context(repo, snapshot_root=snapshot_root)
    stored = RepositorySnapshotStore(snapshot_root).load_bound_snapshot("project:custom-name")
    assert result["status"] == "bound"
    assert result["namespace"] == "project:custom-name"
    assert result["repository_what"]["eligible"] is False
    assert result["repository_what"]["reason"] == "head_changed"
    assert stored is not None
    assert stored["binding_state"] == "stale"
    assert stored["stale_reason"] == "head_changed"


def test_worktrees_and_clones_do_not_collapse_on_shared_origin(tmp_path: Path) -> None:
    source = make_repo(tmp_path, "source")
    bare = tmp_path / "origin.git"
    run_git("clone", "--bare", str(source), str(bare))
    clone_a = tmp_path / "clone-a"
    clone_b = tmp_path / "clone-b"
    run_git("clone", "-q", str(bare), str(clone_a))
    run_git("clone", "-q", str(bare), str(clone_b))
    linked = tmp_path / "linked"
    git(clone_a, "worktree", "add", "-q", str(linked), "HEAD")
    snapshot_root = tmp_path / "repository"
    identity_a = bind_current(clone_a, snapshot_root, "project:clone-a")
    identity_linked = bind_current(linked, snapshot_root, "project:linked")
    identity_b = repository_identity(clone_b)
    assert (
        identity_a["logical_repository_identity"]
        == identity_linked["logical_repository_identity"]
        == identity_b["logical_repository_identity"]
    )
    assert len({identity_a["repository_id"], identity_linked["repository_id"], identity_b["repository_id"]}) == 3
    assert resolve_project_context(clone_a, snapshot_root=snapshot_root)["namespace"] == "project:clone-a"
    assert resolve_project_context(linked / "pkg", snapshot_root=snapshot_root)["namespace"] == "project:linked"
    unbound = resolve_project_context(clone_b, snapshot_root=snapshot_root)
    assert unbound["status"] == "no_binding"
    assert unbound["namespace"] is None
    assert unbound["repository_identity"]["repository_id"] == identity_b["repository_id"]


def test_posix_and_windows_path_spellings_follow_repository_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path)
    snapshot_root = tmp_path / "repository"
    identity = bind_current(repo, snapshot_root, "project:custom-name")
    monkeypatch.chdir(repo.parent)
    spellings = [
        str(repo),
        str(repo) + os.sep,
        str(repo / "."),
        os.path.join(str(repo.parent), repo.name),
        repo.name,
        str(Path(repo.name) / "pkg"),
    ]
    if os.name == "nt" and repo.drive:
        rest = str(repo)[len(repo.drive) :]
        spellings.extend([repo.as_posix(), repo.drive.lower() + rest, repo.drive.upper() + rest])
    resolved_ids = {
        resolve_project_context(spelling, snapshot_root=snapshot_root)["repository_identity"]["repository_id"]
        for spelling in spellings
    }
    assert resolved_ids == {identity["repository_id"]}
    link = tmp_path / "linked-path"
    try:
        link.symlink_to(repo, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are unavailable")
    linked = resolve_project_context(link, snapshot_root=snapshot_root)
    assert linked["namespace"] == "project:custom-name"
    assert linked["repository_identity"]["repository_id"] == identity["repository_id"]

    windows_roots = {
        str(tmp_path / "win-a"): r"C:\worktrees\a",
        str(tmp_path / "win-b"): r"C:\worktrees\b",
    }
    for path in windows_roots:
        Path(path).mkdir()

    def fake_git(root: Path, *args: str) -> str | None:
        key = str(Path(root).resolve())
        if key not in windows_roots:
            return None
        if args == ("rev-parse", "--is-inside-work-tree"):
            return "true"
        if args == ("rev-parse", "--show-toplevel"):
            return windows_roots[key]
        if args == ("config", "--get", "remote.origin.url"):
            return "https://github.com/example/repo.git"
        if args == ("rev-parse", "HEAD"):
            return "abc123"
        return None

    monkeypatch.setattr("agent_mem_bridge.repository_snapshot_store._git", fake_git)
    left = resolve_project_context(tmp_path / "win-a", snapshot_root=snapshot_root)
    right = resolve_project_context(tmp_path / "win-b", snapshot_root=snapshot_root)
    assert (
        left["repository_identity"]["logical_repository_identity"]
        == right["repository_identity"]["logical_repository_identity"]
    )
    assert left["repository_identity"]["repository_id"] != right["repository_identity"]["repository_id"]
    windows_remotes = (
        r"C:\src\repo.git",
        "C:/src/repo.git",
        r"c:\src\repo\\",
        "C:/SRC/repo.git",
    )
    assert {_safe_remote_identity(remote) for remote in windows_remotes} == {"file/c:/src/repo"}
    checkout = tmp_path / "same-checkout"
    checkout.mkdir()
    current_remote = {"value": windows_remotes[0]}

    def fake_remote(root: Path, *args: str) -> str | None:
        if args == ("rev-parse", "--show-toplevel"):
            return str(checkout)
        if args == ("config", "--get", "remote.origin.url"):
            return current_remote["value"]
        return None

    monkeypatch.setattr("agent_mem_bridge.repository_snapshot_store._git", fake_remote)
    repository_ids = []
    for remote in windows_remotes:
        current_remote["value"] = remote
        repository_ids.append(repository_identity(checkout)["repository_id"])
    assert len(set(repository_ids)) == 1


def test_provenance_fields_cannot_override_binding(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = make_repo(tmp_path)
    snapshot_root = tmp_path / "repository"
    identity = repository_identity(repo)
    snapshot_root.mkdir()
    (snapshot_root / "bindings.json").write_text(
        json.dumps(
            {
                "store_schema": "repository.binding.v1",
                "bindings": {
                    "project:real": {
                        "repository_id": identity["repository_id"],
                        "namespace": "project:override",
                        "client_workspace": "project:hint",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_DEFAULT_CLIENT_WORKSPACE", "project:from-env")
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_DEFAULT_SOURCE_CLIENT", "codex")
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_DEFAULT_SOURCE_MODEL", "gpt")
    result = resolve_project_context(
        repo,
        snapshot_root=snapshot_root,
        provenance={
            "client_workspace": "project:from-caller",
            "namespace": "project:from-field",
            "source_client": "codex",
            "source_model": "gpt",
            "tags": ["project:tagged"],
            "prompt_text": "use project:from-prompt",
        },
    )
    encoded = json.dumps(result, sort_keys=True)
    assert result["namespace"] == "project:real"
    assert result["ignored_provenance_keys"] == [
        "client_workspace",
        "namespace",
        "prompt_text",
        "source_client",
        "source_model",
        "tags",
    ]
    for leaked in (
        "project:from-env",
        "project:from-caller",
        "project:from-field",
        "project:override",
        "project:hint",
        "project:tagged",
        "project:from-prompt",
    ):
        assert leaked not in encoded


def test_non_repository_and_missing_git_fail_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot_root = tmp_path / "repository"
    plain = tmp_path / "plain"
    plain.mkdir()
    missing = resolve_project_context(plain, snapshot_root=snapshot_root)
    assert missing["status"] == "not_a_repository"
    assert missing["namespace"] is None
    assert missing["repository_identity"] is None
    assert snapshot_root.exists() is False

    repo = make_repo(tmp_path)
    monkeypatch.setattr("agent_mem_bridge.project_resolution._git_available", lambda: False)
    monkeypatch.setattr("agent_mem_bridge.repository_snapshot_store._git", lambda root, *args: None)
    unavailable = resolve_project_context(repo, snapshot_root=snapshot_root)
    assert unavailable["status"] == "repository_unavailable"
    assert unavailable["namespace"] is None
    assert unavailable["ambiguity"]["reason"] == "git_unavailable"


def test_cli_resolve_is_machine_readable_and_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_repo(tmp_path)
    home = isolate_home(tmp_path, monkeypatch)
    parser = _build_parser()
    parsed = parser.parse_args(["project", "resolve", str(repo)])
    assert parsed.command == "project"
    assert parsed.project_command == "resolve"
    with pytest.raises(SystemExit):
        parser.parse_args(["project", "resolve", "--namespace", "project:nope"])

    assert main(["project", "resolve", str(repo)]) == 1
    unbound = json.loads(capsys.readouterr().out)
    assert unbound["status"] == "no_binding"
    assert namespace_for_host_adapter(unbound) is None
    assert tree_bytes(home) == {}

    bind_current(repo, home / "repository", "project:custom-name")
    assert main(["project", "resolve", str(repo / "pkg")]) == 0
    bound = json.loads(capsys.readouterr().out)
    assert bound["namespace"] == "project:custom-name"
    assert namespace_for_host_adapter(bound) == "project:custom-name"
    assert bound["namespace"] != propose_project_namespace(repo.name)

    RepositorySnapshotStore(home / "repository").bind_namespace(
        "project:other", bound["repository_identity"]["repository_id"]
    )
    assert main(["project", "resolve", str(repo)]) == 1
    ambiguous = json.loads(capsys.readouterr().out)
    assert ambiguous["status"] == "ambiguous_binding"
    assert namespace_for_host_adapter(ambiguous) is None
    assert main(["project"]) == 2
    assert "project resolve" in capsys.readouterr().err


def test_mixed_case_bound_repository_resolves(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, name="Moebius")
    snapshot_root = tmp_path / "repository"
    identity = bind_current(repo, snapshot_root, "project:Moebius")
    result = resolve_project_context(repo, snapshot_root=snapshot_root)
    assert result["status"] == "bound"
    assert result["namespace"] == "project:moebius"
    assert namespace_for_host_adapter(result) == "project:moebius"
    assert result["repository_identity"] == identity
