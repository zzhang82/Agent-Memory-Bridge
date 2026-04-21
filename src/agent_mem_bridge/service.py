from __future__ import annotations

import json
import os
import time

from .consolidation import ConsolidationEngine, build_default_consolidation_config
from .paths import (
    resolve_checkpoint_min_messages,
    resolve_checkpoint_seconds,
    resolve_bridge_db_path,
    resolve_bridge_home,
    resolve_bridge_log_dir,
    resolve_consolidation_scan_limit,
    resolve_consolidation_state_path,
    resolve_idle_seconds,
    resolve_poll_seconds,
    resolve_reflex_state_path,
    resolve_reflex_scan_limit,
    resolve_sessions_root,
    resolve_watcher_log_dir,
    resolve_watcher_notes_root,
    resolve_watcher_state_path,
)
from .reflex import ReflexEngine, build_default_reflex_config
from .storage import MemoryStore
from .telemetry import Telemetry
from .watcher import CodexSessionWatcher, WatcherConfig


def run_service() -> None:
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

    once = os.environ.get("AGENT_MEMORY_BRIDGE_RUN_ONCE", "0") == "1"
    poll_seconds = resolve_poll_seconds()

    if once:
        with telemetry.span("amb.service.run_once", {"mode": "once"}) as span:
            result = {
                "watcher": watcher.run_once(),
                "reflex": reflex.run_once(),
                "consolidation": consolidation.run_once(),
            }
            span.set_attributes(
                {
                    "watcher_processed_count": result["watcher"].get("processed_count", 0),
                    "reflex_processed_count": result["reflex"].get("processed_count", 0),
                    "consolidation_processed_count": result["consolidation"].get("processed_count", 0),
                }
            )
        print(json.dumps(result, indent=2))
        return

    while True:
        with telemetry.span("amb.service.poll_cycle", {"poll_seconds": poll_seconds}) as span:
            watcher_result = watcher.run_once()
            reflex_result = reflex.run_once()
            consolidation_result = consolidation.run_once()
            span.set_attributes(
                {
                    "watcher_processed_count": watcher_result.get("processed_count", 0),
                    "reflex_processed_count": reflex_result.get("processed_count", 0),
                    "consolidation_processed_count": consolidation_result.get("processed_count", 0),
                }
            )
        if watcher_result["processed_count"] or reflex_result["processed_count"] or consolidation_result["processed_count"]:
            print(
                json.dumps(
                    {
                        "watcher": watcher_result,
                        "reflex": reflex_result,
                        "consolidation": consolidation_result,
                    },
                    indent=2,
                )
            )
        time.sleep(poll_seconds)


def main() -> None:
    run_service()
