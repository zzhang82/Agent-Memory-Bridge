from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .codex_rollout import (
    build_checkpoint_payload,
    build_closeout_payload,
    build_session_seen_payload,
    has_checkpoint_signal,
    parse_rollout_file,
)
from .session_closeout import persist_session_payload
from .storage import MemoryStore


@dataclass(slots=True)
class WatcherConfig:
    sessions_root: Path
    notes_root: Path
    runtime_dir: Path
    state_path: Path
    db_path: Path | None = None
    log_dir: Path | None = None
    idle_seconds: int = 60
    checkpoint_seconds: int = 300
    checkpoint_min_messages: int = 2


class CodexSessionWatcher:
    def __init__(self, config: WatcherConfig) -> None:
        self.config = config
        self.config.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.config.notes_root.mkdir(parents=True, exist_ok=True)
        self.config.state_path.parent.mkdir(parents=True, exist_ok=True)
        db_path = self.config.db_path or (self.config.runtime_dir / "bridge.db")
        log_dir = self.config.log_dir or (self.config.runtime_dir / "logs")
        self.store = MemoryStore(
            db_path=db_path,
            log_dir=log_dir,
        )

    def run_once(self, now_ts: float | None = None) -> dict[str, Any]:
        now_ts = time.time() if now_ts is None else now_ts
        state = self._load_state()
        processed: list[dict[str, Any]] = []

        for rollout_path in sorted(self.config.sessions_root.rglob("rollout-*.jsonl")):
            stat = rollout_path.stat()
            fingerprint = f"{stat.st_mtime_ns}:{stat.st_size}"
            key = str(rollout_path)
            entry = self._normalize_state_entry(state.get(key))

            summary = parse_rollout_file(rollout_path)
            if not summary.thread_id:
                continue

            is_idle = now_ts - stat.st_mtime >= self.config.idle_seconds

            if not is_idle and not entry.get("session_seen"):
                payload = build_session_seen_payload(summary)
                sync_result = self.store.store(
                    namespace=payload["namespace"],
                    content=payload["content"],
                    kind=payload["kind"],
                    tags=payload["tags"],
                    session_id=payload["session_id"],
                    actor=payload["actor"],
                    title=payload["title"],
                    correlation_id=payload["correlation_id"],
                    source_app=payload["source_app"],
                )
                entry["session_seen"] = True
                processed.append(
                    {
                        "mode": "session-seen",
                        "rollout_path": key,
                        "thread_id": summary.thread_id,
                        "sync_result": sync_result,
                    }
                )

            if is_idle:
                if entry.get("closeout_fingerprint") != fingerprint:
                    result = persist_session_payload(
                        self.store,
                        build_closeout_payload(summary),
                        self.config.notes_root,
                    )
                    entry["closeout_fingerprint"] = fingerprint
                    entry["checkpoint_fingerprint"] = fingerprint
                    entry["last_checkpoint_ts"] = now_ts
                    processed.append(
                        {
                            "mode": "closeout",
                            "rollout_path": key,
                            "thread_id": summary.thread_id,
                            "note_path": result["note_path"],
                            "sync_result": result["sync_result"],
                        }
                    )
            elif self._should_checkpoint(summary, entry, fingerprint, now_ts):
                result = persist_session_payload(
                    self.store,
                    build_checkpoint_payload(summary),
                    self.config.notes_root,
                )
                entry["checkpoint_fingerprint"] = fingerprint
                entry["last_checkpoint_ts"] = now_ts
                processed.append(
                    {
                        "mode": "checkpoint",
                        "rollout_path": key,
                        "thread_id": summary.thread_id,
                        "note_path": result["note_path"],
                        "sync_result": result["sync_result"],
                    }
                )

            state[key] = entry

        self._save_state(state)
        return {"processed_count": len(processed), "processed": processed}

    def _should_checkpoint(
        self,
        summary: Any,
        entry: dict[str, Any],
        fingerprint: str,
        now_ts: float,
    ) -> bool:
        if self.config.checkpoint_seconds <= 0:
            return False
        if entry.get("closeout_fingerprint") == fingerprint or entry.get("checkpoint_fingerprint") == fingerprint:
            return False
        last_checkpoint_ts = float(entry.get("last_checkpoint_ts") or 0)
        if now_ts - last_checkpoint_ts < self.config.checkpoint_seconds:
            return False
        total_messages = len(summary.user_messages) + len(summary.assistant_messages)
        if total_messages < self.config.checkpoint_min_messages:
            return False
        return has_checkpoint_signal(summary)

    def _load_state(self) -> dict[str, Any]:
        if not self.config.state_path.exists():
            return {}
        return json.loads(self.config.state_path.read_text(encoding="utf-8"))

    def _save_state(self, state: dict[str, Any]) -> None:
        self.config.state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    @staticmethod
    def _normalize_state_entry(raw: Any) -> dict[str, Any]:
        if isinstance(raw, dict):
            return dict(raw)
        if isinstance(raw, str):
            return {"closeout_fingerprint": raw}
        return {}
