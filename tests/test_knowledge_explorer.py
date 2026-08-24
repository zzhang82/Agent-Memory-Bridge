from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from agent_mem_bridge.knowledge_explorer import (
    _build_explorer,
    _presentation_from_build,
    _primary_why_ids,
    build_explorer_projection,
    render_explorer_human_markdown,
    render_explorer_markdown,
    render_explorer_technical_markdown,
)
from agent_mem_bridge.repository_bootstrap import compile_repository_snapshot
from agent_mem_bridge.repository_snapshot_store import RepositorySnapshotStore


class FakeMemoryStore:
    def __init__(self, items: list[dict[str, Any]]) -> None:
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


def durable_items() -> list[dict[str, Any]]:
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
    rendered = render_explorer_technical_markdown(projection)
    assert "read-only, rebuildable projection" in rendered
    assert "governed_durable_memory" in rendered
    assert "derived_repository" in rendered
    assert "memory-decision-1" in rendered
    assert render_explorer_markdown(projection) == rendered


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


ROOT = Path(__file__).resolve().parents[1]


def _human_build(tmp_path: Path, items: list[dict[str, Any]], *, bind: bool = True, name: str = "fixture"):
    repo = tmp_path / name
    if not repo.exists():
        repo = fixture_repo(tmp_path) if name == "fixture" else _named_fixture_repo(tmp_path, name)
    snapshot_root = bind_fixture(repo, tmp_path, namespace=f"project:{name}") if bind else tmp_path / "snapshots"
    return _build_explorer(
        namespace=f"project:{name}",
        snapshot_root=snapshot_root,
        memory_store=FakeMemoryStore(items),
    )


def _named_fixture_repo(tmp_path: Path, name: str) -> Path:
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


def test_technical_markdown_matches_unbound_golden() -> None:
    items = durable_items()
    projection = build_explorer_projection(
        namespace="project:fixture",
        snapshot_root=Path("/tmp/no-snapshot-explorer-golden"),
        memory_store=FakeMemoryStore(items),
    )
    rendered = render_explorer_technical_markdown(projection)
    expected = (ROOT / "tests/fixtures/explorer_technical_unbound.md").read_text(encoding="utf-8")
    assert rendered == expected
    expected_json = json.loads((ROOT / "tests/fixtures/explorer_projection_unbound.json").read_text(encoding="utf-8"))
    assert json.dumps(projection, sort_keys=True) == json.dumps(expected_json, sort_keys=True)


def test_human_markdown_python_repo_decision_and_constraint(tmp_path: Path) -> None:
    items = [
        {
            "id": "memory-decision-1",
            "kind": "memory",
            "title": "Local first",
            "content": "record_type: decision\nclaim: Do not introduce Redis\nreason: local-first deployment",
        },
        {
            "id": "memory-constraint-1",
            "kind": "memory",
            "title": "Python support",
            "content": "record_type: constraint\nclaim: Keep Python 3.11+\nreason: existing tooling",
        },
    ]
    build = _human_build(tmp_path, items)
    rendered = render_explorer_human_markdown(build)
    presentation = _presentation_from_build(build)
    assert presentation.why_ids == _primary_why_ids(build.primary_items)
    assert "PROJECT: fixture" in rendered or "PROJECT:" in rendered
    assert "CODE / WHAT" in rendered
    assert "Runtime: Python >=3.11" in rendered
    assert "CONVERSATION / WHY" in rendered
    assert "Decision" in rendered
    assert "Do not introduce Redis" in rendered
    assert "Reason: local-first deployment" in rendered
    assert "Constraint" in rendered
    assert "Keep Python 3.11+" in rendered
    assert "derived_repository" not in rendered
    assert "governed_durable_memory" not in rendered
    assert "record_type" not in rendered


def test_human_markdown_typescript_repo(tmp_path: Path) -> None:
    repo = tmp_path / "node-app"
    repo.mkdir()
    (repo / "package.json").write_text('{"name":"node-app","packageManager":"pnpm@9.0.0"}\n', encoding="utf-8")
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "fixture@example.invalid")
    git(repo, "config", "user.name", "Fixture")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "initial")
    snapshot_root = bind_fixture(repo, tmp_path, namespace="project:node-app")
    build = _build_explorer(
        namespace="project:node-app",
        snapshot_root=snapshot_root,
        memory_store=FakeMemoryStore([]),
    )
    rendered = render_explorer_human_markdown(build)
    assert "Package manager: pnpm@9.0.0" in rendered
    assert "No project decisions or constraints have been explicitly stored yet." in rendered


def test_human_markdown_empty_why(tmp_path: Path) -> None:
    rendered = render_explorer_human_markdown(_human_build(tmp_path, []))
    assert "No project decisions or constraints have been explicitly stored yet." in rendered
    assert "Remember that we decided X because Y." in rendered
    assert "automatic learning" not in rendered.casefold()


