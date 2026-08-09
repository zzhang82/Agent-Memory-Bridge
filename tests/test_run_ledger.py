from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from multiprocessing import get_context
from pathlib import Path

import pytest

from agent_mem_bridge import run_ledger
from agent_mem_bridge.run_projection import (
    inspect_run_projections,
    rebuild_run_projections,
    validate_work_item_transition,
)
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


def _cas_terminal_process(
    db_path: str,
    log_dir: str,
    run_id: str,
    work_item_id: str,
    suffix: str,
) -> tuple[str, str]:
    store = MemoryStore(Path(db_path), log_dir=Path(log_dir))
    try:
        result = store.record_run_event(
            workspace_key="project:bridge",
            run_id=run_id,
            work_item_id=work_item_id,
            event_type="work_item_completed",
            summary=f"CAS terminal writer {suffix}.",
            expected_last_sequence=0,
            expected_work_item_status="active",
            idempotency_key=f"event:cas-process:{suffix}",
        )
    except (RuntimeError, ValueError) as error:
        return "conflict", str(error)
    return "ok", str(result["sequence"])


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
    assert restored["snapshot_epoch"] == store.database_epoch()
    assert restored["snapshot_last_sequence"] == restored["latest_sequence"] == 1
    assert restored["projection_health"]["ok"] is True
    assert restored["degraded"] is False


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


@pytest.mark.parametrize(
    ("status", "event_type"),
    [
        ("pending", "blocker"),
        ("pending", "work_item_completed"),
        ("pending", "work_item_failed"),
        ("blocked", "work_item_completed"),
        ("completed", "work_item_started"),
        ("failed", "work_item_completed"),
        ("abandoned", "blocker"),
    ],
)
def test_work_item_transition_matrix_rejects_illegal_edges(status: str, event_type: str) -> None:
    with pytest.raises(ValueError):
        validate_work_item_transition(status, event_type)


def test_work_item_fsm_rejections_are_atomic_and_terminal_items_cannot_reopen(tmp_path: Path) -> None:
    store = _store(tmp_path)
    started = store.begin_run(
        workspace_key="project:bridge",
        goal="Enforce the work-item state machine.",
        idempotency_key="begin:fsm",
    )
    blocked = store.record_run_event(
        workspace_key="project:bridge",
        run_id=started["run_id"],
        work_item_id=started["root_work_item_id"],
        event_type="blocker",
        summary="A blocking condition is active.",
        idempotency_key="event:fsm:blocked",
    )
    assert blocked["sequence"] == 1

    def snapshot() -> tuple[int, tuple[object, ...]]:
        with store._connect() as conn:
            count = int(conn.execute("SELECT COUNT(*) FROM run_events").fetchone()[0])
            state = conn.execute(
                """
                SELECT status, last_sequence, ended_at, last_summary
                FROM run_work_item_state_projection
                WHERE work_item_id = ?
                """,
                (started["root_work_item_id"],),
            ).fetchone()
        assert state is not None
        return count, tuple(state)

    before_blocked_completion = snapshot()
    with pytest.raises(ValueError, match="requires active status; actual status is blocked"):
        store.record_run_event(
            workspace_key="project:bridge",
            run_id=started["run_id"],
            work_item_id=started["root_work_item_id"],
            event_type="work_item_completed",
            summary="Blocked work cannot complete directly.",
            idempotency_key="event:fsm:blocked-complete",
        )
    assert snapshot() == before_blocked_completion

    resumed = store.record_run_event(
        workspace_key="project:bridge",
        run_id=started["run_id"],
        work_item_id=started["root_work_item_id"],
        event_type="work_item_started",
        summary="Resume the blocked v1 work item explicitly.",
        idempotency_key="event:fsm:resumed",
    )
    assert resumed["sequence"] == 2
    assert snapshot()[1][0] == "active"

    failed = store.record_run_event(
        workspace_key="project:bridge",
        run_id=started["run_id"],
        work_item_id=started["root_work_item_id"],
        event_type="work_item_failed",
        summary="The resumed work item failed terminally.",
        idempotency_key="event:fsm:failed",
    )
    assert failed["sequence"] == 3
    before_reopen = snapshot()
    with pytest.raises(ValueError, match="terminal work item cannot accept work_item_started"):
        store.record_run_event(
            workspace_key="project:bridge",
            run_id=started["run_id"],
            work_item_id=started["root_work_item_id"],
            event_type="work_item_started",
            summary="A terminal work item must not reopen.",
            idempotency_key="event:fsm:reopen",
        )
    assert snapshot() == before_reopen


