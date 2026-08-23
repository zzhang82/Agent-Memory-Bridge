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
        return {"items": self.items[:limit]}


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
    assert {node["authority"] for node in first["nodes"]} == {
        "derived_projection",
        "derived_repository",
        "governed_durable_memory",
    }
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


def _real_store(tmp_path: Path, monkeypatch):
    from agent_mem_bridge.storage import MemoryStore

    db_path = tmp_path / "governed.db"
    monkeypatch.setattr("agent_mem_bridge.knowledge_explorer.resolve_bridge_db_path", lambda: db_path)
    return MemoryStore(db_path, log_dir=tmp_path / "logs")


def _active_decision(store, content: str, title: str = "Decision") -> dict[str, object]:
    return store.store(
        namespace="project:fixture",
        title=title,
        content=f"record_type: decision\n{content}",
        kind="memory",
    )


def test_superseded_decision_is_not_active_but_successor_is(tmp_path: Path, monkeypatch) -> None:
    store = _real_store(tmp_path, monkeypatch)
    predecessor = _active_decision(store, "claim: old decision", "Old")
    successor = store.revise(
        str(predecessor["id"]),
        replacement_content="record_type: decision\nclaim: new decision",
        title="New",
    )
    projection = build_explorer_projection(
        namespace="project:fixture", snapshot_root=tmp_path / "snapshots", memory_store=None
    )
    node_ids = {node["id"] for node in projection["nodes"]}
    assert f"memory:{predecessor['id']}" not in node_ids
    assert f"memory:{successor['successor_id']}" in node_ids
    assert any(
        item["memory_id"] == predecessor["id"] and item["reason"] == "superseded_revision"
        for item in projection["diagnostics"]
    )


def test_validity_ineligible_decision_is_not_active(tmp_path: Path, monkeypatch) -> None:
    from datetime import UTC, datetime, timedelta

    store = _real_store(tmp_path, monkeypatch)
    expired = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    item = _active_decision(store, f"claim: expired decision\nvalid_until: {expired}", "Expired")
    projection = build_explorer_projection(
        namespace="project:fixture", snapshot_root=tmp_path / "snapshots", memory_store=None
    )
    assert f"memory:{item['id']}" not in {node["id"] for node in projection["nodes"]}
    assert any(
        diagnostic["memory_id"] == item["id"] and diagnostic["reason"] == "validity:expired"
        for diagnostic in projection["diagnostics"]
    )


def test_missing_relation_target_is_diagnostic_only(tmp_path: Path, monkeypatch) -> None:
    store = _real_store(tmp_path, monkeypatch)
    source = _active_decision(store, "claim: source\nsupports: does-not-exist", "Source")
    projection = build_explorer_projection(
        namespace="project:fixture", snapshot_root=tmp_path / "snapshots", memory_store=None
    )
    assert not any(edge["relation"] == "supports" for edge in projection["edges"])
    assert "memory:does-not-exist" not in {node["id"] for node in projection["nodes"]}
    assert any(item["target_memory_id"] == "does-not-exist" for item in projection["diagnostics"])
    assert f"memory:{source['id']}" in {node["id"] for node in projection["nodes"]}


def test_ineligible_relation_target_is_not_active_edge_or_node(tmp_path: Path, monkeypatch) -> None:
    store = _real_store(tmp_path, monkeypatch)
    target = _active_decision(store, "claim: old target", "Old target")
    source = _active_decision(store, f"claim: source\nsupports: {target['id']}", "Source")
    successor = store.revise(
        str(target["id"]),
        replacement_content="record_type: decision\nclaim: replacement target",
        title="Replacement target",
    )
    projection = build_explorer_projection(
        namespace="project:fixture", snapshot_root=tmp_path / "snapshots", memory_store=None
    )
    ids = {node["id"] for node in projection["nodes"]}
    assert f"memory:{target['id']}" not in ids
    assert f"memory:{successor['successor_id']}" in ids
    assert not any(edge["target"] == f"memory:{target['id']}" for edge in projection["edges"])
    assert any(
        diagnostic.get("memory_id") == target["id"] or diagnostic.get("target_memory_id") == target["id"]
        for diagnostic in projection["diagnostics"]
    )
    assert f"memory:{source['id']}" in ids


def test_limit_refills_after_suppressed_rows(tmp_path: Path, monkeypatch) -> None:
    store = _real_store(tmp_path, monkeypatch)
    from datetime import UTC, datetime, timedelta

    expired = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    _active_decision(store, f"claim: expired one\nvalid_until: {expired}", "Expired one")
    _active_decision(store, f"claim: expired two\nvalid_until: {expired}", "Expired two")
    current_one = _active_decision(store, "claim: current one", "Current one")
    current_two = _active_decision(store, "claim: current two", "Current two")
    projection = build_explorer_projection(
        namespace="project:fixture", snapshot_root=tmp_path / "snapshots", memory_store=None, limit=2
    )
    ids = {node["id"] for node in projection["nodes"]}
    assert f"memory:{current_one['id']}" in ids
    assert f"memory:{current_two['id']}" in ids
    assert len([node for node in projection["nodes"] if node["type"] == "durable_memory"]) == 2


