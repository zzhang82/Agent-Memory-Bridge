from __future__ import annotations

import json
import os
import sqlite3
import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent_mem_bridge.cli import main
from agent_mem_bridge.database_maintenance import (
    backup_database,
    checkpoint_database,
    cleanup_signals,
    inspect_database,
    rebuild_database_projections,
    restore_database,
    verify_backup,
)
from agent_mem_bridge.filesystem_safety import path_storage_warnings
from agent_mem_bridge.service_lock import ServiceFileLock, ServiceLockConflict
from agent_mem_bridge.storage import MemoryStore


def _seed_store(tmp_path: Path, *, content: str = "original durable memory") -> MemoryStore:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    store.store(namespace="project:test", kind="memory", title="Seed", content=content)
    return store


def test_database_health_checks_integrity_structures_and_metrics(tmp_path: Path) -> None:
    store = _seed_store(tmp_path)

    report = inspect_database(store.db_path, log_dir=store.log_dir)

    assert report["ok"] is True
    assert report["integrity"] == {"ok": True, "results": ["ok"]}
    assert report["foreign_keys"]["violation_count"] == 0
    assert report["content"]["ok"] is True
    assert report["metrics"]["memory_count"] == 1
    assert report["metrics"]["database_bytes"] > 0
    assert report["metrics"]["journal_mode"] == "wal"


def test_database_health_detects_malformed_json_and_embedding_shape(tmp_path: Path) -> None:
    store = _seed_store(tmp_path)
    with store._connect() as conn:
        memory_id = conn.execute("SELECT id FROM memories LIMIT 1").fetchone()[0]
        content_hash = conn.execute("SELECT content_hash FROM memories WHERE id = ?", (memory_id,)).fetchone()[0]
        conn.execute("UPDATE memories SET tags_json = '{' WHERE id = ?", (memory_id,))
        conn.execute(
            """
            INSERT INTO memory_embeddings (
                memory_id, content_hash, embedding_model, embedding_dim, vector_json, created_at
            ) VALUES (?, ?, 'broken', 3, '[1.0, 2.0]', '2026-01-01T00:00:00+00:00')
            """,
            (memory_id, content_hash),
        )
        conn.commit()

    report = inspect_database(store.db_path)

    assert report["ok"] is False
    counts = report["content"]["counts"]
    assert counts["malformed_tags_json_count"] == 1
    assert counts["embedding_dimension_mismatch_count"] == 1


def test_backup_verify_and_restore_round_trip_with_recovery_copy(tmp_path: Path) -> None:
    store = _seed_store(tmp_path)
    backup_path = tmp_path / "backups" / "bridge-backup.db"

    backup = backup_database(store.db_path, backup_path)
    verified = verify_backup(backup_path)
    store.store(namespace="project:test", kind="memory", title="Later", content="later mutation")
    restored = restore_database(backup_path, store.db_path, force=True)

    assert backup["ok"] is True
    assert verified["ok"] is True
    assert restored["ok"] is True
    assert restored["recovery_backup"] is not None
    assert Path(restored["recovery_backup"]).is_file()
    recovered_store = MemoryStore(store.db_path, log_dir=store.log_dir)
    items = recovered_store.browse(namespace="project:test", kind="memory", limit=10)["items"]
    assert [item["content"] for item in items] == ["original durable memory"]


def test_backup_refuses_overwrite_without_force(tmp_path: Path) -> None:
    store = _seed_store(tmp_path)
    backup_path = tmp_path / "backup.db"
    backup_database(store.db_path, backup_path)

    with pytest.raises(FileExistsError):
        backup_database(store.db_path, backup_path)

    assert backup_database(store.db_path, backup_path, force=True)["ok"] is True


def test_wal_checkpoint_reports_frames_and_rejects_invalid_mode(tmp_path: Path) -> None:
    store = _seed_store(tmp_path)

    report = checkpoint_database(store.db_path, mode="truncate")

    assert report["ok"] is True
    assert report["mode"] == "truncate"
    assert report["busy"] == 0
    with pytest.raises(ValueError, match="checkpoint mode"):
        checkpoint_database(store.db_path, mode="unsafe")


def test_signal_cleanup_is_dry_run_by_default_and_tombstones_applied_deletes(tmp_path: Path) -> None:
    store = _seed_store(tmp_path)
    signal = store.store(namespace="project:test", kind="signal", content="old signal")
    with store._connect() as conn:
        conn.execute(
            """
            UPDATE memories
            SET signal_status = 'acked', acknowledged_at = '2025-01-01T00:00:00+00:00'
            WHERE id = ?
            """,
            (signal["id"],),
        )
        conn.commit()

    preview = cleanup_signals(
        store.db_path,
        acked_older_than_days=30,
        expired_older_than_days=7,
        now=datetime(2026, 7, 21, tzinfo=UTC),
    )
    applied = cleanup_signals(
        store.db_path,
        acked_older_than_days=30,
        expired_older_than_days=7,
        apply=True,
        now=datetime(2026, 7, 21, tzinfo=UTC),
    )

    assert preview["candidate_count"] == 1
    assert preview["deleted_count"] == 0
    assert preview["applied"] is False
    assert applied["deleted_count"] == 1
    with store._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM memories WHERE id = ?", (signal["id"],)).fetchone()[0] == 0
        tombstone = conn.execute(
            "SELECT cause FROM memory_tombstones WHERE forgotten_id = ?",
            (signal["id"],),
        ).fetchone()
    assert tombstone["cause"] == "signal-retention"