@pytest.mark.parametrize(
    ("parent_status", "parent_event"),
    [
        ("blocked", "blocker"),
        ("completed", "work_item_completed"),
        ("failed", "work_item_failed"),
        ("abandoned", "work_item_abandoned"),
    ],
)
def test_new_child_requires_an_active_parent_and_rejects_atomically(
    tmp_path: Path,
    parent_status: str,
    parent_event: str,
) -> None:
    store = _store(tmp_path)
    started = store.begin_run(
        workspace_key="project:bridge",
        goal="Keep work-item hierarchy lifecycle-consistent.",
        idempotency_key=f"begin:parent-state:{parent_status}",
    )
    store.record_run_event(
        workspace_key="project:bridge",
        run_id=started["run_id"],
        work_item_id=started["root_work_item_id"],
        event_type=parent_event,
        summary=f"Move the parent to {parent_status}.",
        idempotency_key=f"event:parent-state:{parent_status}",
    )

    def counts() -> tuple[int, int]:
        with store._connect() as conn:
            event_count = int(
                conn.execute("SELECT COUNT(*) FROM run_events WHERE run_id = ?", (started["run_id"],)).fetchone()[0]
            )
            work_item_count = int(
                conn.execute("SELECT COUNT(*) FROM run_work_items WHERE run_id = ?", (started["run_id"],)).fetchone()[0]
            )
        return event_count, work_item_count

    before = counts()
    with pytest.raises(ValueError, match=rf"new work item requires an active parent; actual status is {parent_status}"):
        store.record_run_event(
            workspace_key="project:bridge",
            run_id=started["run_id"],
            event_type="work_item_started",
            summary="A non-active parent must not gain a new child.",
            parent_work_item_id=started["root_work_item_id"],
            work_item_goal="This child must not be created.",
            idempotency_key=f"event:child-rejected:{parent_status}",
        )
    assert counts() == before
    restored = store.get_run(workspace_key="project:bridge", run_id=started["run_id"])
    assert len(restored["work_items"]) == 1
    assert restored["work_items"][0]["status"] == parent_status


def test_event_compare_and_swap_conflicts_report_actual_state_without_writes(tmp_path: Path) -> None:
    store = _store(tmp_path)
    started = store.begin_run(
        workspace_key="project:bridge",
        goal="Reject stale event writers atomically.",
        idempotency_key="begin:cas",
    )
    first = store.record_run_event(
        workspace_key="project:bridge",
        run_id=started["run_id"],
        work_item_id=started["root_work_item_id"],
        event_type="checkpoint",
        summary="Establish sequence one.",
        expected_last_sequence=0,
        expected_work_item_status="active",
        idempotency_key="event:cas:first",
    )
    assert first["sequence"] == 1

    def snapshot() -> tuple[int, tuple[object, ...], tuple[object, ...]]:
        with store._connect() as conn:
            count = int(conn.execute("SELECT COUNT(*) FROM run_events").fetchone()[0])
            run_state = conn.execute(
                "SELECT status, last_sequence FROM run_state_projection WHERE run_id = ?",
                (started["run_id"],),
            ).fetchone()
            item_state = conn.execute(
                "SELECT status, last_sequence FROM run_work_item_state_projection WHERE work_item_id = ?",
                (started["root_work_item_id"],),
            ).fetchone()
        assert run_state is not None and item_state is not None
        return count, tuple(run_state), tuple(item_state)

    before = snapshot()
    with pytest.raises(ValueError, match=r"run sequence conflict: expected 0, actual 1"):
        store.record_run_event(
            workspace_key="project:bridge",
            run_id=started["run_id"],
            work_item_id=started["root_work_item_id"],
            event_type="decision",
            summary="This writer observed a stale sequence.",
            expected_last_sequence=0,
            expected_work_item_status="active",
            idempotency_key="event:cas:stale-sequence",
        )
    assert snapshot() == before
    with pytest.raises(ValueError, match=r"work-item status conflict: expected blocked, actual active"):
        store.record_run_event(
            workspace_key="project:bridge",
            run_id=started["run_id"],
            work_item_id=started["root_work_item_id"],
            event_type="decision",
            summary="This writer observed a stale status.",
            expected_last_sequence=1,
            expected_work_item_status="blocked",
            idempotency_key="event:cas:stale-status",
        )
    assert snapshot() == before

    accepted = store.record_run_event(
        workspace_key="project:bridge",
        run_id=started["run_id"],
        work_item_id=started["root_work_item_id"],
        event_type="decision",
        summary="The current preconditions match.",
        expected_last_sequence=1,
        expected_work_item_status="active",
        idempotency_key="event:cas:accepted",
    )
    assert accepted["sequence"] == 2
    replay = store.record_run_event(
        workspace_key="project:bridge",
        run_id=started["run_id"],
        work_item_id=started["root_work_item_id"],
        event_type="decision",
        summary="The current preconditions match.",
        expected_last_sequence=1,
        expected_work_item_status="active",
        idempotency_key="event:cas:accepted",
    )
    assert replay == {**accepted, "idempotent_replay": True}


