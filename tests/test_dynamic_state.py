from __future__ import annotations

import sqlite3
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from multiprocessing import get_context
from pathlib import Path

import pytest

from agent_mem_bridge.database_maintenance import (
    backup_database,
    inspect_database,
    rebuild_database_projections,
    restore_database,
)
from agent_mem_bridge.storage import MemoryStore


def _store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")


def _transition(
    store: MemoryStore,
    *,
    to_status: str,
    expected_version: int,
    expected_database_epoch: str,
    idempotency_key: str,
) -> dict[str, object]:
    return store.dynamic_state.transition_status(
        workspace_key="project:bridge",
        state_key="release:current",
        to_status=to_status,
        expected_version=expected_version,
        expected_database_epoch=expected_database_epoch,
        idempotency_key=idempotency_key,
        provenance={
            "session_id": "session:dynamic-state",
            "correlation_id": "change:dynamic-state",
            "actor": "test-agent",
            "source_client": "pytest",
        },
    )


def _assign_owner(
    store: MemoryStore,
    *,
    owner: str,
    expected_version: int,
    expected_database_epoch: str,
    idempotency_key: str,
) -> dict[str, object]:
    return store.dynamic_state.assign_owner(
        workspace_key="project:bridge",
        state_key="release:current",
        owner=owner,
        expected_version=expected_version,
        expected_database_epoch=expected_database_epoch,
        idempotency_key=idempotency_key,
        provenance={"actor": "test-agent"},
    )


def _process_competing_transition(
    db_path: str,
    log_dir: str,
    database_epoch: str,
    idempotency_key: str,
) -> tuple[str, str]:
    store = MemoryStore(Path(db_path), log_dir=Path(log_dir))
    try:
        store.dynamic_state.transition_status(
            workspace_key="project:bridge",
            state_key="release:current",
            to_status="draft",
            expected_version=0,
            expected_database_epoch=database_epoch,
            idempotency_key=idempotency_key,
        )
        return "ok", idempotency_key
    except ValueError as exc:
        return "conflict", str(exc)


def test_exact_key_release_state_stays_outside_semantic_memory(tmp_path: Path) -> None:
    store = _store(tmp_path)
    absent = store.dynamic_state.read(workspace_key="project:bridge", state_key="release:current")

    assert absent == {
        "workspace_key": "project:bridge",
        "state_key": "release:current",
        "state_type": "release-state",
        "version": 0,
        "value": None,
        "value_hash": None,
        "last_mutation_id": None,
        "updated_at": None,
        "database_epoch": store.database_epoch(),
        "exists": False,
    }
    first = _transition(
        store,
        to_status="draft",
        expected_version=0,
        expected_database_epoch=str(absent["database_epoch"]),
        idempotency_key="state:first-transition",
    )
    assigned = _assign_owner(
        store,
        owner="release",
        expected_version=1,
        expected_database_epoch=str(first["database_epoch"]),
        idempotency_key="state:assign-owner",
    )

    assert first["command"] == "status_transition"
    assert assigned["command"] == "owner_assignment"
    current = store.dynamic_state.read(workspace_key="project:bridge", state_key="release:current")
    assert current["version"] == 2
    assert current["value"] == {"owner": "release", "status": "draft"}
    assert store.recall(namespace="project:bridge", query="draft release", kind="memory")["count"] == 0
    history = store.dynamic_state.history(workspace_key="project:bridge", state_key="release:current")
    assert [mutation["command"] for mutation in history["mutations"]] == [
        "status_transition",
        "owner_assignment",
    ]


def test_request_identity_includes_version_epoch_and_replays_success(tmp_path: Path) -> None:
    store = _store(tmp_path)
    snapshot = store.dynamic_state.read(workspace_key="project:bridge", state_key="release:current")
    first = _transition(
        store,
        to_status="draft",
        expected_version=0,
        expected_database_epoch=str(snapshot["database_epoch"]),
        idempotency_key="state:accepted",
    )

    replay = _transition(
        store,
        to_status="draft",
        expected_version=0,
        expected_database_epoch=str(snapshot["database_epoch"]),
        idempotency_key="state:accepted",
    )
    assert replay == {**first, "idempotent_replay": True}
    with pytest.raises(ValueError, match="idempotency key was already used with a different request"):
        _transition(
            store,
            to_status="draft",
            expected_version=1,
            expected_database_epoch=str(snapshot["database_epoch"]),
            idempotency_key="state:accepted",
        )
    with pytest.raises(ValueError, match="idempotency key was already used with a different request"):
        _transition(
            store,
            to_status="draft",
            expected_version=0,
            expected_database_epoch="rotated-epoch",
            idempotency_key="state:accepted",
        )