def test_database_maintenance_cli_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    store = _seed_store(tmp_path)
    backup_path = tmp_path / "cli-backup.db"
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_HOME", str(tmp_path))
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_DB_PATH", str(store.db_path))
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_LOG_DIR", str(store.log_dir))

    assert main(["db-health", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    assert main(["backup", "--output", str(backup_path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    assert main(["verify-backup", "--input", str(backup_path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    assert main(["wal-checkpoint", "--mode", "passive", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode bits only")
def test_managed_database_logs_and_state_are_private(tmp_path: Path) -> None:
    store = _seed_store(tmp_path)
    store._log("test", {"ok": True})

    assert stat.S_IMODE(store.db_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(store.log_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE((store.log_dir / "test.log").stat().st_mode) == 0o600


def test_storage_path_warnings_cover_sync_and_network_locations() -> None:
    assert path_storage_warnings(Path("/mnt/c/Users/Test/OneDrive/AMB")) == [
        "sync-folder-path",
        "wsl-mounted-sync-folder",
    ]
    assert path_storage_warnings(Path("//server/share/amb")) == ["network-share-path"]


def test_restore_rotates_database_epoch_and_rejects_pre_restore_poll_cursor(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    store.store(namespace="project:test", kind="signal", content="before backup")
    initial_poll = store.recall(namespace="project:test", kind="signal", limit=1)
    backup_path = tmp_path / "backup.db"
    backup_database(store.db_path, backup_path)
    for index in range(5):
        store.store(namespace="project:test", kind="signal", content=f"after backup {index}")
    advanced_cursor = store.recall(namespace="project:test", kind="signal", limit=20)["next_since"]

    restored = restore_database(backup_path, store.db_path, force=True)
    reopened = MemoryStore(store.db_path, log_dir=store.log_dir)

    assert restored["database_epoch"]
    with pytest.raises(ValueError, match="database epoch mismatch"):
        reopened.recall(namespace="project:test", kind="signal", since=advanced_cursor, limit=20)
    with pytest.raises(ValueError, match="database epoch mismatch"):
        reopened.recall(namespace="project:test", kind="signal", since=initial_poll["next_since"], limit=20)


def test_restore_uses_service_lock_and_keeps_open_connections_on_one_database_view(tmp_path: Path) -> None:
    target = _seed_store(tmp_path, content="old authority")
    backup_store = MemoryStore(tmp_path / "source" / "bridge.db", log_dir=tmp_path / "source" / "logs")
    backup_store.store(namespace="project:test", content="restored authority")
    backup_path = tmp_path / "backup.db"
    backup_database(backup_store.db_path, backup_path)
    lock_path = tmp_path / "service.lock"

    with ServiceFileLock(lock_path):
        with pytest.raises(ServiceLockConflict):
            restore_database(backup_path, target.db_path, force=True, service_lock_path=lock_path)

    open_connection = sqlite3.connect(target.db_path)
    try:
        assert open_connection.execute("SELECT content FROM memories").fetchone()[0] == "old authority"
        restore_database(backup_path, target.db_path, force=True, service_lock_path=lock_path)
        assert open_connection.execute("SELECT content FROM memories").fetchone()[0] == "restored authority"
    finally:
        open_connection.close()


def test_wal_checkpoint_exposes_busy_reader_then_truncates_after_release(tmp_path: Path) -> None:
    store = _seed_store(tmp_path)
    reader = sqlite3.connect(store.db_path)
    reader.execute("BEGIN")
    reader.execute("SELECT COUNT(*) FROM memories").fetchone()
    for index in range(200):
        store.store(namespace="project:wal", kind="signal", content=f"wal row {index}")
    wal_path = Path(f"{store.db_path}-wal")
    assert wal_path.stat().st_size > 0

    busy = checkpoint_database(store.db_path, mode="truncate")
    assert busy["ok"] is False
    assert busy["busy"] == 1
    reader.rollback()
    reader.close()

    completed = checkpoint_database(store.db_path, mode="truncate")
    assert completed["ok"] is True
    assert completed["wal_bytes"] == 0


def test_signal_cleanup_degrades_retained_dependents_and_keeps_health_green(tmp_path: Path) -> None:
    store = _seed_store(tmp_path)
    signal = store.store(namespace="project:test", kind="signal", content="referenced old signal")
    dependent = store.store(
        namespace="project:test",
        content=f"record_type: learn\nclaim: Retained dependent.\ndepends_on: {signal['id']}",
        tags=["kind:learn"],
    )
    with store._connect() as conn:
        conn.execute(
            """
            UPDATE memories
            SET signal_status = 'acked', acknowledged_at = '2025-01-01T00:00:00+00:00'
            WHERE id = ?
            """,
            (signal["id"],),
        )
        conn.commit()

    result = cleanup_signals(
        store.db_path,
        acked_older_than_days=30,
        expired_older_than_days=7,
        apply=True,
        now=datetime(2026, 7, 21, tzinfo=UTC),
    )
    health = inspect_database(store.db_path)
    retained = store.recall(namespace="project:test", query="Retained dependent", limit=1)["items"][0]

    assert result["deleted_ids"] == [signal["id"]]
    assert retained["id"] == dependent["id"]
    assert retained["lineage_status"] == "degraded"
    assert health["ok"] is True


def test_database_health_detects_stale_metadata_tag_edge_and_fts_projections(tmp_path: Path) -> None:
    store = _seed_store(tmp_path)
    target = store.store(namespace="project:test", content="record_type: learn\nclaim: Target")
    source = store.store(
        namespace="project:test",
        content=f"record_type: learn\nclaim: Before mutation\nsupports: {target['id']}",
        tags=["kind:learn", "topic:old"],
        title="Before title",
    )
    with store._connect() as conn:
        conn.execute(
            """
            UPDATE memories
            SET title = 'After title', content = 'record_type: belief\nclaim: After mutation',
                tags_json = '["kind:belief", "topic:new"]'
            WHERE id = ?
            """,
            (source["id"],),
        )
        conn.commit()

    report = inspect_database(store.db_path)
    counts = report["content"]["counts"]

    assert report["ok"] is False
    assert counts["stale_content_hash_count"] == 1
    assert counts["stale_metadata_projection_count"] == 1
    assert counts["stale_tag_projection_count"] == 1
    assert counts["stale_edge_projection_count"] == 1
    assert counts["stale_fts_projection_count"] == 1

    repaired = rebuild_database_projections(store.db_path)
    assert repaired["ok"] is True
    assert repaired["rebuilt_count"] == 3


def test_database_health_flags_canonical_metadata_validation_issues(tmp_path: Path) -> None:
    store = _seed_store(tmp_path)
    with store._connect() as conn:
        memory_id = conn.execute("SELECT id FROM memories LIMIT 1").fetchone()["id"]
        conn.execute(
            """
            UPDATE memories
            SET content = 'record_type: belief\nclaim: Invalid confidence\nconfidence: 2.0'
            WHERE id = ?
            """,
            (memory_id,),
        )
        conn.commit()

    repaired = rebuild_database_projections(store.db_path)
    report = inspect_database(store.db_path)

    assert repaired["ok"] is False
    assert report["ok"] is False
    assert report["content"]["counts"]["invalid_metadata_value_count"] == 1
    with store._connect() as conn:
        validation_issues = json.loads(
            conn.execute(
                "SELECT validation_issues_json FROM memory_metadata WHERE memory_id = ?",
                (memory_id,),
            ).fetchone()["validation_issues_json"]
        )
    assert {issue["type"] for issue in validation_issues} == {"invalid_confidence"}


def test_projection_repair_restores_missing_insertion_sequence_and_rotates_epoch(tmp_path: Path) -> None:
    store = _seed_store(tmp_path)
    before_epoch = store.database_epoch()
    with store._connect() as conn:
        memory_id = conn.execute("SELECT id FROM memories LIMIT 1").fetchone()["id"]
        conn.execute("DELETE FROM memory_insertions WHERE memory_id = ?", (memory_id,))
        conn.commit()

    unhealthy = inspect_database(store.db_path)
    repaired = rebuild_database_projections(store.db_path)

    assert unhealthy["ok"] is False
    assert unhealthy["content"]["counts"]["missing_insertion_sequence_count"] == 1
    assert repaired["ok"] is True
    assert repaired["repaired_insertion_sequence_count"] == 1
    assert repaired["database_epoch"] != before_epoch
    with store._connect() as conn:
        sequence = conn.execute(
            "SELECT sequence FROM memory_insertions WHERE memory_id = ?",
            (memory_id,),
        ).fetchone()
    assert sequence is not None


def test_database_health_and_repair_handle_invalid_signal_timestamps(tmp_path: Path) -> None:
    store = _seed_store(tmp_path)
    signal = store.store(namespace="project:test", kind="signal", content="repair invalid timestamps")
    with store._connect() as conn:
        conn.execute("DROP TRIGGER validate_signal_state_update")
        conn.execute(
            """
            UPDATE memories
            SET signal_status = 'claimed', claimed_by = 'worker',
                claimed_at = 'also-not-a-date', lease_expires_at = 'not-a-date'
            WHERE id = ?
            """,
            (signal["id"],),
        )
        conn.commit()

    report = inspect_database(store.db_path)
    claim = store.claim_signal(
        namespace="project:test",
        consumer="other",
        lease_seconds=60,
        signal_id=str(signal["id"]),
    )
    repaired = store.repair_signal(str(signal["id"]), reason="invalid timestamps", actor="doctor")

    assert report["ok"] is False
    assert report["content"]["counts"]["invalid_signal_state_count"] == 1
    assert claim["claimed"] is False
    assert repaired["repaired"] is True
    assert {"invalid-claimed-at", "invalid-lease-expires-at"}.issubset(repaired["validation_issues"])
