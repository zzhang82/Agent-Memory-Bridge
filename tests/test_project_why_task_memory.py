"""Governed project WHY (decision/constraint) alignment with task memory."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from agent_mem_bridge import task_memory as task_memory_module
from agent_mem_bridge.context_manifest import compile_context, render_context
from agent_mem_bridge.database_maintenance import rebuild_database_projections
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


def _store_crowding_fillers(store: MemoryStore, *, count: int, query: str) -> list[str]:
    ids: list[str] = []
    for index in range(count):
        stored = store.store(
            namespace=NAMESPACE,
            title=f"Crowding note {index:02d} {query}",
            content=(
                f"record_type: note\nclaim: {query} crowding filler {index:02d}.\n"
                f"notes: unrelated durable memory that must not hide project WHY.\n"
                f"{query}"
            ),
            kind="memory",
        )
        ids.append(str(stored["id"]))
    return ids


def _corrupt_project_why_metadata(store: MemoryStore, *, memory_id: str, content: str) -> None:
    with store._connect() as conn:
        conn.execute("UPDATE memories SET content = ? WHERE id = ?", (content, memory_id))
        conn.commit()
    rebuild_database_projections(store.db_path)


def test_project_why_candidate_window_is_bounded() -> None:
    assert PROJECT_WHY_CANDIDATE_CEILING == 30


def _assert_governed_why_selected(
    store: MemoryStore,
    *,
    memory_id: str,
    query: str,
    section: str,
) -> None:
    report = assemble_task_memory(store, query=query, project_namespace=NAMESPACE)
    assert [item["id"] for item in report[section]] == [memory_id]

    inspect = build_memory_inspect_report(store, namespace=NAMESPACE, query=query, technical=True)
    assert any(item["memory_id"] == memory_id for item in inspect["selected"])
    markdown = render_memory_inspect_markdown(inspect)
    assert "No governed memories surfaced" not in markdown

    manifest = compile_context(task_memory=report, budget_tokens=2048)
    rendered = render_context(manifest)
    label = "[Project Decision]" if section == "decision_hits" else "[Project Constraint]"
    assert label in rendered
    assert memory_id in {item.item_id for item in manifest.items}


def test_mixed_hits_beyond_initial_window_still_select_project_decision(tmp_path: Path) -> None:
    store = _store(tmp_path)
    initial_window = 9
    _store_crowding_fillers(store, count=initial_window + 3, query=QUERY)
    stored = _store_decision(store)

    narrow = store.recall(namespace=NAMESPACE, query=QUERY, limit=initial_window)
    assert stored["id"] not in {item["id"] for item in narrow["items"]}

    bounded = store.recall(namespace=NAMESPACE, query=QUERY, limit=PROJECT_WHY_CANDIDATE_CEILING)
    assert any(item["id"] == stored["id"] for item in bounded["items"])
    assert len(bounded["items"]) <= PROJECT_WHY_CANDIDATE_CEILING

    _assert_governed_why_selected(
        store,
        memory_id=str(stored["id"]),
        query=QUERY,
        section="decision_hits",
    )


def test_mixed_hits_beyond_initial_window_still_select_project_constraint(tmp_path: Path) -> None:
    store = _store(tmp_path)
    query = "Must remain single-node constraint"
    initial_window = 9
    _store_crowding_fillers(store, count=initial_window + 3, query=query)
    stored = _store_constraint(store)

    narrow = store.recall(namespace=NAMESPACE, query=query, limit=initial_window)
    assert stored["id"] not in {item["id"] for item in narrow["items"]}

    bounded = store.recall(namespace=NAMESPACE, query=query, limit=PROJECT_WHY_CANDIDATE_CEILING)
    assert any(item["id"] == stored["id"] for item in bounded["items"])

    _assert_governed_why_selected(
        store,
        memory_id=str(stored["id"]),
        query=query,
        section="constraint_hits",
    )


def test_project_why_beyond_hard_ceiling_is_not_scanned(tmp_path: Path, monkeypatch) -> None:
    store = _store(tmp_path)
    _store_crowding_fillers(store, count=PROJECT_WHY_CANDIDATE_CEILING, query=QUERY)
    buried = _store_decision(store, title="Beyond ceiling Redis ban")

    requested_limits: list[int] = []
    original_recall_hits = task_memory_module._recall_hits

    def tracking_recall_hits(*args, **kwargs):
        requested_limits.append(int(kwargs["limit"]))
        return original_recall_hits(*args, **kwargs)

    monkeypatch.setattr(task_memory_module, "_recall_hits", tracking_recall_hits)

    ceiling_recall = store.recall(namespace=NAMESPACE, query=QUERY, limit=PROJECT_WHY_CANDIDATE_CEILING)
    assert buried["id"] not in {item["id"] for item in ceiling_recall["items"]}
    assert len(ceiling_recall["items"]) == PROJECT_WHY_CANDIDATE_CEILING

    beyond_recall = store.recall(namespace=NAMESPACE, query=QUERY, limit=PROJECT_WHY_CANDIDATE_CEILING + 5)
    assert any(item["id"] == buried["id"] for item in beyond_recall["items"])

    report = assemble_task_memory(store, query=QUERY, project_namespace=NAMESPACE)
    assert buried["id"] not in {item["id"] for item in report["decision_hits"]}
    inspect = build_memory_inspect_report(store, namespace=NAMESPACE, query=QUERY)
    assert buried["id"] not in {item["memory_id"] for item in inspect["selected"]}
    rendered = render_context(compile_context(task_memory=report, budget_tokens=2048))
    assert buried["id"] not in rendered

    why_limits = [limit for limit in requested_limits if limit == PROJECT_WHY_CANDIDATE_CEILING]
    assert why_limits
    assert requested_limits
    assert max(requested_limits) <= PROJECT_WHY_CANDIDATE_CEILING


def test_relation_expanded_invalid_project_why_is_suppressed(tmp_path: Path, monkeypatch) -> None:
    store = _store(tmp_path)
    valid_target = store.store(
        namespace=NAMESPACE,
        title="Local-first rationale",
        content="record_type: belief\nclaim: Local-first deployments avoid Redis.",
        kind="memory",
        tags=["kind:belief"],
    )
    invalid_target = store.store(
        namespace=NAMESPACE,
        title="Invalid structured WHY",
        content=_constraint_content(claim="Must use a graph database."),
        kind="memory",
    )
    _corrupt_project_why_metadata(
        store,
        memory_id=str(invalid_target["id"]),
        content=(
            "record_type: constraint\n"
            "claim: Must use a graph database.\n"
            "reason: No distributed coordination layer.\n"
            f"scope: {NAMESPACE}\n"
            "confidence: 2.0"
        ),
    )
    source = _store_decision(
        store,
        content=_decision_content(extra=f"supports: {invalid_target['id']} | {valid_target['id']}"),
    )

    with store._connect() as conn:
        issues = conn.execute(
            "SELECT validation_issues_json FROM memory_metadata WHERE memory_id = ?",
            (invalid_target["id"],),
        ).fetchone()
    assert issues is not None and "invalid_confidence" in str(issues["validation_issues_json"])

    report = assemble_task_memory(store, query=QUERY, project_namespace=NAMESPACE)
    assert [item["id"] for item in report["decision_hits"]] == [source["id"]]
    support_ids = {item["id"] for item in report["supporting_hits"]}
    belief_ids = {item["id"] for item in report["belief_hits"]}
    assert valid_target["id"] in support_ids or valid_target["id"] in belief_ids
    assert invalid_target["id"] not in support_ids
    assert invalid_target["id"] not in {item["id"] for item in report["decision_hits"]}
    assert invalid_target["id"] not in {item["id"] for item in report["constraint_hits"]}
    assert any(
        item["id"] == invalid_target["id"] and item["reason"] == "invalid_structured_metadata"
        for item in report["suppressed_items"]
    )

    inspect = build_memory_inspect_report(store, namespace=NAMESPACE, query=QUERY, technical=True)
    selected_ids = {item["memory_id"] for item in inspect["selected"]}
    assert source["id"] in selected_ids
    assert invalid_target["id"] not in selected_ids

    manifest = compile_context(task_memory=report, budget_tokens=2048)
    rendered = render_context(manifest)
    assert "[Project Decision]" in rendered
    context_ids = {item.item_id for item in manifest.items}
    assert source["id"] in context_ids
    assert valid_target["id"] in context_ids
    assert invalid_target["id"] not in context_ids
    assert "[Project Constraint]" not in rendered

    monkeypatch.setattr("agent_mem_bridge.knowledge_explorer.resolve_bridge_db_path", lambda: store.db_path)
    projection = build_explorer_projection(
        namespace=NAMESPACE,
        snapshot_root=tmp_path / "why-align-snapshots",
        memory_store=None,
    )
    nodes = {node["id"]: node for node in projection["nodes"]}
    assert nodes[f"memory:{source['id']}"]["authority"] == "governed_durable_memory"
    assert f"memory:{invalid_target['id']}" not in nodes
    assert any(
        diagnostic.get("kind") == "suppressed_memory"
        and diagnostic.get("memory_id") == invalid_target["id"]
        and diagnostic.get("reason") == "invalid_structured_metadata"
        for diagnostic in projection["diagnostics"]
    )


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