def test_terminal_conflicts_and_rejections_are_persisted_and_replayed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    snapshot = store.dynamic_state.read(workspace_key="project:bridge", state_key="release:current")
    first = _transition(
        store,
        to_status="draft",
        expected_version=0,
        expected_database_epoch=str(snapshot["database_epoch"]),
        idempotency_key="state:initial",
    )

    with pytest.raises(ValueError, match="STATE_VERSION_CONFLICT: expected 0, actual 1"):
        _transition(
            store,
            to_status="review",
            expected_version=0,
            expected_database_epoch=str(first["database_epoch"]),
            idempotency_key="state:stale",
        )
    _assign_owner(
        store,
        owner="operator",
        expected_version=1,
        expected_database_epoch=str(first["database_epoch"]),
        idempotency_key="state:advance-after-conflict",
    )
    with pytest.raises(ValueError, match="STATE_VERSION_CONFLICT: expected 0, actual 1"):
        _transition(
            store,
            to_status="review",
            expected_version=0,
            expected_database_epoch=str(first["database_epoch"]),
            idempotency_key="state:stale",
        )
    with pytest.raises(ValueError, match="idempotency key was already used with a different request"):
        _transition(
            store,
            to_status="blocked",
            expected_version=0,
            expected_database_epoch=str(first["database_epoch"]),
            idempotency_key="state:stale",
        )

    new_key = store.dynamic_state.read(workspace_key="project:bridge", state_key="other:release")
    with pytest.raises(ValueError, match="new release state must transition to draft at version zero"):
        store.dynamic_state.transition_status(
            workspace_key="project:bridge",
            state_key="other:release",
            to_status="published",
            expected_version=0,
            expected_database_epoch=str(new_key["database_epoch"]),
            idempotency_key="state:reject-new-published",
        )
    _transition(
        store,
        to_status="review",
        expected_version=2,
        expected_database_epoch=str(first["database_epoch"]),
        idempotency_key="state:advance-existing",
    )
    with pytest.raises(ValueError, match="new release state must transition to draft at version zero"):
        store.dynamic_state.transition_status(
            workspace_key="project:bridge",
            state_key="other:release",
            to_status="published",
            expected_version=0,
            expected_database_epoch=str(new_key["database_epoch"]),
            idempotency_key="state:reject-new-published",
        )

    with store._connect() as conn:
        outcomes = conn.execute("SELECT outcome_type FROM state_request_outcomes ORDER BY created_at").fetchall()
    assert {str(row["outcome_type"]) for row in outcomes} == {"accepted", "conflict", "rejected"}


def test_typed_commands_enforce_status_transitions_and_owner_mutation_boundary(tmp_path: Path) -> None:
    store = _store(tmp_path)
    snapshot = store.dynamic_state.read(workspace_key="project:bridge", state_key="release:current")
    with pytest.raises(ValueError, match="new release state must transition to draft at version zero"):
        _transition(
            store,
            to_status="published",
            expected_version=0,
            expected_database_epoch=str(snapshot["database_epoch"]),
            idempotency_key="state:no-initial-publish",
        )
    first = _transition(
        store,
        to_status="draft",
        expected_version=0,
        expected_database_epoch=str(snapshot["database_epoch"]),
        idempotency_key="state:draft",
    )
    with pytest.raises(ValueError, match="release state transition draft->published is not allowed"):
        _transition(
            store,
            to_status="published",
            expected_version=1,
            expected_database_epoch=str(first["database_epoch"]),
            idempotency_key="state:no-skip-review",
        )
    assigned = _assign_owner(
        store,
        owner="release-owner",
        expected_version=1,
        expected_database_epoch=str(first["database_epoch"]),
        idempotency_key="state:owner",
    )
    assert assigned["value"] == {"owner": "release-owner", "status": "draft"}
    reviewed = _transition(
        store,
        to_status="review",
        expected_version=2,
        expected_database_epoch=str(first["database_epoch"]),
        idempotency_key="state:review",
    )
    assert reviewed["value"] == {"owner": "release-owner", "status": "review"}
    with pytest.raises(ValueError, match="owner assignment must change the current owner"):
        _assign_owner(
            store,
            owner="release-owner",
            expected_version=3,
            expected_database_epoch=str(first["database_epoch"]),
            idempotency_key="state:owner-noop",
        )


