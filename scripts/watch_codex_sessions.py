from __future__ import annotations

import json
import os
import time

from agent_mem_bridge.paths import (
    resolve_bridge_db_path,
    resolve_bridge_home,
    resolve_checkpoint_min_messages,
    resolve_checkpoint_seconds,
    resolve_idle_seconds,
    resolve_poll_seconds,
    resolve_sessions_root,
    resolve_watcher_legacy_memory_mode,
    resolve_watcher_log_dir,
    resolve_watcher_notes_root,
    resolve_watcher_state_path,
)
from agent_mem_bridge.watcher import CodexSessionWatcher, WatcherConfig


def main() -> None:
    runtime_dir = resolve_bridge_home()
    watcher = CodexSessionWatcher(
        WatcherConfig(
            sessions_root=resolve_sessions_root(),
            notes_root=resolve_watcher_notes_root(),
            runtime_dir=runtime_dir,
            state_path=resolve_watcher_state_path(),
            db_path=resolve_bridge_db_path(),
            log_dir=resolve_watcher_log_dir(),
            idle_seconds=resolve_idle_seconds(),
            checkpoint_seconds=resolve_checkpoint_seconds(),
            checkpoint_min_messages=resolve_checkpoint_min_messages(),
            legacy_memory_mode=resolve_watcher_legacy_memory_mode(),
        )
    )

    poll_seconds = resolve_poll_seconds()
    once = os.environ.get("AGENT_MEMORY_BRIDGE_RUN_ONCE", "0") == "1"

    if once:
        print(json.dumps(watcher.run_once(), indent=2))
        return

    while True:
        result = watcher.run_once()
        if result["processed_count"]:
            print(json.dumps(result, indent=2))
        time.sleep(poll_seconds)


if __name__ == "__main__":
    main()
