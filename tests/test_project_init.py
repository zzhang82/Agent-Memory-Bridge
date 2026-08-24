from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from agent_mem_bridge.cli import _build_parser, main
from agent_mem_bridge.knowledge_explorer import (
    EMPTY_WHY_GUIDANCE,
    build_explorer_projection,
    render_explorer_human_markdown,
    render_explorer_technical_markdown,
)
from agent_mem_bridge.project_init import apply_project_init, plan_project_init, propose_project_namespace
from agent_mem_bridge.repository_snapshot_store import RepositorySnapshotStore
from agent_mem_bridge.storage import MemoryStore

ROOT = Path(__file__).resolve().parents[1]


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def make_repo(tmp_path: Path, name: str = "Agent-Memory-Bridge") -> Path:
    repo = tmp_path / name
    repo.mkdir()
    (repo / "README.md").write_text("Fixture project\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text('[project]\nname="fixture"\nrequires-python=">=3.11"\n', encoding="utf-8")
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "fixture@example.invalid")
    git(repo, "config", "user.name", "Fixture")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "initial")
    return repo


def isolate_home(tmp_path: Path, monkeypatch) -> Path:
    home = tmp_path / "amb-home"
    db_path = home / "bridge.db"
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_HOME", str(home))
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_DB_PATH", str(db_path))
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_LOG_DIR", str(home / "logs"))
    return home


def snapshot_bytes(home: Path) -> dict[str, bytes]:
    payload: dict[str, bytes] = {}
    for path in sorted(home.rglob("*")):
        if path.is_file():
            payload[str(path.relative_to(home))] = path.read_bytes()
    return payload


def git_sha(repo: Path) -> str:
    return subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()


def write_bindings_json(home: Path, bindings: dict[str, object] | None = None) -> Path:
    store_root = home / "repository"
    store_root.mkdir(parents=True, exist_ok=True)
    path = store_root / "bindings.json"
    payload = {"store_schema": "repository.binding.v1", "bindings": bindings or {}}
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    return path


def test_cli_nested_project_init_parses() -> None:
    parser = _build_parser()
    parsed = parser.parse_args(["project", "init", ".", "--namespace", "project:amb", "--yes"])
    assert parsed.command == "project"
    assert parsed.project_command == "init"
    assert parsed.namespace == "project:amb"
    assert parsed.yes is True


