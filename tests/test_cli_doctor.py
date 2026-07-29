from __future__ import annotations

import json
import os
from pathlib import Path

from agent_mem_bridge.cli import main
from agent_mem_bridge.onboarding import run_doctor
from agent_mem_bridge.storage import MemoryStore

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _isolate_doctor_runtime(tmp_path: Path, monkeypatch) -> dict[str, Path]:
    bridge_home = tmp_path / "bridge-home"
    db_path = bridge_home / "bridge.db"
    log_dir = bridge_home / "logs"
    config_path = tmp_path / "config.toml"
    config_path.write_text("", encoding="utf-8")
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_HOME", str(bridge_home))
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_DB_PATH", str(db_path))
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_LOG_DIR", str(log_dir))
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_CONFIG", str(config_path))
    MemoryStore(db_path=db_path, log_dir=log_dir)
    return {
        "bridge_home": bridge_home,
        "db_path": db_path,
        "log_dir": log_dir,
        "config_path": config_path,
    }


def test_run_doctor_returns_structured_checks(tmp_path: Path, monkeypatch) -> None:
    runtime = _isolate_doctor_runtime(tmp_path, monkeypatch)

    report = run_doctor(project_root=PROJECT_ROOT)

    assert report["ok"] is True
    assert report["bridge_home"] == str(runtime["bridge_home"])
    assert report["db_path"] == str(runtime["db_path"])
    assert report["log_dir"] == str(runtime["log_dir"])
    assert report["config_path"] == str(runtime["config_path"])
    check_names = {check["name"] for check in report["checks"]}
    assert {
        "python_version",
        "sqlite_fts5",
        "signal_lifecycle_state",
        "config_path",
        "resolved_defaults",
    } <= check_names


def test_cli_doctor_json_output(tmp_path: Path, monkeypatch, capsys) -> None:
    runtime = _isolate_doctor_runtime(tmp_path, monkeypatch)

    exit_code = main(["doctor", "--json", "--project-root", str(PROJECT_ROOT)])
    captured = capsys.readouterr()

    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["ok"] is True
    assert payload["bridge_home"] == str(runtime["bridge_home"])
    assert payload["db_path"] == str(runtime["db_path"])
    assert payload["log_dir"] == str(runtime["log_dir"])
    assert payload["config_path"] == str(runtime["config_path"])
    assert any(check["name"] == "sqlite_fts5" for check in payload["checks"])


def test_run_doctor_fails_for_claimed_signal_without_lease(tmp_path: Path, monkeypatch) -> None:
    bridge_home = tmp_path / "bridge-home"
    db_path = bridge_home / "bridge.db"
    log_dir = bridge_home / "logs"
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_HOME", str(bridge_home))
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_DB_PATH", str(db_path))
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_LOG_DIR", str(log_dir))
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_CONFIG", str(tmp_path / "missing-config.toml"))

    store = MemoryStore(db_path=db_path, log_dir=log_dir)
    signal = store.store(
        namespace="project:test",
        kind="signal",
        content="stuck signal",
    )
    with store._connect() as conn:
        conn.execute("DROP TRIGGER validate_signal_state_update")
        conn.execute(
            """
            UPDATE memories
            SET signal_status = 'claimed',
                claimed_by = 'worker-a',
                claimed_at = '2026-07-21T12:00:00+00:00',
                lease_expires_at = NULL
            WHERE id = ?
            """,
            (signal["id"],),
        )
        conn.commit()

    report = run_doctor(project_root=Path(__file__).resolve().parents[1])
    check = next(item for item in report["checks"] if item["name"] == "signal_lifecycle_state")

    assert report["ok"] is False
    assert check["status"] == "fail"
    assert check["invalid_count"] == 1
    assert check["invalid_ids"] == [signal["id"]]


def test_hardened_doctor_fails_for_non_private_posix_bridge_home(tmp_path: Path, monkeypatch) -> None:
    if os.name != "posix":
        return
    bridge_home = tmp_path / "bridge-home"
    db_path = bridge_home / "bridge.db"
    log_dir = bridge_home / "logs"
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_HOME", str(bridge_home))
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_DB_PATH", str(db_path))
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_LOG_DIR", str(log_dir))
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_OPERATING_PROFILE", "hardened-local")
    MemoryStore(db_path=db_path, log_dir=log_dir)
    bridge_home.chmod(0o755)

    report = run_doctor(project_root=Path(__file__).resolve().parents[1])
    check = next(item for item in report["checks"] if item["name"] == "bridge_home_permissions")

    assert report["ok"] is False
    assert check["status"] == "fail"
