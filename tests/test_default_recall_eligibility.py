from pathlib import Path
from typing import Any, cast

import pytest

import agent_mem_bridge.query as query_module
from agent_mem_bridge.embedding_index import EmbeddingConfig, hash_embed_text
from agent_mem_bridge.embedding_scheduler import EmbeddingSchedulerConfig, run_embedding_sidecar_maintenance
from agent_mem_bridge.repository import fetch_row_by_id
from agent_mem_bridge.retrieval_feedback import decode_recall_receipt
from agent_mem_bridge.storage import MemoryStore
from agent_mem_bridge.task_memory import assemble_task_memory


def _store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")


def _ids(payload: dict[str, object]) -> list[str]:
    items = cast(list[dict[str, Any]], payload["items"])
    return [str(item["id"]) for item in items]


def _procedure_content(status: str, label: str) -> str:
    return (
        "record_type: procedure\n"
        f"goal: Execute the {label} checkout deployment procedure.\n"
        "when_to_use: Before a production checkout deployment.\n"
        "steps: verify deployment | deploy checkout | validate checkout\n"
        f"procedure_status: {status}\n"
    )


def _actionable_ids(report: dict[str, Any]) -> list[str]:
    actionables: list[str] = []
    for section in (
        "procedure_hits",
        "decision_hits",
        "constraint_hits",
        "concept_hits",
        "belief_hits",
        "domain_hits",
        "supporting_hits",
        "corrective_items",
    ):
        actionables.extend(str(item["id"]) for item in report.get(section) or [])
    return actionables