def test_suggests_normalized_namespace_from_clean_repo(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = make_repo(tmp_path)
    isolate_home(tmp_path, monkeypatch)
    assert main(["project", "init", str(repo), "--dry-run"]) == 0
    output = capsys.readouterr().out
    assert "Project detected: Agent-Memory-Bridge" in output
    assert "Suggested namespace: project:agent-memory-bridge" in output
    assert "Chosen namespace: project:agent-memory-bridge" in output
    assert propose_project_namespace("Agent-Memory-Bridge") == "project:agent-memory-bridge"


def test_confirmation_no_writes_nothing(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = make_repo(tmp_path)
    home = isolate_home(tmp_path, monkeypatch)
    monkeypatch.setattr("agent_mem_bridge.cli._confirm_setup_mutation", lambda prompt: False)
    before = snapshot_bytes(home)
    assert main(["project", "init", str(repo)]) == 0
    output = capsys.readouterr().out
    assert "Use this namespace?" not in output
    assert "No changes were made." in output
    assert snapshot_bytes(home) == before


def test_confirmation_eof_writes_nothing(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = make_repo(tmp_path)
    home = isolate_home(tmp_path, monkeypatch)
    monkeypatch.setattr("builtins.input", lambda prompt: (_ for _ in ()).throw(EOFError()))
    before = snapshot_bytes(home)
    assert main(["project", "init", str(repo)]) == 0
    assert "No changes were made." in capsys.readouterr().out
    assert snapshot_bytes(home) == before


def test_yes_bootstraps_and_renders_human_explore(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = make_repo(tmp_path)
    home = isolate_home(tmp_path, monkeypatch)
    assert main(["project", "init", str(repo), "--yes"]) == 0
    output = capsys.readouterr().out
    assert "Initialized project: Agent-Memory-Bridge" in output
    assert "Namespace: project:agent-memory-bridge" in output
    assert "CODE / WHAT" in output
    assert "CONVERSATION / WHY" in output
    assert "Tell your connected coding agent:" in output
    assert EMPTY_WHY_GUIDANCE.splitlines()[0] in output
    store = RepositorySnapshotStore(home / "repository")
    assert "project:agent-memory-bridge" in store.bindings()["bindings"]
    durable = MemoryStore(home / "bridge.db", log_dir=home / "logs")
    stats = durable.stats("project:agent-memory-bridge")
    assert stats["total_count"] == 0


def test_explicit_namespace_is_used_not_rewritten(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = make_repo(tmp_path)
    isolate_home(tmp_path, monkeypatch)
    assert main(["project", "init", str(repo), "--namespace", "project:amb", "--yes"]) == 0
    output = capsys.readouterr().out
    assert "Suggested namespace: project:agent-memory-bridge" in output
    assert "Chosen namespace: project:amb" in output
    assert "Namespace: project:amb" in output


def test_invalid_namespace_writes_nothing(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = make_repo(tmp_path)
    home = isolate_home(tmp_path, monkeypatch)
    before = snapshot_bytes(home)
    assert main(["project", "init", str(repo), "--namespace", "not a namespace", "--yes"]) == 2
    err = capsys.readouterr().err
    assert "namespace must be" in err
    assert snapshot_bytes(home) == before


def test_dirty_repo_fails_closed(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = make_repo(tmp_path)
    home = isolate_home(tmp_path, monkeypatch)
    (repo / "README.md").write_text("dirty\n", encoding="utf-8")
    before = snapshot_bytes(home)
    assert main(["project", "init", str(repo), "--yes"]) == 1
    output = capsys.readouterr().out
    assert "worktree is dirty" in output
    assert snapshot_bytes(home) == before


def test_non_git_path_fails_closed(tmp_path: Path, monkeypatch, capsys) -> None:
    isolate_home(tmp_path, monkeypatch)
    plain = tmp_path / "plain"
    plain.mkdir()
    assert main(["project", "init", str(plain), "--yes"]) == 1
    assert "not a Git repository" in capsys.readouterr().out


def test_existing_why_remains_and_is_not_rewritten(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = make_repo(tmp_path)
    home = isolate_home(tmp_path, monkeypatch)
    durable = MemoryStore(home / "bridge.db", log_dir=home / "logs")
    stored = durable.store(
        namespace="project:agent-memory-bridge",
        title="Local first",
        content="record_type: decision\nclaim: Do not introduce Redis\nreason: local-first deployment",
        kind="memory",
    )
    assert main(["project", "init", str(repo), "--yes"]) == 0
    output = capsys.readouterr().out
    assert "Do not introduce Redis" in output
    assert "Tell your connected coding agent:" not in output
    after = durable.stats("project:agent-memory-bridge")
    assert after["total_count"] == 1
    recalled = durable.browse("project:agent-memory-bridge", limit=5)
    assert recalled["items"][0]["id"] == stored["id"]
    assert recalled["items"][0]["kind"] == "memory"


def test_repeat_init_is_idempotent(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = make_repo(tmp_path)
    home = isolate_home(tmp_path, monkeypatch)
    assert main(["project", "init", str(repo), "--yes"]) == 0
    capsys.readouterr()
    store = RepositorySnapshotStore(home / "repository")
    first_bindings = json.dumps(store.bindings(), sort_keys=True)
    first_snapshots = sorted(path.relative_to(home).as_posix() for path in (home / "repository").rglob("current.json"))
    durable = MemoryStore(home / "bridge.db", log_dir=home / "logs")
    first_count = durable.stats("project:agent-memory-bridge")["total_count"]
    assert main(["project", "init", str(repo), "--yes"]) == 0
    capsys.readouterr()
    assert json.dumps(store.bindings(), sort_keys=True) == first_bindings
    second_snapshots = sorted(path.relative_to(home).as_posix() for path in (home / "repository").rglob("current.json"))
    assert second_snapshots == first_snapshots
    assert durable.stats("project:agent-memory-bridge")["total_count"] == first_count


def test_namespace_collision_does_not_rebind(tmp_path: Path, monkeypatch, capsys) -> None:
    first = make_repo(tmp_path, "first-app")
    second = make_repo(tmp_path, "second-app")
    home = isolate_home(tmp_path, monkeypatch)
    assert main(["project", "init", str(first), "--namespace", "project:shared", "--yes"]) == 0
    capsys.readouterr()
    store = RepositorySnapshotStore(home / "repository")
    before = json.dumps(store.bindings(), sort_keys=True)
    assert main(["project", "init", str(second), "--namespace", "project:shared", "--yes"]) == 1
    output = capsys.readouterr().out
    assert "already bound to a different repository" in output
    assert json.dumps(store.bindings(), sort_keys=True) == before


def test_existing_repo_binding_blocks_second_namespace(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = make_repo(tmp_path)
    home = isolate_home(tmp_path, monkeypatch)
    assert main(["project", "init", str(repo), "--namespace", "project:one", "--yes"]) == 0
    capsys.readouterr()
    store = RepositorySnapshotStore(home / "repository")
    before = json.dumps(store.bindings(), sort_keys=True)
    assert main(["project", "init", str(repo), "--namespace", "project:two", "--yes"]) == 1
    output = capsys.readouterr().out
    assert "already bound as `project:one`" in output
    assert json.dumps(store.bindings(), sort_keys=True) == before


def test_confirmed_stale_refresh(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = make_repo(tmp_path)
    home = isolate_home(tmp_path, monkeypatch)
    assert main(["project", "init", str(repo), "--yes"]) == 0
    capsys.readouterr()
    (repo / "README.md").write_text("changed\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-qm", "changed")
    assert main(["project", "init", str(repo), "--yes"]) == 0
    output = capsys.readouterr().out
    assert "CODE / WHAT" in output
    store = RepositorySnapshotStore(home / "repository")
    bound = store.load_bound_snapshot("project:agent-memory-bridge")
    assert bound is not None
    assert bound.get("binding_state") == "current"


def test_no_durable_memory_or_learning_candidates(tmp_path: Path, monkeypatch) -> None:
    repo = make_repo(tmp_path)
    home = isolate_home(tmp_path, monkeypatch)
    assert main(["project", "init", str(repo), "--yes"]) == 0
    db_path = home / "bridge.db"
    if db_path.exists():
        connection = sqlite3.connect(db_path)
        try:
            memories = connection.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            candidates = connection.execute("SELECT COUNT(*) FROM memories WHERE is_learning_candidate = 1").fetchone()[
                0
            ]
            feedback = connection.execute("SELECT COUNT(*) FROM retrieval_feedback").fetchone()[0]
        except sqlite3.OperationalError:
            memories = candidates = feedback = 0
        finally:
            connection.close()
        assert memories == 0
        assert candidates == 0
        assert feedback == 0


def test_explorer_json_contract_unchanged_after_init(tmp_path: Path, monkeypatch) -> None:
    from tests.test_knowledge_explorer import FakeMemoryStore, durable_items

    items = durable_items()
    projection = build_explorer_projection(
        namespace="project:fixture",
        snapshot_root=Path("/tmp/no-snapshot-explorer-golden"),
        memory_store=FakeMemoryStore(items),
    )
    expected = json.loads((ROOT / "tests/fixtures/explorer_projection_unbound.json").read_text(encoding="utf-8"))
    assert json.dumps(projection, sort_keys=True) == json.dumps(expected, sort_keys=True)
    expected_md = (ROOT / "tests/fixtures/explorer_technical_unbound.md").read_text(encoding="utf-8")
    assert render_explorer_technical_markdown(projection) == expected_md
    repo = make_repo(tmp_path)
    isolate_home(tmp_path, monkeypatch)
    assert main(["project", "init", str(repo), "--yes"]) == 0
    after = build_explorer_projection(
        namespace="project:fixture",
        snapshot_root=Path("/tmp/no-snapshot-explorer-golden"),
        memory_store=FakeMemoryStore(items),
    )
    assert json.dumps(after, sort_keys=True) == json.dumps(expected, sort_keys=True)
    human = render_explorer_human_markdown(
        __import__("agent_mem_bridge.knowledge_explorer", fromlist=["_build_explorer"])._build_explorer(
            namespace="project:fixture",
            snapshot_root=Path("/tmp/no-snapshot-explorer-golden"),
            memory_store=FakeMemoryStore(items),
        )
    )
    assert "CODE / WHAT" in human


def test_apply_aborts_when_worktree_becomes_dirty_after_plan(tmp_path: Path, monkeypatch) -> None:
    repo = make_repo(tmp_path)
    home = isolate_home(tmp_path, monkeypatch)
    snapshot_root = home / "repository"
    plan = plan_project_init(repo, namespace=None, snapshot_root=snapshot_root)
    assert plan.blocking_error is None
    (repo / "README.md").write_text("dirty after plan\n", encoding="utf-8")
    before = snapshot_bytes(home)
    with pytest.raises(ValueError, match="worktree is dirty"):
        apply_project_init(plan, snapshot_root=snapshot_root)
    assert snapshot_bytes(home) == before
    assert not (snapshot_root / "bindings.json").exists()
    assert not (snapshot_root / "bindings.lock").exists()
    assert not list(snapshot_root.rglob("current.json"))


def test_apply_saves_fresh_head_after_clean_commit(tmp_path: Path, monkeypatch) -> None:
    repo = make_repo(tmp_path)
    home = isolate_home(tmp_path, monkeypatch)
    snapshot_root = home / "repository"
    plan = plan_project_init(repo, namespace=None, snapshot_root=snapshot_root)
    head_a = str(plan.snapshot.get("commit") or "")
    assert head_a == git_sha(repo)
    (repo / "README.md").write_text("later HEAD\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-qm", "later")
    head_b = git_sha(repo)
    assert head_b != head_a
    result = apply_project_init(plan, snapshot_root=snapshot_root)
    assert result["snapshot"]["commit"] == head_b
    store = RepositorySnapshotStore(snapshot_root)
    bound = store.load_bound_snapshot("project:agent-memory-bridge")
    assert bound is not None
    assert bound.get("commit") == head_b
    persisted = json.loads(Path(str(bound["snapshot_path"])).read_text(encoding="utf-8"))
    assert persisted["snapshot"]["commit"] == head_b
    assert persisted["snapshot"]["commit"] != head_a


def test_apply_aborts_when_repository_identity_changes(tmp_path: Path, monkeypatch) -> None:
    repo = make_repo(tmp_path)
    home = isolate_home(tmp_path, monkeypatch)
    snapshot_root = home / "repository"
    plan = plan_project_init(repo, namespace=None, snapshot_root=snapshot_root)
    git(repo, "remote", "add", "origin", "https://example.com/other/repo.git")
    before = snapshot_bytes(home)
    with pytest.raises(ValueError, match="identity changed after confirmation"):
        apply_project_init(plan, snapshot_root=snapshot_root)
    assert snapshot_bytes(home) == before
    assert not (snapshot_root / "bindings.json").exists()
    assert not list(snapshot_root.rglob("current.json"))


def test_apply_aborts_when_binding_conflict_appears_after_plan(tmp_path: Path, monkeypatch) -> None:
    first = make_repo(tmp_path, "first-app")
    second = make_repo(tmp_path, "second-app")
    home = isolate_home(tmp_path, monkeypatch)
    snapshot_root = home / "repository"
    plan = plan_project_init(second, namespace="project:shared", snapshot_root=snapshot_root)
    assert plan.blocking_error is None
    assert main(["project", "init", str(first), "--namespace", "project:shared", "--yes"]) == 0
    store = RepositorySnapshotStore(snapshot_root)
    before_bindings = json.dumps(store.peek_bindings(), sort_keys=True)
    before_snapshots = {
        path.as_posix(): path.read_bytes() for path in sorted(snapshot_root.rglob("current.json")) if path.is_file()
    }
    with pytest.raises(ValueError, match="already bound to a different repository"):
        apply_project_init(plan, snapshot_root=snapshot_root)
    assert json.dumps(store.peek_bindings(), sort_keys=True) == before_bindings
    after_snapshots = {
        path.as_posix(): path.read_bytes() for path in sorted(snapshot_root.rglob("current.json")) if path.is_file()
    }
    assert after_snapshots == before_snapshots


def test_existing_bindings_without_lock_stay_unmutated(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = make_repo(tmp_path)
    home = isolate_home(tmp_path, monkeypatch)
    bindings_path = write_bindings_json(home, {"project:other": {"repository_id": "fixture-id"}})
    store_root = home / "repository"
    before = snapshot_bytes(home)
    assert main(["project", "init", str(repo), "--dry-run"]) == 0
    capsys.readouterr()
    assert snapshot_bytes(home) == before
    assert not (store_root / "bindings.lock").exists()
    monkeypatch.setattr("agent_mem_bridge.cli._confirm_setup_mutation", lambda prompt: False)
    assert main(["project", "init", str(repo)]) == 0
    assert "No changes were made." in capsys.readouterr().out
    assert snapshot_bytes(home) == before
    assert bindings_path.read_bytes() == before[str(bindings_path.relative_to(home))]
    assert not (store_root / "bindings.lock").exists()
    assert list(store_root.rglob("*.tmp")) == []


def test_existing_lock_is_not_rewritten_during_planning(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = make_repo(tmp_path)
    home = isolate_home(tmp_path, monkeypatch)
    bindings_path = write_bindings_json(home)
    lock_path = home / "repository" / "bindings.lock"
    lock_path.write_bytes(b"keep-me")
    lock_stat = lock_path.stat()
    bindings_bytes = bindings_path.read_bytes()
    assert main(["project", "init", str(repo), "--dry-run"]) == 0
    capsys.readouterr()
    assert lock_path.read_bytes() == b"keep-me"
    assert lock_path.stat().st_size == lock_stat.st_size
    assert lock_path.stat().st_mtime_ns == lock_stat.st_mtime_ns
    assert bindings_path.read_bytes() == bindings_bytes
    monkeypatch.setattr("agent_mem_bridge.cli._confirm_setup_mutation", lambda prompt: False)
    assert main(["project", "init", str(repo)]) == 0
    assert lock_path.read_bytes() == b"keep-me"
    assert lock_path.stat().st_mtime_ns == lock_stat.st_mtime_ns
    assert bindings_path.read_bytes() == bindings_bytes
