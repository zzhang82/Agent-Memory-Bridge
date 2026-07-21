from __future__ import annotations

import json

import pytest

from agent_mem_bridge.cli import main
from agent_mem_bridge import service as service_module


def test_cli_service_once_requests_single_cycle(monkeypatch) -> None:
    calls: list[bool | None] = []

    def fake_run_service(*, once: bool | None = None) -> None:
        calls.append(once)

    monkeypatch.setattr(service_module, "run_service", fake_run_service)

    exit_code = main(["service", "--once"])

    assert exit_code == 0
    assert calls == [True]


def test_cli_service_without_once_preserves_loop_mode(monkeypatch) -> None:
    calls: list[bool | None] = []

    def fake_run_service(*, once: bool | None = None) -> None:
        calls.append(once)

    monkeypatch.setattr(service_module, "run_service", fake_run_service)

    exit_code = main(["service"])

    assert exit_code == 0
    assert calls == [False]


def test_service_once_respects_disabled_watcher_and_reflex(monkeypatch, capsys) -> None:
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
    monkeypatch.setattr(service_module, "resolve_bridge_home", lambda: __import__("pathlib").Path("."))
    monkeypatch.setattr(service_module, "resolve_bridge_db_path", lambda: __import__("pathlib").Path("bridge.db"))
    monkeypatch.setattr(service_module, "resolve_bridge_log_dir", lambda: __import__("pathlib").Path("logs"))
    monkeypatch.setattr(service_module, "resolve_sessions_root", lambda: __import__("pathlib").Path("sessions"))
    monkeypatch.setattr(service_module, "resolve_watcher_notes_root", lambda: __import__("pathlib").Path("notes"))
    monkeypatch.setattr(service_module, "resolve_watcher_state_path", lambda: __import__("pathlib").Path("watcher-state.json"))
    monkeypatch.setattr(service_module, "resolve_watcher_log_dir", lambda: __import__("pathlib").Path("watcher-logs"))
    monkeypatch.setattr(service_module, "resolve_reflex_state_path", lambda: __import__("pathlib").Path("reflex-state.json"))
    monkeypatch.setattr(service_module, "resolve_consolidation_state_path", lambda: __import__("pathlib").Path("consolidation-state.json"))
    monkeypatch.setattr(service_module, "resolve_governance_trigger_state_path", lambda: __import__("pathlib").Path("governance-state.json"))

    service_module.run_service(once=True)

    payload = json.loads(capsys.readouterr().out)
    assert calls == ["consolidation", "governance"]
    assert payload["watcher"]["enabled"] is False
    assert payload["reflex"]["enabled"] is False


def test_service_once_isolates_lane_failures(monkeypatch, capsys) -> None:
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
    monkeypatch.setattr(service_module, "resolve_bridge_home", lambda: __import__("pathlib").Path("."))
    monkeypatch.setattr(service_module, "resolve_bridge_db_path", lambda: __import__("pathlib").Path("bridge.db"))
    monkeypatch.setattr(service_module, "resolve_bridge_log_dir", lambda: __import__("pathlib").Path("logs"))
    monkeypatch.setattr(service_module, "resolve_sessions_root", lambda: __import__("pathlib").Path("sessions"))
    monkeypatch.setattr(service_module, "resolve_watcher_notes_root", lambda: __import__("pathlib").Path("notes"))
    monkeypatch.setattr(service_module, "resolve_watcher_state_path", lambda: __import__("pathlib").Path("watcher-state.json"))
    monkeypatch.setattr(service_module, "resolve_watcher_log_dir", lambda: __import__("pathlib").Path("watcher-logs"))
    monkeypatch.setattr(service_module, "resolve_reflex_state_path", lambda: __import__("pathlib").Path("reflex-state.json"))
    monkeypatch.setattr(service_module, "resolve_consolidation_state_path", lambda: __import__("pathlib").Path("consolidation-state.json"))
    monkeypatch.setattr(service_module, "resolve_governance_trigger_state_path", lambda: __import__("pathlib").Path("governance-state.json"))

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


@pytest.mark.parametrize("interrupt", [KeyboardInterrupt(), SystemExit()])
def test_service_lane_does_not_swallow_process_control_exceptions(interrupt: BaseException) -> None:
    def runner() -> dict[str, object]:
        raise interrupt

    lane = service_module._ServiceLane("test", runner, lambda: True)

    with pytest.raises(type(interrupt)):
        lane.run(now=0.0, base_backoff_seconds=1.0)
