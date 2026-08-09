from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_mem_bridge.database_maintenance import rebuild_database_projections
from agent_mem_bridge.retrieval_feedback import decode_recall_receipt, encode_recall_receipt, recall_receipt_hash
from agent_mem_bridge.run_projection import inspect_memory_utility_shadow, rebuild_memory_utility_shadow
from agent_mem_bridge.schema import rotate_database_epoch
from agent_mem_bridge.storage import MemoryStore


def _store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")


def _run(store: MemoryStore, key: str) -> dict[str, object]:
    return store.begin_run(
        workspace_key="project:bridge",
        goal="Exercise receipt-bound memory attribution.",
        idempotency_key=f"begin:{key}",
    )


def _complete_root_work_item(store: MemoryStore, run: dict[str, object], key: str) -> None:
    store.record_run_event(
        workspace_key="project:bridge",
        run_id=str(run["run_id"]),
        work_item_id=str(run["root_work_item_id"]),
        event_type="work_item_completed",
        summary="The root work item completed.",
        idempotency_key=f"event:root-completed:{key}",
    )


def _receipt(
    store: MemoryStore, *, content: str = "Use the durable receipt attribution contract."
) -> dict[str, object]:
    stored = store.store(namespace="project:bridge", content=content, kind="memory")
    recalled = store.recall(namespace="project:bridge", query="receipt attribution", kind="memory", limit=5)
    item = next(item for item in recalled["items"] if item["id"] == stored["id"])
    return {
        "memory_id": str(stored["id"]),
        "result_rank": recalled["items"].index(item) + 1,
        "token": str(recalled["recall_receipt"]["token"]),
    }


def _empty_receipt(store: MemoryStore) -> str:
    recalled = store.recall(
        namespace="project:bridge",
        query="zero-result-receipt-query-that-has-no-matching-memory",
        kind="memory",
        limit=5,
    )
    assert recalled["count"] == 0
    return str(recalled["recall_receipt"]["token"])


def _recalled_event(
    store: MemoryStore, run: dict[str, object], receipt: dict[str, object], key: str
) -> dict[str, object]:
    return store.record_run_event(
        workspace_key="project:bridge",
        run_id=str(run["run_id"]),
        work_item_id=str(run["root_work_item_id"]),
        event_type="memory_recalled",
        summary="The agent received one durable memory exposure.",
        idempotency_key=f"event:recalled:{key}",
        memory_attribution={
            "namespace": "project:bridge",
            "recall_receipt": receipt["token"],
            "items": [{"memory_id": receipt["memory_id"], "result_rank": receipt["result_rank"]}],
        },
    )


def test_memory_recalled_links_a_valid_receipt_without_persisting_the_token(tmp_path: Path) -> None:
    store = _store(tmp_path)
    run = _run(store, "recalled")
    receipt = _receipt(store)

    event = _recalled_event(store, run, receipt, "recalled")
    replay = _recalled_event(store, run, receipt, "recalled")

    assert replay == {**event, "idempotent_replay": True}
    with store._connect() as conn:
        links = conn.execute(
            """
            SELECT event_id, outcome_id, memory_id, exact_content_version, receipt_hash,
                   exposure_rank, feedback_id, relation, review_required
            FROM run_memory_links
            """
        ).fetchall()
        durable_text = "\n".join(
            str(value)
            for row in conn.execute("SELECT payload_json, evidence_json, request_digest FROM run_events").fetchall()
            for value in row
        )
    assert len(links) == 1
    assert links[0]["event_id"] == event["event_id"]
    assert links[0]["outcome_id"] is None
    assert links[0]["memory_id"] == receipt["memory_id"]
    assert links[0]["exposure_rank"] == receipt["result_rank"]
    assert links[0]["receipt_hash"] is not None
    assert links[0]["relation"] == "recalled"
    assert links[0]["review_required"] == 0
    assert str(receipt["token"]) not in durable_text

    with pytest.raises(ValueError, match="different payload"):
        store.record_run_event(
            workspace_key="project:bridge",
            run_id=str(run["run_id"]),
            work_item_id=str(run["root_work_item_id"]),
            event_type="memory_recalled",
            summary="A conflicting retry.",
            idempotency_key="event:recalled:recalled",
            memory_attribution={
                "namespace": "project:bridge",
                "recall_receipt": receipt["token"],
                "items": [{"memory_id": receipt["memory_id"], "result_rank": receipt["result_rank"]}],
            },
        )


