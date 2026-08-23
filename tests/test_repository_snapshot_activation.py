from __future__ import annotations

import json
import multiprocessing
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from agent_mem_bridge import repository_snapshot_store as snapshot_store_module
from agent_mem_bridge import server
from agent_mem_bridge.cli import main
from agent_mem_bridge.context_manifest import compile_context, render_context
from agent_mem_bridge.evidence_inspect import build_memory_inspect_report, render_memory_inspect_markdown
from agent_mem_bridge.repository_bootstrap import compile_repository_snapshot
from agent_mem_bridge.repository_snapshot_store import RepositorySnapshotStore
from agent_mem_bridge.storage import MemoryStore


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout.strip()


def _bind_worker(repository_root: str, namespace: str, repository_id: str) -> None:
    RepositorySnapshotStore(Path(repository_root)).bind_namespace(namespace, repository_id)


def _bind_or_unbind_worker(repository_root: str, action: str, namespace: str, repository_id: str) -> None:
    store = RepositorySnapshotStore(Path(repository_root))
    if action == "bind":
        store.bind_namespace(namespace, repository_id)
    else:
        store.unbind_namespace(namespace)


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text('[project]\nname = "demo"\nrequires-python = ">=3.11"\n', encoding="utf-8")
    (repo / "README.md").write_text("Run pytest for the project tests.\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "initial"], check=True)
    return repo


def test_snapshot_persists_and_reloads_with_stable_identity(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    store = RepositorySnapshotStore(tmp_path / "amb-home" / "repository")
    snapshot = compile_repository_snapshot(repo)
    saved = store.save_snapshot(snapshot)
    loaded = store.load_snapshot(saved["repository_id"])
    assert loaded is not None
    assert loaded["repository_id"] == saved["repository_id"]
    assert Path(loaded["snapshot_path"]).name == "current.json"
    assert (
        json.loads(Path(loaded["snapshot_path"]).read_text(encoding="utf-8"))["store_schema"]
        == "repository.snapshot.v1"
    )


@pytest.mark.parametrize(
    "remote",
    [
        "https://user:secret-token@example.com/org/repo.git",
        "ssh://user:secret-token@example.com/org/repo.git",
        "git@example.com:org/repo.git",
    ],
)
def test_remote_identity_redacts_credentials_from_persisted_snapshot(tmp_path: Path, monkeypatch, remote: str) -> None:
    repo = _make_repo(tmp_path)
    original_git = snapshot_store_module._git

    def fake_git(root: Path, *args: str) -> str | None:
        if args == ("rev-parse", "--show-toplevel"):
            return str(repo)
        if args == ("config", "--get", "remote.origin.url"):
            return remote
        return original_git(root, *args)

    monkeypatch.setattr(snapshot_store_module, "_git", fake_git)
    store = RepositorySnapshotStore(tmp_path / "repository")
    saved = store.save_snapshot(compile_repository_snapshot(repo))
    raw = Path(saved["snapshot_path"]).read_bytes()
    assert b"secret-token" not in raw
    identity = snapshot_store_module.repository_identity(repo)
    assert "secret-token" not in json.dumps(identity, sort_keys=True)
    assert "example.com/org/repo" in identity["identity_basis"]


def test_multiple_clones_have_distinct_local_source_ids(tmp_path: Path) -> None:
    origin = _make_repo(tmp_path)
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "clone", "--bare", str(origin), str(bare)], check=True, capture_output=True)
    clone_a = tmp_path / "clone-a"
    clone_b = tmp_path / "clone-b"
    subprocess.run(["git", "clone", "-q", str(bare), str(clone_a)], check=True)
    subprocess.run(["git", "clone", "-q", str(bare), str(clone_b)], check=True)
    for clone, marker in ((clone_a, "A"), (clone_b, "B")):
        (clone / "README.md").write_text(f"Clone {marker}.", encoding="utf-8")
        subprocess.run(["git", "-C", str(clone), "add", "README.md"], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(clone),
                "-c",
                "user.email=test@example.com",
                "-c",
                "user.name=Test",
                "commit",
                "-qm",
                f"clone {marker}",
            ],
            check=True,
        )
    store = RepositorySnapshotStore(tmp_path / "repository")
    saved_a = store.save_snapshot(compile_repository_snapshot(clone_a))
    saved_b = store.save_snapshot(compile_repository_snapshot(clone_b))
    assert saved_a["repository_id"] != saved_b["repository_id"]
    assert saved_a["logical_repository_identity"] == saved_b["logical_repository_identity"]
    store.bind_namespace("project:a", saved_a["repository_id"])
    store.bind_namespace("project:b", saved_b["repository_id"])
    loaded_a = store.load_bound_snapshot("project:a")
    loaded_b = store.load_bound_snapshot("project:b")
    assert loaded_a["root"] == str(clone_a.resolve())
    assert loaded_b["root"] == str(clone_b.resolve())
    assert loaded_a["commit"] != loaded_b["commit"]
    assert loaded_a["current_commit"] == loaded_a["commit"]
    assert loaded_b["current_commit"] == loaded_b["commit"]


