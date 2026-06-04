from __future__ import annotations

import json

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
