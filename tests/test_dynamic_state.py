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


def _process_competing_commit(
    db_path: str,
    log_dir: str,
    database_epoch: str,
    status: str,
) -> tuple[str, str]:
    store = MemoryStore(Path(db_path), log_dir=Path(log_dir))
    try:
        store.dynamic_state.commit(
            workspace_key="project:bridge",
            state_key="release:current",
            value={"status": status},
            expected_version=0,
            expected_database_epoch=database_epoch,
            idempotency_key=f"state:process:{status}",
        )
        return "ok", status
    except ValueError as exc:
        return "conflict", str(exc)


def _commit(
    store: MemoryStore,
    *,
    value: dict[str, object],
    expected_version: int,
    expected_database_epoch: str,
    idempotency_key: str,
) -> dict[str, object]:
    return store.dynamic_state.commit(
        workspace_key="project:bridge",
        state_key="release:current",
        value=value,
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


def test_exact_key_reads_and_commits_stay_outside_semantic_memory(tmp_path: Path) -> None:
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
    first = _commit(
        store,
        value={"status": "draft", "owner": "release"},
        expected_version=0,
        expected_database_epoch=str(absent["database_epoch"]),
        idempotency_key="state:first",
    )

    assert first["version"] == 1
    assert first["base_version"] == 0
    assert first["operation"] == "set"
    current = store.dynamic_state.read(workspace_key="project:bridge", state_key="release:current")
    assert current["exists"] is True
    assert current["version"] == 1
    assert current["value"] == {"owner": "release", "status": "draft"}
    assert current["database_epoch"] == first["database_epoch"]
    assert store.recall(namespace="project:bridge", query="draft release", kind="memory")["count"] == 0

    history = store.dynamic_state.history(workspace_key="project:bridge", state_key="release:current")
    assert history["has_more"] is False
    assert history["mutations"] == [
        {
            "mutation_id": first["mutation_id"],
            "base_version": 0,
            "version": 1,
            "operation": "set",
            "value": {"owner": "release", "status": "draft"},
            "value_hash": first["value_hash"],
            "restore_of_mutation_id": None,
            "provenance": {
                "session_id": "session:dynamic-state",
                "correlation_id": "change:dynamic-state",
                "actor": "test-agent",
                "source_client": "pytest",
            },
            "created_at": first["created_at"],
        }
    ]


def test_stale_writers_and_conflicting_idempotency_keys_leave_authority_unchanged(tmp_path: Path) -> None:
    store = _store(tmp_path)
    snapshot = store.dynamic_state.read(workspace_key="project:bridge", state_key="release:current")
    first = _commit(
        store,
        value={"status": "draft"},
        expected_version=0,
        expected_database_epoch=str(snapshot["database_epoch"]),
        idempotency_key="state:accepted",
    )

    replay = _commit(
        store,
        value={"status": "draft"},
        expected_version=0,
        expected_database_epoch=str(snapshot["database_epoch"]),
        idempotency_key="state:accepted",
    )
    assert replay == {
        "mutation_id": first["mutation_id"],
        "base_version": 0,
        "version": 1,
        "operation": "set",
        "value": {"status": "draft"},
        "value_hash": first["value_hash"],
        "restore_of_mutation_id": None,
        "created_at": first["created_at"],
        "database_epoch": first["database_epoch"],
        "idempotent_replay": True,
    }
    with store._connect() as conn:
        before_count = int(conn.execute("SELECT COUNT(*) FROM state_mutations").fetchone()[0])

    with pytest.raises(ValueError, match="STATE_VERSION_CONFLICT: expected 0, actual 1"):
        _commit(
            store,
            value={"status": "published"},
            expected_version=0,
            expected_database_epoch=str(snapshot["database_epoch"]),
            idempotency_key="state:stale",
        )
    with pytest.raises(ValueError, match="idempotency key was already used with a different payload"):
        _commit(
            store,
            value={"status": "published"},
            expected_version=1,
            expected_database_epoch=str(snapshot["database_epoch"]),
            idempotency_key="state:accepted",
        )

    with store._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM state_mutations").fetchone()[0] == before_count
    assert store.dynamic_state.read(workspace_key="project:bridge", state_key="release:current")["value"] == {
        "status": "draft"
    }


def test_restore_is_a_new_version_and_heads_are_rebuildable(tmp_path: Path) -> None:
    store = _store(tmp_path)
    initial = store.dynamic_state.read(workspace_key="project:bridge", state_key="release:current")
    first = _commit(
        store,
        value={"status": "draft"},
        expected_version=0,
        expected_database_epoch=str(initial["database_epoch"]),
        idempotency_key="state:one",
    )
    second = _commit(
        store,
        value={"status": "published"},
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
    assert restored["operation"] == "restore"
    assert restored["version"] == 3
    assert restored["restore_of_mutation_id"] == first["mutation_id"]
    assert restored["value"] == {"status": "draft"}

    with store._connect() as conn:
        conn.execute("DELETE FROM state_heads")
        conn.commit()
    database_health = inspect_database(store.db_path)
    assert database_health["ok"] is False
    assert database_health["content"]["counts"]["missing_state_head_count"] == 1
    degraded = store.dynamic_state.inspect_heads()
    assert degraded["ok"] is False
    assert degraded["counts"]["missing_state_head_count"] == 1
    with pytest.raises(RuntimeError, match="state head projection health is degraded; write refused"):
        _commit(
            store,
            value={"status": "blocked"},
            expected_version=3,
            expected_database_epoch=str(restored["database_epoch"]),
            idempotency_key="state:must-not-write-to-degraded-head",
        )

    rebuilt_maintenance = rebuild_database_projections(store.db_path)
    assert rebuilt_maintenance["state_head_rebuilt_count"] == 1
    assert rebuilt_maintenance["health"]["ok"] is True
    assert store.dynamic_state.inspect_heads()["ok"] is True
    rebuilt = store.dynamic_state.read(workspace_key="project:bridge", state_key="release:current")
    assert rebuilt["version"] == 3
    assert rebuilt["value"] == {"status": "draft"}


def test_restore_rotates_epoch_and_rejects_pre_restore_state_writer(tmp_path: Path) -> None:
    store = _store(tmp_path)
    initial = store.dynamic_state.read(workspace_key="project:bridge", state_key="release:current")
    first = _commit(
        store,
        value={"status": "draft"},
        expected_version=0,
        expected_database_epoch=str(initial["database_epoch"]),
        idempotency_key="state:before-backup",
    )
    backup_path = tmp_path / "backup.db"
    backup_database(store.db_path, backup_path)
    stale_epoch = str(first["database_epoch"])

    restored = restore_database(backup_path, store.db_path, force=True)
    reopened = MemoryStore(store.db_path, log_dir=store.log_dir)
    restored_state = reopened.dynamic_state.read(workspace_key="project:bridge", state_key="release:current")

    assert restored["database_epoch"] != stale_epoch
    assert restored_state["version"] == 1
    assert restored_state["value"] == {"status": "draft"}
    with pytest.raises(ValueError, match="DATABASE_EPOCH_CONFLICT"):
        _commit(
            reopened,
            value={"status": "published"},
            expected_version=1,
            expected_database_epoch=stale_epoch,
            idempotency_key="state:pre-restore-writer",
        )
    assert reopened.dynamic_state.read(workspace_key="project:bridge", state_key="release:current")["version"] == 1


def test_one_of_two_concurrent_writers_wins_the_same_version_compare_and_swap(tmp_path: Path) -> None:
    store = _store(tmp_path)
    snapshot = store.dynamic_state.read(workspace_key="project:bridge", state_key="release:current")

    def competing_writer(status: str) -> tuple[str, str]:
        try:
            _commit(
                store,
                value={"status": status},
                expected_version=0,
                expected_database_epoch=str(snapshot["database_epoch"]),
                idempotency_key=f"state:concurrent:{status}",
            )
            return "ok", status
        except ValueError as exc:
            return "conflict", str(exc)

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(competing_writer, ("review", "published")))

    assert [status for status, _ in outcomes].count("ok") == 1
    assert [status for status, _ in outcomes].count("conflict") == 1
    assert "STATE_VERSION_CONFLICT: expected 0, actual 1" in next(
        detail for status, detail in outcomes if status == "conflict"
    )
    current = store.dynamic_state.read(workspace_key="project:bridge", state_key="release:current")
    assert current["version"] == 1
    assert current["value"] in ({"status": "review"}, {"status": "published"})


def test_two_processes_competing_on_same_state_version_allow_exactly_one_writer(tmp_path: Path) -> None:
    store = _store(tmp_path)
    snapshot = store.dynamic_state.read(workspace_key="project:bridge", state_key="release:current")
    context = get_context("spawn")

    with ProcessPoolExecutor(max_workers=2, mp_context=context) as executor:
        futures = [
            executor.submit(
                _process_competing_commit,
                str(store.db_path),
                str(store.log_dir),
                str(snapshot["database_epoch"]),
                status,
            )
            for status in ("review", "published")
        ]
        outcomes = [future.result(timeout=30) for future in futures]

    assert [result for result, _ in outcomes].count("ok") == 1
    assert [result for result, _ in outcomes].count("conflict") == 1
    assert "STATE_VERSION_CONFLICT: expected 0, actual 1" in next(
        detail for result, detail in outcomes if result == "conflict"
    )
    current = store.dynamic_state.read(workspace_key="project:bridge", state_key="release:current")
    assert current["version"] == 1
    assert current["value"] in ({"status": "review"}, {"status": "published"})


def test_database_enforces_immutable_resources_and_mutations(tmp_path: Path) -> None:
    store = _store(tmp_path)
    initial = store.dynamic_state.read(workspace_key="project:bridge", state_key="release:current")
    mutation = _commit(
        store,
        value={"status": "draft"},
        expected_version=0,
        expected_database_epoch=str(initial["database_epoch"]),
        idempotency_key="state:immutable",
    )

    with store._connect() as conn:
        with pytest.raises(sqlite3.IntegrityError, match="state_resources is immutable"):
            conn.execute(
                "UPDATE state_resources SET state_type = 'release-state' WHERE workspace_key = ? AND state_key = ?",
                ("project:bridge", "release:current"),
            )
        with pytest.raises(sqlite3.IntegrityError, match="state_mutations is append-only"):
            conn.execute(
                "UPDATE state_mutations SET value_json = '{}' WHERE mutation_id = ?",
                (mutation["mutation_id"],),
            )


def test_state_value_rejects_durable_hidden_reasoning_fields(tmp_path: Path) -> None:
    store = _store(tmp_path)
    snapshot = store.dynamic_state.read(workspace_key="project:bridge", state_key="release:current")

    with pytest.raises(ValueError, match="release state value rejects field: chain_of_thought"):
        _commit(
            store,
            value={"chain_of_thought": "private"},
            expected_version=0,
            expected_database_epoch=str(snapshot["database_epoch"]),
            idempotency_key="state:reject-private-reasoning",
        )
    with pytest.raises(ValueError, match="release state status must be one of"):
        _commit(
            store,
            value={"status": "unknown"},
            expected_version=0,
            expected_database_epoch=str(snapshot["database_epoch"]),
            idempotency_key="state:reject-unknown-status",
        )
    with pytest.raises(ValueError, match="release state value has unsupported fields: \\['unexpected'\\]"):
        _commit(
            store,
            value={"status": "draft", "unexpected": "value"},
            expected_version=0,
            expected_database_epoch=str(snapshot["database_epoch"]),
            idempotency_key="state:reject-unknown-field",
        )
    assert store.dynamic_state.read(workspace_key="project:bridge", state_key="release:current")["exists"] is False
