"""Governed project WHY (decision/constraint) alignment with task memory."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from agent_mem_bridge.context_manifest import compile_context, render_context
from agent_mem_bridge.evidence_inspect import build_memory_inspect_report, render_memory_inspect_markdown
from agent_mem_bridge.knowledge_explorer import build_explorer_projection
from agent_mem_bridge.schema import CURRENT_SCHEMA_VERSION, schema_version
from agent_mem_bridge.storage import MemoryStore
from agent_mem_bridge.task_memory import PROJECT_WHY_CANDIDATE_CEILING, assemble_task_memory

NAMESPACE = "project:why-alignment"
OTHER_NAMESPACE = "project:other-why"
QUERY = "Do not introduce Redis local-first"


def _store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")


def _decision_content(
    *,
    claim: str = "Do not introduce Redis.",
    reason: str = "This project is intentionally local-first and single-node.",
    scope: str = NAMESPACE,
    extra: str = "",
) -> str:
    body = f"record_type: decision\nclaim: {claim}\nreason: {reason}\nscope: {scope}\nconfidence: observed"
    if extra:
        body = f"{body}\n{extra}"
    return body


def _constraint_content(
    *,
    claim: str = "Must remain single-node.",
    reason: str = "No distributed coordination layer.",
    scope: str = NAMESPACE,
    extra: str = "",
) -> str:
    body = f"record_type: constraint\nclaim: {claim}\nreason: {reason}\nscope: {scope}\nconfidence: observed"
    if extra:
        body = f"{body}\n{extra}"
    return body


def _store_decision(store: MemoryStore, *, title: str = "No Redis", content: str | None = None) -> dict:
    return store.store(
        namespace=NAMESPACE,
        title=title,
        content=content or _decision_content(),
        kind="memory",
        tags=None,
    )


def _store_constraint(store: MemoryStore, *, title: str = "Single node", content: str | None = None) -> dict:
    return store.store(
        namespace=NAMESPACE,
        title=title,
        content=content or _constraint_content(),
        kind="memory",
        tags=None,
    )


def _assert_path_selects(store: MemoryStore, *, memory_id: str, query: str, section: str) -> None:
    recall = store.recall(namespace=NAMESPACE, query=query, limit=5)
    assert any(item["id"] == memory_id for item in recall["items"])

    report = assemble_task_memory(store, query=query, project_namespace=NAMESPACE)
    assert [item["id"] for item in report[section]] == [memory_id]
    assert report["procedure_hits"] == [] or section == "procedure_hits"

    inspect = build_memory_inspect_report(store, namespace=NAMESPACE, query=query, technical=True)
    assert any(item["memory_id"] == memory_id for item in inspect["selected"])
    markdown = render_memory_inspect_markdown(inspect)
    assert "No governed memories surfaced" not in markdown
    selected = next(item for item in inspect["selected"] if item["memory_id"] == memory_id)
    assert any("matches this task" in reason for reason in selected["why"])
    assert any("project namespace" in reason for reason in selected["why"])

    manifest = compile_context(task_memory=report, budget_tokens=2048)
    rendered = render_context(manifest)
    label = "[Project Decision]" if section == "decision_hits" else "[Project Constraint]"
    assert label in rendered
    assert memory_id in {item.item_id for item in manifest.items}
    assert "[Dynamic State]" not in rendered or "Project Decision" in rendered

    projection = build_explorer_projection(
        namespace=NAMESPACE,
        snapshot_root=Path("/tmp/why-align-snapshots"),
        memory_store=store,
    )
    assert any(
        node["id"] == f"memory:{memory_id}" and node["authority"] == "governed_durable_memory"
        for node in projection["nodes"]
    )


def test_plain_project_decision_flows_recall_task_inspect_context_explore(tmp_path: Path) -> None:
    store = _store(tmp_path)
    stored = _store_decision(store)
    assert not stored.get("tags")
    _assert_path_selects(store, memory_id=str(stored["id"]), query=QUERY, section="decision_hits")


def test_plain_project_constraint_flows_recall_task_inspect_context_explore(tmp_path: Path) -> None:
    store = _store(tmp_path)
    stored = _store_constraint(store)
    assert not stored.get("tags")
    _assert_path_selects(
        store,
        memory_id=str(stored["id"]),
        query="Must remain single-node constraint",
        section="constraint_hits",
    )


def test_superseded_decision_predecessor_not_active_successor_eligible(tmp_path: Path) -> None:
    store = _store(tmp_path)
    predecessor = _store_decision(store, title="Old Redis ban", content=_decision_content(claim="Old Redis ban."))
    successor = store.revise(
        str(predecessor["id"]),
        replacement_content=_decision_content(claim="Do not introduce Redis."),
        title="No Redis",
    )
    successor_id = str(successor["successor_id"])
    report = assemble_task_memory(store, query=QUERY, project_namespace=NAMESPACE)
    assert [item["id"] for item in report["decision_hits"]] == [successor_id]
    assert predecessor["id"] not in {item["id"] for item in report["decision_hits"]}
    assert any(
        item["id"] == predecessor["id"] and item["reason"] in {"superseded", "superseded_revision"}
        for item in report["suppressed_items"]
    ) or predecessor["id"] not in {
        item["id"] for section in ("decision_hits", "supporting_hits") for item in report.get(section) or []
    }

    inspect = build_memory_inspect_report(store, namespace=NAMESPACE, query=QUERY, technical=True)
    selected_ids = {item["memory_id"] for item in inspect["selected"]}
    assert successor_id in selected_ids
    assert predecessor["id"] not in selected_ids


def test_validity_ineligible_decision_not_selected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    expired = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    stored = _store_decision(
        store,
        title="Expired Redis ban",
        content=_decision_content(extra=f"valid_until: {expired}"),
    )
    report = assemble_task_memory(store, query=QUERY, project_namespace=NAMESPACE)
    assert report["decision_hits"] == []
    assert any(
        item["id"] == stored["id"] and str(item["reason"]).startswith("validity:")
        for item in report["suppressed_items"]
    )
    inspect = build_memory_inspect_report(store, namespace=NAMESPACE, query=QUERY)
    assert stored["id"] not in {item["memory_id"] for item in inspect["selected"]}


def test_degraded_lineage_decision_governed_out(tmp_path: Path) -> None:
    store = _store(tmp_path)
    stored = _store_decision(
        store,
        title="Degraded Redis ban",
        content=_decision_content(extra="lineage_status: degraded"),
    )
    report = assemble_task_memory(store, query=QUERY, project_namespace=NAMESPACE)
    assert report["decision_hits"] == []
    assert any(
        item["id"] == stored["id"] and item["reason"] == "lineage_status:degraded"
        for item in report["suppressed_items"]
    )


def test_unrelated_project_namespace_no_leakage(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _store_decision(store)
    other = store.store(
        namespace=OTHER_NAMESPACE,
        title="Other decision",
        content=_decision_content(scope=OTHER_NAMESPACE, claim="Use Redis in other project."),
        kind="memory",
    )
    report = assemble_task_memory(store, query=QUERY, project_namespace=NAMESPACE)
    assert all(item["namespace"] == NAMESPACE for item in report["decision_hits"])
    assert other["id"] not in {item["id"] for item in report["decision_hits"]}


def test_relation_target_keeps_existing_governance_semantics(tmp_path: Path) -> None:
    store = _store(tmp_path)
    support = store.store(
        namespace=NAMESPACE,
        title="Local-first rationale",
        content="record_type: belief\nclaim: Local-first deployments avoid Redis.",
        kind="memory",
        tags=["kind:belief"],
    )
    decision = _store_decision(
        store,
        content=_decision_content(extra=f"supports: {support['id']}"),
    )
    report = assemble_task_memory(store, query=QUERY, project_namespace=NAMESPACE)
    assert [item["id"] for item in report["decision_hits"]] == [decision["id"]]
    support_ids = {item["id"] for item in report["supporting_hits"]}
    assert support["id"] in support_ids or support["id"] in {item["id"] for item in report["belief_hits"]}


def test_existing_procedure_behavior_unchanged(tmp_path: Path) -> None:
    store = _store(tmp_path)
    procedure = store.store(
        namespace=NAMESPACE,
        kind="memory",
        title="[[Procedure]] release cutover",
        content=(
            "record_type: procedure\n"
            "goal: Run release cutover safely.\n"
            "when_to_use: Before tagging a release.\n"
            "steps: verify | tag | announce\n"
            "procedure_status: validated\n"
        ),
        tags=["kind:procedure", "domain:release"],
    )
    _store_decision(store)
    report = assemble_task_memory(
        store,
        query="release cutover",
        project_namespace=NAMESPACE,
        task_domain="release",
    )
    assert [item["id"] for item in report["procedure_hits"]] == [procedure["id"]]
    assert report["procedure_hits"][0]["task_memory"]["selected_as"] == "procedure-anchor"


def test_repository_what_remains_separate_derived_input(tmp_path: Path) -> None:
    store = _store(tmp_path)
    stored = _store_decision(store)
    report = assemble_task_memory(store, query=QUERY, project_namespace=NAMESPACE)
    repository_items = [
        {
            "fact_kind": "test_command",
            "value": "pytest",
            "source": "pyproject.toml",
            "commit": "abc123",
        }
    ]
    before = store.stats(NAMESPACE)["total_count"]
    manifest = compile_context(
        task_memory=report,
        repository_items=repository_items,
        budget_tokens=2048,
    )
    rendered = render_context(manifest)
    assert "[Repository WHAT]" in rendered
    assert "[Project Decision]" in rendered
    assert any(item.source == "derived_repository" for item in manifest.items)
    assert any(item.source == "task_memory" and item.section == "decision" for item in manifest.items)
    assert store.stats(NAMESPACE)["total_count"] == before
    assert stored["id"] in {item.item_id for item in manifest.items if item.source == "task_memory"}


def test_no_automatic_durable_write_from_inspect_or_context(tmp_path: Path) -> None:
    store = _store(tmp_path)
    stored = _store_decision(store)
    before_count = store.stats(NAMESPACE)["total_count"]
    before_ids = {item["id"] for item in store.browse(NAMESPACE, limit=20)["items"]}
    report = assemble_task_memory(store, query=QUERY, project_namespace=NAMESPACE)
    build_memory_inspect_report(store, namespace=NAMESPACE, query=QUERY)
    compile_context(task_memory=report, budget_tokens=2048)
    assert store.stats(NAMESPACE)["total_count"] == before_count
    after_ids = {item["id"] for item in store.browse(NAMESPACE, limit=20)["items"]}
    assert after_ids == before_ids
    assert stored["id"] in after_ids


def test_schema_remains_v12(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _store_decision(store)
    with store._connect() as conn:
        assert schema_version(conn) == CURRENT_SCHEMA_VERSION == 12


def test_project_why_candidate_window_is_bounded() -> None:
    assert PROJECT_WHY_CANDIDATE_CEILING == 30


def test_dogfood_amb_graph_database_decision(tmp_path: Path) -> None:
    """Recreate the realistic AMB bootstrap WHY story across process boundaries."""
    db_path = tmp_path / "amb.db"
    log_dir = tmp_path / "logs"
    namespace = "project:amb"
    claim = "Do not introduce a graph database for Knowledge Explorer."
    content = (
        "record_type: decision\n"
        f"claim: {claim}\n"
        "reason: Explorer should remain a derived read-only projection over existing "
        "authority lanes.\n"
        "scope: project:amb\n"
        "confidence: observed"
    )
    writer = MemoryStore(db_path, log_dir=log_dir)
    stored = writer.store(namespace=namespace, title="No graph DB", content=content, kind="memory")
    del writer

    reader = MemoryStore(db_path, log_dir=log_dir)
    query = "Should Knowledge Explorer use a graph database?"
    recall = reader.recall(namespace=namespace, query=query, limit=5)
    assert any(item["id"] == stored["id"] for item in recall["items"])

    report = assemble_task_memory(reader, query=query, project_namespace=namespace)
    assert [item["id"] for item in report["decision_hits"]] == [stored["id"]]

    inspect = build_memory_inspect_report(reader, namespace=namespace, query=query, technical=True)
    assert any(item["memory_id"] == stored["id"] for item in inspect["selected"])
    assert "No governed memories surfaced" not in render_memory_inspect_markdown(inspect)

    rendered = render_context(compile_context(task_memory=report, budget_tokens=2048))
    assert "[Project Decision]" in rendered
    assert "graph database" in rendered.lower()

    projection = build_explorer_projection(
        namespace=namespace,
        snapshot_root=tmp_path / "snapshots",
        memory_store=reader,
    )
    assert any(
        node["id"] == f"memory:{stored['id']}" and node["authority"] == "governed_durable_memory"
        for node in projection["nodes"]
    )
