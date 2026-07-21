from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from agent_mem_bridge.cli import main
from agent_mem_bridge.database_maintenance import inspect_database
from agent_mem_bridge.storage import MemoryStore


def test_strict_signal_policy_requires_claim_before_ack(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_REQUIRE_CLAIM_BEFORE_ACK", "true")
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    created = store.store(namespace="project:signals", content="strict ack", kind="signal")

    pending = store.ack_signal(str(created["id"]))
    claimed = store.claim_signal(
        namespace="project:signals",
        consumer="worker-a",
        lease_seconds=60,
        signal_id=str(created["id"]),
    )
    acked = store.ack_signal(str(created["id"]), consumer="worker-a")

    assert pending["acked"] is False
    assert pending["reason"] == "claim-required"
    assert claimed["claimed"] is True
    assert acked["acked"] is True


def test_database_trigger_rejects_new_malformed_claimed_state(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    created = store.store(namespace="project:signals", content="trigger guard", kind="signal")

    with store._connect() as conn, pytest.raises(sqlite3.IntegrityError, match="invalid signal state"):
        conn.execute(
            """
            UPDATE memories
            SET signal_status = 'claimed', claimed_by = 'worker-a', claimed_at = NULL,
                lease_expires_at = NULL
            WHERE id = ?
            """,
            (created["id"],),
        )


def test_database_trigger_rejects_pending_claim_residue_and_acked_lease(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    pending = store.store(namespace="project:signals", content="pending residue", kind="signal")
    acked = store.store(namespace="project:signals", content="acked residue", kind="signal")

    with store._connect() as conn, pytest.raises(sqlite3.IntegrityError, match="invalid signal state"):
        conn.execute(
            "UPDATE memories SET claimed_by = 'ghost' WHERE id = ?",
            (pending["id"],),
        )
    with store._connect() as conn, pytest.raises(sqlite3.IntegrityError, match="invalid signal state"):
        conn.execute(
            """
            UPDATE memories
            SET signal_status = 'acked',
                acknowledged_at = '2026-07-21T12:01:00+00:00',
                lease_expires_at = '2026-07-21T12:02:00+00:00'
            WHERE id = ?
            """,
            (acked["id"],),
        )


def test_database_health_flags_pending_claim_residue(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    pending = store.store(namespace="project:signals", content="legacy pending residue", kind="signal")
    with store._connect() as conn:
        conn.execute("DROP TRIGGER validate_signal_state_update")
        conn.execute(
            """
            UPDATE memories
            SET claimed_by = 'ghost',
                claimed_at = '2026-07-21T12:00:00+00:00',
                lease_expires_at = '2026-07-21T12:01:00+00:00'
            WHERE id = ?
            """,
            (pending["id"],),
        )
        conn.commit()

    report = inspect_database(store.db_path, full=False)

    assert report["ok"] is False
    assert report["content"]["counts"]["invalid_signal_state_count"] == 1


def test_explicit_signal_repair_resets_invalid_claim_and_records_reason(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    created = store.store(namespace="project:signals", content="repair me", kind="signal")
    with store._connect() as conn:
        conn.execute("DROP TRIGGER validate_signal_state_update")
        conn.execute(
            """
            UPDATE memories
            SET signal_status = 'claimed', claimed_by = 'worker-a',
                claimed_at = '2026-07-21T12:00:00+00:00', lease_expires_at = NULL
            WHERE id = ?
            """,
            (created["id"],),
        )
        conn.commit()

    result = store.repair_signal(
        str(created["id"]),
        reason="Legacy row has no lease expiry",
        actor="operator-a",
    )

    assert result["repaired"] is True
    assert result["previous_state"]["signal_status"] == "claimed"
    assert result["repaired_state"]["signal_status"] == "pending"
    assert result["item"]["signal_status"] == "pending"
    with store._connect() as conn:
        receipt = conn.execute("SELECT * FROM signal_repairs").fetchone()
    assert receipt["signal_id"] == created["id"]
    assert receipt["reason"] == "Legacy row has no lease expiry"
    assert receipt["actor"] == "operator-a"
    assert json.loads(receipt["previous_state_json"])["lease_expires_at"] is None


def test_signal_repair_cli_uses_explicit_reason_and_nonzero_for_missing(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    bridge_home = tmp_path / "bridge-home"
    db_path = bridge_home / "bridge.db"
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_HOME", str(bridge_home))
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_DB_PATH", str(db_path))
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_LOG_DIR", str(bridge_home / "logs"))
    store = MemoryStore(db_path, log_dir=bridge_home / "logs")
    created = store.store(namespace="project:signals", content="repair through cli", kind="signal")
    with store._connect() as conn:
        conn.execute("DROP TRIGGER validate_signal_state_update")
        conn.execute(
            "UPDATE memories SET signal_status = 'claimed', lease_expires_at = NULL WHERE id = ?",
            (created["id"],),
        )
        conn.commit()

    exit_code = main(
        [
            "signal-repair",
            "--id",
            str(created["id"]),
            "--reason",
            "CLI operator repair",
            "--actor",
            "operator-cli",
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["repaired"] is True
    assert payload["reason"] == "CLI operator repair"
