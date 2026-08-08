from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from agent_mem_bridge import run_ledger
from agent_mem_bridge.run_projection import inspect_run_projections, rebuild_run_projections
from agent_mem_bridge.storage import MemoryStore


def _store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")


def _complete_root_work_item(store: MemoryStore, run: dict[str, object], key: str) -> dict[str, object]:
    return store.record_run_event(
        workspace_key="project:bridge",
        run_id=str(run["run_id"]),
        work_item_id=str(run["root_work_item_id"]),
        event_type="work_item_completed",
        summary="The root work item completed.",
        idempotency_key=f"event:root-completed:{key}",
    )


def test_run_lifecycle_is_server_minted_stateless_and_idempotent(tmp_path: Path) -> None:
    store = _store(tmp_path)
    started = store.begin_run(
        workspace_key="project:bridge",
        goal="Build a closed-loop episode ledger.",
        idempotency_key="begin:episode-ledger",
        agent_id="codex-worker-1",
        thread_id="thread-1",
        memory_scopes=["project", "task"],
        budget={"tokens": 1000},
    )

    assert started["run_id"].startswith("run_")
    assert started["root_work_item_id"].startswith("work_")
    assert started["initial_sequence"] == 0
    replay = store.begin_run(
        workspace_key="project:bridge",
        goal="Build a closed-loop episode ledger.",
        idempotency_key="begin:episode-ledger",
        agent_id="codex-worker-1",
        thread_id="thread-1",
        memory_scopes=["project", "task"],
        budget={"tokens": 1000},
    )
    assert replay == {**started, "idempotent_replay": True}
    with pytest.raises(ValueError, match="different payload"):
        store.begin_run(
            workspace_key="project:bridge",
            goal="A conflicting goal.",
            idempotency_key="begin:episode-ledger",
        )

    event = store.record_run_event(
        workspace_key="project:bridge",
        run_id=started["run_id"],
        work_item_id=started["root_work_item_id"],
        event_type="checkpoint",
        summary="The schema checkpoint passed.",
        payload={"check": "schema", "passed": True},
        idempotency_key="event:schema-checkpoint",
    )
    event_replay = store.record_run_event(
        workspace_key="project:bridge",
        run_id=started["run_id"],
        work_item_id=started["root_work_item_id"],
        event_type="checkpoint",
        summary="The schema checkpoint passed.",
        payload={"check": "schema", "passed": True},
        idempotency_key="event:schema-checkpoint",
    )
    assert event_replay == {**event, "idempotent_replay": True}
    with pytest.raises(ValueError, match="different payload"):
        store.record_run_event(
            workspace_key="project:bridge",
            run_id=started["run_id"],
            work_item_id=started["root_work_item_id"],
            event_type="checkpoint",
            summary="Conflicting checkpoint.",
            idempotency_key="event:schema-checkpoint",
        )

    restored = store.get_run(workspace_key="project:bridge", run_id=started["run_id"])
    assert restored["run"]["status"] == "active"
    assert restored["run"]["last_sequence"] == 1
    assert restored["run"]["memory_scopes"] == ["project", "task"]
    assert restored["events"][0]["event_id"] == event["event_id"]