def test_zero_result_receipt_records_no_synthetic_memory_links(tmp_path: Path) -> None:
    store = _store(tmp_path)
    run = _run(store, "empty-receipt")
    empty_receipt = _empty_receipt(store)

    with pytest.raises(ValueError, match="receipt_hash does not match"):
        store.record_run_event(
            workspace_key="project:bridge",
            run_id=str(run["run_id"]),
            work_item_id=str(run["root_work_item_id"]),
            event_type="memory_recalled",
            summary="A malformed zero-result receipt payload must not persist.",
            idempotency_key="event:empty-receipt:bad-hash",
            payload={"receipt_hash": "0" * 64},
            memory_attribution={
                "namespace": "project:bridge",
                "recall_receipt": empty_receipt,
                "items": [],
            },
        )
    with store._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM run_events WHERE run_id = ?", (run["run_id"],)).fetchone()[0] == 0
        assert (
            conn.execute("SELECT COUNT(*) FROM run_memory_links WHERE run_id = ?", (run["run_id"],)).fetchone()[0] == 0
        )

    recalled = store.record_run_event(
        workspace_key="project:bridge",
        run_id=str(run["run_id"]),
        work_item_id=str(run["root_work_item_id"]),
        event_type="memory_recalled",
        summary="The signed zero-result recall completed without an exposure.",
        idempotency_key="event:empty-receipt:valid",
        payload={
            "result_ids": [],
            "result_ranks": [],
            "receipt_hash": recall_receipt_hash(empty_receipt),
        },
        memory_attribution={
            "namespace": "project:bridge",
            "recall_receipt": empty_receipt,
            "items": [],
        },
    )
    with store._connect() as conn:
        durable_values = conn.execute(
            "SELECT payload_json, evidence_json, request_digest FROM run_events WHERE event_id = ?",
            (recalled["event_id"],),
        ).fetchone()
        link_count = conn.execute(
            "SELECT COUNT(*) FROM run_memory_links WHERE run_id = ?", (run["run_id"],)
        ).fetchone()[0]
    assert durable_values is not None
    assert empty_receipt not in "\n".join(str(value) for value in durable_values)
    assert link_count == 0

    nonempty_receipt = _receipt(store)
    with pytest.raises(ValueError, match="empty memory_attribution.items requires a zero-result recall receipt"):
        store.record_run_event(
            workspace_key="project:bridge",
            run_id=str(run["run_id"]),
            work_item_id=str(run["root_work_item_id"]),
            event_type="memory_recalled",
            summary="A nonempty receipt cannot claim an empty exposure set.",
            idempotency_key="event:empty-receipt:nonempty-source",
            memory_attribution={
                "namespace": "project:bridge",
                "recall_receipt": nonempty_receipt["token"],
                "items": [],
            },
        )
    with store._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM run_events WHERE run_id = ?", (run["run_id"],)).fetchone()[0] == 1
        assert (
            conn.execute("SELECT COUNT(*) FROM run_memory_links WHERE run_id = ?", (run["run_id"],)).fetchone()[0] == 0
        )

    source_run = _run(store, "empty-source")
    source_recalled = _recalled_event(store, source_run, nonempty_receipt, "empty-source")
    with store._connect() as conn:
        before_counts = (
            conn.execute("SELECT COUNT(*) FROM run_events").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM run_memory_links").fetchone()[0],
        )
    for event_type, attribution in (
        (
            "memory_applied",
            {"source_recall_event_id": source_recalled["event_id"], "items": []},
        ),
        (
            "memory_rejected",
            {"source_recall_event_id": source_recalled["event_id"], "items": []},
        ),
        ("memory_applied", {"items": []}),
        ("memory_rejected", {"items": []}),
    ):
        with pytest.raises(ValueError, match="between 1 and 32"):
            store.record_run_event(
                workspace_key="project:bridge",
                run_id=str(source_run["run_id"]),
                work_item_id=str(source_run["root_work_item_id"]),
                event_type=event_type,
                summary="Applied and rejected attribution requires an explicit selection.",
                idempotency_key=f"event:empty-selection:{event_type}:{len(attribution)}",
                memory_attribution=attribution,
            )
    with store._connect() as conn:
        after_counts = (
            conn.execute("SELECT COUNT(*) FROM run_events").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM run_memory_links").fetchone()[0],
        )
    assert after_counts == before_counts