def test_restore_is_new_version_and_head_rebuild_preserves_logical_metadata(tmp_path: Path) -> None:
    store = _store(tmp_path)
    initial = store.dynamic_state.read(workspace_key="project:bridge", state_key="release:current")
    first = _transition(
        store,
        to_status="draft",
        expected_version=0,
        expected_database_epoch=str(initial["database_epoch"]),
        idempotency_key="state:one",
    )
    second = _transition(
        store,
        to_status="review",
        expected_version=1,
        expected_database_epoch=str(first["database_epoch"]),
        idempotency_key="state:two",
    )
    restored = store.dynamic_state.restore(
        workspace_key="project:bridge",
        state_key="release:current",
        mutation_id=str(first["mutation_id"]),
        expected_version=2,
        expected_database_epoch=str(second["database_epoch"]),
        idempotency_key="state:restore-one",
        provenance={"actor": "operator"},
    )
    assert restored["command"] == "restore"
    assert restored["operation"] == "restore"
    assert restored["version"] == 3
    assert restored["restore_of_mutation_id"] == first["mutation_id"]
    before_rebuild = store.dynamic_state.read(workspace_key="project:bridge", state_key="release:current")

    with store._connect() as conn:
        conn.execute("DELETE FROM state_heads")
        conn.commit()
    database_health = inspect_database(store.db_path)
    assert database_health["ok"] is False
    assert database_health["content"]["counts"]["missing_state_head_count"] == 1
    rebuilt_maintenance = rebuild_database_projections(store.db_path)
    assert rebuilt_maintenance["state_head_rebuilt_count"] == 1
    assert rebuilt_maintenance["health"]["ok"] is True
    rebuilt = store.dynamic_state.read(workspace_key="project:bridge", state_key="release:current")
    assert rebuilt == before_rebuild


def test_projection_health_detects_head_and_authority_hash_drift(tmp_path: Path) -> None:
    store = _store(tmp_path)
    initial = store.dynamic_state.read(workspace_key="project:bridge", state_key="release:current")
    first = _transition(
        store,
        to_status="draft",
        expected_version=0,
        expected_database_epoch=str(initial["database_epoch"]),
        idempotency_key="state:hash",
    )
    with store._connect() as conn:
        conn.execute("UPDATE state_heads SET value_hash = ?", ("0" * 64,))
        conn.commit()
    head_drift = store.dynamic_state.inspect_heads()
    assert head_drift["ok"] is False
    assert head_drift["counts"]["stale_state_head_count"] == 1
    store.dynamic_state.rebuild_heads()

    with store._connect() as conn:
        conn.execute("DROP TRIGGER prevent_state_mutations_update")
        conn.execute(
            "UPDATE state_mutations SET value_hash = ? WHERE mutation_id = ?", ("0" * 64, first["mutation_id"])
        )
        conn.commit()
    authority_drift = store.dynamic_state.inspect_heads()
    assert authority_drift["ok"] is False
    assert authority_drift["counts"]["invalid_state_history_count"] == 1
    with pytest.raises(RuntimeError, match="state mutation history is invalid; rebuild refused"):
        store.dynamic_state.rebuild_heads()


