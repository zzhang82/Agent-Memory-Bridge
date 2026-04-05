from __future__ import annotations

import json
import sys
from pathlib import Path

from agent_mem_bridge.paths import (
    resolve_bridge_db_path,
    resolve_bridge_log_dir,
    resolve_watcher_notes_root,
)
from agent_mem_bridge.session_closeout import closeout_session_from_json
from agent_mem_bridge.storage import MemoryStore


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python scripts/closeout_session.py <payload.json>")

    payload_path = Path(sys.argv[1]).resolve()
    store = MemoryStore(
        db_path=resolve_bridge_db_path(),
        log_dir=resolve_bridge_log_dir(),
    )
    result = closeout_session_from_json(store, payload_path, resolve_watcher_notes_root())
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