def test_work_item_creation_and_sequence_pagination_are_explicit(tmp_path: Path) -> None:
    store = _store(tmp_path)
    started = store.begin_run(
        workspace_key="project:bridge",
        goal="Exercise a parent-child work tree.",
        idempotency_key="begin:tree",
    )
    store.record_run_event(
        workspace_key="project:bridge",
        run_id=started["run_id"],
        work_item_id=started["root_work_item_id"],
        event_type="plan_created",
        summary="Create one child work item.",
        idempotency_key="event:plan",
    )
    child = store.record_run_event(
        workspace_key="project:bridge",
        run_id=started["run_id"],
        event_type="work_item_started",
        summary="Start the child work item.",
        parent_work_item_id=started["root_work_item_id"],
        work_item_goal="Implement the child slice.",
        owner_agent_id="worker-2",
        idempotency_key="event:start-child",
    )
    child_replay = store.record_run_event(
        workspace_key="project:bridge",
        run_id=started["run_id"],
        event_type="work_item_started",
        summary="Start the child work item.",
        parent_work_item_id=started["root_work_item_id"],
        work_item_goal="Implement the child slice.",
        owner_agent_id="worker-2",
        idempotency_key="event:start-child",
    )
    assert child["created_work_item"] is True
    assert child_replay["work_item_id"] == child["work_item_id"]
    assert child_replay["created_work_item"] is True
    store.record_run_event(
        workspace_key="project:bridge",
        run_id=started["run_id"],
        work_item_id=child["work_item_id"],
        event_type="decision",
        summary="Keep the child implementation bounded.",
        idempotency_key="event:child-decision",
    )

    page_one = store.get_run(
        workspace_key="project:bridge",
        run_id=started["run_id"],
        since_sequence=1,
        event_limit=1,
    )
    assert [event["sequence"] for event in page_one["events"]] == [2]
    assert page_one["has_more"] is True
    page_two = store.get_run(
        workspace_key="project:bridge",
        run_id=started["run_id"],
        since_sequence=page_one["next_sequence"],
        event_limit=10,
    )
    assert [event["sequence"] for event in page_two["events"]] == [3]
    assert page_two["has_more"] is False
    child_state = next(item for item in page_two["work_items"] if item["work_item_id"] == child["work_item_id"])
    assert child_state["parent_work_item_id"] == started["root_work_item_id"]
    assert child_state["status"] == "active"


def test_completion_evidence_corrections_and_workspace_isolation(tmp_path: Path) -> None:
    store = _store(tmp_path)
    started = store.begin_run(
        workspace_key="project:bridge",
        goal="Validate completion evidence.",
        idempotency_key="begin:outcome",
    )
    with pytest.raises(ValueError, match="verified_success requires"):
        store.complete_run(
            workspace_key="project:bridge",
            run_id=started["run_id"],
            outcome="verified_success",
            evaluator_type="agent",
            idempotency_key="outcome:invalid-self-report",
        )
    _complete_root_work_item(store, started, "outcome")

    initial = store.complete_run(
        workspace_key="project:bridge",
        run_id=started["run_id"],
        outcome="unverified",
        evaluator_type="agent",
        evidence=[{"kind": "self_report"}],
        idempotency_key="outcome:initial",
    )
    correction = store.complete_run(
        workspace_key="project:bridge",
        run_id=started["run_id"],
        outcome="verified_success",
        evaluator_type="human",
        evidence=[{"kind": "review", "reference": "review-42"}],
        supersedes_outcome_id=initial["outcome_id"],
        idempotency_key="outcome:reviewed",
    )
    restored = store.get_run(workspace_key="project:bridge", run_id=started["run_id"])
    assert restored["run"]["status"] == "completed"
    assert restored["outcome"]["outcome_id"] == correction["outcome_id"]
    assert restored["outcome"]["supersedes_outcome_id"] == initial["outcome_id"]
    with store._connect() as conn:
        assert (
            conn.execute("SELECT COUNT(*) FROM run_outcomes WHERE run_id = ?", (started["run_id"],)).fetchone()[0] == 2
        )

    with pytest.raises(ValueError, match="cannot accept new events"):
        store.record_run_event(
            workspace_key="project:bridge",
            run_id=started["run_id"],
            work_item_id=started["root_work_item_id"],
            event_type="checkpoint",
            summary="Too late.",
            idempotency_key="event:after-completion",
        )
    with pytest.raises(ValueError, match="declared workspace"):
        store.get_run(workspace_key="project:other", run_id=started["run_id"])


