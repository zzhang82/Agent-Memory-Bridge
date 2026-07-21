from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Callable

from .consolidation import ConsolidationEngine, build_default_consolidation_config
from .embedding_scheduler import run_embedding_sidecar_maintenance
from .governance_trigger import GovernanceTriggerConfig, GovernanceTriggerEngine
from .paths import (
    hardened_local_profile_enabled,
    resolve_bridge_db_path,
    resolve_bridge_home,
    resolve_bridge_log_dir,
    resolve_checkpoint_min_messages,
    resolve_checkpoint_seconds,
    resolve_consolidation_scan_limit,
    resolve_consolidation_state_path,
    resolve_governance_trigger_scan_limit,
    resolve_governance_trigger_state_path,
    resolve_idle_seconds,
    resolve_poll_seconds,
    resolve_reflex_enabled,
    resolve_reflex_scan_limit,
    resolve_reflex_state_path,
    resolve_service_slow_lane_seconds,
    resolve_sessions_root,
    resolve_watcher_enabled,
    resolve_watcher_log_dir,
    resolve_watcher_notes_root,
    resolve_watcher_state_path,
)
from .reflex import ReflexEngine, build_default_reflex_config
from .service_health import ServiceHealthWriter
from .service_lock import ServiceFileLock
from .storage import MemoryStore
from .telemetry import Telemetry
from .watcher import CodexSessionWatcher, WatcherConfig

MAX_LANE_BACKOFF_SECONDS = 300.0


def _disabled_lane(reason: str = "disabled") -> dict[str, object]:
    return {
        "enabled": False,
        "processed_count": 0,
        "processed": [],
        "stored": [],
        "status": "disabled",
        "reason": reason,
    }


@dataclass(slots=True)
class _ServiceLane:
    name: str
    runner: Callable[[], dict[str, object]]
    enabled: Callable[[], bool]
    failure_count: int = 0
    consecutive_failures: int = 0
    retry_at: float = 0.0

    def run(
        self,
        *,
        now: float,
        base_backoff_seconds: float,
        slow_lane_seconds: float = 30.0,
    ) -> dict[str, object]:
        started_at = time.perf_counter()

        def finish(result: dict[str, object], *, measured_lane: bool) -> dict[str, object]:
            duration_seconds = max(0.0, time.perf_counter() - started_at)
            slow = measured_lane and slow_lane_seconds > 0 and duration_seconds >= slow_lane_seconds
            result["duration_ms"] = round(duration_seconds * 1000.0, 3)
            result["slow"] = slow
            if slow:
                try:
                    print(
                        f"agent-memory-bridge: service lane {self.name} was slow "
                        f"({duration_seconds:.3f}s >= {slow_lane_seconds:.3f}s)",
                        file=sys.stderr,
                    )
                except OSError:
                    pass
            return result

        try:
            if not self.enabled():
                return finish(_disabled_lane(), measured_lane=False)
            if now < self.retry_at:
                return finish(
                    {
                        "enabled": True,
                        "processed_count": 0,
                        "processed": [],
                        "stored": [],
                        "status": "backoff",
                        "failure_count": self.failure_count,
                        "consecutive_failures": self.consecutive_failures,
                        "retry_after_seconds": max(0.0, self.retry_at - now),
                    },
                    measured_lane=False,
                )
            result = dict(self.runner())
        except Exception as exc:
            self.failure_count += 1
            self.consecutive_failures += 1
            exponent = min(self.consecutive_failures - 1, 16)
            backoff_seconds = min(
                max(0.1, base_backoff_seconds) * (2**exponent),
                MAX_LANE_BACKOFF_SECONDS,
            )
            self.retry_at = now + backoff_seconds
            try:
                print(
                    f"agent-memory-bridge: service lane {self.name} failed ({type(exc).__name__})",
                    file=sys.stderr,
                )
            except OSError:
                pass
            return finish(
                {
                    "enabled": True,
                    "processed_count": 0,
                    "processed": [],
                    "stored": [],
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "failure_count": self.failure_count,
                    "consecutive_failures": self.consecutive_failures,
                    "backoff_seconds": backoff_seconds,
                },
                measured_lane=True,
            )

        self.consecutive_failures = 0
        self.retry_at = 0.0
        result.setdefault("enabled", True)
        result.setdefault("processed_count", 0)
        result["status"] = "disabled" if result.get("enabled") is False else "ok"
        result["failure_count"] = self.failure_count
        result["consecutive_failures"] = 0
        return finish(result, measured_lane=result.get("enabled") is not False)


def _run_cycle(
    lanes: dict[str, _ServiceLane],
    *,
    now: float,
    base_backoff_seconds: float,
    slow_lane_seconds: float = 30.0,
    on_lane_start: Callable[[str], None] | None = None,
    on_lane_complete: Callable[[str, dict[str, object]], None] | None = None,
) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for name, lane in lanes.items():
        if on_lane_start is not None:
            on_lane_start(name)
        lane_result = lane.run(
            now=now,
            base_backoff_seconds=base_backoff_seconds,
            slow_lane_seconds=slow_lane_seconds,
        )
        result[name] = lane_result
        if on_lane_complete is not None:
            on_lane_complete(name, lane_result)
    return result