def test_selection_ignores_incidental_absolute_path_metadata() -> None:
    snapshot = {
        "facts": [
            {"key": "repository_root", "value": "/tmp/pytest-of-runner/repo", "source": "."},
            {"key": "commit_sha", "value": "abc123", "source": ".git/HEAD"},
            {"key": "python_requires", "value": ">=3.11", "source": "pyproject.toml"},
        ]
    }
    selected, _ = snapshot_store_module.select_repository_facts(snapshot, "Python 3.11, please.")
    assert [(fact["key"], fact["value"], fact["source"]) for fact in selected] == [
        ("python_requires", ">=3.11", "pyproject.toml")
    ]


def test_binding_updates_from_different_processes_are_not_lost(tmp_path: Path) -> None:
    store = RepositorySnapshotStore(tmp_path / "repository")
    root = str(store.root)
    context = multiprocessing.get_context("spawn")
    processes = [
        context.Process(target=_bind_worker, args=(root, "project:a", "repo-a")),
        context.Process(target=_bind_worker, args=(root, "project:b", "repo-b")),
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0
    bindings = store.bindings()["bindings"]
    assert bindings["project:a"]["repository_id"] == "repo-a"
    assert bindings["project:b"]["repository_id"] == "repo-b"


def test_concurrent_bind_unbind_preserves_unrelated_binding(tmp_path: Path) -> None:
    store = RepositorySnapshotStore(tmp_path / "repository")
    store.bind_namespace("project:keep", "repo-keep")
    store.bind_namespace("project:remove", "repo-remove")
    root = str(store.root)
    context = multiprocessing.get_context("spawn")
    processes = [
        context.Process(target=_bind_or_unbind_worker, args=(root, "bind", "project:add", "repo-add")),
        context.Process(target=_bind_or_unbind_worker, args=(root, "unbind", "project:remove", "repo-remove")),
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0
    bindings = store.bindings()["bindings"]
    assert bindings["project:keep"]["repository_id"] == "repo-keep"
    assert bindings["project:add"]["repository_id"] == "repo-add"
    assert "project:remove" not in bindings


def test_binding_conflict_requires_explicit_rebind(tmp_path: Path) -> None:
    store = RepositorySnapshotStore(tmp_path / "repository")
    assert store.bind_namespace("project:demo", "repo-a")["rebound"] is False
    assert store.bind_namespace("project:demo", "repo-a")["rebound"] is False
    with pytest.raises(ValueError, match="explicit rebind"):
        store.bind_namespace("project:demo", "repo-b")
    assert store.bind_namespace("project:demo", "repo-b", allow_rebind=True)["rebound"] is True


def test_stale_snapshot_is_ineligible_after_head_change(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    store = RepositorySnapshotStore(tmp_path / "repository")
    saved = store.save_snapshot(compile_repository_snapshot(repo))
    store.bind_namespace("project:demo", saved["repository_id"])
    (repo / "README.md").write_text("Changed after snapshot.\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "second"], check=True)
    current = store.load_bound_snapshot("project:demo")
    assert current is not None
    assert current["binding_state"] == "stale"
    assert current["stale_reason"] == "head_changed"


def test_cache_delete_preserves_memory_and_rebuilds(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    repository_root = tmp_path / "repository"
    store = RepositorySnapshotStore(repository_root)
    saved = store.save_snapshot(compile_repository_snapshot(repo))
    store.bind_namespace("project:demo", saved["repository_id"])
    db = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    db.store(namespace="project:demo", kind="memory", title="Why", content="Keep the project local-first.")
    snapshot_path = Path(saved["snapshot_path"])
    snapshot_path.unlink()
    assert any(
        item["content"] == "Keep the project local-first."
        for item in db.recall("project:demo", query="local-first")["items"]
    )
    rebuilt = store.save_snapshot(compile_repository_snapshot(repo))
    assert rebuilt["repository_id"] == saved["repository_id"]
    assert store.load_bound_snapshot("project:demo")["binding_state"] == "current"


def test_atomic_snapshot_replacement_has_no_partial_json_for_readers(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    store = RepositorySnapshotStore(tmp_path / "repository")
    snapshot = compile_repository_snapshot(repo)
    saved = store.save_snapshot(snapshot)

    def read_snapshot() -> bool:
        loaded = store.load_snapshot(saved["repository_id"])
        return loaded is not None and loaded["repository_id"] == saved["repository_id"]

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = []
        for _ in range(20):
            futures.append(executor.submit(store.save_snapshot, snapshot))
            futures.extend(executor.submit(read_snapshot) for _ in range(8))
        results = [future.result() for future in futures]
    assert all(result is True or isinstance(result, dict) for result in results)
    assert store.load_snapshot(saved["repository_id"]) is not None


def test_cli_bootstrap_binds_and_inspect_reads_repository_what(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _make_repo(tmp_path)
    bridge_home = tmp_path / "amb-home"
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_HOME", str(bridge_home))
    assert main(["bootstrap-repo", str(repo), "--namespace", "project:demo", "--format", "json"]) == 0
    bootstrap_output = json.loads(capsys.readouterr().out)
    assert bootstrap_output["binding"] == "git_commit"
    assert bootstrap_output["binding_action"]["namespace"] == "project:demo"
    assert main(["inspect", "--namespace", "project:demo", "--query", "python 3.11", "--format", "json"]) == 0
    inspect_output = json.loads(capsys.readouterr().out)
    assert inspect_output["repository_knowledge"]["selected"][0]["fact_kind"] == "python_requires"
    assert inspect_output["repository_knowledge"]["selected"][0]["value"] == ">=3.11"
    assert inspect_output["repository_knowledge"]["selected"][0]["source"] == "pyproject.toml"
    assert inspect_output["repository_knowledge"]["snapshot"]["binding_state"] == "current"
    assert not any(item["id"] == bootstrap_output["repository_id"] for item in inspect_output["selected"])


def test_mcp_recall_exposes_repository_sidecar_without_durable_contamination(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    repo = _make_repo(tmp_path)
    bridge_home = tmp_path / "amb-home"
    durable_db = tmp_path / "bridge.db"
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_HOME", str(bridge_home))
    durable_store = MemoryStore(durable_db, log_dir=tmp_path / "logs")
    monkeypatch.setattr(server, "bridge", durable_store)
    assert main(["bootstrap-repo", str(repo), "--namespace", "project:p5-proof", "--format", "json"]) == 0
    capsys.readouterr()

    first = server.recall("project:p5-proof", "Python 3.11 local-first.", kind="memory", limit=5)
    assert first["items"] == []
    assert first["repository_knowledge"]["selected"] == [
        {
            "authority": "derived_repository",
            "commit": first["repository_knowledge"]["commit"],
            "key": "python_requires",
            "source": "pyproject.toml",
            "value": ">=3.11",
        }
    ]
    serialized = json.dumps(first, sort_keys=True)
    assert "excluded" not in first["repository_knowledge"]
    assert first["repository_knowledge"]["excluded_count"] >= 1
    assert str(repo) not in serialized
    assert "source_digest" not in serialized
    durable = durable_store.store(
        namespace="project:p5-proof",
        title="No Redis",
        content=(
            "Do not introduce Redis because this project intentionally remains local-first and single-node. "
            "Python 3.11 projects remain local-first."
        ),
    )
    second = server.recall("project:p5-proof", "Python 3.11 local-first.", kind="memory", limit=5)
    assert [item["id"] for item in second["items"]] == [durable["id"]]
    assert second["repository_knowledge"]["selected"] == first["repository_knowledge"]["selected"]
    assert second["recall_receipt"]["result_count"] == len(second["items"])


def test_unbind_cli_is_reversible_and_memory_preserving(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _make_repo(tmp_path)
    bridge_home = tmp_path / "amb-home"
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_HOME", str(bridge_home))
    assert main(["bootstrap-repo", str(repo), "--namespace", "project:demo", "--format", "json"]) == 0
    capsys.readouterr()
    assert main(["unbind-repo", "--namespace", "project:demo", "--format", "json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result == {"memory_unchanged": True, "namespace": "project:demo", "unbound": True}
    assert RepositorySnapshotStore(bridge_home / "repository").load_bound_snapshot("project:demo") is None


def test_context_compiler_accepts_repository_what_as_derived_input() -> None:
    empty_report = {
        "assembly_mode": "relation-aware",
        "query": "pytest",
        "project_namespace": "project:demo",
        "global_namespace": "global",
        "summary": "",
        "procedure_hits": [],
        "decision_hits": [],
        "constraint_hits": [],
        "concept_hits": [],
        "belief_hits": [],
        "domain_hits": [],
        "supporting_hits": [],
        "corrective_items": [],
        "suppressed_items": [],
    }
    repository_items = [
        {"fact_kind": "test_command", "value": "pytest", "source": "pyproject.toml", "commit": "abc123"}
    ]
    manifest = compile_context(task_memory=empty_report, repository_items=repository_items, budget_tokens=100)
    assert manifest.items[0].source == "derived_repository"
    assert manifest.items[0].provenance[0] == ("authority", "derived_repository")
    assert manifest.used_tokens <= 100
    assert "[Repository WHAT]" in render_context(manifest)
    changed = compile_context(
        task_memory=empty_report,
        repository_items=[{**repository_items[0], "value": "python -m pytest"}],
        budget_tokens=100,
    )
    assert changed.input_fingerprint != manifest.input_fingerprint


def test_inspect_keeps_repository_what_separate_from_durable_why(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    repository_root = tmp_path / "repository"
    store = RepositorySnapshotStore(repository_root)
    saved = store.save_snapshot(compile_repository_snapshot(repo))
    store.bind_namespace("project:demo", saved["repository_id"])
    db = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    db.store(
        namespace="global",
        kind="memory",
        tags=["kind:concept-note"],
        title="Why",
        content="Use Python 3.11 locally; keep the project local-first.",
    )
    snapshot = store.load_bound_snapshot("project:demo")
    report = build_memory_inspect_report(
        db,
        namespace="project:demo",
        query="Python 3.11, please.",
        repository_snapshot=snapshot,
    )
    assert report["repository_knowledge"]["snapshot"]["authority"] == "derived_repository"
    assert report["repository_knowledge"]["selected"]
    assert report["selected"] or report["excluded"]
    rendered = render_memory_inspect_markdown(report)
    assert "Repository knowledge (WHAT)" in rendered
    assert "Durable project memory (WHY)" in rendered
    assert "authority: `derived_repository`" in rendered
    assert "local-first" not in rendered or "Durable project memory" in rendered
