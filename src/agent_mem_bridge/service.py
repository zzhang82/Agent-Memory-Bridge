from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from typing import Callable

from .consolidation import ConsolidationEngine, build_default_consolidation_config
from .embedding_scheduler import run_embedding_sidecar_maintenance
from .governance_trigger import GovernanceTriggerConfig, GovernanceTriggerEngine
from .paths import (
    resolve_checkpoint_min_messages,
    resolve_checkpoint_seconds,
    resolve_bridge_db_path,
    resolve_bridge_home,
    resolve_bridge_log_dir,
    resolve_consolidation_scan_limit,
    resolve_consolidation_state_path,
    resolve_governance_trigger_scan_limit,
    resolve_governance_trigger_state_path,
    resolve_idle_seconds,
    resolve_poll_seconds,
    resolve_reflex_enabled,
    resolve_reflex_state_path,
    resolve_reflex_scan_limit,
    resolve_sessions_root,
    resolve_watcher_enabled,
    resolve_watcher_log_dir,
    resolve_watcher_notes_root,
    resolve_watcher_state_path,
)
from .reflex import ReflexEngine, build_default_reflex_config
from .storage import MemoryStore
from .telemetry import Telemetry
from .watcher import CodexSessionWatcher, WatcherConfig


MAX_LANE_BACKOFF_SECONDS = 300.0


def _disabled_lane(reason: str = "disabled") -> dict[str, object]:
    return {"enabled": False, "processed_count": 0, "processed": [], "stored": [], "reason": reason}


@dataclass(slots=True)
class _ServiceLane:
    name: str
    runner: Callable[[], dict[str, object]]
    enabled: Callable[[], bool]
    failure_count: int = 0
    consecutive_failures: int = 0
    retry_at: float = 0.0

    def run(self, *, now: float, base_backoff_seconds: float) -> dict[str, object]:
        try:
            if not self.enabled():
                return _disabled_lane()
            if now < self.retry_at:
                return {
                    "enabled": True,
                    "processed_count": 0,
                    "processed": [],
                    "stored": [],
                    "status": "backoff",
                    "failure_count": self.failure_count,
                    "consecutive_failures": self.consecutive_failures,
                    "retry_after_seconds": max(0.0, self.retry_at - now),
                }
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
            return {
                "enabled": True,
                "processed_count": 0,
                "processed": [],
                "stored": [],
                "status": "failed",
                "error_type": type(exc).__name__,
                "failure_count": self.failure_count,
                "consecutive_failures": self.consecutive_failures,
                "backoff_seconds": backoff_seconds,
            }

        self.consecutive_failures = 0
        self.retry_at = 0.0
        result.setdefault("enabled", True)
        result.setdefault("processed_count", 0)
        result["status"] = "ok"
        result["failure_count"] = self.failure_count
        result["consecutive_failures"] = 0
        return result


def _run_cycle(
    lanes: dict[str, _ServiceLane],
    *,
    now: float,
    base_backoff_seconds: float,
) -> dict[str, dict[str, object]]:
    return {
        name: lane.run(now=now, base_backoff_seconds=base_backoff_seconds)
        for name, lane in lanes.items()
    }


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
        }
    )


def run_service(*, once: bool | None = None) -> None:
    bridge_home = resolve_bridge_home()
    bridge_home.mkdir(parents=True, exist_ok=True)
    telemetry = Telemetry.from_env()

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
            result = _run_cycle(lanes, now=time.monotonic(), base_backoff_seconds=poll_seconds)
            _set_cycle_span_attributes(span, result)
        print(json.dumps(result, indent=2))
        return

    while True:
        with telemetry.span("amb.service.poll_cycle", {"poll_seconds": poll_seconds}) as span:
            result = _run_cycle(lanes, now=time.monotonic(), base_backoff_seconds=poll_seconds)
            _set_cycle_span_attributes(span, result)
        if any(
            item.get("processed_count", 0) or item.get("status") == "failed"
            for item in result.values()
        ):
            print(json.dumps(result, indent=2))
        time.sleep(poll_seconds)


def main() -> None:
    run_service()