def test_invalid_receipt_attribution_is_atomic_and_memory_events_require_it(tmp_path: Path) -> None:
    store = _store(tmp_path)
    run = _run(store, "invalid")
    receipt = _receipt(store)
    decoded = decode_recall_receipt(str(receipt["token"]), secret=store.recall_receipt_secret)
    decoded["expires_at"] = "2000-01-01T00:00:00+00:00"
    expired = encode_recall_receipt(decoded, secret=store.recall_receipt_secret)
    wrong_version = decode_recall_receipt(str(receipt["token"]), secret=store.recall_receipt_secret)
    wrong_version["exposure_set"][0]["exact_content_hash"] = "0" * 64
    wrong_version["exposure_set"][0]["content_version"] = "0" * 64
    signed_wrong_version = encode_recall_receipt(wrong_version, secret=store.recall_receipt_secret)
    token = str(receipt["token"])
    tampered = f"{token[:-1]}{'A' if token[-1] != 'A' else 'B'}"
    invalid_attributions = (
        {
            "namespace": "project:bridge",
            "recall_receipt": tampered,
            "items": [{"memory_id": receipt["memory_id"], "result_rank": receipt["result_rank"]}],
        },
        {
            "namespace": "project:other",
            "recall_receipt": receipt["token"],
            "items": [{"memory_id": receipt["memory_id"], "result_rank": receipt["result_rank"]}],
        },
        {
            "namespace": "project:bridge",
            "recall_receipt": expired,
            "items": [{"memory_id": receipt["memory_id"], "result_rank": receipt["result_rank"]}],
        },
        {
            "namespace": "project:bridge",
            "recall_receipt": receipt["token"],
            "items": [{"memory_id": receipt["memory_id"], "result_rank": 99}],
        },
        {
            "namespace": "project:bridge",
            "recall_receipt": receipt["token"],
            "items": [{"memory_id": "missing-memory", "result_rank": receipt["result_rank"]}],
        },
        {
            "namespace": "project:bridge",
            "recall_receipt": signed_wrong_version,
            "items": [{"memory_id": receipt["memory_id"], "result_rank": receipt["result_rank"]}],
        },
    )
    for index, attribution in enumerate(invalid_attributions):
        with pytest.raises(ValueError, match="invalid recall receipt"):
            store.record_run_event(
                workspace_key="project:bridge",
                run_id=str(run["run_id"]),
                work_item_id=str(run["root_work_item_id"]),
                event_type="memory_recalled",
                summary="This invalid receipt must not persist an event.",
                idempotency_key=f"event:invalid:{index}",
                memory_attribution=attribution,
            )
    with pytest.raises(ValueError, match="memory_attribution is required"):
        store.record_run_event(
            workspace_key="project:bridge",
            run_id=str(run["run_id"]),
            work_item_id=str(run["root_work_item_id"]),
            event_type="memory_applied",
            summary="A payload alone must not claim application.",
            idempotency_key="event:missing-attribution",
        )
    with pytest.raises(ValueError, match="rejects field: recall_receipt"):
        store.record_run_event(
            workspace_key="project:bridge",
            run_id=str(run["run_id"]),
            work_item_id=str(run["root_work_item_id"]),
            event_type="observation",
            summary="Receipt tokens cannot be copied into arbitrary event payloads.",
            idempotency_key="event:payload-token",
            payload={"recall_receipt": receipt["token"]},
        )
    with store._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM run_events").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM run_memory_links").fetchone()[0] == 0
        rotate_database_epoch(conn)
        conn.commit()
    with pytest.raises(ValueError, match="database epoch mismatch"):
        store.record_run_event(
            workspace_key="project:bridge",
            run_id=str(run["run_id"]),
            work_item_id=str(run["root_work_item_id"]),
            event_type="memory_recalled",
            summary="A receipt from a prior epoch cannot be attributed.",
            idempotency_key="event:old-epoch",
            memory_attribution={
                "namespace": "project:bridge",
                "recall_receipt": receipt["token"],
                "items": [{"memory_id": receipt["memory_id"], "result_rank": receipt["result_rank"]}],
            },
        )