def test_restore_rotates_epoch_and_replays_original_stale_epoch_conflict(tmp_path: Path) -> None:
    store = _store(tmp_path)
    initial = store.dynamic_state.read(workspace_key="project:bridge", state_key="release:current")
    first = _transition(
        store,
        to_status="draft",
        expected_version=0,
        expected_database_epoch=str(initial["database_epoch"]),
        idempotency_key="state:before-backup",
    )
    backup_path = tmp_path / "backup.db"
    backup_database(store.db_path, backup_path)
    stale_epoch = str(first["database_epoch"])

    restored = restore_database(backup_path, store.db_path, force=True)
    reopened = MemoryStore(store.db_path, log_dir=store.log_dir)
    assert restored["database_epoch"] != stale_epoch
    with pytest.raises(ValueError, match="DATABASE_EPOCH_CONFLICT"):
        _transition(
            reopened,
            to_status="review",
            expected_version=1,
            expected_database_epoch=stale_epoch,
            idempotency_key="state:pre-restore-writer",
        )
    current_epoch = str(reopened.database_epoch())
    reviewed = _transition(
        reopened,
        to_status="review",
        expected_version=1,
        expected_database_epoch=current_epoch,
        idempotency_key="state:post-restore-writer",
    )
    assert reviewed["version"] == 2
    with pytest.raises(ValueError, match=f"DATABASE_EPOCH_CONFLICT: expected {stale_epoch}"):
        _transition(
            reopened,
            to_status="review",
            expected_version=1,
            expected_database_epoch=stale_epoch,
            idempotency_key="state:pre-restore-writer",
        )


def test_one_of_two_threads_wins_same_version_compare_and_swap(tmp_path: Path) -> None:
    store = _store(tmp_path)
    snapshot = store.dynamic_state.read(workspace_key="project:bridge", state_key="release:current")

    def competing_writer(idempotency_key: str) -> tuple[str, str]:
        try:
            _transition(
                store,
                to_status="draft",
                expected_version=0,
                expected_database_epoch=str(snapshot["database_epoch"]),
                idempotency_key=idempotency_key,
            )
            return "ok", idempotency_key
        except ValueError as exc:
            return "conflict", str(exc)

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(competing_writer, ("state:thread:one", "state:thread:two")))
    assert [status for status, _ in outcomes].count("ok") == 1
    assert [status for status, _ in outcomes].count("conflict") == 1
    assert "STATE_VERSION_CONFLICT: expected 0, actual 1" in next(
        detail for status, detail in outcomes if status == "conflict"
    )


def test_one_of_two_processes_wins_same_version_compare_and_swap(tmp_path: Path) -> None:
    store = _store(tmp_path)
    snapshot = store.dynamic_state.read(workspace_key="project:bridge", state_key="release:current")
    with ProcessPoolExecutor(max_workers=2, mp_context=get_context("spawn")) as executor:
        futures = [
            executor.submit(
                _process_competing_transition,
                str(store.db_path),
                str(store.log_dir),
                str(snapshot["database_epoch"]),
                key,
            )
            for key in ("state:process:one", "state:process:two")
        ]
        outcomes = [future.result(timeout=30) for future in futures]
    assert [status for status, _ in outcomes].count("ok") == 1
    assert [status for status, _ in outcomes].count("conflict") == 1


def test_database_enforces_immutable_state_authority_and_request_outcomes(tmp_path: Path) -> None:
    store = _store(tmp_path)
    initial = store.dynamic_state.read(workspace_key="project:bridge", state_key="release:current")
    mutation = _transition(
        store,
        to_status="draft",
        expected_version=0,
        expected_database_epoch=str(initial["database_epoch"]),
        idempotency_key="state:immutable",
    )
    with store._connect() as conn:
        with pytest.raises(sqlite3.IntegrityError, match="state_resources is immutable"):
            conn.execute("UPDATE state_resources SET state_type = 'release-state'")
        with pytest.raises(sqlite3.IntegrityError, match="state_mutations is append-only"):
            conn.execute(
                "UPDATE state_mutations SET value_json = '{}' WHERE mutation_id = ?", (mutation["mutation_id"],)
            )
        with pytest.raises(sqlite3.IntegrityError, match="state_request_outcomes is append-only"):
            conn.execute("UPDATE state_request_outcomes SET outcome_type = 'conflict'")
