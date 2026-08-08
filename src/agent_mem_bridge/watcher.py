from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .codex_rollout import (
    build_checkpoint_payload,
    build_closeout_payload,
    build_session_seen_payload,
    build_watcher_episode_begin_request,
    build_watcher_episode_checkpoint_request,
    build_watcher_episode_closeout_request,
    has_checkpoint_signal,
    parse_rollout_file,
)
from .session_closeout import persist_session_payload
from .state_io import load_json_state, write_json_state_atomic
from .storage import MemoryStore

_EPISODE_STATE_VERSION = 1
_EPISODE_STATE_GATES = (
    "session_seen",
    "checkpoint_fingerprint",
    "closeout_fingerprint",
    "terminal_skip_fingerprint",
    "last_checkpoint_ts",
)


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
    legacy_memory_mode: bool = False


class CodexSessionWatcher:
    def __init__(self, config: WatcherConfig) -> None:
        self.config = config
        self.config.runtime_dir.mkdir(parents=True, exist_ok=True)
        if self.config.legacy_memory_mode:
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

            if self.config.legacy_memory_mode:
                processed.extend(
                    self._process_legacy_rollout(
                        summary=summary,
                        entry=entry,
                        fingerprint=fingerprint,
                        is_idle=is_idle,
                        now_ts=now_ts,
                        rollout_path=key,
                    )
                )
            else:
                processed.extend(
                    self._process_episode_rollout(
                        summary=summary,
                        entry=entry,
                        fingerprint=fingerprint,
                        is_idle=is_idle,
                        now_ts=now_ts,
                        rollout_path=key,
                    )
                )

            state[key] = entry

        self._save_state(state)
        return {"processed_count": len(processed), "processed": processed}

    def _process_legacy_rollout(
        self,
        *,
        summary: Any,
        entry: dict[str, Any],
        fingerprint: str,
        is_idle: bool,
        now_ts: float,
        rollout_path: str,
    ) -> list[dict[str, Any]]:
        """Preserve the pre-ledger memory and note workflow behind the temporary switch."""

        processed: list[dict[str, Any]] = []
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
                    "rollout_path": rollout_path,
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
                        "rollout_path": rollout_path,
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
                    "rollout_path": rollout_path,
                    "thread_id": summary.thread_id,
                    "note_path": result["note_path"],
                    "sync_result": result["sync_result"],
                }
            )
        return processed

    def _process_episode_rollout(
        self,
        *,
        summary: Any,
        entry: dict[str, Any],
        fingerprint: str,
        is_idle: bool,
        now_ts: float,
        rollout_path: str,
    ) -> list[dict[str, Any]]:
        """Record one rollout's lifecycle in the explicit run ledger only."""

        processed: list[dict[str, Any]] = []
        begin_request = build_watcher_episode_begin_request(
            summary,
            workspace_key=self._stored_workspace_key(entry),
        )
        begin_result = self.store.begin_run(**begin_request)
        run_id = str(begin_result["run_id"])
        root_work_item_id = str(begin_result["root_work_item_id"])
        run_status = str(begin_result["status"])
        self._bind_episode_state(entry, run_id=run_id)
        entry["run_id"] = run_id
        entry["root_work_item_id"] = root_work_item_id
        entry["run_status"] = run_status
        entry["workspace_key"] = begin_request["workspace_key"]

        result_base = {
            "rollout_path": rollout_path,
            "thread_id": summary.thread_id,
            "run_id": run_id,
            "root_work_item_id": root_work_item_id,
            "run_status": run_status,
            "begin_idempotent_replay": bool(begin_result["idempotent_replay"]),
        }
        if run_status != "active":
            if entry.get("terminal_skip_fingerprint") != fingerprint:
                entry["terminal_skip_fingerprint"] = fingerprint
                processed.append({"mode": "terminal-skip", **result_base})
            return processed

        if not is_idle and not entry.get("session_seen"):
            entry["session_seen"] = True
            processed.append({"mode": "session-seen", **result_base})

        workspace_key = str(begin_request["workspace_key"])
        if is_idle:
            if entry.get("closeout_fingerprint") != fingerprint:
                closeout_request = build_watcher_episode_closeout_request(
                    summary,
                    fingerprint,
                    workspace_key=workspace_key,
                )
                terminal_event = self.store.record_run_event(
                    workspace_key=workspace_key,
                    run_id=run_id,
                    work_item_id=root_work_item_id,
                    event_type="work_item_completed",
                    summary="Observed an idle Codex rollout root work item completion from metadata only.",
                    idempotency_key=f"{closeout_request['idempotency_key']}:work-item-completed",
                    agent_id="codex-session-watcher",
                    thread_id=str(closeout_request["provenance"]["client_session_id"]),
                    provenance=closeout_request["provenance"],
                )
                outcome_result = self.store.complete_run(
                    workspace_key=workspace_key,
                    run_id=run_id,
                    **closeout_request,
                )
                entry["closeout_fingerprint"] = fingerprint
                entry["checkpoint_fingerprint"] = fingerprint
                entry["last_checkpoint_ts"] = now_ts
                entry["terminal_skip_fingerprint"] = fingerprint
                entry["run_status"] = "completed"
                processed.append(
                    {
                        "mode": "closeout",
                        **result_base,
                        "run_status": "completed",
                        "terminal_event_id": terminal_event["event_id"],
                        "terminal_event_idempotent_replay": terminal_event["idempotent_replay"],
                        "outcome_id": outcome_result["outcome_id"],
                        "outcome_idempotent_replay": outcome_result["idempotent_replay"],
                    }
                )
        elif self._should_checkpoint(summary, entry, fingerprint, now_ts):
            checkpoint_request = build_watcher_episode_checkpoint_request(
                summary,
                fingerprint,
                workspace_key=workspace_key,
            )
            event_result = self.store.record_run_event(
                workspace_key=workspace_key,
                run_id=run_id,
                work_item_id=root_work_item_id,
                **checkpoint_request,
            )
            entry["checkpoint_fingerprint"] = fingerprint
            entry["last_checkpoint_ts"] = now_ts
            processed.append(
                {
                    "mode": "checkpoint",
                    **result_base,
                    "event_id": event_result["event_id"],
                    "event_idempotent_replay": event_result["idempotent_replay"],
                }
            )
        return processed

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
        return load_json_state(self.config.state_path)

    def _save_state(self, state: dict[str, Any]) -> None:
        write_json_state_atomic(self.config.state_path, state)

    @staticmethod
    def _normalize_state_entry(raw: Any) -> dict[str, Any]:
        if isinstance(raw, dict):
            return dict(raw)
        if isinstance(raw, str):
            return {"closeout_fingerprint": raw}
        return {}

    @staticmethod
    def _stored_workspace_key(entry: dict[str, Any]) -> str | None:
        candidate = entry.get("workspace_key")
        if isinstance(candidate, str) and candidate.startswith("project:") and len(candidate) <= 512:
            return candidate
        return None

    @staticmethod
    def _bind_episode_state(entry: dict[str, Any], *, run_id: str) -> None:
        """Bind local episode gates to the authoritative run returned by the ledger."""

        if not CodexSessionWatcher._episode_state_matches_run(entry, run_id=run_id):
            for field in _EPISODE_STATE_GATES:
                entry.pop(field, None)
        entry["episode_state_version"] = _EPISODE_STATE_VERSION
        entry["run_id"] = run_id

    @staticmethod
    def _episode_state_matches_run(entry: dict[str, Any], *, run_id: str) -> bool:
        return (
            type(entry.get("episode_state_version")) is int
            and entry["episode_state_version"] == _EPISODE_STATE_VERSION
            and entry.get("run_id") == run_id
        )