def test_source_and_manual_attribution_validate_feedback_and_shadow_counters(tmp_path: Path) -> None:
    store = _store(tmp_path)
    run = _run(store, "source")
    receipt = _receipt(store)
    recalled = _recalled_event(store, run, receipt, "source")
    feedback = store.feedback(
        namespace="project:bridge",
        recall_receipt=str(receipt["token"]),
        memory_id=str(receipt["memory_id"]),
        result_rank=int(receipt["result_rank"]),
        outcome="helpful",
    )
    unrelated_receipt = _receipt(store, content="Use a separate receipt attribution memory for mismatch validation.")
    unrelated_feedback = store.feedback(
        namespace="project:bridge",
        recall_receipt=str(unrelated_receipt["token"]),
        memory_id=str(unrelated_receipt["memory_id"]),
        result_rank=int(unrelated_receipt["result_rank"]),
        outcome="helpful",
    )
    with pytest.raises(ValueError, match="does not match the recalled memory exposure"):
        store.record_run_event(
            workspace_key="project:bridge",
            run_id=str(run["run_id"]),
            work_item_id=str(run["root_work_item_id"]),
            event_type="memory_applied",
            summary="A feedback head for another exposure must be rejected.",
            idempotency_key="event:feedback-mismatch",
            memory_attribution={
                "source_recall_event_id": recalled["event_id"],
                "items": [
                    {
                        "memory_id": receipt["memory_id"],
                        "result_rank": receipt["result_rank"],
                        "feedback_id": unrelated_feedback["feedback_id"],
                    }
                ],
            },
        )
    applied = store.record_run_event(
        workspace_key="project:bridge",
        run_id=str(run["run_id"]),
        work_item_id=str(run["root_work_item_id"]),
        event_type="memory_applied",
        summary="The agent explicitly applied the recalled memory.",
        idempotency_key="event:applied",
        memory_attribution={
            "source_recall_event_id": recalled["event_id"],
            "items": [
                {
                    "memory_id": receipt["memory_id"],
                    "result_rank": receipt["result_rank"],
                    "feedback_id": feedback["feedback_id"],
                }
            ],
        },
    )
    source_rejected = store.record_run_event(
        workspace_key="project:bridge",
        run_id=str(run["run_id"]),
        work_item_id=str(run["root_work_item_id"]),
        event_type="memory_rejected",
        summary="The agent explicitly rejected the same source-linked exposure.",
        idempotency_key="event:source-rejected",
        memory_attribution={
            "source_recall_event_id": recalled["event_id"],
            "items": [{"memory_id": receipt["memory_id"], "result_rank": receipt["result_rank"]}],
        },
    )
    with store._connect() as conn:
        exact_content_version = str(
            conn.execute("SELECT exact_content_hash FROM memories WHERE id = ?", (receipt["memory_id"],)).fetchone()[0]
        )
    manual = store.record_run_event(
        workspace_key="project:bridge",
        run_id=str(run["run_id"]),
        work_item_id=str(run["root_work_item_id"]),
        event_type="memory_rejected",
        summary="The agent separately recorded a manual rejection for review.",
        idempotency_key="event:manual-rejected",
        memory_attribution={
            "items": [{"memory_id": receipt["memory_id"], "exact_content_version": exact_content_version}],
        },
    )
    with pytest.raises(ValueError, match="was not exposed"):
        store.record_run_event(
            workspace_key="project:bridge",
            run_id=str(run["run_id"]),
            work_item_id=str(run["root_work_item_id"]),
            event_type="memory_rejected",
            summary="This must not apply every recalled memory implicitly.",
            idempotency_key="event:not-exposed",
            memory_attribution={
                "source_recall_event_id": recalled["event_id"],
                "items": [{"memory_id": receipt["memory_id"], "result_rank": 99}],
            },
        )

    _complete_root_work_item(store, run, "source")
    store.complete_run(
        workspace_key="project:bridge",
        run_id=str(run["run_id"]),
        outcome="verified_success",
        evaluator_type="human",
        evidence=[{"kind": "review", "reference": "review:source-memory"}],
        idempotency_key="outcome:source",
    )
    with store._connect() as conn:
        before = inspect_memory_utility_shadow(conn)
        assert before["ok"] is False
        rebuilt = rebuild_memory_utility_shadow(conn, computed_at="2026-08-01T00:00:00+00:00")
        assert rebuilt == {"memory_version_count": 1}
        assert inspect_memory_utility_shadow(conn)["ok"] is True
        links = conn.execute(
            "SELECT event_id, relation, receipt_hash, exposure_rank, feedback_id, review_required FROM run_memory_links ORDER BY event_id"
        ).fetchall()
        shadow = conn.execute("SELECT * FROM memory_utility_shadow").fetchone()
    assert {row["event_id"] for row in links} >= {
        applied["event_id"],
        source_rejected["event_id"],
        manual["event_id"],
    }
    assert any(row["relation"] == "applied" and row["feedback_id"] == feedback["feedback_id"] for row in links)
    assert any(
        row["event_id"] == source_rejected["event_id"]
        and row["relation"] == "rejected"
        and row["receipt_hash"] is not None
        and row["review_required"] == 0
        for row in links
    )
    assert any(
        row["event_id"] == manual["event_id"]
        and row["relation"] == "rejected"
        and row["receipt_hash"] is None
        and row["exposure_rank"] is None
        and row["review_required"] == 1
        for row in links
    )
    assert shadow["helpful_count"] == 1
    assert shadow["supporting_run_count"] == 0
    assert shadow["shadow_score"] == 0.0

    correction = store.feedback(
        namespace="project:bridge",
        recall_receipt=str(receipt["token"]),
        memory_id=str(receipt["memory_id"]),
        result_rank=int(receipt["result_rank"]),
        outcome="outdated",
        reason="A reviewed correction supersedes the prior vote.",
        feedback_type="correction",
        supersedes_feedback_id=int(feedback["feedback_id"]),
    )
    second_run = _run(store, "stale-feedback")
    second_recalled = _recalled_event(store, second_run, receipt, "stale-feedback")
    with pytest.raises(ValueError, match="current effective feedback head"):
        store.record_run_event(
            workspace_key="project:bridge",
            run_id=str(second_run["run_id"]),
            work_item_id=str(second_run["root_work_item_id"]),
            event_type="memory_applied",
            summary="A superseded feedback head cannot receive new attribution.",
            idempotency_key="event:stale-feedback",
            memory_attribution={
                "source_recall_event_id": second_recalled["event_id"],
                "items": [
                    {
                        "memory_id": receipt["memory_id"],
                        "result_rank": receipt["result_rank"],
                        "feedback_id": feedback["feedback_id"],
                    }
                ],
            },
        )
    assert correction["effective_vote"] is True
    repaired = rebuild_database_projections(store.db_path)
    assert repaired["ok"] is True
    assert repaired["memory_utility_shadow_rebuilt_count"] == 0