def test_two_processes_competing_on_the_same_terminal_cas_allow_exactly_one_writer(tmp_path: Path) -> None:
    store = _store(tmp_path)
    started = store.begin_run(
        workspace_key="project:bridge",
        goal="Prove process-level terminal compare-and-swap.",
        idempotency_key="begin:process-cas",
    )
    context = get_context("spawn")
    with ProcessPoolExecutor(max_workers=2, mp_context=context) as executor:
        futures = [
            executor.submit(
                _cas_terminal_process,
                str(store.db_path),
                str(store.log_dir),
                str(started["run_id"]),
                str(started["root_work_item_id"]),
                suffix,
            )
            for suffix in ("one", "two")
        ]
        results = [future.result(timeout=30) for future in futures]

    assert [status for status, _ in results].count("ok") == 1
    assert [status for status, _ in results].count("conflict") == 1
    conflict = next(message for status, message in results if status == "conflict")
    assert "run sequence conflict: expected 0, actual 1" in conflict
    restored = store.get_run(workspace_key="project:bridge", run_id=started["run_id"])
    assert restored["latest_sequence"] == 1
    assert restored["work_items"][0]["status"] == "completed"


def test_projection_drift_blocks_writes_but_get_run_returns_authority_snapshot(tmp_path: Path) -> None:
    store = _store(tmp_path)
    started = store.begin_run(
        workspace_key="project:bridge",
        goal="Recover from a degraded derived projection.",
        idempotency_key="begin:projection-drift",
    )
    with store._connect() as conn:
        conn.execute(
            "UPDATE run_state_projection SET last_sequence = 99 WHERE run_id = ?",
            (started["run_id"],),
        )

    degraded = store.get_run(workspace_key="project:bridge", run_id=started["run_id"])
    assert degraded["degraded"] is True
    assert degraded["run"]["status"] == "active"
    assert degraded["run"]["last_sequence"] == 0
    assert degraded["snapshot_last_sequence"] == degraded["latest_sequence"] == 0
    assert degraded["projection_health"]["counts"]["stale_run_state_projection_count"] == 1
    with store._connect() as conn:
        before = int(conn.execute("SELECT COUNT(*) FROM run_events").fetchone()[0])
    with pytest.raises(RuntimeError, match="run projection health is degraded; write refused"):
        store.record_run_event(
            workspace_key="project:bridge",
            run_id=started["run_id"],
            work_item_id=started["root_work_item_id"],
            event_type="checkpoint",
            summary="A degraded projection must fail closed.",
            idempotency_key="event:projection-drift:rejected",
        )
    with store._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM run_events").fetchone()[0] == before
        rebuild_run_projections(conn)

    repaired = store.record_run_event(
        workspace_key="project:bridge",
        run_id=started["run_id"],
        work_item_id=started["root_work_item_id"],
        event_type="checkpoint",
        summary="The repaired projection accepts a new append.",
        idempotency_key="event:projection-drift:repaired",
    )
    assert repaired["sequence"] == 1


