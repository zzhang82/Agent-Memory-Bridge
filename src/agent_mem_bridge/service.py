from __future__ import annotations

import json
import os
import time

from .consolidation import ConsolidationConfig, ConsolidationEngine
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
from .reflex import ReflexConfig, ReflexEngine
from .storage import MemoryStore
from .watcher import CodexSessionWatcher, WatcherConfig


def run_service() -> None:
    bridge_home = resolve_bridge_home()
    bridge_home.mkdir(parents=True, exist_ok=True)

    store = MemoryStore(
        db_path=resolve_bridge_db_path(),
        log_dir=resolve_bridge_log_dir(),
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
        config=ReflexConfig(
            state_path=resolve_reflex_state_path(),
            scan_limit=resolve_reflex_scan_limit(),
        ),
    )
    consolidation = ConsolidationEngine(
        store=store,
        config=ConsolidationConfig(
            state_path=resolve_consolidation_state_path(),
            scan_limit=resolve_consolidation_scan_limit(),
        ),
    )

    once = os.environ.get("AGENT_MEMORY_BRIDGE_RUN_ONCE", "0") == "1"
    poll_seconds = resolve_poll_seconds()

    if once:
        result = {
            "watcher": watcher.run_once(),
            "reflex": reflex.run_once(),
            "consolidation": consolidation.run_once(),
        }
        print(json.dumps(result, indent=2))
        return

    while True:
        watcher_result = watcher.run_once()
        reflex_result = reflex.run_once()
        consolidation_result = consolidation.run_once()
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
