from __future__ import annotations

import json
import sys
from pathlib import Path

from agent_mem_bridge.codex_rollout import build_checkpoint_payload, parse_rollout_file
from agent_mem_bridge.paths import (
    resolve_bridge_db_path,
    resolve_bridge_log_dir,
    resolve_codex_home,
    resolve_watcher_notes_root,
)
from agent_mem_bridge.session_closeout import persist_session_payload
from agent_mem_bridge.storage import MemoryStore


def main() -> None:
    target = _resolve_target(sys.argv[1] if len(sys.argv) > 1 else None)
    summary = parse_rollout_file(target)
    if not summary.thread_id:
        raise SystemExit(f"Could not extract thread id from {target}")

    store = MemoryStore(
        db_path=resolve_bridge_db_path(),
        log_dir=resolve_bridge_log_dir(),
    )
    result = persist_session_payload(
        store=store,
        payload=build_checkpoint_payload(summary),
        notes_root=resolve_watcher_notes_root(),
    )
    print(json.dumps({"rollout_path": str(target), **result}, indent=2))


def _resolve_target(raw: str | None) -> Path:
    sessions_root = resolve_codex_home() / "sessions"
    if not raw:
        rollout_files = sorted(sessions_root.rglob("rollout-*.jsonl"), key=lambda path: path.stat().st_mtime, reverse=True)
        if not rollout_files:
            raise SystemExit(f"No rollout files found under {sessions_root}")
        return rollout_files[0]

    candidate = Path(raw)
    if candidate.exists():
        return candidate.resolve()

    matches = sorted(sessions_root.rglob(f"*{raw}*.jsonl"), key=lambda path: path.stat().st_mtime, reverse=True)
    if matches:
        return matches[0]
    raise SystemExit(f"Could not resolve rollout target from: {raw}")


if __name__ == "__main__":
    main()

