from __future__ import annotations

import sys
from pathlib import Path

from agent_mem_bridge.paths import resolve_bridge_db_path, resolve_bridge_log_dir
from agent_mem_bridge.storage import MemoryStore
from agent_mem_bridge.sync_notes import summarize_sync_results, sync_markdown_path


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python scripts/sync_session_notes.py <note-file-or-directory>")

    target = Path(sys.argv[1]).resolve()
    store = MemoryStore(
        db_path=resolve_bridge_db_path(),
        log_dir=resolve_bridge_log_dir(),
    )
    results = sync_markdown_path(store, target)
    print(summarize_sync_results(results))


if __name__ == "__main__":
    main()