def test_get_run_uses_one_snapshot_during_a_concurrent_append(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path)
    started = store.begin_run(
        workspace_key="project:bridge",
        goal="Read one coherent recovery snapshot.",
        idempotency_key="begin:snapshot",
    )
    reader_ready = threading.Event()
    release_reader = threading.Event()
    original_derive = run_ledger.derive_run_authority_state

    def paused_derive(conn: sqlite3.Connection, *, run_id: str) -> dict[str, object]:
        authority = original_derive(conn, run_id=run_id)
        if threading.current_thread().name.startswith("snapshot-reader"):
            reader_ready.set()
            if not release_reader.wait(timeout=10):
                raise TimeoutError("snapshot reader was not released")
        return authority

    monkeypatch.setattr(run_ledger, "derive_run_authority_state", paused_derive)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="snapshot-reader") as executor:
        future = executor.submit(
            store.get_run,
            workspace_key="project:bridge",
            run_id=started["run_id"],
        )
        assert reader_ready.wait(timeout=10)
        try:
            appended = store.record_run_event(
                workspace_key="project:bridge",
                run_id=started["run_id"],
                work_item_id=started["root_work_item_id"],
                event_type="checkpoint",
                summary="Commit while the reader holds its snapshot.",
                idempotency_key="event:snapshot:concurrent",
            )
            assert appended["sequence"] == 1
        finally:
            release_reader.set()
        snapshot = future.result(timeout=10)

    assert snapshot["snapshot_last_sequence"] == snapshot["latest_sequence"] == 0
    assert snapshot["run"]["last_sequence"] == 0
    assert snapshot["events"] == []
    assert snapshot["projection_health"]["ok"] is True
    fresh = store.get_run(workspace_key="project:bridge", run_id=started["run_id"])
    assert fresh["snapshot_last_sequence"] == fresh["latest_sequence"] == 1
    assert [event["sequence"] for event in fresh["events"]] == [1]


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


def test_regression_target_is_rejected_for_non_regression_outcomes_atomically(tmp_path: Path) -> None:
    store = _store(tmp_path)
    source = store.begin_run(
        workspace_key="project:bridge",
        goal="Reject an inverse-inconsistent regression target.",
        idempotency_key="begin:non-regression-target:source",
    )
    target = store.begin_run(
        workspace_key="project:bridge",
        goal="Provide a distinct target handle.",
        idempotency_key="begin:non-regression-target:target",
    )
    _complete_root_work_item(store, source, "non-regression-target")
    with store._connect() as conn:
        before = int(conn.execute("SELECT COUNT(*) FROM run_outcomes").fetchone()[0])

    with pytest.raises(ValueError, match="regression_of_run_id is only valid for a regression outcome"):
        store.complete_run(
            workspace_key="project:bridge",
            run_id=source["run_id"],
            outcome="unverified",
            evaluator_type="agent",
            regression_of_run_id=target["run_id"],
            idempotency_key="outcome:non-regression-target",
        )

    with store._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM run_outcomes").fetchone()[0] == before


def test_legacy_declared_verified_success_is_readable_but_not_regression_authority(tmp_path: Path) -> None:
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
        with pytest.raises(ValueError, match="current strong verified outcome"):
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
    assert verified_outcome["authority_class"] == "legacy_declared"
    assert verified_outcome["strong_verified"] is False
    verified_readback = store.get_run(
        workspace_key="project:bridge",
        run_id=str(verified_target["run_id"]),
    )
    assert verified_readback["outcome"]["authority_class"] == "legacy_declared"
    assert verified_readback["outcome"]["strong_verified"] is False
    regressing_run = begin("project:bridge", "same-workspace")
    _complete_root_work_item(store, regressing_run, "same-workspace")
    assert_rejected(
        str(verified_target["run_id"]),
        "same-workspace-declared",
        source_run=regressing_run,
    )

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
    assert restored["run"]["terminal_at"] == initial["created_at"]
    assert restored["run"]["ended_at"] == initial["created_at"]
    assert restored["run"]["current_outcome_updated_at"] == correction["created_at"]
    statuses = {item["work_item_id"]: item["status"] for item in restored["work_items"]}
    assert statuses == {started["root_work_item_id"]: "completed", child["work_item_id"]: "failed"}
    with store._connect() as conn:
        assert inspect_run_projections(conn)["ok"] is True
        rebuild_run_projections(conn)
        assert inspect_run_projections(conn)["ok"] is True
    rebuilt = store.get_run(workspace_key="project:bridge", run_id=started["run_id"])
    assert rebuilt["run"]["terminal_at"] == initial["created_at"]
    assert rebuilt["run"]["ended_at"] == initial["created_at"]
    assert rebuilt["run"]["current_outcome_updated_at"] == correction["created_at"]


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
