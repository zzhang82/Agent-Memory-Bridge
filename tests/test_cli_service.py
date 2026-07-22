from __future__ import annotations

import json
import multiprocessing
from pathlib import Path

import pytest

from agent_mem_bridge import service as service_module
from agent_mem_bridge.cli import main
from agent_mem_bridge.service_health import ServiceHealthWriter
from agent_mem_bridge.service_lock import ServiceFileLock, ServiceLockConflict
from agent_mem_bridge.storage import MemoryStore


def _hold_service_lock(lock_path: str, ready: object, release: object) -> None:
    with ServiceFileLock(Path(lock_path)):
        ready.set()
        release.wait(10)


def test_cli_service_once_requests_single_cycle(monkeypatch) -> None:
    calls: list[tuple[bool | None, bool]] = []

    def fake_run_service(
        *,
        once: bool | None = None,
        allow_multiple_services: bool = False,
    ) -> dict[str, dict[str, object]]:
        calls.append((once, allow_multiple_services))
        return {"watcher": {"status": "ok"}}

    monkeypatch.setattr(service_module, "run_service", fake_run_service)

    exit_code = main(["service", "--once"])

    assert exit_code == 0
    assert calls == [(True, False)]


def test_cli_service_without_once_preserves_loop_mode(monkeypatch) -> None:
    calls: list[tuple[bool | None, bool]] = []

    def fake_run_service(
        *,
        once: bool | None = None,
        allow_multiple_services: bool = False,
    ) -> None:
        calls.append((once, allow_multiple_services))

    monkeypatch.setattr(service_module, "run_service", fake_run_service)

    exit_code = main(["service"])

    assert exit_code == 0
    assert calls == [(False, False)]


def test_service_health_marks_backoff_cycle_degraded(tmp_path: Path) -> None:
    writer = ServiceHealthWriter(tmp_path / "service-health.json", version="test")
    result = {
        "embedding": {
            "status": "backoff",
            "consecutive_failures": 3,
        }
    }

    writer.lane_completed("embedding", result["embedding"])
    writer.cycle_completed(result)
    health = json.loads((tmp_path / "service-health.json").read_text(encoding="utf-8"))

    assert health["status"] == "degraded"
    assert health["failed_lane_count"] == 0
    assert health["backoff_lane_count"] == 1
    assert health["lanes"]["embedding"]["status"] == "backoff"


def test_cli_service_allows_explicit_multiple_service_override(monkeypatch) -> None:
    calls: list[tuple[bool | None, bool]] = []

    def fake_run_service(
        *,
        once: bool | None = None,
        allow_multiple_services: bool = False,
    ) -> dict[str, dict[str, object]]:
        calls.append((once, allow_multiple_services))
        return {"watcher": {"status": "ok"}}

    monkeypatch.setattr(service_module, "run_service", fake_run_service)

    exit_code = main(["service", "--once", "--allow-multiple-services"])

    assert exit_code == 0
    assert calls == [(True, True)]


def test_hardened_profile_rejects_multiple_service_override(monkeypatch, tmp_path: Path, capsys) -> None:
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_OPERATING_PROFILE", "hardened-local")
    monkeypatch.setattr(service_module, "resolve_bridge_home", lambda: tmp_path)

    assert main(["service", "--once", "--allow-multiple-services"]) == 2
    assert "does not allow multiple service instances" in capsys.readouterr().err


def test_cli_service_once_returns_nonzero_when_a_lane_fails(monkeypatch) -> None:
    monkeypatch.setattr(
        service_module,
        "run_service",
        lambda **kwargs: {
            "watcher": {"status": "failed"},
            "reflex": {"status": "ok"},
        },
    )

    assert main(["service", "--once"]) == 1


def test_cli_service_returns_lock_conflict_exit_code(monkeypatch, capsys, tmp_path: Path) -> None:
    def fail_with_lock_conflict(**kwargs) -> None:
        del kwargs
        raise ServiceLockConflict(tmp_path / "service.lock", {"pid": 12345})

    monkeypatch.setattr(service_module, "run_service", fail_with_lock_conflict)

    exit_code = main(["service", "--once"])

    assert exit_code == 3
    assert "service lock is already held" in capsys.readouterr().err


