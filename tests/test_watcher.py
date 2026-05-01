import json
import time
from pathlib import Path

from agent_mem_bridge.watcher import CodexSessionWatcher, WatcherConfig


def test_watcher_processes_idle_rollout_once(tmp_path: Path) -> None:
    sessions_root = tmp_path / "sessions"
    sessions_root.mkdir()
    rollout = sessions_root / "rollout-2026-04-04T13-17-22-019d597f-d23c-7391-9214-4c5b847d13ce.jsonl"
    lines = [
        {
            "timestamp": "2026-04-04T17:18:07.854Z",
            "type": "session_meta",
            "payload": {
                "id": "019d597f-d23c-7391-9214-4c5b847d13ce",
                "timestamp": "2026-04-04T17:17:22.372Z",
                "cwd": "C:\\workspaces\\demo\\mem-store",
                "originator": "Codex Desktop",
            },
        },
        {
            "timestamp": "2026-04-04T17:18:07.856Z",
            "type": "event_msg",
            "payload": {"type": "user_message", "message": "Build a memory bridge."},
        },
        {
            "timestamp": "2026-04-04T17:18:11.235Z",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Built the foundation and tests."}],
            },
        },
    ]
    rollout.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")
    old_time = time.time() - 120
    os_times = (old_time, old_time)
    rollout.touch()
    import os
    os.utime(rollout, os_times)

    watcher = CodexSessionWatcher(
        WatcherConfig(
            sessions_root=sessions_root,
            notes_root=tmp_path / "notes",
            runtime_dir=tmp_path / "runtime",
            state_path=tmp_path / "runtime" / "watcher-state.json",
            idle_seconds=10,
        )
    )

    first = watcher.run_once(now_ts=time.time())
    second = watcher.run_once(now_ts=time.time())

    assert first["processed_count"] == 1
    assert second["processed_count"] == 0


def test_watcher_creates_checkpoint_for_active_changed_rollout(tmp_path: Path) -> None:
    sessions_root = tmp_path / "sessions"
    sessions_root.mkdir()
    rollout = sessions_root / "rollout-2026-04-04T13-17-22-019d597f-d23c-7391-9214-4c5b847d13ce.jsonl"
    lines = [
        {
            "timestamp": "2026-04-04T17:18:07.854Z",
            "type": "session_meta",
            "payload": {
                "id": "019d597f-d23c-7391-9214-4c5b847d13ce",
                "timestamp": "2026-04-04T17:17:22.372Z",
                "cwd": "C:\\workspaces\\demo\\mem-store",
                "originator": "Codex Desktop",
            },
        },
        {
            "timestamp": "2026-04-04T17:18:07.856Z",
            "type": "event_msg",
            "payload": {"type": "user_message", "message": "We found the wrong DB issue and need a checkpoint."},
        },
        {
            "timestamp": "2026-04-04T17:18:11.235Z",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Fix: use one canonical bridge database so recall stays trustworthy."}],
            },
        },
    ]
    rollout.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")

    watcher = CodexSessionWatcher(
        WatcherConfig(
            sessions_root=sessions_root,
            notes_root=tmp_path / "notes",
            runtime_dir=tmp_path / "runtime",
            state_path=tmp_path / "runtime" / "watcher-state.json",
            idle_seconds=3600,
            checkpoint_seconds=1,
            checkpoint_min_messages=2,
        )
    )

    result = watcher.run_once(now_ts=time.time())
    recall = watcher.store.recall(namespace="project:mem-store", tags_any=["auto-checkpoint"], limit=5)
    seen = watcher.store.recall(namespace="project:mem-store", tags_any=["kind:session-seen"], limit=5)

    assert result["processed_count"] == 2
    assert {item["mode"] for item in result["processed"]} == {"session-seen", "checkpoint"}
    assert recall["count"] == 1
    assert seen["count"] == 1
    assert "auto-checkpoint" in recall["items"][0]["tags"]


def test_watcher_marks_active_session_seen_before_closeout(tmp_path: Path) -> None:
    sessions_root = tmp_path / "sessions"
    sessions_root.mkdir()
    rollout = sessions_root / "rollout-2026-04-05T10-00-00-019d597f-d23c-7391-9214-4c5b847d13ce.jsonl"
    lines = [
        {
            "timestamp": "2026-04-05T10:00:00.000Z",
            "type": "session_meta",
            "payload": {
                "id": "019d597f-d23c-7391-9214-4c5b847d13ce",
                "timestamp": "2026-04-05T10:00:00.000Z",
                "cwd": "C:\\workspaces\\demo\\resume-work",
                "originator": "Codex Desktop",
            },
        },
        {
            "timestamp": "2026-04-05T10:00:10.000Z",
            "type": "event_msg",
            "payload": {"type": "user_message", "message": "Review the current draft."},
        },
    ]
    rollout.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")

    watcher = CodexSessionWatcher(
        WatcherConfig(
            sessions_root=sessions_root,
            notes_root=tmp_path / "notes",
            runtime_dir=tmp_path / "runtime",
            state_path=tmp_path / "runtime" / "watcher-state.json",
            idle_seconds=3600,
            checkpoint_seconds=300,
            checkpoint_min_messages=2,
        )
    )

    first = watcher.run_once(now_ts=time.time())
    second = watcher.run_once(now_ts=time.time())
    seen = watcher.store.recall(namespace="project:resume-work", tags_any=["kind:session-seen"], limit=5)

    assert first["processed_count"] == 1
    assert first["processed"][0]["mode"] == "session-seen"
    assert second["processed_count"] == 0
    assert seen["count"] == 1
    assert "status:active" in seen["items"][0]["tags"]
    assert seen["items"][0]["source_app"] == "codex-session-seen"