def test_attribution_rejects_caller_managed_fields_and_keeps_memory_authority_unchanged(tmp_path: Path) -> None:
    store = _store(tmp_path)
    run = _run(store, "caller-fields")
    receipt = _receipt(store)
    with store._connect() as conn:
        memories_before = [tuple(row) for row in conn.execute("SELECT * FROM memories ORDER BY rowid").fetchall()]
    with pytest.raises(ValueError, match="server-managed"):
        store.record_run_event(
            workspace_key="project:bridge",
            run_id=str(run["run_id"]),
            work_item_id=str(run["root_work_item_id"]),
            event_type="memory_recalled",
            summary="Caller-owned link fields are rejected.",
            idempotency_key="event:caller-fields",
            memory_attribution={
                "namespace": "project:bridge",
                "recall_receipt": receipt["token"],
                "relation": "applied",
                "items": [{"memory_id": receipt["memory_id"], "result_rank": receipt["result_rank"]}],
            },
        )
    with store._connect() as conn:
        memories_after = [tuple(row) for row in conn.execute("SELECT * FROM memories ORDER BY rowid").fetchall()]
    assert json.dumps(memories_after, default=str) == json.dumps(memories_before, default=str)


def test_memory_utility_shadow_counts_only_current_effective_feedback_with_terminal_outcomes(tmp_path: Path) -> None:
    store = _store(tmp_path)
    cases = (
        ("helpful", "verified_success", "human", 1, 0, 0, 0, 0, 0),
        ("misleading", "failed", "agent", 0, 1, 0, 0, 0, 1),
        ("outdated", "user_corrected", "agent", 0, 0, 1, 0, 0, 1),
        ("not_used", "partial_success", "agent", 0, 0, 0, 1, 0, 0),
    )
    memory_ids: dict[str, str] = {}
    for feedback_outcome, run_outcome, evaluator_type, *_ in cases:
        run = _run(store, f"counter:{feedback_outcome}")
        receipt = _receipt(store, content=f"Receipt attribution counter evidence: {feedback_outcome}.")
        recalled = _recalled_event(store, run, receipt, f"counter:{feedback_outcome}")
        feedback_kwargs: dict[str, object] = {}
        if feedback_outcome in {"misleading", "outdated"}:
            feedback_kwargs["reason"] = "Reviewed counter evidence."
        feedback = store.feedback(
            namespace="project:bridge",
            recall_receipt=str(receipt["token"]),
            memory_id=str(receipt["memory_id"]),
            result_rank=int(receipt["result_rank"]),
            outcome=feedback_outcome,
            **feedback_kwargs,
        )
        store.record_run_event(
            workspace_key="project:bridge",
            run_id=str(run["run_id"]),
            work_item_id=str(run["root_work_item_id"]),
            event_type="memory_applied",
            summary="Record one selected source-linked memory application.",
            idempotency_key=f"event:counter:{feedback_outcome}",
            memory_attribution={
                "source_recall_event_id": recalled["event_id"],
                "items": [
                    {
                        "memory_id": receipt["memory_id"],
                        "result_rank": receipt["result_rank"],
                        "feedback_id": feedback["feedback_id"],
                    }
                ],
            },
        )
        outcome_kwargs: dict[str, object] = {}
        if run_outcome == "verified_success":
            outcome_kwargs["evidence"] = [{"kind": "review", "reference": "review:counter"}]
        _complete_root_work_item(store, run, f"counter:{feedback_outcome}")
        store.complete_run(
            workspace_key="project:bridge",
            run_id=str(run["run_id"]),
            outcome=run_outcome,
            evaluator_type=evaluator_type,
            idempotency_key=f"outcome:counter:{feedback_outcome}",
            **outcome_kwargs,
        )
        memory_ids[feedback_outcome] = str(receipt["memory_id"])

    with store._connect() as conn:
        rebuild_memory_utility_shadow(conn, computed_at="2026-08-01T00:00:00+00:00")
        rows = {str(row["memory_id"]): row for row in conn.execute("SELECT * FROM memory_utility_shadow").fetchall()}
    for feedback_outcome, _, _, helpful, misleading, outdated, not_used, supporting, contradicting in cases:
        row = rows[memory_ids[feedback_outcome]]
        assert (
            row["helpful_count"],
            row["misleading_count"],
            row["outdated_count"],
            row["not_used_count"],
            row["supporting_run_count"],
            row["contradicting_run_count"],
            row["shadow_score"],
        ) == (helpful, misleading, outdated, not_used, supporting, contradicting, 0.0)


