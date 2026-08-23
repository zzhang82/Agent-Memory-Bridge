from __future__ import annotations

import json
import subprocess
from pathlib import Path

from agent_mem_bridge.knowledge_explorer import build_explorer_projection, render_explorer_markdown
from agent_mem_bridge.repository_bootstrap import compile_repository_snapshot
from agent_mem_bridge.repository_snapshot_store import RepositorySnapshotStore


class FakeMemoryStore:
    def __init__(self, items: list[dict[str, object]]) -> None:
        self.items = items
        self.browse_calls: list[tuple[str, int]] = []

    def browse(self, namespace: str, *, limit: int) -> dict[str, object]:
        self.browse_calls.append((namespace, limit))
        return {"items": self.items}


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def fixture_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "fixture"
    repo.mkdir()
    (repo / "README.md").write_text("Fixture project\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text('[project]\nname="fixture"\nrequires-python=">=3.11"\n', encoding="utf-8")
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "fixture@example.invalid")
    git(repo, "config", "user.name", "Fixture")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "initial")
    return repo


def bind_fixture(repo: Path, tmp_path: Path, namespace: str = "project:fixture") -> Path:
    store = RepositorySnapshotStore(tmp_path / "snapshots")
    snapshot = compile_repository_snapshot(repo)
    assert snapshot["binding"] == "git_commit"
    saved = store.save_snapshot(snapshot)
    store.bind_namespace(namespace, str(saved["repository_id"]))
    return tmp_path / "snapshots"


def durable_items() -> list[dict[str, object]]:
    return [
        {
            "id": "memory-decision-1",
            "kind": "memory",
            "title": "Local first",
            "content": "record_type: decision\nDo not introduce Redis\nreason: local-first deployment",
        },
        {
            "id": "memory-constraint-1",
            "kind": "memory",
            "title": "Python support",
            "content": "record_type: constraint\nsupports: memory-decision-1",
        },
        {"id": "ordinary-1", "kind": "memory", "title": "ordinary", "content": "not projected"},
    ]


def test_projection_is_deterministic_and_authority_explicit(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    snapshot_root = bind_fixture(repo, tmp_path)
    fake = FakeMemoryStore(durable_items())
    first = build_explorer_projection(namespace="project:fixture", snapshot_root=snapshot_root, memory_store=fake)
    second = build_explorer_projection(
        namespace="project:fixture", snapshot_root=snapshot_root, memory_store=FakeMemoryStore(durable_items())
    )
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert first["read_only"] is True
    assert first["rebuildable"] is True
    assert {node["authority"] for node in first["nodes"]} == {"derived_repository", "governed_durable_memory"}
    assert any(edge["relation"] == "has_decision" for edge in first["edges"])
    assert any(edge["relation"] == "supports" for edge in first["edges"])
    assert all("source_ref" in node for node in first["nodes"])
    assert all("evidence" in edge for edge in first["edges"])
    assert "ordinary-1" not in json.dumps(first)


def test_rendering_explains_projection_and_provenance(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    projection = build_explorer_projection(
        namespace="project:fixture",
        snapshot_root=bind_fixture(repo, tmp_path),
        memory_store=FakeMemoryStore(durable_items()),
    )
    rendered = render_explorer_markdown(projection)
    assert "read-only, rebuildable projection" in rendered
    assert "governed_durable_memory" in rendered
    assert "derived_repository" in rendered
    assert "memory-decision-1" in rendered


def test_stale_repository_withholds_repository_edges_but_keeps_durable_why(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    snapshot_root = bind_fixture(repo, tmp_path)
    (repo / "README.md").write_text("changed\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-qm", "changed")
    projection = build_explorer_projection(
        namespace="project:fixture",
        snapshot_root=snapshot_root,
        memory_store=FakeMemoryStore(durable_items()),
    )
    assert projection["repository"]["binding_state"] == "stale"
    assert not any(node["authority"] == "derived_repository" for node in projection["nodes"])
    assert any(node["id"] == "memory:memory-decision-1" for node in projection["nodes"])


def test_delete_and_rebuild_snapshot_is_semantically_equivalent(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    snapshot_root = bind_fixture(repo, tmp_path)
    memories = durable_items()
    first = build_explorer_projection(
        namespace="project:fixture", snapshot_root=snapshot_root, memory_store=FakeMemoryStore(memories)
    )
    store = RepositorySnapshotStore(snapshot_root)
    binding = store.bindings()["bindings"]["project:fixture"]
    snapshot_path = store.snapshot_path(binding["repository_id"])
    snapshot_path.unlink()
    missing = build_explorer_projection(
        namespace="project:fixture", snapshot_root=snapshot_root, memory_store=FakeMemoryStore(memories)
    )
    assert missing["repository"]["binding_state"] == "missing_snapshot"
    assert any(node["authority"] == "governed_durable_memory" for node in missing["nodes"])
    bind_fixture(repo, tmp_path)
    rebuilt = build_explorer_projection(
        namespace="project:fixture", snapshot_root=snapshot_root, memory_store=FakeMemoryStore(memories)
    )
    assert first == rebuilt


def test_cli_parser_adds_explore_without_changing_public_mcp_surface() -> None:
    from agent_mem_bridge.cli import _build_parser
    from agent_mem_bridge.mcp_boundary import PUBLIC_TOOL_NAMES, PUBLIC_TOOL_ORDER

    assert _build_parser().parse_args(["explore", "--namespace", "project:fixture"]).command == "explore"
    assert len(PUBLIC_TOOL_ORDER) == 17
    assert len(PUBLIC_TOOL_NAMES) == 17


def test_simultaneous_explore_reads_are_safe(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    repo = fixture_repo(tmp_path)
    snapshot_root = bind_fixture(repo, tmp_path)

    def read_once() -> str:
        projection = build_explorer_projection(
            namespace="project:fixture",
            snapshot_root=snapshot_root,
            memory_store=FakeMemoryStore(durable_items()),
        )
        return json.dumps(projection, sort_keys=True)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: read_once(), range(16)))
    assert len(set(results)) == 1


def test_read_only_database_projection_does_not_change_database(tmp_path: Path, monkeypatch) -> None:
    from agent_mem_bridge.storage import MemoryStore

    db_path = tmp_path / "bridge.db"
    store = MemoryStore(db_path, log_dir=tmp_path / "logs")
    stored = store.store(
        namespace="project:fixture",
        title="Local first",
        content="record_type: decision\nDo not introduce Redis",
        kind="memory",
    )
    assert stored["id"]
    before = db_path.read_bytes()
    monkeypatch.setattr("agent_mem_bridge.knowledge_explorer.resolve_bridge_db_path", lambda: db_path)
    projection = build_explorer_projection(
        namespace="project:fixture",
        snapshot_root=tmp_path / "snapshots",
        memory_store=None,
    )
    assert any(node["id"] == f"memory:{stored['id']}" for node in projection["nodes"])
    assert db_path.read_bytes() == before