def test_human_markdown_many_repository_facts_and_why_overflow(tmp_path: Path) -> None:
    items = [
        {
            "id": f"decision-{index}",
            "kind": "memory",
            "title": f"Decision {index}",
            "content": f"record_type: decision\nclaim: Decision {index}\nreason: because {index}",
        }
        for index in range(5)
    ] + [
        {
            "id": f"constraint-{index}",
            "kind": "memory",
            "title": f"Constraint {index}",
            "content": f"record_type: constraint\nclaim: Constraint {index}\nreason: because {index}",
        }
        for index in range(5)
    ]
    build = _human_build(tmp_path, items)
    rendered = render_explorer_human_markdown(build)
    presentation = _presentation_from_build(build)
    assert len(presentation.decisions) == 3
    assert len(presentation.constraints) == 3
    assert presentation.decision_overflow == 2
    assert presentation.constraint_overflow == 2
    assert "+2 more" in rendered
    assert presentation.why_ids == _primary_why_ids(build.primary_items)
    assert len(presentation.why_ids) == 10


def test_human_markdown_omits_superseded_and_invalid_why(tmp_path: Path, monkeypatch) -> None:
    store = _real_store(tmp_path, monkeypatch)
    predecessor = _active_decision(store, "claim: old decision\nreason: outdated", "Old")
    store.revise(
        str(predecessor["id"]),
        replacement_content="record_type: decision\nclaim: new decision\nreason: current",
        title="New",
    )
    from datetime import UTC, datetime, timedelta

    expired = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    invalid = _active_decision(store, f"claim: expired decision\nreason: gone\nvalid_until: {expired}", "Expired")
    build = _build_explorer(namespace="project:fixture", snapshot_root=tmp_path / "snapshots", memory_store=None)
    rendered = render_explorer_human_markdown(build)
    assert "old decision" not in rendered
    assert "expired decision" not in rendered
    assert "new decision" in rendered
    assert str(predecessor["id"]) not in _presentation_from_build(build).why_ids
    assert str(invalid["id"]) not in _presentation_from_build(build).why_ids


def test_human_markdown_missing_reason_is_deterministic(tmp_path: Path) -> None:
    items = [
        {
            "id": "decision-no-reason",
            "kind": "memory",
            "title": "Local first",
            "content": "record_type: decision\nclaim: Do not introduce Redis",
        }
    ]
    rendered = render_explorer_human_markdown(_human_build(tmp_path, items))
    assert "Reason: Reason not explicitly recorded." in rendered
    assert "Do not introduce Redis" in rendered


def test_human_markdown_stale_and_dirty_keep_why(tmp_path: Path) -> None:
    items = [
        {
            "id": "memory-decision-1",
            "kind": "memory",
            "title": "Local first",
            "content": "record_type: decision\nclaim: Do not introduce Redis\nreason: local-first deployment",
        }
    ]
    repo = fixture_repo(tmp_path)
    snapshot_root = bind_fixture(repo, tmp_path)
    (repo / "README.md").write_text("changed\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-qm", "changed")
    stale = _build_explorer(
        namespace="project:fixture", snapshot_root=snapshot_root, memory_store=FakeMemoryStore(items)
    )
    stale_md = render_explorer_human_markdown(stale)
    assert "stale" in stale_md.casefold()
    assert "Do not introduce Redis" in stale_md
    assert "Runtime:" not in stale_md

    (repo / "README.md").write_text("dirty\n", encoding="utf-8")
    dirty = _build_explorer(
        namespace="project:fixture", snapshot_root=snapshot_root, memory_store=FakeMemoryStore(items)
    )
    dirty_md = render_explorer_human_markdown(dirty)
    assert "dirty" in dirty_md.casefold()
    assert "Do not introduce Redis" in dirty_md
    assert "Runtime:" not in dirty_md


def test_human_markdown_unbound_keeps_why(tmp_path: Path) -> None:
    items = [
        {
            "id": "memory-decision-1",
            "kind": "memory",
            "title": "Local first",
            "content": "record_type: decision\nclaim: Do not introduce Redis\nreason: local-first deployment",
        }
    ]
    rendered = render_explorer_human_markdown(_human_build(tmp_path, items, bind=False))
    assert "No repository binding exists" in rendered
    assert "Do not introduce Redis" in rendered
    assert "Runtime:" not in rendered