def test_regression_target_must_be_distinct_current_verified_success_in_workspace(tmp_path: Path) -> None:
    store = _store(tmp_path)

    def begin(workspace_key: str, label: str) -> dict[str, object]:
        return store.begin_run(
            workspace_key=workspace_key,
            goal=f"Exercise regression target validation: {label}.",
            idempotency_key=f"begin:regression:{label}",
        )

    def complete_verified(
        run: dict[str, object], label: str, *, workspace_key: str = "project:bridge"
    ) -> dict[str, object]:
        store.record_run_event(
            workspace_key=workspace_key,
            run_id=str(run["run_id"]),
            work_item_id=str(run["root_work_item_id"]),
            event_type="work_item_completed",
            summary="The root work item completed.",
            idempotency_key=f"event:root-completed:{label}",
        )
        return store.complete_run(
            workspace_key=workspace_key,
            run_id=str(run["run_id"]),
            outcome="verified_success",
            evaluator_type="human",
            evidence=[{"kind": "review", "reference": label}],
            idempotency_key=f"outcome:verified:{label}",
        )

    def state_snapshot(run_id: str) -> tuple[int, tuple[object, ...]]:
        with store._connect() as conn:
            outcome_count = int(
                conn.execute("SELECT COUNT(*) FROM run_outcomes WHERE run_id = ?", (run_id,)).fetchone()[0]
            )
            projection = conn.execute(
                """
                SELECT status, outcome_id, ended_at, termination_reason
                FROM run_state_projection
                WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
        assert projection is not None
        return outcome_count, tuple(projection)

    def assert_rejected(
        target_run_id: str,
        label: str,
        *,
        source_run: dict[str, object] | None = None,
        supersedes: str | None = None,
    ) -> None:
        source = source_run or begin("project:bridge", f"rejected-{label}")
        source_run_id = str(source["run_id"])
        if source_run is None:
            _complete_root_work_item(store, source, f"rejected-{label}")
        before = state_snapshot(source_run_id)
        with pytest.raises(ValueError, match="regression_of_run_id does not exist"):
            store.complete_run(
                workspace_key="project:bridge",
                run_id=source_run_id,
                outcome="regression",
                evaluator_type="agent",
                supersedes_outcome_id=supersedes,
                regression_of_run_id=target_run_id,
                idempotency_key=f"outcome:rejected-regression:{label}",
            )
        assert state_snapshot(source_run_id) == before

    verified_target = begin("project:bridge", "verified-target")
    verified_outcome = complete_verified(verified_target, "verified-target")
    regressing_run = begin("project:bridge", "same-workspace")
    _complete_root_work_item(store, regressing_run, "same-workspace")
    same_workspace = store.complete_run(
        workspace_key="project:bridge",
        run_id=str(regressing_run["run_id"]),
        outcome="regression",
        evaluator_type="agent",
        regression_of_run_id=str(verified_target["run_id"]),
        idempotency_key="outcome:same-workspace-regression",
    )
    assert same_workspace["regression_of_run_id"] == verified_target["run_id"]

    assert_rejected(
        str(verified_target["run_id"]),
        "self",
        source_run=verified_target,
        supersedes=str(verified_outcome["outcome_id"]),
    )

    active_target = begin("project:bridge", "active-target")
    assert_rejected(str(active_target["run_id"]), "active")

    unverified_target = begin("project:bridge", "unverified-target")
    _complete_root_work_item(store, unverified_target, "unverified-target")
    store.complete_run(
        workspace_key="project:bridge",
        run_id=str(unverified_target["run_id"]),
        outcome="unverified",
        evaluator_type="agent",
        idempotency_key="outcome:unverified-target",
    )
    assert_rejected(str(unverified_target["run_id"]), "unverified")

    failed_target = begin("project:bridge", "failed-target")
    _complete_root_work_item(store, failed_target, "failed-target")
    store.complete_run(
        workspace_key="project:bridge",
        run_id=str(failed_target["run_id"]),
        outcome="failed",
        evaluator_type="agent",
        idempotency_key="outcome:failed-target",
    )
    assert_rejected(str(failed_target["run_id"]), "failed")

    superseded_target = begin("project:bridge", "superseded-target")
    initial_success = complete_verified(superseded_target, "superseded-target")
    store.complete_run(
        workspace_key="project:bridge",
        run_id=str(superseded_target["run_id"]),
        outcome="unverified",
        evaluator_type="agent",
        supersedes_outcome_id=str(initial_success["outcome_id"]),
        idempotency_key="outcome:supersede-verified-target",
    )
    assert_rejected(str(superseded_target["run_id"]), "superseded")

    other_workspace_target = begin("project:other", "other-workspace-target")
    complete_verified(other_workspace_target, "other-workspace-target", workspace_key="project:other")
    assert_rejected(str(other_workspace_target["run_id"]), "cross-workspace")


def test_run_payload_privacy_validation_rejects_nested_normalized_reasoning_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    started = store.begin_run(
        workspace_key="project:bridge",
        goal="Reject raw hidden reasoning.",
        idempotency_key="begin:privacy",
    )
    for index, payload in enumerate(
        (
            {"nested": {"raw_cot": "private"}},
            {"nested": {"chain-of-thought": "private"}},
            {"nested": {"transcript": "private"}},
            {"nested": {"messages": ["private"]}},
            {"nested": {"hidden_reasoning": "private"}},
            {"nested": {"reasoning-text": "private"}},
            {"nested": {"Thought_Process": "private"}},
            {"nested": {"ANALYSIS": "private"}},
            {"nested": {"reasoning": "private"}},
        )
    ):
        with pytest.raises(ValueError, match="durable run structured data rejects field"):
            store.record_run_event(
                workspace_key="project:bridge",
                run_id=started["run_id"],
                work_item_id=started["root_work_item_id"],
                event_type="observation",
                summary="A bounded observation.",
                payload=payload,
                idempotency_key=f"event:private:{index}",
            )
    allowed = store.record_run_event(
        workspace_key="project:bridge",
        run_id=started["run_id"],
        work_item_id=started["root_work_item_id"],
        event_type="observation",
        summary="A factual digest is permitted.",
        payload={"analysis_digest": "a" * 64},
        idempotency_key="event:analysis-digest",
    )
    assert allowed["sequence"] == 1
    for key, value in (
        ("evidence", [{"nested": {"analysis": "private"}}]),
        ("metrics", {"nested": {"thought-process": "private"}}),
    ):
        with pytest.raises(ValueError, match="durable run structured data rejects field"):
            store.complete_run(
                workspace_key="project:bridge",
                run_id=started["run_id"],
                outcome="unverified",
                evaluator_type="agent",
                idempotency_key=f"outcome:private:{key}",
                **{key: value},
            )
    with pytest.raises(ValueError, match="durable run structured data rejects field"):
        store.begin_run(
            workspace_key="project:bridge",
            goal="Reject private budget metadata.",
            idempotency_key="begin:private-budget",
            budget={"Reasoning-Text": "private"},
        )
    with store._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM run_events").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM run_outcomes").fetchone()[0] == 0

    class FakeConnection:
        def __init__(self, failures: list[sqlite3.OperationalError]) -> None:
            self.failures = failures
            self.execute_calls = 0

        def execute(self, query: str) -> None:
            assert query == "BEGIN IMMEDIATE"
            self.execute_calls += 1
            if self.failures:
                raise self.failures.pop(0)

    sleep_calls: list[float] = []
    monkeypatch.setattr(run_ledger.time, "sleep", sleep_calls.append)

    transient_connection = FakeConnection(
        [sqlite3.OperationalError("database is locked"), sqlite3.OperationalError("SQLITE_BUSY")]
    )
    run_ledger._begin_run_ledger_write_transaction(transient_connection)
    assert transient_connection.execute_calls == 3
    assert sleep_calls == [0.05, 0.10]

    sleep_calls.clear()
    final_error = sqlite3.OperationalError("database is busy")
    exhausted_connection = FakeConnection(
        [
            sqlite3.OperationalError("database is locked"),
            sqlite3.OperationalError("SQLITE_BUSY"),
            sqlite3.OperationalError("database is locked"),
            final_error,
        ]
    )
    with pytest.raises(sqlite3.OperationalError) as exhausted:
        run_ledger._begin_run_ledger_write_transaction(exhausted_connection)
    assert exhausted.value is final_error
    assert exhausted_connection.execute_calls == 4
    assert sleep_calls == [0.05, 0.10, 0.20]

    sleep_calls.clear()
    non_lock_error = sqlite3.OperationalError("disk I/O error")
    non_lock_connection = FakeConnection([non_lock_error])
    with pytest.raises(sqlite3.OperationalError) as non_lock:
        run_ledger._begin_run_ledger_write_transaction(non_lock_connection)
    assert non_lock.value is non_lock_error
    assert non_lock_connection.execute_calls == 1
    assert sleep_calls == []


def test_receipt_shaped_values_are_rejected_before_durable_run_writes(tmp_path: Path) -> None:
    store = _store(tmp_path)
    receipt = "v2.eyJzY2hlbWEiOiJhbWIucmVjYWxsLXJlY2VpcHQudjIifQ." + ("a" * 43)
    started = store.begin_run(
        workspace_key="project:bridge",
        goal="Reject receipt-shaped durable values.",
        idempotency_key="begin:receipt-values",
    )

    def assert_rejected(write: object) -> None:
        assert callable(write)
        with pytest.raises(ValueError, match="receipt-shaped value") as error:
            write()
        assert receipt not in str(error.value)

    event_kwargs = {
        "workspace_key": "project:bridge",
        "run_id": started["run_id"],
        "work_item_id": started["root_work_item_id"],
        "event_type": "observation",
    }
    assert_rejected(
        lambda: store.record_run_event(
            **event_kwargs,
            summary=f"Receipt: {receipt}",
            idempotency_key="event:receipt-summary",
        )
    )
    assert_rejected(
        lambda: store.record_run_event(
            **event_kwargs,
            summary="Reject a receipt in an otherwise ordinary payload value.",
            payload={"reference": receipt},
            idempotency_key="event:receipt-payload",
        )
    )
    assert_rejected(
        lambda: store.record_run_event(
            **event_kwargs,
            summary="Reject a receipt in event evidence.",
            evidence=[{"reference": receipt}],
            idempotency_key="event:receipt-evidence",
        )
    )
    assert_rejected(
        lambda: store.record_run_event(
            workspace_key="project:bridge",
            run_id=started["run_id"],
            work_item_id=started["root_work_item_id"],
            event_type="artifact_created",
            summary="Reject a receipt in artifact metadata.",
            payload={
                "artifact": {
                    "digest": "a" * 64,
                    "mime_type": "application/json",
                    "uri": "artifact://proofs/receipt.json",
                    "metadata": {"reference": receipt},
                }
            },
            idempotency_key="event:receipt-artifact-metadata",
        )
    )
    assert_rejected(
        lambda: store.record_run_event(
            **event_kwargs,
            summary="Reject a receipt in provenance.",
            provenance={"source_client": receipt},
            idempotency_key="event:receipt-provenance",
        )
    )
    assert_rejected(
        lambda: store.complete_run(
            workspace_key="project:bridge",
            run_id=started["run_id"],
            outcome="unverified",
            evaluator_type="agent",
            evidence=[{"reference": receipt}],
            idempotency_key="outcome:receipt-evidence",
        )
    )
    assert_rejected(
        lambda: store.complete_run(
            workspace_key="project:bridge",
            run_id=started["run_id"],
            outcome="unverified",
            evaluator_type="agent",
            metrics={"reference": receipt},
            idempotency_key="outcome:receipt-metrics",
        )
    )

    with store._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM run_events WHERE run_id = ?", (started["run_id"],)).fetchone()[0] == 0
        assert (
            conn.execute("SELECT COUNT(*) FROM run_artifacts WHERE run_id = ?", (started["run_id"],)).fetchone()[0] == 0
        )
        assert (
            conn.execute("SELECT COUNT(*) FROM run_outcomes WHERE run_id = ?", (started["run_id"],)).fetchone()[0] == 0
        )

    receipt_idempotency_key = f"event:receipt-idempotency:{receipt}"
    allowed = store.record_run_event(
        **event_kwargs,
        summary="The idempotency key is retained only as a digest.",
        idempotency_key=receipt_idempotency_key,
    )
    assert allowed["sequence"] == 1
    with store._connect() as conn:
        idempotency_digest = conn.execute(
            "SELECT idempotency_key_digest FROM run_events WHERE event_id = ?", (allowed["event_id"],)
        ).fetchone()[0]
    assert idempotency_digest == run_ledger._idempotency_digest(receipt_idempotency_key)


def test_artifact_created_is_server_minted_atomic_idempotent_and_page_consistent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    started = store.begin_run(
        workspace_key="project:bridge",
        goal="Record a durable artifact reference.",
        idempotency_key="begin:artifact",
    )
    artifact_payload = {
        "artifact": {
            "digest": "a" * 64,
            "mime_type": "application/json",
            "uri": "artifact://proofs/run.json",
            "metadata": {"label": "deterministic proof", "analysis_digest": "b" * 64},
        }
    }
    event = store.record_run_event(
        workspace_key="project:bridge",
        run_id=started["run_id"],
        work_item_id=started["root_work_item_id"],
        event_type="artifact_created",
        summary="The deterministic proof artifact was created.",
        payload=artifact_payload,
        idempotency_key="event:artifact",
    )
    artifact = event["artifact"]
    assert artifact["artifact_id"].startswith("artifact_")
    assert artifact["artifact_version"] == 1
    assert artifact["producing_event_id"] == event["event_id"]
    assert artifact["metadata"] == artifact_payload["artifact"]["metadata"]
    replay = store.record_run_event(
        workspace_key="project:bridge",
        run_id=started["run_id"],
        work_item_id=started["root_work_item_id"],
        event_type="artifact_created",
        summary="The deterministic proof artifact was created.",
        payload=artifact_payload,
        idempotency_key="event:artifact",
    )
    assert replay == {**event, "idempotent_replay": True}
    with pytest.raises(ValueError, match="different payload"):
        store.record_run_event(
            workspace_key="project:bridge",
            run_id=started["run_id"],
            work_item_id=started["root_work_item_id"],
            event_type="artifact_created",
            summary="A conflicting artifact retry.",
            payload=artifact_payload,
            idempotency_key="event:artifact",
        )
    page = store.get_run(
        workspace_key="project:bridge",
        run_id=started["run_id"],
        event_limit=1,
    )
    assert [item["artifact_id"] for item in page["artifacts"]] == [artifact["artifact_id"]]
    assert page["artifacts"][0]["producing_event_id"] == page["events"][0]["event_id"]
    assert (
        store.get_run(
            workspace_key="project:bridge",
            run_id=started["run_id"],
            since_sequence=page["next_sequence"],
        )["artifacts"]
        == []
    )

    for event_type, payload, message in (
        ("artifact_created", {}, "requires payload.artifact"),
        ("checkpoint", artifact_payload, "only valid for artifact_created"),
        (
            "artifact_created",
            {"artifact": {**artifact_payload["artifact"], "artifact_id": "artifact_" + "0" * 32}},
            "server-managed",
        ),
        (
            "artifact_created",
            {"artifact": {**artifact_payload["artifact"], "digest": "A" * 64}},
            "lowercase SHA-256",
        ),
        (
            "artifact_created",
            {"artifact": {**artifact_payload["artifact"], "metadata": {"content": "must not persist"}}},
            "must not store content",
        ),
        (
            "artifact_created",
            {"artifact": {**artifact_payload["artifact"], "metadata": {"body": "must not persist"}}},
            "must not store content",
        ),
        (
            "artifact_created",
            {"artifact": {**artifact_payload["artifact"], "metadata": {"file_body": "must not persist"}}},
            "must not store content",
        ),
        (
            "artifact_created",
            {"artifact": {**artifact_payload["artifact"], "metadata": {"file body": "must not persist"}}},
            "must not store content",
        ),
        (
            "artifact_created",
            {"artifact": {**artifact_payload["artifact"], "metadata": {"file/body": "must not persist"}}},
            "must not store content",
        ),
        (
            "artifact_created",
            {"artifact": {**artifact_payload["artifact"], "metadata": {"file.body": "must not persist"}}},
            "must not store content",
        ),
        (
            "artifact_created",
            {"artifact": {**artifact_payload["artifact"], "metadata": {"FILE-BODY": "must not persist"}}},
            "must not store content",
        ),
        (
            "artifact_created",
            {
                "artifact": {
                    **artifact_payload["artifact"],
                    "metadata": {"nested": {"fileBody": "must not persist"}},
                }
            },
            "must not store content",
        ),
        (
            "artifact_created",
            {
                "artifact": {
                    **artifact_payload["artifact"],
                    "metadata": {"nested": {"file/body": "must not persist"}},
                }
            },
            "must not store content",
        ),
        (
            "artifact_created",
            {"artifact": {**artifact_payload["artifact"], "metadata": {"analysis": "private"}}},
            "durable run structured data rejects field",
        ),
        (
            "artifact_created",
            {"artifact": {**artifact_payload["artifact"], "uri": "data:text/plain;base64,SGVsbG8="}},
            "must not use the data scheme",
        ),
        (
            "artifact_created",
            {"artifact": {**artifact_payload["artifact"], "uri": "DATA:text/plain;base64,SGVsbG8="}},
            "must not use the data scheme",
        ),
        (
            "artifact_created",
            {
                "artifact": {
                    **artifact_payload["artifact"],
                    "metadata": {"nested": {"reference": "data:text/plain;base64,SGVsbG8="}},
                }
            },
            "must not use the data scheme",
        ),
        (
            "artifact_created",
            {"artifact": {**artifact_payload["artifact"], "uri": "inline-artifact-content"}},
            "reference-like URI",
        ),
        (
            "artifact_created",
            {"artifact": {**artifact_payload["artifact"], "uri": "proof+opaque:run 42"}},
            "reference-like URI",
        ),
        (
            "artifact_created",
            {"artifact": {**artifact_payload["artifact"], "uri": "proof+opaque:run\x80-42"}},
            "reference-like URI",
        ),
    ):
        with pytest.raises(ValueError, match=message):
            store.record_run_event(
                workspace_key="project:bridge",
                run_id=started["run_id"],
                work_item_id=started["root_work_item_id"],
                event_type=event_type,
                summary="Reject malformed artifact data.",
                payload=payload,
                idempotency_key=f"event:artifact:invalid:{event_type}:{message}",
            )

    with store._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM run_events WHERE run_id = ?", (started["run_id"],)).fetchone()[0] == 1
        assert (
            conn.execute("SELECT COUNT(*) FROM run_artifacts WHERE run_id = ?", (started["run_id"],)).fetchone()[0] == 1
        )

    opaque_artifact = store.record_run_event(
        workspace_key="project:bridge",
        run_id=started["run_id"],
        work_item_id=started["root_work_item_id"],
        event_type="artifact_created",
        summary="A broad opaque artifact reference was created.",
        payload={"artifact": {**artifact_payload["artifact"], "uri": "proof+opaque:run-42"}},
        idempotency_key="event:artifact:opaque-uri",
    )
    assert opaque_artifact["artifact"]["uri"] == "proof+opaque:run-42"

    original_insert = run_ledger._insert_run_artifact

    def fail_artifact_insert(*args: object, **kwargs: object) -> dict[str, object] | None:
        raise RuntimeError("injected artifact write failure")

    monkeypatch.setattr(run_ledger, "_insert_run_artifact", fail_artifact_insert)
    with pytest.raises(RuntimeError, match="injected artifact write failure"):
        store.record_run_event(
            workspace_key="project:bridge",
            run_id=started["run_id"],
            work_item_id=started["root_work_item_id"],
            event_type="artifact_created",
            summary="The artifact insertion must roll back the event.",
            payload={"artifact": {**artifact_payload["artifact"], "uri": "artifact://proofs/rollback.json"}},
            idempotency_key="event:artifact:rollback",
        )
    monkeypatch.setattr(run_ledger, "_insert_run_artifact", original_insert)
    with store._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM run_events WHERE run_id = ?", (started["run_id"],)).fetchone()[0] == 2
        assert (
            conn.execute("SELECT COUNT(*) FROM run_artifacts WHERE run_id = ?", (started["run_id"],)).fetchone()[0] == 2
        )


def test_first_outcome_requires_terminal_root_and_children_and_rebuilds_consistently(tmp_path: Path) -> None:
    store = _store(tmp_path)
    started = store.begin_run(
        workspace_key="project:bridge",
        goal="Close every explicit work item before the outcome.",
        idempotency_key="begin:terminal-work-items",
    )
    complete_kwargs = {
        "workspace_key": "project:bridge",
        "run_id": started["run_id"],
        "outcome": "unverified",
        "evaluator_type": "agent",
    }
    with pytest.raises(ValueError, match="first run outcome requires terminal work items"):
        store.complete_run(idempotency_key="outcome:active-root", **complete_kwargs)
    child = store.record_run_event(
        workspace_key="project:bridge",
        run_id=started["run_id"],
        event_type="work_item_started",
        summary="Start a child work item.",
        parent_work_item_id=started["root_work_item_id"],
        work_item_goal="Run the child validation.",
        idempotency_key="event:active-child",
    )
    with pytest.raises(ValueError, match="first run outcome requires terminal work items"):
        store.complete_run(idempotency_key="outcome:active-child", **complete_kwargs)
    store.record_run_event(
        workspace_key="project:bridge",
        run_id=started["run_id"],
        work_item_id=child["work_item_id"],
        event_type="work_item_failed",
        summary="The child validation failed conclusively.",
        idempotency_key="event:child-failed",
    )
    with pytest.raises(ValueError, match="first run outcome requires terminal work items"):
        store.complete_run(idempotency_key="outcome:active-root-after-child", **complete_kwargs)
    _complete_root_work_item(store, started, "terminal-work-items")
    initial = store.complete_run(idempotency_key="outcome:terminal-work-items", **complete_kwargs)
    correction = store.complete_run(
        workspace_key="project:bridge",
        run_id=started["run_id"],
        outcome="verified_success",
        evaluator_type="human",
        evidence=[{"kind": "review", "reference": "terminal-work-items"}],
        supersedes_outcome_id=initial["outcome_id"],
        idempotency_key="outcome:terminal-work-items:correction",
    )
    restored = store.get_run(workspace_key="project:bridge", run_id=started["run_id"])
    assert restored["outcome"]["outcome_id"] == correction["outcome_id"]
    statuses = {item["work_item_id"]: item["status"] for item in restored["work_items"]}
    assert statuses == {started["root_work_item_id"]: "completed", child["work_item_id"]: "failed"}
    with store._connect() as conn:
        assert inspect_run_projections(conn)["ok"] is True
        rebuild_run_projections(conn)
        assert inspect_run_projections(conn)["ok"] is True


def test_twenty_concurrent_writers_append_one_thousand_monotonic_events(tmp_path: Path) -> None:
    store = _store(tmp_path)
    started = store.begin_run(
        workspace_key="project:bridge",
        goal="Prove concurrent event sequencing.",
        idempotency_key="begin:concurrency",
    )

    def write_event(index: int) -> dict[str, object]:
        return store.record_run_event(
            workspace_key="project:bridge",
            run_id=started["run_id"],
            work_item_id=started["root_work_item_id"],
            event_type="observation",
            summary=f"Concurrent observation {index}.",
            payload={"index": index},
            idempotency_key=f"event:concurrent:{index}",
        )

    with ThreadPoolExecutor(max_workers=20) as executor:
        results = list(executor.map(write_event, range(1000)))

    sequences = sorted(int(result["sequence"]) for result in results)
    assert sequences == list(range(1, 1001))
    assert len({str(result["event_id"]) for result in results}) == 1000
    with store._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM run_events").fetchone()[0] == 1000
        assert conn.execute("SELECT COUNT(DISTINCT sequence) FROM run_events").fetchone()[0] == 1000