def test_cli_index_rebuild_refuses_when_service_lock_is_held_by_process(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    bridge_home = tmp_path / "bridge-home"
    db_path = bridge_home / "bridge.db"
    log_dir = bridge_home / "logs"
    MemoryStore(db_path=db_path, log_dir=log_dir).store(
        namespace="project:test",
        kind="memory",
        content="index rebuild must respect the service exclusion lock",
    )
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_HOME", str(bridge_home))
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_DB_PATH", str(db_path))
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_LOG_DIR", str(log_dir))

    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    process = context.Process(
        target=_hold_service_lock,
        args=(str(bridge_home / "service.lock"), ready, release),
    )
    process.start()
    try:
        assert ready.wait(10)
        exit_code = main(["index-rebuild", "--fts"])
        captured = capsys.readouterr()
    finally:
        release.set()
        process.join(10)
        if process.is_alive():
            process.terminate()
            process.join(5)

    assert process.exitcode == 0
    assert exit_code == 1
    assert captured.out == ""
    assert "agent-memory-bridge: index rebuild failed: service lock is already held" in captured.err
    assert str(bridge_home / "service.lock") in captured.err


def test_service_once_respects_disabled_watcher_and_reflex(monkeypatch, capsys, tmp_path: Path) -> None:
    calls: list[str] = []

    class FakeWatcher:
        def __init__(self, config) -> None:
            pass

        def run_once(self) -> dict[str, object]:
            calls.append("watcher")
            return {"processed_count": 1}

    class FakeReflex:
        def __init__(self, *, store, config) -> None:
            pass

        def run_once(self) -> dict[str, object]:
            calls.append("reflex")
            return {"processed_count": 1}

    class FakeConsolidation:
        def __init__(self, *, store, config) -> None:
            pass

        def run_once(self) -> dict[str, object]:
            calls.append("consolidation")
            return {"processed_count": 0}

    class FakeGovernance:
        def __init__(self, *, store, config) -> None:
            pass

        def run_once(self) -> dict[str, object]:
            calls.append("governance")
            return {"processed_count": 0}

    monkeypatch.setattr(service_module, "MemoryStore", lambda **kwargs: object())
    monkeypatch.setattr(service_module, "CodexSessionWatcher", FakeWatcher)
    monkeypatch.setattr(service_module, "ReflexEngine", FakeReflex)
    monkeypatch.setattr(service_module, "ConsolidationEngine", FakeConsolidation)
    monkeypatch.setattr(service_module, "GovernanceTriggerEngine", FakeGovernance)
    monkeypatch.setattr(service_module, "run_embedding_sidecar_maintenance", lambda store: {"processed_count": 0})
    monkeypatch.setattr(service_module, "resolve_watcher_enabled", lambda: False)
    monkeypatch.setattr(service_module, "resolve_reflex_enabled", lambda: False)
    monkeypatch.setattr(service_module, "resolve_bridge_home", lambda: tmp_path)
    monkeypatch.setattr(service_module, "resolve_bridge_db_path", lambda: __import__("pathlib").Path("bridge.db"))
    monkeypatch.setattr(service_module, "resolve_bridge_log_dir", lambda: __import__("pathlib").Path("logs"))
    monkeypatch.setattr(service_module, "resolve_sessions_root", lambda: __import__("pathlib").Path("sessions"))
    monkeypatch.setattr(service_module, "resolve_watcher_notes_root", lambda: __import__("pathlib").Path("notes"))
    monkeypatch.setattr(
        service_module, "resolve_watcher_state_path", lambda: __import__("pathlib").Path("watcher-state.json")
    )
    monkeypatch.setattr(service_module, "resolve_watcher_log_dir", lambda: __import__("pathlib").Path("watcher-logs"))
    monkeypatch.setattr(
        service_module, "resolve_reflex_state_path", lambda: __import__("pathlib").Path("reflex-state.json")
    )
    monkeypatch.setattr(
        service_module,
        "resolve_consolidation_state_path",
        lambda: __import__("pathlib").Path("consolidation-state.json"),
    )
    monkeypatch.setattr(
        service_module,
        "resolve_governance_trigger_state_path",
        lambda: __import__("pathlib").Path("governance-state.json"),
    )

    service_module.run_service(once=True)

    payload = json.loads(capsys.readouterr().out)
    health = json.loads((tmp_path / "service-health.json").read_text(encoding="utf-8"))
    assert calls == ["consolidation", "governance"]
    assert payload["watcher"]["enabled"] is False
    assert payload["reflex"]["enabled"] is False
    assert health["status"] == "ok"
    assert health["last_cycle_started_at"]
    assert health["last_cycle_completed_at"]
    assert health["active_lane"] is None
    assert health["lanes"]["watcher"]["enabled"] is False
    assert health["lanes"]["consolidation"]["status"] == "ok"


def test_service_once_isolates_lane_failures(monkeypatch, capsys, tmp_path: Path) -> None:
    calls: list[str] = []

    class FakeWatcher:
        def __init__(self, config) -> None:
            pass

        def run_once(self) -> dict[str, object]:
            calls.append("watcher")
            raise RuntimeError("watcher failed")

    class FakeReflex:
        def __init__(self, *, store, config) -> None:
            pass

        def run_once(self) -> dict[str, object]:
            calls.append("reflex")
            return {"processed_count": 0}

    class FakeConsolidation:
        def __init__(self, *, store, config) -> None:
            pass

        def run_once(self) -> dict[str, object]:
            calls.append("consolidation")
            return {"processed_count": 0}

    class FakeGovernance:
        def __init__(self, *, store, config) -> None:
            pass

        def run_once(self) -> dict[str, object]:
            calls.append("governance")
            return {"processed_count": 0}

    monkeypatch.setattr(service_module, "MemoryStore", lambda **kwargs: object())
    monkeypatch.setattr(service_module, "CodexSessionWatcher", FakeWatcher)
    monkeypatch.setattr(service_module, "ReflexEngine", FakeReflex)
    monkeypatch.setattr(service_module, "ConsolidationEngine", FakeConsolidation)
    monkeypatch.setattr(service_module, "GovernanceTriggerEngine", FakeGovernance)
    monkeypatch.setattr(
        service_module,
        "run_embedding_sidecar_maintenance",
        lambda store: calls.append("embeddings") or {"processed_count": 0},
    )
    monkeypatch.setattr(service_module, "resolve_watcher_enabled", lambda: True)
    monkeypatch.setattr(service_module, "resolve_reflex_enabled", lambda: True)
    monkeypatch.setattr(service_module, "resolve_bridge_home", lambda: tmp_path)
    monkeypatch.setattr(service_module, "resolve_bridge_db_path", lambda: __import__("pathlib").Path("bridge.db"))
    monkeypatch.setattr(service_module, "resolve_bridge_log_dir", lambda: __import__("pathlib").Path("logs"))
    monkeypatch.setattr(service_module, "resolve_sessions_root", lambda: __import__("pathlib").Path("sessions"))
    monkeypatch.setattr(service_module, "resolve_watcher_notes_root", lambda: __import__("pathlib").Path("notes"))
    monkeypatch.setattr(
        service_module, "resolve_watcher_state_path", lambda: __import__("pathlib").Path("watcher-state.json")
    )
    monkeypatch.setattr(service_module, "resolve_watcher_log_dir", lambda: __import__("pathlib").Path("watcher-logs"))
    monkeypatch.setattr(
        service_module, "resolve_reflex_state_path", lambda: __import__("pathlib").Path("reflex-state.json")
    )
    monkeypatch.setattr(
        service_module,
        "resolve_consolidation_state_path",
        lambda: __import__("pathlib").Path("consolidation-state.json"),
    )
    monkeypatch.setattr(
        service_module,
        "resolve_governance_trigger_state_path",
        lambda: __import__("pathlib").Path("governance-state.json"),
    )

    service_module.run_service(once=True)

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert calls == ["watcher", "reflex", "consolidation", "governance", "embeddings"]
    assert payload["watcher"]["status"] == "failed"
    assert payload["watcher"]["error_type"] == "RuntimeError"
    assert payload["watcher"]["failure_count"] == 1
    assert payload["reflex"]["status"] == "ok"
    assert "service lane watcher failed (RuntimeError)" in captured.err


def test_service_lane_backoff_is_bounded_and_resets_after_success() -> None:
    attempts = 0

    def runner() -> dict[str, object]:
        nonlocal attempts
        attempts += 1
        if attempts <= 2:
            raise RuntimeError("temporary failure")
        return {"processed_count": 1}

    lane = service_module._ServiceLane("test", runner, lambda: True)

    first = lane.run(now=0.0, base_backoff_seconds=2.0)
    blocked = lane.run(now=1.0, base_backoff_seconds=2.0)
    second = lane.run(now=2.0, base_backoff_seconds=2.0)
    recovered = lane.run(now=6.0, base_backoff_seconds=2.0)

    assert first["status"] == "failed"
    assert first["backoff_seconds"] == 2.0
    assert blocked["status"] == "backoff"
    assert attempts == 3
    assert second["backoff_seconds"] == 4.0
    assert recovered["status"] == "ok"
    assert recovered["failure_count"] == 2
    assert recovered["consecutive_failures"] == 0


def test_service_lane_backoff_has_a_hard_cap() -> None:
    lane = service_module._ServiceLane(
        "test",
        lambda: (_ for _ in ()).throw(RuntimeError("persistent failure")),
        lambda: True,
    )
    result: dict[str, object] = {}
    now = 0.0

    for _ in range(12):
        result = lane.run(now=now, base_backoff_seconds=30.0)
        now = lane.retry_at

    assert result["backoff_seconds"] == service_module.MAX_LANE_BACKOFF_SECONDS


def test_service_lane_preserves_runner_disabled_status() -> None:
    lane = service_module._ServiceLane(
        "test",
        lambda: {"enabled": False, "processed_count": 0, "reason": "disabled"},
        lambda: True,
    )

    result = lane.run(now=0.0, base_backoff_seconds=1.0)

    assert result["status"] == "disabled"
    assert result["slow"] is False


def test_service_lane_isolates_enablement_failures() -> None:
    lane = service_module._ServiceLane(
        "test",
        lambda: {"processed_count": 1},
        lambda: (_ for _ in ()).throw(ValueError("invalid lane config")),
    )

    result = lane.run(now=0.0, base_backoff_seconds=1.0)

    assert result["status"] == "failed"
    assert result["error_type"] == "ValueError"
    assert result["failure_count"] == 1


def test_service_lane_reports_duration_and_slow_warning(monkeypatch, capsys) -> None:
    ticks = iter([10.0, 10.25])
    monkeypatch.setattr(service_module.time, "perf_counter", lambda: next(ticks))
    lane = service_module._ServiceLane("test", lambda: {"processed_count": 1}, lambda: True)

    result = lane.run(now=0.0, base_backoff_seconds=1.0, slow_lane_seconds=0.1)

    assert result["duration_ms"] == 250.0
    assert result["slow"] is True
    assert "service lane test was slow" in capsys.readouterr().err


@pytest.mark.parametrize("interrupt", [KeyboardInterrupt(), SystemExit()])
def test_service_lane_does_not_swallow_process_control_exceptions(interrupt: BaseException) -> None:
    def runner() -> dict[str, object]:
        raise interrupt

    lane = service_module._ServiceLane("test", runner, lambda: True)

    with pytest.raises(type(interrupt)):
        lane.run(now=0.0, base_backoff_seconds=1.0)


def test_service_file_lock_excludes_second_holder_and_allows_reacquire(tmp_path: Path) -> None:
    lock_path = tmp_path / "service.lock"

    with ServiceFileLock(lock_path):
        metadata = json.loads(lock_path.read_text(encoding="utf-8"))
        assert metadata["pid"]
        assert metadata["started_at"]
        assert metadata["hostname"]
        assert metadata["version"]
        with pytest.raises(ServiceLockConflict) as exc_info:
            ServiceFileLock(lock_path).acquire()
        assert exc_info.value.metadata["pid"] == metadata["pid"]

    assert lock_path.is_file()
    with ServiceFileLock(lock_path):
        pass


def test_service_file_lock_excludes_another_process(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    lock_path = tmp_path / "service.lock"
    process = context.Process(
        target=_hold_service_lock,
        args=(str(lock_path), ready, release),
    )
    process.start()
    try:
        assert ready.wait(10)
        with pytest.raises(ServiceLockConflict):
            ServiceFileLock(lock_path).acquire()
    finally:
        release.set()
        process.join(10)
        if process.is_alive():
            process.terminate()
            process.join(5)
    assert process.exitcode == 0
