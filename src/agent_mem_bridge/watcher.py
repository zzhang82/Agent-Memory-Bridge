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
    scan_rollout_file_incremental,
)
from .session_closeout import persist_session_payload
from .state_io import load_json_state, write_json_state_atomic
from .storage import MemoryStore

_EPISODE_STATE_VERSION = 2
_EPISODE_STATE_GATES = (
    "session_seen",
    "checkpoint_fingerprint",
    "closeout_fingerprint",
    "pause_fingerprint",
    "resume_fingerprint",
    "terminal_fingerprint",
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
            key = str(rollout_path)
            entry = self._normalize_state_entry(state.get(key))
            if self.config.legacy_memory_mode:
                summary = parse_rollout_file(rollout_path)
                fingerprint = f"{stat.st_mtime_ns}:{stat.st_size}"
            else:
                summary, cursor = scan_rollout_file_incremental(rollout_path, entry.get("rollout_cursor"))
                entry["rollout_cursor"] = cursor
                fingerprint = str(cursor["fingerprint"])
            if not summary.thread_id:
                state[key] = entry
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
        stored_continuation_of = entry.get("continuation_of_run_id")
        stored_continuation_key = entry.get("continuation_key")
        if not isinstance(stored_continuation_of, str) or not stored_continuation_of.startswith("run_"):
            stored_continuation_of = None
            stored_continuation_key = None
        begin_request = build_watcher_episode_begin_request(
            summary,
            workspace_key=self._stored_workspace_key(entry),
            continuation_of_run_id=stored_continuation_of,
            continuation_key=str(stored_continuation_key or "") or None,
        )
        begin_result = self.store.begin_run(**begin_request)
        run_id = str(begin_result["run_id"])
        root_work_item_id = str(begin_result["root_work_item_id"])
        run_status = str(begin_result["status"])
        database_epoch = str(begin_result["database_epoch"])
        run_generation = int(begin_result["run_generation"])
        workspace_key = str(begin_request["workspace_key"])
        self._bind_episode_state(
            entry,
            run_id=run_id,
            database_epoch=database_epoch,
            run_generation=run_generation,
        )

        if run_status != "active":
            continuity = self._terminal_rollout_continuity(
                summary=summary,
                fingerprint=fingerprint,
                workspace_key=workspace_key,
                run_id=run_id,
            )
            if continuity == "changed":
                continuation_request = build_watcher_episode_begin_request(
                    summary,
                    workspace_key=workspace_key,
                    continuation_of_run_id=run_id,
                    continuation_key=fingerprint,
                )
                continuation = self.store.begin_run(**continuation_request)
                prior_run_id = run_id
                begin_request = continuation_request
                begin_result = continuation
                run_id = str(continuation["run_id"])
                root_work_item_id = str(continuation["root_work_item_id"])
                run_status = str(continuation["status"])
                database_epoch = str(continuation["database_epoch"])
                run_generation = int(continuation["run_generation"])
                self._bind_episode_state(
                    entry,
                    run_id=run_id,
                    database_epoch=database_epoch,
                    run_generation=run_generation,
                )
                entry["checkpoint_fingerprint"] = fingerprint
                entry["last_checkpoint_ts"] = now_ts
                stored_continuation_of = prior_run_id
                stored_continuation_key = fingerprint
                processed.append(
                    {
                        "mode": "continuation-started",
                        "rollout_path": rollout_path,
                        "thread_id": summary.thread_id,
                        "run_id": run_id,
                        "root_work_item_id": root_work_item_id,
                        "run_status": run_status,
                        "database_epoch": database_epoch,
                        "run_generation": run_generation,
                        "continuation_of_run_id": prior_run_id,
                        "begin_idempotent_replay": bool(continuation["idempotent_replay"]),
                    }
                )
            elif entry.get("terminal_skip_fingerprint") != fingerprint:
                entry["terminal_skip_fingerprint"] = fingerprint
                processed.append(
                    {
                        "mode": "terminal-skip" if continuity == "same" else "terminal-continuity-unknown",
                        "rollout_path": rollout_path,
                        "thread_id": summary.thread_id,
                        "run_id": run_id,
                        "root_work_item_id": root_work_item_id,
                        "run_status": run_status,
                        "database_epoch": database_epoch,
                        "run_generation": run_generation,
                        "begin_idempotent_replay": bool(begin_result["idempotent_replay"]),
                    }
                )
                return processed
            else:
                return processed

        entry["run_id"] = run_id
        entry["root_work_item_id"] = root_work_item_id
        entry["run_status"] = run_status
        entry["workspace_key"] = begin_request["workspace_key"]
        if stored_continuation_of:
            entry["continuation_of_run_id"] = stored_continuation_of
            entry["continuation_key"] = str(stored_continuation_key or fingerprint)
        else:
            entry.pop("continuation_of_run_id", None)
            entry.pop("continuation_key", None)

        result_base = {
            "rollout_path": rollout_path,
            "thread_id": summary.thread_id,
            "run_id": run_id,
            "root_work_item_id": root_work_item_id,
            "run_status": run_status,
            "database_epoch": database_epoch,
            "run_generation": run_generation,
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

        if summary.explicit_close_reason:
            if entry.get("terminal_fingerprint") == fingerprint:
                return processed
            snapshot = self.store.get_run(workspace_key=workspace_key, run_id=run_id)
            root_status = self._work_item_status(snapshot, root_work_item_id)
            closeout_request = build_watcher_episode_closeout_request(
                summary,
                fingerprint,
                workspace_key=workspace_key,
                termination_reason=f"host_explicit_close:{summary.explicit_close_reason}",
            )
            terminal_event = self.store.record_run_event(
                workspace_key=workspace_key,
                run_id=run_id,
                work_item_id=root_work_item_id,
                event_type="work_item_completed",
                summary="Observed an explicit host close for the Codex rollout root work item.",
                idempotency_key=f"{closeout_request['idempotency_key']}:work-item-completed",
                expected_last_sequence=int(snapshot["snapshot_last_sequence"]),
                expected_work_item_status=root_status,
                expected_database_epoch=str(snapshot["snapshot_epoch"]),
                expected_run_generation=int(snapshot["run"]["run_generation"]),
                agent_id="codex-session-watcher",
                thread_id=str(closeout_request["provenance"]["client_session_id"]),
                provenance=closeout_request["provenance"],
            )
            outcome_result = self.store.complete_run(
                workspace_key=workspace_key,
                run_id=run_id,
                expected_last_sequence=int(terminal_event["sequence"]),
                expected_database_epoch=str(terminal_event["database_epoch"]),
                expected_run_generation=int(terminal_event["run_generation"]),
                **closeout_request,
            )
            self._advance_episode_state(entry, outcome_result)
            entry["closeout_fingerprint"] = fingerprint
            entry["checkpoint_fingerprint"] = fingerprint
            entry["last_checkpoint_ts"] = now_ts
            entry["terminal_fingerprint"] = fingerprint
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
            return processed

        if is_idle:
            if entry.get("pause_fingerprint") != fingerprint:
                event_result = self._record_episode_checkpoint(
                    summary=summary,
                    workspace_key=workspace_key,
                    run_id=run_id,
                    root_work_item_id=root_work_item_id,
                    fingerprint=fingerprint,
                    lifecycle_state="paused_on_idle",
                )
                self._advance_episode_state(entry, event_result)
                entry["pause_fingerprint"] = fingerprint
                entry["checkpoint_fingerprint"] = fingerprint
                entry["last_checkpoint_ts"] = now_ts
                processed.append(
                    {
                        "mode": "paused",
                        **result_base,
                        "event_id": event_result["event_id"],
                        "event_idempotent_replay": event_result["idempotent_replay"],
                    }
                )
            return processed

        paused_fingerprint = entry.get("pause_fingerprint")
        if paused_fingerprint and paused_fingerprint != fingerprint:
            event_result = self._record_episode_checkpoint(
                summary=summary,
                workspace_key=workspace_key,
                run_id=run_id,
                root_work_item_id=root_work_item_id,
                fingerprint=fingerprint,
                lifecycle_state="resumed",
            )
            self._advance_episode_state(entry, event_result)
            entry.pop("pause_fingerprint", None)
            entry["resume_fingerprint"] = fingerprint
            entry["checkpoint_fingerprint"] = fingerprint
            entry["last_checkpoint_ts"] = now_ts
            processed.append(
                {
                    "mode": "resumed",
                    **result_base,
                    "event_id": event_result["event_id"],
                    "event_idempotent_replay": event_result["idempotent_replay"],
                }
            )
        elif self._should_checkpoint(summary, entry, fingerprint, now_ts):
            event_result = self._record_episode_checkpoint(
                summary=summary,
                workspace_key=workspace_key,
                run_id=run_id,
                root_work_item_id=root_work_item_id,
                fingerprint=fingerprint,
                lifecycle_state="active",
            )
            self._advance_episode_state(entry, event_result)
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

    def _record_episode_checkpoint(
        self,
        *,
        summary: Any,
        workspace_key: str,
        run_id: str,
        root_work_item_id: str,
        fingerprint: str,
        lifecycle_state: str,
    ) -> dict[str, Any]:
        snapshot = self.store.get_run(workspace_key=workspace_key, run_id=run_id)
        checkpoint_request = build_watcher_episode_checkpoint_request(
            summary,
            fingerprint,
            workspace_key=workspace_key,
            lifecycle_state=lifecycle_state,
        )
        return self.store.record_run_event(
            workspace_key=workspace_key,
            run_id=run_id,
            work_item_id=root_work_item_id,
            expected_last_sequence=int(snapshot["snapshot_last_sequence"]),
            expected_work_item_status=self._work_item_status(snapshot, root_work_item_id),
            expected_database_epoch=str(snapshot["snapshot_epoch"]),
            expected_run_generation=int(snapshot["run"]["run_generation"]),
            **checkpoint_request,
        )

    def _terminal_rollout_continuity(
        self,
        *,
        summary: Any,
        fingerprint: str,
        workspace_key: str,
        run_id: str,
    ) -> str:
        """Compare current metadata with the terminal authority without reopening it."""

        snapshot = self.store.get_run(workspace_key=workspace_key, run_id=run_id)
        outcome = snapshot.get("outcome")
        if not isinstance(outcome, dict):
            return "unknown"
        metrics = outcome.get("metrics")
        if not isinstance(metrics, dict):
            return "unknown"
        rollout = metrics.get("rollout")
        if not isinstance(rollout, dict):
            return "unknown"
        authority_fingerprint = str(rollout.get("fingerprint") or "").strip()
        if authority_fingerprint == fingerprint:
            return "same"
        authority_last_updated = str(rollout.get("last_updated") or "").strip()
        authority_total_count = rollout.get("total_count")
        try:
            total_count = int(summary.user_message_count) + int(summary.assistant_message_count)
            if int(authority_total_count) == total_count and authority_last_updated == str(summary.last_updated or ""):
                return "same"
        except (TypeError, ValueError):
            return "unknown"
        return "changed"

    @staticmethod
    def _work_item_status(snapshot: dict[str, Any], work_item_id: str) -> str:
        for item in snapshot.get("work_items", []):
            if str(item.get("work_item_id")) == work_item_id:
                return str(item["status"])
        raise RuntimeError("watcher root work item is missing from the run snapshot")

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
        total_messages = int(summary.user_message_count) + int(summary.assistant_message_count)
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
    def _bind_episode_state(
        entry: dict[str, Any],
        *,
        run_id: str,
        database_epoch: str,
        run_generation: int,
    ) -> None:
        """Bind local episode gates to the authoritative run returned by the ledger."""

        if not CodexSessionWatcher._episode_state_matches_run(
            entry,
            run_id=run_id,
            database_epoch=database_epoch,
            run_generation=run_generation,
        ):
            for field in _EPISODE_STATE_GATES:
                entry.pop(field, None)
        entry["episode_state_version"] = _EPISODE_STATE_VERSION
        entry["run_id"] = run_id
        entry["database_epoch"] = database_epoch
        entry["run_generation"] = run_generation

    @staticmethod
    def _episode_state_matches_run(
        entry: dict[str, Any],
        *,
        run_id: str,
        database_epoch: str,
        run_generation: int,
    ) -> bool:
        return (
            type(entry.get("episode_state_version")) is int
            and entry["episode_state_version"] == _EPISODE_STATE_VERSION
            and entry.get("run_id") == run_id
            and entry.get("database_epoch") == database_epoch
            and type(entry.get("run_generation")) is int
            and entry["run_generation"] == run_generation
        )

    @staticmethod
    def _advance_episode_state(entry: dict[str, Any], result: dict[str, Any]) -> None:
        entry["database_epoch"] = str(result["database_epoch"])
        entry["run_generation"] = int(result["run_generation"])