def _set_cycle_span_attributes(span: object, result: dict[str, dict[str, object]]) -> None:
    span.set_attributes(
        {
            "watcher_processed_count": result["watcher"].get("processed_count", 0),
            "reflex_processed_count": result["reflex"].get("processed_count", 0),
            "consolidation_processed_count": result["consolidation"].get("processed_count", 0),
            "governance_processed_count": result["governance"].get("processed_count", 0),
            "embedding_processed_count": result["embeddings"].get("processed_count", 0),
            "embedding_due": result["embeddings"].get("due", False),
            "failed_lane_count": sum(item.get("status") == "failed" for item in result.values()),
            "slow_lane_count": sum(bool(item.get("slow")) for item in result.values()),
        }
    )


def run_service(
    *,
    once: bool | None = None,
    allow_multiple_services: bool = False,
) -> dict[str, dict[str, object]] | None:
    bridge_home = resolve_bridge_home()
    bridge_home.mkdir(parents=True, exist_ok=True)
    if allow_multiple_services:
        if hardened_local_profile_enabled():
            raise ValueError("hardened-local profile does not allow multiple service instances")
        return _run_service_with_home(bridge_home=bridge_home, once=once)
    with ServiceFileLock(bridge_home / "service.lock"):
        return _run_service_with_home(bridge_home=bridge_home, once=once)


def _run_service_with_home(
    *,
    bridge_home: Path,
    once: bool | None,
) -> dict[str, dict[str, object]] | None:
    telemetry = Telemetry.from_env()
    health = ServiceHealthWriter(bridge_home / "service-health.json", version=_package_version())

    store = MemoryStore(
        db_path=resolve_bridge_db_path(),
        log_dir=resolve_bridge_log_dir(),
        telemetry=telemetry,
    )
    watcher = CodexSessionWatcher(
        WatcherConfig(
            sessions_root=resolve_sessions_root(),
            notes_root=resolve_watcher_notes_root(),
            runtime_dir=bridge_home,
            state_path=resolve_watcher_state_path(),
            db_path=resolve_bridge_db_path(),
            log_dir=resolve_watcher_log_dir(),
            idle_seconds=resolve_idle_seconds(),
            checkpoint_seconds=resolve_checkpoint_seconds(),
            checkpoint_min_messages=resolve_checkpoint_min_messages(),
        )
    )
    reflex = ReflexEngine(
        store=store,
        config=build_default_reflex_config(
            state_path=resolve_reflex_state_path(),
            scan_limit=resolve_reflex_scan_limit(),
        ),
    )
    consolidation = ConsolidationEngine(
        store=store,
        config=build_default_consolidation_config(
            state_path=resolve_consolidation_state_path(),
            scan_limit=resolve_consolidation_scan_limit(),
        ),
    )
    governance_trigger = GovernanceTriggerEngine(
        store=store,
        config=GovernanceTriggerConfig(
            state_path=resolve_governance_trigger_state_path(),
            scan_limit=resolve_governance_trigger_scan_limit(),
        ),
    )

    if once is None:
        once = os.environ.get("AGENT_MEMORY_BRIDGE_RUN_ONCE", "0") == "1"
    poll_seconds = resolve_poll_seconds()
    slow_lane_seconds = resolve_service_slow_lane_seconds()
    if slow_lane_seconds < 0:
        raise ValueError("service slow_lane_seconds must not be negative")
    lanes = {
        "watcher": _ServiceLane("watcher", watcher.run_once, resolve_watcher_enabled),
        "reflex": _ServiceLane("reflex", reflex.run_once, resolve_reflex_enabled),
        "consolidation": _ServiceLane("consolidation", consolidation.run_once, lambda: True),
        "governance": _ServiceLane("governance", governance_trigger.run_once, lambda: True),
        "embeddings": _ServiceLane(
            "embeddings",
            lambda: run_embedding_sidecar_maintenance(store),
            lambda: True,
        ),
    }

    if once:
        with telemetry.span("amb.service.run_once", {"mode": "once"}) as span:
            health.cycle_started()
            result = _run_cycle(
                lanes,
                now=time.monotonic(),
                base_backoff_seconds=poll_seconds,
                slow_lane_seconds=slow_lane_seconds,
                on_lane_start=health.lane_started,
                on_lane_complete=health.lane_completed,
            )
            health.cycle_completed(result)
            _set_cycle_span_attributes(span, result)
        print(json.dumps(result, indent=2))
        return result

    while True:
        with telemetry.span("amb.service.poll_cycle", {"poll_seconds": poll_seconds}) as span:
            health.cycle_started()
            result = _run_cycle(
                lanes,
                now=time.monotonic(),
                base_backoff_seconds=poll_seconds,
                slow_lane_seconds=slow_lane_seconds,
                on_lane_start=health.lane_started,
                on_lane_complete=health.lane_completed,
            )
            health.cycle_completed(result)
            _set_cycle_span_attributes(span, result)
        if any(item.get("processed_count", 0) or item.get("status") == "failed" for item in result.values()):
            print(json.dumps(result, indent=2))
        time.sleep(poll_seconds)


def main() -> None:
    run_service()


def _package_version() -> str:
    try:
        return version("agent-memory-bridge")
    except PackageNotFoundError:
        return "0.0.0"