def test_eligible_relation_target_outside_primary_window_is_resolved() -> None:
    target = {
        "id": "target-outside-window",
        "kind": "memory",
        "title": "Target",
        "content": "record_type: decision\nclaim: target",
    }
    source = {
        "id": "source-inside-window",
        "kind": "memory",
        "title": "Source",
        "content": "record_type: decision\nclaim: source\nsupports: target-outside-window",
    }
    filler = {
        "id": "filler-inside-window",
        "kind": "memory",
        "title": "Filler",
        "content": "record_type: decision\nclaim: filler",
    }
    projection = build_explorer_projection(
        namespace="project:fixture",
        snapshot_root=Path("/tmp/no-snapshot"),
        memory_store=FakeMemoryStore([source, filler, target]),
        limit=2,
    )
    ids = {node["id"] for node in projection["nodes"]}
    assert "memory:target-outside-window" in ids
    assert any(
        edge["relation"] == "supports" and edge["target"] == "memory:target-outside-window"
        for edge in projection["edges"]
    )
    assert not any(
        diagnostic.get("target_memory_id") == "target-outside-window" for diagnostic in projection["diagnostics"]
    )


def test_relation_resolution_has_a_hard_bound() -> None:
    targets = [
        {
            "id": f"target-{index:03d}",
            "kind": "memory",
            "title": f"Target {index}",
            "content": "record_type: decision\nclaim: target",
        }
        for index in range(150)
    ]
    source = {
        "id": "source-many-relations",
        "kind": "memory",
        "title": "Source",
        "content": "record_type: decision\nclaim: source\nsupports: " + "|".join(item["id"] for item in targets),
    }
    projection = build_explorer_projection(
        namespace="project:fixture",
        snapshot_root=Path("/tmp/no-snapshot"),
        memory_store=FakeMemoryStore([source, *targets]),
        limit=1,
    )
    support_edges = [edge for edge in projection["edges"] if edge["relation"] == "supports"]
    budget_diagnostics = [
        item for item in projection["diagnostics"] if item.get("reason") == "relation_resolution_budget_exhausted"
    ]
    assert len(support_edges) == 100
    assert len(budget_diagnostics) == 50
    assert len(projection["nodes"]) <= 102


def test_injected_and_query_only_paths_share_validity_governance(tmp_path: Path, monkeypatch) -> None:
    from datetime import UTC, datetime, timedelta

    expired = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    expired_item = {
        "id": "expired-parity",
        "kind": "memory",
        "title": "Expired",
        "content": f"record_type: decision\nclaim: expired\nvalid_until: {expired}",
    }
    current_item = {
        "id": "current-parity",
        "kind": "memory",
        "title": "Current",
        "content": "record_type: decision\nclaim: current",
    }
    injected = build_explorer_projection(
        namespace="project:fixture",
        snapshot_root=tmp_path / "snapshots",
        memory_store=FakeMemoryStore([expired_item, current_item]),
        limit=2,
    )
    db_store = _real_store(tmp_path, monkeypatch)
    db_store.store(namespace="project:fixture", title="Expired", content=expired_item["content"], kind="memory")
    db_store.store(namespace="project:fixture", title="Current", content=current_item["content"], kind="memory")
    query_only = build_explorer_projection(
        namespace="project:fixture", snapshot_root=tmp_path / "other-snapshots", memory_store=None, limit=2
    )
    injected_ids = {node["id"] for node in injected["nodes"] if node["type"] == "durable_memory"}
    query_ids = {node["id"] for node in query_only["nodes"] if node["type"] == "durable_memory"}
    assert injected_ids == {"memory:current-parity"}
    assert len(query_ids) == 1
    assert any(item["reason"] == "validity:expired" for item in injected["diagnostics"])


def test_production_target_lookup_resolves_beyond_primary_scan_window(tmp_path: Path, monkeypatch) -> None:
    store = _real_store(tmp_path, monkeypatch)
    target = _active_decision(store, "claim: target beyond primary window", "Far target")
    source = _active_decision(store, f"claim: primary source\nsupports: {target['id']}", "Primary source")
    for index in range(501):
        _active_decision(store, f"claim: filler {index}", f"Filler {index}")
    with store._connect() as connection:
        connection.execute(
            "UPDATE memories SET created_at = ? WHERE id = ?", ("2099-01-01T00:00:00+00:00", target["id"])
        )
        connection.commit()
    projection = build_explorer_projection(
        namespace="project:fixture", snapshot_root=tmp_path / "snapshots", memory_store=None, limit=1
    )
    target_node = f"memory:{target['id']}"
    source_node = f"memory:{source['id']}"
    assert source_node in {node["id"] for node in projection["nodes"]}
    assert target_node in {node["id"] for node in projection["nodes"]}
    assert any(
        edge["source"] == source_node and edge["relation"] == "supports" and edge["target"] == target_node
        for edge in projection["edges"]
    )
    assert not any(
        diagnostic.get("target_memory_id") == target["id"]
        and diagnostic.get("reason") in {"missing_target", "ineligible_target"}
        for diagnostic in projection["diagnostics"]
    )
