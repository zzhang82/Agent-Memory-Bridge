from pathlib import Path
from typing import Any, cast

from agent_mem_bridge.repository import fetch_row_by_id
from agent_mem_bridge.storage import MemoryStore


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
        include_ineligible=True,
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
        include_ineligible=True,
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
        include_ineligible=True,
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