def test_default_recall_hides_revision_predecessors_but_historical_access_and_direct_lookup_remain(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    predecessor = store.store(
        namespace="project:checkout",
        title="Legacy checkout deployment",
        content="The checkout production deploy procedure is: run legacy-deploy --force.",
    )
    revision = store.revise(
        str(predecessor["id"]),
        replacement_content=(
            "The checkout production deploy procedure is: run safe-deploy --verified. "
            "The legacy command must not be used."
        ),
    )
    successor_id = str(revision["successor_id"])

    default_recall = store.recall(namespace="project:checkout", query="legacy deploy force", limit=5)
    historical_recall = store.recall(
        namespace="project:checkout",
        query="legacy deploy force",
        limit=5,
        eligibility="historical",
    )

    assert str(predecessor["id"]) not in _ids(default_recall)
    assert successor_id in _ids(default_recall)
    assert {str(predecessor["id"]), successor_id} <= set(_ids(historical_recall))
    assert default_recall["retrieval"]["suppression_reason_counts"] == {"superseded_revision": 1}
    with store._connect() as conn:
        assert fetch_row_by_id(conn, str(predecessor["id"])) is not None
        revisions = conn.execute(
            "SELECT predecessor_id, successor_id FROM memory_revisions WHERE predecessor_id = ?",
            (str(predecessor["id"]),),
        ).fetchall()
    assert [(row["predecessor_id"], row["successor_id"]) for row in revisions] == [
        (str(predecessor["id"]), successor_id)
    ]


def test_default_recall_exposes_only_current_revision_in_chain(tmp_path: Path) -> None:
    store = _store(tmp_path)
    v1 = store.store(namespace="project:checkout", content="checkout rollout guidance version one")
    v2 = store.revise(str(v1["id"]), replacement_content="checkout rollout guidance version two")
    v3 = store.revise(str(v2["successor_id"]), replacement_content="checkout rollout guidance version three")

    default_recall = store.recall(namespace="project:checkout", query="checkout rollout guidance", limit=5)
    historical_recall = store.recall(
        namespace="project:checkout",
        query="checkout rollout guidance",
        limit=5,
        eligibility="historical",
    )

    assert _ids(default_recall) == [str(v3["successor_id"])]
    assert {str(v1["id"]), str(v2["successor_id"]), str(v3["successor_id"])} <= set(_ids(historical_recall))
    assert default_recall["retrieval"]["suppressed_count"] == 2
    assert default_recall["retrieval"]["suppression_reason_counts"] == {"superseded_revision": 2}


def test_default_recall_filters_only_recognized_ineligible_procedures_and_refills_limit(tmp_path: Path) -> None:
    store = _store(tmp_path)
    unsafe = store.store(
        namespace="project:checkout",
        title="Unsafe checkout procedure",
        content=_procedure_content("unsafe", "unsafe"),
        tags=["kind:procedure", "domain:release"],
    )
    stale = store.store(
        namespace="project:checkout",
        title="Stale checkout procedure",
        content=_procedure_content("stale", "stale"),
        tags=["kind:procedure", "domain:release"],
    )
    replaced = store.store(
        namespace="project:checkout",
        title="Replaced checkout procedure",
        content=_procedure_content("replaced", "replaced"),
        tags=["kind:procedure", "domain:release"],
    )
    validated = store.store(
        namespace="project:checkout",
        title="Validated checkout procedure",
        content=_procedure_content("validated", "validated"),
        tags=["kind:procedure", "domain:release"],
    )
    ordinary_prose = store.store(
        namespace="project:checkout",
        content="This ordinary note says unsafe, stale, and replaced, but is not a structured procedure.",
    )
    eligible_a = store.store(
        namespace="project:checkout", content="checkout deployment procedure eligible memory alpha"
    )
    eligible_b = store.store(namespace="project:checkout", content="checkout deployment procedure eligible memory beta")
    eligible_c = store.store(
        namespace="project:checkout", content="checkout deployment procedure eligible memory gamma"
    )

    default_recall = store.recall(namespace="project:checkout", query="checkout deployment procedure", limit=4)
    historical_recall = store.recall(
        namespace="project:checkout",
        query="checkout deployment procedure",
        limit=10,
        eligibility="historical",
    )
    prose_recall = store.recall(namespace="project:checkout", query="ordinary note unsafe stale replaced", limit=3)
    refill_recall = store.recall(namespace="project:checkout", query="checkout deployment procedure", limit=3)

    default_ids = _ids(default_recall)
    historical_ids = set(_ids(historical_recall))
    assert str(unsafe["id"]) not in default_ids
    assert str(stale["id"]) not in default_ids
    assert str(replaced["id"]) not in default_ids
    assert str(validated["id"]) in default_ids
    assert {str(unsafe["id"]), str(stale["id"]), str(replaced["id"]), str(validated["id"])} <= historical_ids
    assert _ids(prose_recall) == [str(ordinary_prose["id"])]
    assert len(_ids(refill_recall)) == 3
    assert {str(eligible_a["id"]), str(eligible_b["id"]), str(eligible_c["id"])} <= set(default_ids)
    assert len(default_ids) == len(set(default_ids))
    assert default_recall["retrieval"]["suppression_reason_counts"] == {
        "procedure_replaced": 1,
        "procedure_stale": 1,
        "procedure_unsafe": 1,
    }


def test_default_recall_returns_partial_result_when_only_ineligible_procedures_match(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.store(
        namespace="project:checkout",
        content=_procedure_content("unsafe", "unsafe only"),
        tags=["kind:procedure"],
    )

    recalled = store.recall(namespace="project:checkout", query="unsafe only checkout deployment", limit=5)

    assert recalled["count"] == 0
    assert recalled["items"] == []
    assert recalled["retrieval"]["suppression_reason_counts"] == {"procedure_unsafe": 1}


def test_default_recall_refills_beyond_initial_candidate_window(tmp_path: Path) -> None:
    store = _store(tmp_path)
    for index in range(12):
        predecessor = store.store(
            namespace="project:checkout",
            content=("checkout lifecycle fixture highrank " * 4) + f"obsolete predecessor {index}",
        )
        store.revise(
            str(predecessor["id"]),
            replacement_content=f"corrected checkout deployment guidance {index}",
        )
    for index in range(12):
        store.store(
            namespace="project:checkout",
            content=_procedure_content("unsafe", ("checkout lifecycle fixture highrank " * 4) + f"unsafe {index}"),
            tags=["kind:procedure"],
        )
    eligible = [
        store.store(
            namespace="project:checkout",
            content=f"checkout lifecycle fixture highrank eligible {index} " + ("padding " * 200),
        )
        for index in range(3)
    ]

    recalled = store.recall(namespace="project:checkout", query="checkout lifecycle fixture highrank", limit=3)

    assert _ids(recalled) == [str(item["id"]) for item in eligible]
    assert recalled["retrieval"]["suppressed_count"] >= 20
    assert recalled["retrieval"]["suppression_reason_counts"] == {
        "procedure_unsafe": 12,
        "superseded_revision": 12,
    }


@pytest.mark.parametrize("relation_aware", [True, False])
def test_task_memory_cannot_reintroduce_revision_predecessors(
    tmp_path: Path,
    relation_aware: bool,
) -> None:
    store = _store(tmp_path)
    predecessor = store.store(
        namespace="project:checkout",
        title="Legacy checkout deployment procedure",
        content=_procedure_content("validated", "legacy"),
        tags=["kind:procedure"],
    )
    predecessor_id = str(predecessor["id"])
    revision = store.revise(
        predecessor_id,
        replacement_content=_procedure_content("validated", "corrected"),
        title="Corrected checkout deployment procedure",
    )
    successor_id = str(revision["successor_id"])
    unsafe = store.store(
        namespace="project:checkout",
        title="Unsafe checkout deployment procedure",
        content=_procedure_content("unsafe", "unsafe"),
        tags=["kind:procedure"],
    )

    report = assemble_task_memory(
        store,
        query="checkout deployment procedure",
        project_namespace="project:checkout",
        relation_aware=relation_aware,
        task_domain="release",
    )

    actionable = _actionable_ids(report)
    assert predecessor_id not in actionable
    assert str(unsafe["id"]) not in actionable
    assert [str(item["id"]) for item in report["procedure_hits"]] == [successor_id]
    suppressed_by_id = {str(entry["id"]): str(entry["reason"]) for entry in report["suppressed_items"]}
    assert suppressed_by_id[str(unsafe["id"])] == "procedure_status:unsafe"
    if relation_aware:
        assert suppressed_by_id[predecessor_id] == "superseded_revision"
    with store._connect() as conn:
        assert fetch_row_by_id(conn, predecessor_id) is not None
        chain = conn.execute(
            "SELECT predecessor_id, successor_id FROM memory_revisions WHERE predecessor_id = ?",
            (predecessor_id,),
        ).fetchall()
    assert [(row["predecessor_id"], row["successor_id"]) for row in chain] == [(predecessor_id, successor_id)]
    historical_recall = store.recall(
        namespace="project:checkout",
        query="checkout deployment procedure",
        tags_any=["kind:procedure"],
        limit=5,
        eligibility="historical",
    )
    assert {predecessor_id, successor_id, str(unsafe["id"])} <= set(_ids(historical_recall))


@pytest.mark.parametrize("relation_aware", [True, False])
def test_task_memory_exposes_only_final_revision_of_procedure_chain(
    tmp_path: Path,
    relation_aware: bool,
) -> None:
    store = _store(tmp_path)
    v1 = store.store(
        namespace="project:checkout",
        title="Checkout rollout chain one",
        content=_procedure_content("validated", "chain one"),
        tags=["kind:procedure"],
    )
    v2 = store.revise(
        str(v1["id"]),
        replacement_content=_procedure_content("validated", "chain two"),
        title="Checkout rollout chain two",
    )
    v3 = store.revise(
        str(v2["successor_id"]),
        replacement_content=_procedure_content("validated", "chain three"),
        title="Checkout rollout chain three",
    )
    v1_id, v2_id, v3_id = str(v1["id"]), str(v2["successor_id"]), str(v3["successor_id"])

    report = assemble_task_memory(
        store,
        query="checkout deployment procedure",
        project_namespace="project:checkout",
        relation_aware=relation_aware,
        task_domain="release",
    )

    actionable = _actionable_ids(report)
    assert v1_id not in actionable
    assert v2_id not in actionable
    assert [str(item["id"]) for item in report["procedure_hits"]] == [v3_id]
    if relation_aware:
        suppressed_by_id = {str(entry["id"]): str(entry["reason"]) for entry in report["suppressed_items"]}
        assert suppressed_by_id[v1_id] == "superseded_revision"
        assert suppressed_by_id[v2_id] == "superseded_revision"
    historical_recall = store.recall(
        namespace="project:checkout",
        query="checkout deployment procedure",
        tags_any=["kind:procedure"],
        limit=5,
        eligibility="historical",
    )
    assert {v1_id, v2_id, v3_id} <= set(_ids(historical_recall))


def test_task_memory_procedure_governance_reporting_survives_revision_exclusion(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    predecessor = store.store(
        namespace="project:checkout",
        title="Legacy governed checkout procedure",
        content=_procedure_content("validated", "legacy governed"),
        tags=["kind:procedure"],
    )
    predecessor_id = str(predecessor["id"])
    successor = store.revise(
        predecessor_id,
        replacement_content=_procedure_content("validated", "corrected governed"),
        title="Corrected governed checkout procedure",
    )
    statuses = {
        status: str(
            store.store(
                namespace="project:checkout",
                title=f"{status.capitalize()} governed checkout procedure",
                content=_procedure_content(status, status),
                tags=["kind:procedure"],
            )["id"]
        )
        for status in ("unsafe", "stale", "replaced", "validated")
    }

    report = assemble_task_memory(
        store,
        query="checkout deployment procedure",
        project_namespace="project:checkout",
        procedure_limit=6,
        task_domain="release",
    )

    actionable = _actionable_ids(report)
    assert predecessor_id not in actionable
    assert statuses["unsafe"] not in actionable
    assert statuses["stale"] not in actionable
    assert statuses["replaced"] not in actionable
    assert {str(successor["successor_id"]), statuses["validated"]} <= {
        str(item["id"]) for item in report["procedure_hits"]
    }
    suppressed_by_id = {str(entry["id"]): str(entry["reason"]) for entry in report["suppressed_items"]}
    assert suppressed_by_id[statuses["unsafe"]] == "procedure_status:unsafe"
    assert suppressed_by_id[statuses["stale"]] == "procedure_status:stale"
    assert suppressed_by_id[statuses["replaced"]] == "procedure_status:replaced"
    assert suppressed_by_id[predecessor_id] == "superseded_revision"


def _warm_deterministic_semantic_index(
    store: MemoryStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    query_config = EmbeddingConfig(provider="command", capability="semantic", model="fixture-hash", dim=8)
    monkeypatch.setattr(query_module, "active_embedding_config", lambda: query_config)
    warmed = run_embedding_sidecar_maintenance(
        store,
        config=EmbeddingSchedulerConfig(
            enabled=True,
            state_path=tmp_path / "embedding-state.json",
            interval_seconds=0,
            batch_size=100,
            embedding_config=EmbeddingConfig(model="fixture-hash", dim=8),
        ),
    )
    assert warmed["remaining_count"] == 0
    monkeypatch.setattr(
        query_module,
        "embed_texts",
        lambda texts, *, config: [hash_embed_text(text, dim=config.dim) for text in texts],
    )


def _decoded_receipt_ids(store: MemoryStore, response: dict[str, object]) -> tuple[list[str], list[str]]:
    receipt = response["recall_receipt"]
    assert isinstance(receipt, dict)
    payload = decode_recall_receipt(str(receipt["token"]), secret=store.recall_receipt_secret)
    results = cast(list[dict[str, str]], payload["results"])
    exposure_set = cast(list[dict[str, str]], payload["exposure_set"])
    return (
        [str(entry["memory_id"]) for entry in results],
        [str(entry["memory_id"]) for entry in exposure_set],
    )


@pytest.mark.parametrize("retrieval_mode", ["semantic", "hybrid"])
def test_semantic_and_hybrid_recall_cannot_reintroduce_revision_predecessors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    retrieval_mode: str,
) -> None:
    namespace = "project:semantic-eligibility"
    store = _store(tmp_path)
    predecessor = store.store(
        namespace=namespace,
        kind="memory",
        title="Zephyrblade legacy runbook",
        content="zephyrblade zephyrblade zephyrblade deployment runbook legacy steps",
    )
    revision = store.revise(
        str(predecessor["id"]),
        replacement_content="corrected zephyrblade deployment runbook verified safe steps",
        title="Zephyrblade corrected runbook",
    )
    successor_id = str(revision["successor_id"])
    eligible_notes = [
        str(
            store.store(
                namespace=namespace,
                kind="memory",
                title=f"Zephyrblade eligible note {index}",
                content=f"zephyrblade deployment runbook eligible note {index} current guidance",
            )["id"]
        )
        for index in range(2)
    ]
    _warm_deterministic_semantic_index(store, tmp_path, monkeypatch)
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_RETRIEVAL_MODE", retrieval_mode)

    recalled = store.recall(
        namespace=namespace,
        query="zephyrblade deployment runbook",
        kind="memory",
        limit=3,
    )
    historical = store.recall(
        namespace=namespace,
        query="zephyrblade deployment runbook",
        kind="memory",
        limit=3,
        eligibility="historical",
    )

    returned_ids = _ids(recalled)
    assert str(predecessor["id"]) not in returned_ids
    assert set(returned_ids) == {successor_id, *eligible_notes}
    assert len(returned_ids) == len(set(returned_ids))
    assert str(predecessor["id"]) in _ids(historical)
    assert recalled["retrieval"]["mode"] == retrieval_mode
    assert recalled["retrieval"]["semantic_available"] is True
    assert recalled["retrieval"]["semantic_completeness"] == "complete"
    assert recalled["retrieval"]["suppression_reason_counts"] == {"superseded_revision": 1}
    receipt_result_ids, exposure_ids = _decoded_receipt_ids(store, recalled)
    assert receipt_result_ids == returned_ids
    assert exposure_ids == returned_ids


@pytest.mark.parametrize("retrieval_mode", ["semantic", "hybrid"])
def test_semantic_and_hybrid_recall_cannot_reintroduce_ineligible_procedures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    retrieval_mode: str,
) -> None:
    namespace = "project:semantic-eligibility"
    store = _store(tmp_path)

    def _semantic_procedure_content(status: str) -> str:
        return (
            "record_type: procedure\n"
            f"goal: Execute the {status} zephyrblade deployment runbook cutover.\n"
            "when_to_use: Before the zephyrblade deployment runbook cutover.\n"
            "steps: verify zephyrblade | deploy runbook | validate deployment\n"
            f"procedure_status: {status}\n"
        )

    unsafe = store.store(
        namespace=namespace,
        kind="memory",
        title="Zephyrblade unsafe procedure",
        content=_semantic_procedure_content("unsafe"),
        tags=["kind:procedure"],
    )
    validated = store.store(
        namespace=namespace,
        kind="memory",
        title="Zephyrblade validated procedure",
        content=_semantic_procedure_content("validated"),
        tags=["kind:procedure"],
    )
    eligible_notes = [
        str(
            store.store(
                namespace=namespace,
                kind="memory",
                title=f"Zephyrblade eligible note {index}",
                content=f"zephyrblade deployment runbook eligible note {index} current guidance",
            )["id"]
        )
        for index in range(2)
    ]
    _warm_deterministic_semantic_index(store, tmp_path, monkeypatch)
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_RETRIEVAL_MODE", retrieval_mode)

    recalled = store.recall(
        namespace=namespace,
        query="zephyrblade deployment runbook",
        kind="memory",
        limit=3,
    )
    historical = store.recall(
        namespace=namespace,
        query="zephyrblade deployment runbook",
        kind="memory",
        limit=3,
        eligibility="historical",
    )

    returned_ids = _ids(recalled)
    assert str(unsafe["id"]) not in returned_ids
    assert set(returned_ids) == {str(validated["id"]), *eligible_notes}
    assert len(returned_ids) == len(set(returned_ids))
    assert str(unsafe["id"]) in _ids(historical)
    assert recalled["retrieval"]["mode"] == retrieval_mode
    assert recalled["retrieval"]["semantic_available"] is True
    assert recalled["retrieval"]["semantic_completeness"] == "complete"
    assert recalled["retrieval"]["suppression_reason_counts"] == {"procedure_unsafe": 1}
    receipt_result_ids, exposure_ids = _decoded_receipt_ids(store, recalled)
    assert receipt_result_ids == returned_ids
    assert exposure_ids == returned_ids


def test_relation_expansion_suppresses_dependent_of_revision_predecessor(tmp_path: Path) -> None:
    store = _store(tmp_path)
    predecessor = store.store(
        namespace="project:checkout",
        kind="memory",
        title="Legacy checkout rollout dependency",
        content="legacy dependency guidance for checkout rollout",
    )
    predecessor_id = str(predecessor["id"])
    revision = store.revise(
        predecessor_id,
        replacement_content="corrected dependency guidance for checkout rollout",
        title="Corrected checkout rollout dependency",
    )
    successor_id = str(revision["successor_id"])
    anchor = store.store(
        namespace="project:checkout",
        kind="memory",
        title="[[Procedure]] checkout rollout validated path",
        content=(
            "record_type: procedure\n"
            "goal: Run the checkout rollout procedure.\n"
            "when_to_use: During a checkout rollout.\n"
            "steps: read dependency | run rollout\n"
            "procedure_status: validated\n"
            f"depends_on: {predecessor_id}\n"
        ),
        tags=["kind:procedure"],
    )

    report = assemble_task_memory(
        store,
        query="checkout rollout",
        project_namespace="project:checkout",
    )
    default_recall = store.recall(namespace="project:checkout", query="checkout rollout", limit=5)
    historical_recall = store.recall(
        namespace="project:checkout",
        query="checkout rollout",
        limit=5,
        eligibility="historical",
    )

    actionable = _actionable_ids(report)
    assert predecessor_id not in actionable
    assert str(anchor["id"]) not in actionable
    assert [str(item["id"]) for item in report["procedure_hits"]] == []
    assert [str(item["id"]) for item in report["supporting_hits"]] == []
    suppressed_by_id = {str(entry["id"]): entry for entry in report["suppressed_items"]}
    assert suppressed_by_id[predecessor_id]["reason"] == "superseded_revision"
    assert suppressed_by_id[str(anchor["id"])]["reason"] == "depends_on:ineligible"
    assert suppressed_by_id[str(anchor["id"])]["by_id"] == predecessor_id
    assert successor_id in _ids(default_recall)
    assert {predecessor_id, successor_id} <= set(_ids(historical_recall))
    with store._connect() as conn:
        persisted_anchor = fetch_row_by_id(conn, str(anchor["id"]))
    assert persisted_anchor is not None
    assert f"depends_on: {predecessor_id}" in str(persisted_anchor["content"])
    assert successor_id not in str(persisted_anchor["content"])


@pytest.mark.parametrize("status", ["unsafe", "stale", "replaced"])
def test_relation_expansion_suppresses_dependent_of_ineligible_procedure(
    tmp_path: Path,
    status: str,
) -> None:
    store = _store(tmp_path)
    ineligible = store.store(
        namespace="project:checkout",
        kind="memory",
        title=f"[[Procedure]] {status} midnight migration shortcut",
        content=(
            "record_type: procedure\n"
            "goal: Run the midnight migration shortcut.\n"
            "when_to_use: Never in production work.\n"
            "steps: skip checks | force migrate\n"
            f"procedure_status: {status}\n"
        ),
        tags=["kind:procedure"],
    )
    anchor = store.store(
        namespace="project:checkout",
        kind="memory",
        title="[[Procedure]] release cutover validated path",
        content=(
            "record_type: procedure\n"
            "goal: Run the release cutover procedure.\n"
            "when_to_use: During a release cutover.\n"
            "steps: verify release | run cutover\n"
            "procedure_status: validated\n"
            f"depends_on: {ineligible['id']}\n"
        ),
        tags=["kind:procedure"],
    )

    report = assemble_task_memory(
        store,
        query="release cutover",
        project_namespace="project:checkout",
    )

    actionable = _actionable_ids(report)
    assert str(ineligible["id"]) not in actionable
    assert str(anchor["id"]) not in actionable
    assert [str(item["id"]) for item in report["procedure_hits"]] == []
    suppressed_by_id = {str(entry["id"]): entry for entry in report["suppressed_items"]}
    assert suppressed_by_id[str(ineligible["id"])]["reason"] == f"procedure_status:{status}"
    assert suppressed_by_id[str(anchor["id"])]["reason"] == "depends_on:ineligible"
    assert suppressed_by_id[str(anchor["id"])]["by_id"] == str(ineligible["id"])


def test_relation_expansion_does_not_treat_supports_as_a_hard_dependency(tmp_path: Path) -> None:
    store = _store(tmp_path)
    unsafe = store.store(
        namespace="project:checkout",
        kind="memory",
        title="[[Procedure]] unsafe release support",
        content=_procedure_content("unsafe", "unsafe release support"),
        tags=["kind:procedure"],
    )
    anchor = store.store(
        namespace="project:checkout",
        kind="memory",
        title="[[Procedure]] release cutover supported path",
        content=(
            "record_type: procedure\n"
            "goal: Run the release cutover procedure.\n"
            "when_to_use: During a release cutover.\n"
            "steps: verify release | run cutover\n"
            "procedure_status: validated\n"
            f"supports: {unsafe['id']}\n"
        ),
        tags=["kind:procedure"],
    )

    report = assemble_task_memory(
        store,
        query="release cutover",
        project_namespace="project:checkout",
    )

    assert str(anchor["id"]) in _actionable_ids(report)
    assert [str(item["id"]) for item in report["procedure_hits"]] == [str(anchor["id"])]
    suppressed_by_id = {str(entry["id"]): entry for entry in report["suppressed_items"]}
    assert suppressed_by_id[str(unsafe["id"])]["reason"] == "procedure_status:unsafe"
    assert str(anchor["id"]) not in suppressed_by_id


def test_flat_task_memory_suppresses_ineligible_procedures_without_task_domain(tmp_path: Path) -> None:
    store = _store(tmp_path)
    unsafe = store.store(
        namespace="project:checkout",
        title="Unsafe checkout deployment procedure",
        content=_procedure_content("unsafe", "unsafe"),
        tags=["kind:procedure"],
    )
    validated = store.store(
        namespace="project:checkout",
        title="Validated checkout deployment procedure",
        content=_procedure_content("validated", "validated"),
        tags=["kind:procedure"],
    )

    report = assemble_task_memory(
        store,
        query="checkout deployment procedure",
        project_namespace="project:checkout",
        relation_aware=False,
    )

    actionable = _actionable_ids(report)
    assert str(unsafe["id"]) not in actionable
    assert [str(item["id"]) for item in report["procedure_hits"]] == [str(validated["id"])]
    suppressed_by_id = {str(entry["id"]): str(entry["reason"]) for entry in report["suppressed_items"]}
    assert suppressed_by_id[str(unsafe["id"])] == "procedure_status:unsafe"


def test_flat_supporting_fetch_refills_limit_after_ineligible_records(tmp_path: Path) -> None:
    store = _store(tmp_path)
    predecessor = store.store(
        namespace="project:checkout",
        kind="memory",
        title="Legacy checkout support dependency",
        content="legacy checkout support guidance",
    )
    predecessor_id = str(predecessor["id"])
    store.revise(
        predecessor_id,
        replacement_content="corrected checkout support guidance",
        title="Corrected checkout support dependency",
    )
    unsafe = store.store(
        namespace="project:checkout",
        kind="memory",
        title="[[Procedure]] unsafe checkout support",
        content=_procedure_content("unsafe", "unsafe checkout support"),
        tags=["kind:procedure"],
    )
    eligible_a = store.store(
        namespace="project:checkout",
        kind="memory",
        title="Eligible checkout support A",
        content="current checkout support alpha",
    )
    eligible_b = store.store(
        namespace="project:checkout",
        kind="memory",
        title="Eligible checkout support B",
        content="current checkout support beta",
    )
    anchor = store.store(
        namespace="project:checkout",
        kind="memory",
        title="[[Procedure]] checkout support scan path",
        content=(
            "record_type: procedure\n"
            "goal: Run the checkout support scan.\n"
            "when_to_use: During a checkout support scan.\n"
            "steps: collect support | verify support\n"
            "procedure_status: validated\n"
            f"depends_on: {predecessor_id} | {unsafe['id']}\n"
            f"supports: {eligible_a['id']} | {eligible_b['id']}\n"
        ),
        tags=["kind:procedure"],
    )

    report = assemble_task_memory(
        store,
        query="checkout support scan",
        project_namespace="project:checkout",
        relation_aware=False,
        support_limit=2,
    )

    assert [str(item["id"]) for item in report["procedure_hits"]] == [str(anchor["id"])]
    assert [str(item["id"]) for item in report["supporting_hits"]] == [
        str(eligible_a["id"]),
        str(eligible_b["id"]),
    ]
    supporting_ids = [str(item["id"]) for item in report["supporting_hits"]]
    assert len(supporting_ids) == len(set(supporting_ids))
    suppressed_by_id = {str(entry["id"]): str(entry["reason"]) for entry in report["suppressed_items"]}
    assert suppressed_by_id[predecessor_id] == "superseded_revision"
    assert suppressed_by_id[str(unsafe["id"])] == "procedure_status:unsafe"


@pytest.mark.parametrize("retrieval_mode", ["semantic", "hybrid"])
def test_semantic_and_hybrid_refill_requires_exclusion_aware_second_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    retrieval_mode: str,
) -> None:
    namespace = "project:semantic-refill"
    store = _store(tmp_path)

    def _refill_ineligible_procedure_content(index: int) -> str:
        return (
            "record_type: procedure\n"
            f"goal: Execute zephyrblade deployment runbook variant {index}.\n"
            "when_to_use: During the zephyrblade deployment runbook cutover.\n"
            "steps: zephyrblade deployment runbook | verify | migrate\n"
            "procedure_status: unsafe\n"
            "zephyrblade zephyrblade zephyrblade zephyrblade zephyrblade\n"
        )

    ineligible_ids = [
        str(
            store.store(
                namespace=namespace,
                kind="memory",
                title=f"Zephyrblade unsafe procedure {index}",
                content=_refill_ineligible_procedure_content(index),
                tags=["kind:procedure"],
            )["id"]
        )
        for index in range(22)
    ]
    eligible_ids = [
        str(
            store.store(
                namespace=namespace,
                kind="memory",
                title=f"Zephyrblade eligible note {index}",
                content=(
                    f"zephyrblade deployment runbook eligible note {index} current guidance " + ("padding " * 200)
                ),
            )["id"]
        )
        for index in range(3)
    ]
    _warm_deterministic_semantic_index(store, tmp_path, monkeypatch)
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_RETRIEVAL_MODE", retrieval_mode)

    historical = store.recall(
        namespace=namespace,
        query="zephyrblade deployment runbook",
        kind="memory",
        limit=20,
        eligibility="historical",
    )
    recalled = store.recall(
        namespace=namespace,
        query="zephyrblade deployment runbook",
        kind="memory",
        limit=3,
    )

    historical_ids = _ids(historical)
    assert len(historical_ids) == 20
    assert set(historical_ids) <= set(ineligible_ids)
    returned_ids = _ids(recalled)
    assert set(returned_ids) == set(eligible_ids)
    assert len(returned_ids) == len(set(returned_ids)) == 3
    assert recalled["retrieval"]["mode"] == retrieval_mode
    assert recalled["retrieval"]["semantic_available"] is True
    assert recalled["retrieval"]["suppressed_count"] == 22
    assert recalled["retrieval"]["suppression_reason_counts"] == {"procedure_unsafe": 22}
    receipt_result_ids, exposure_ids = _decoded_receipt_ids(store, recalled)
    assert receipt_result_ids == returned_ids
    assert exposure_ids == returned_ids