def test_memory_lifecycle_payload_cannot_contradict_canonical_memory_links(tmp_path: Path) -> None:
    store = _store(tmp_path)
    run = _run(store, "payload-symmetry")
    receipt = _receipt(store)
    canonical_ids = [receipt["memory_id"]]
    canonical_ranks = [receipt["result_rank"]]
    canonical_receipt_hash = recall_receipt_hash(str(receipt["token"]))
    base_attribution = {
        "namespace": "project:bridge",
        "recall_receipt": receipt["token"],
        "items": [{"memory_id": receipt["memory_id"], "result_rank": receipt["result_rank"]}],
    }
    for index, payload, message in (
        (0, {"result_ids": ["wrong-memory"]}, "result_ids do not match"),
        (1, {"nested": {"result_ranks": [99]}}, "result_ranks do not match"),
        (2, {"receipt_hash": "0" * 64}, "receipt_hash does not match"),
        (3, {"nested": {"relation": "applied"}}, "server-managed"),
    ):
        with pytest.raises(ValueError, match=message):
            store.record_run_event(
                workspace_key="project:bridge",
                run_id=str(run["run_id"]),
                work_item_id=str(run["root_work_item_id"]),
                event_type="memory_recalled",
                summary="Mismatched payload evidence must not become durable authority.",
                idempotency_key=f"event:payload-mismatch:{index}",
                payload=payload,
                memory_attribution=base_attribution,
            )
        with store._connect() as conn:
            assert conn.execute("SELECT COUNT(*) FROM run_events").fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM run_memory_links").fetchone()[0] == 0

    recalled = store.record_run_event(
        workspace_key="project:bridge",
        run_id=str(run["run_id"]),
        work_item_id=str(run["root_work_item_id"]),
        event_type="memory_recalled",
        summary="Matched supplemental payload fields remain diagnostic only.",
        idempotency_key="event:payload-matched-recall",
        payload={
            "trigger": {
                "result_ids": canonical_ids,
                "result_ranks": canonical_ranks,
                "receipt_hash": canonical_receipt_hash,
            },
            "query_digest": "q" * 64,
            "cooldown_seconds": 30,
            "error_digest": "e" * 64,
        },
        memory_attribution=base_attribution,
    )
    source_attribution = {
        "source_recall_event_id": recalled["event_id"],
        "items": [{"memory_id": receipt["memory_id"], "result_rank": receipt["result_rank"]}],
    }
    with store._connect() as conn:
        before = (
            conn.execute("SELECT COUNT(*) FROM run_events").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM run_memory_links").fetchone()[0],
            str(
                conn.execute(
                    "SELECT exact_content_hash FROM memories WHERE id = ?", (receipt["memory_id"],)
                ).fetchone()[0]
            ),
        )
    for index, payload, message in (
        (0, {"source_recall_event_id": "evt_missing"}, "recall event does not match"),
        (1, {"nested": {"receipt_hash": "f" * 64}}, "receipt_hash does not match"),
    ):
        with pytest.raises(ValueError, match=message):
            store.record_run_event(
                workspace_key="project:bridge",
                run_id=str(run["run_id"]),
                work_item_id=str(run["root_work_item_id"]),
                event_type="memory_applied",
                summary="Source payload must agree with its recalled links.",
                idempotency_key=f"event:source-payload-mismatch:{index}",
                payload=payload,
                memory_attribution=source_attribution,
            )
        with store._connect() as conn:
            assert conn.execute("SELECT COUNT(*) FROM run_events").fetchone()[0] == before[0]
            assert conn.execute("SELECT COUNT(*) FROM run_memory_links").fetchone()[0] == before[1]

    store.record_run_event(
        workspace_key="project:bridge",
        run_id=str(run["run_id"]),
        work_item_id=str(run["root_work_item_id"]),
        event_type="memory_applied",
        summary="Matched source fields and harness trigger metadata remain diagnostic only.",
        idempotency_key="event:source-payload-matched",
        payload={
            "source_event_id": "evt_harness_trigger",
            "source_recall_event_id": recalled["event_id"],
            "receipt_hash": canonical_receipt_hash,
            "result_ids": canonical_ids,
            "result_ranks": canonical_ranks,
            "query_digest": "q" * 64,
            "cooldown_seconds": 30,
            "error_digest": "e" * 64,
        },
        memory_attribution=source_attribution,
    )
    with store._connect() as conn:
        after_source = (
            conn.execute("SELECT COUNT(*) FROM run_events").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM run_memory_links").fetchone()[0],
        )

    with pytest.raises(ValueError, match="manual memory_attribution must not claim receipt_hash"):
        store.record_run_event(
            workspace_key="project:bridge",
            run_id=str(run["run_id"]),
            work_item_id=str(run["root_work_item_id"]),
            event_type="memory_rejected",
            summary="Manual attribution cannot claim receipt-bound exposure evidence.",
            idempotency_key="event:manual-payload-mismatch",
            payload={"nested": {"receipt_hash": canonical_receipt_hash}},
            memory_attribution={
                "items": [{"memory_id": receipt["memory_id"], "exact_content_version": before[2]}],
            },
        )
    with store._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM run_events").fetchone()[0] == after_source[0]
        assert conn.execute("SELECT COUNT(*) FROM run_memory_links").fetchone()[0] == after_source[1]