def test_human_markdown_shows_supersedes_not_supports(tmp_path: Path) -> None:
    items = [
        {
            "id": "decision-a",
            "kind": "memory",
            "title": "A",
            "content": "record_type: decision\nclaim: Keep SQLite\nreason: local first\nsupersedes: decision-b",
        },
        {
            "id": "decision-b",
            "kind": "memory",
            "title": "B",
            "content": "record_type: decision\nclaim: Avoid Redis\nreason: complexity\nsupports: decision-a",
        },
    ]
    rendered = render_explorer_human_markdown(_human_build(tmp_path, items, bind=False))
    assert "supersedes" in rendered
    assert "supports" not in rendered


def test_human_markdown_does_not_promote_relation_target_into_primary_why() -> None:
    target = {
        "id": "target-outside-window",
        "kind": "memory",
        "title": "Target",
        "content": "record_type: decision\nclaim: target outside",
    }
    source = {
        "id": "source-inside-window",
        "kind": "memory",
        "title": "Source",
        "content": "record_type: decision\nclaim: source inside\nsupports: target-outside-window",
    }
    filler = {
        "id": "filler-inside-window",
        "kind": "memory",
        "title": "Filler",
        "content": "record_type: decision\nclaim: filler inside",
    }
    build = _build_explorer(
        namespace="project:fixture",
        snapshot_root=Path("/tmp/no-snapshot"),
        memory_store=FakeMemoryStore([source, filler, target]),
        limit=2,
    )
    presentation = _presentation_from_build(build)
    assert presentation.why_ids == ("source-inside-window", "filler-inside-window")
    assert "target-outside-window" not in presentation.why_ids
    rendered = render_explorer_human_markdown(build)
    assert "source inside" in rendered
    assert "filler inside" in rendered
    assert "target outside" not in rendered


def test_human_markdown_is_byte_deterministic(tmp_path: Path) -> None:
    items = durable_items()
    build = _human_build(tmp_path, items)
    assert render_explorer_human_markdown(build) == render_explorer_human_markdown(build)


def test_explore_modes_do_not_write(tmp_path: Path, monkeypatch) -> None:
    from agent_mem_bridge.storage import MemoryStore

    db_path = tmp_path / "bridge.db"
    store = MemoryStore(db_path, log_dir=tmp_path / "logs")
    store.store(
        namespace="project:fixture",
        title="Local first",
        content="record_type: decision\nclaim: Do not introduce Redis\nreason: local-first",
        kind="memory",
    )
    monkeypatch.setattr("agent_mem_bridge.knowledge_explorer.resolve_bridge_db_path", lambda: db_path)
    before = db_path.read_bytes()
    build = _build_explorer(namespace="project:fixture", snapshot_root=tmp_path / "snapshots", memory_store=None)
    render_explorer_human_markdown(build)
    render_explorer_technical_markdown(build.projection)
    json.dumps(build.projection, sort_keys=True)
    assert db_path.read_bytes() == before


def test_human_markdown_is_namespace_isolated(tmp_path: Path, monkeypatch) -> None:
    store = _real_store(tmp_path, monkeypatch)
    store.store(
        namespace="project:alpha",
        title="Alpha",
        content="record_type: decision\nclaim: Alpha only",
        kind="memory",
    )
    store.store(
        namespace="project:beta",
        title="Beta",
        content="record_type: decision\nclaim: Beta only",
        kind="memory",
    )
    alpha = render_explorer_human_markdown(
        _build_explorer(namespace="project:alpha", snapshot_root=tmp_path / "snapshots", memory_store=None)
    )
    beta = render_explorer_human_markdown(
        _build_explorer(namespace="project:beta", snapshot_root=tmp_path / "snapshots", memory_store=None)
    )
    assert "Alpha only" in alpha
    assert "Beta only" not in alpha
    assert "Beta only" in beta
    assert "Alpha only" not in beta


def test_cli_rejects_json_technical_and_defaults_to_human() -> None:
    from agent_mem_bridge.cli import _build_parser

    parser = _build_parser()
    default = parser.parse_args(["explore", "--namespace", "project:fixture"])
    assert default.format == "markdown"
    assert default.technical is False
    technical = parser.parse_args(["explore", "--namespace", "project:fixture", "--format", "markdown", "--technical"])
    assert technical.technical is True
    json_mode = parser.parse_args(["explore", "--namespace", "project:fixture", "--format", "json"])
    assert json_mode.format == "json"
    rejected = parser.parse_args(["explore", "--namespace", "project:fixture", "--format", "json", "--technical"])
    assert rejected.format == "json"
    assert rejected.technical is True


def test_cli_json_technical_exits_2(capsys) -> None:
    import argparse

    from agent_mem_bridge.cli import _run_explore

    args = argparse.Namespace(namespace="project:fixture", format="json", technical=True, limit=100)
    assert _run_explore(args) == 2
    captured = capsys.readouterr()
    assert "--technical is only valid with --format markdown" in captured.err
