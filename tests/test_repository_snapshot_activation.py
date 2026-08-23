from __future__ import annotations

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from agent_mem_bridge.cli import main
from agent_mem_bridge.context_manifest import compile_context, render_context
from agent_mem_bridge.evidence_inspect import build_memory_inspect_report, render_memory_inspect_markdown
from agent_mem_bridge.repository_bootstrap import compile_repository_snapshot
from agent_mem_bridge.repository_snapshot_store import RepositorySnapshotStore
from agent_mem_bridge.storage import MemoryStore


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout.strip()


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
    assert loaded["snapshot_path"].endswith("/current.json")
    assert (
        json.loads(Path(loaded["snapshot_path"]).read_text(encoding="utf-8"))["store_schema"]
        == "repository.snapshot.v1"
    )


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
    assert main(["inspect", "--namespace", "project:demo", "--query", "pytest", "--format", "json"]) == 0
    inspect_output = json.loads(capsys.readouterr().out)
    assert inspect_output["repository_knowledge"]["selected"]
    assert inspect_output["repository_knowledge"]["snapshot"]["binding_state"] == "current"
    assert not any(item["id"] == bootstrap_output["repository_id"] for item in inspect_output["selected"])


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
        content="Use pytest locally; keep the project local-first.",
    )
    snapshot = store.load_bound_snapshot("project:demo")
    report = build_memory_inspect_report(
        db,
        namespace="project:demo",
        query="pytest",
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
