import json
import os
import time
from pathlib import Path

from agent_mem_bridge.codex_rollout import build_watcher_episode_begin_request, parse_rollout_file
from agent_mem_bridge.watcher import CodexSessionWatcher, WatcherConfig

THREAD_ID = "019d597f-d23c-7391-9214-4c5b847d13ce"


def _episode_counts(watcher: CodexSessionWatcher) -> dict[str, int]:
    with watcher.store._connect() as conn:
        return {
            table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in ("memories", "agent_runs", "run_events", "run_outcomes")
        }


def _write_rollout(
    sessions_root: Path,
    *,
    cwd: str = "C:\\workspaces\\demo\\mem-store",
    user_message: str = "Build a memory bridge.",
    assistant_message: str = "Built the foundation and tests.",
) -> Path:
    rollout = sessions_root / f"rollout-2026-04-04T13-17-22-{THREAD_ID}.jsonl"
    lines = [
        {
            "timestamp": "2026-04-04T17:18:07.854Z",
            "type": "session_meta",
            "payload": {
                "id": THREAD_ID,
                "timestamp": "2026-04-04T17:17:22.372Z",
                "cwd": cwd,
                "originator": "Codex Desktop",
            },
        },
        {
            "timestamp": "2026-04-04T17:18:07.856Z",
            "type": "event_msg",
            "payload": {"type": "user_message", "message": user_message},
        },
        {
            "timestamp": "2026-04-04T17:18:11.235Z",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": assistant_message}],
            },
        },
    ]
    rollout.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")
    return rollout


def _write_watcher_state(
    watcher: CodexSessionWatcher,
    rollout: Path,
    entry: dict[str, object] | str,
) -> None:
    watcher.config.state_path.write_text(
        json.dumps({str(rollout): entry}),
        encoding="utf-8",
    )


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
            legacy_memory_mode=True,
        )
    )

    first = watcher.run_once(now_ts=time.time())
    second = watcher.run_once(now_ts=time.time())
    closeout = watcher.store.recall(namespace="project:mem-store", tags_any=["auto-closeout"], limit=5)

    assert first["processed_count"] == 1
    assert second["processed_count"] == 0
    assert list((tmp_path / "notes").rglob("*.md"))
    assert closeout["count"] == 1
    assert closeout["items"][0]["source_app"] == "codex-session-watcher"


def test_watcher_skips_truncated_rollout_tail(tmp_path: Path) -> None:
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
    ]
    rollout.write_text(
        "\n".join(json.dumps(line) for line in lines) + '\n{"timestamp": "2026-04-04',
        encoding="utf-8",
    )
    old_time = time.time() - 120
    import os

    os.utime(rollout, (old_time, old_time))

    watcher = CodexSessionWatcher(
        WatcherConfig(
            sessions_root=sessions_root,
            notes_root=tmp_path / "notes",
            runtime_dir=tmp_path / "runtime",
            state_path=tmp_path / "runtime" / "watcher-state.json",
            idle_seconds=10,
        )
    )

    result = watcher.run_once(now_ts=time.time())

    assert result["processed_count"] == 1


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
                "content": [
                    {
                        "type": "output_text",
                        "text": "Fix: use one canonical bridge database so recall stays trustworthy.",
                    }
                ],
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
            legacy_memory_mode=True,
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
    assert recall["items"][0]["source_app"] == "codex-session-checkpointer"


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
            legacy_memory_mode=True,
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


def test_default_watcher_records_metadata_only_checkpoint_without_memory_or_notes(tmp_path: Path) -> None:
    sessions_root = tmp_path / "sessions"
    sessions_root.mkdir()
    user_message = "DISTINCTIVE_USER_BODY should never reach the run ledger."
    assistant_message = "DISTINCTIVE_ASSISTANT_BODY confirms the checkpoint should remain metadata only."
    rollout = _write_rollout(
        sessions_root,
        user_message=user_message,
        assistant_message=assistant_message,
    )
    notes_root = tmp_path / "notes"
    watcher = CodexSessionWatcher(
        WatcherConfig(
            sessions_root=sessions_root,
            notes_root=notes_root,
            runtime_dir=tmp_path / "runtime",
            state_path=tmp_path / "runtime" / "watcher-state.json",
            idle_seconds=3600,
            checkpoint_seconds=1,
            checkpoint_min_messages=2,
        )
    )

    result = watcher.run_once(now_ts=time.time())

    assert {item["mode"] for item in result["processed"]} == {"session-seen", "checkpoint"}
    checkpoint = next(item for item in result["processed"] if item["mode"] == "checkpoint")
    run = watcher.store.get_run(workspace_key="project:mem-store", run_id=checkpoint["run_id"])
    assert run["run"]["status"] == "active"
    assert run["events"][0]["event_type"] == "checkpoint"
    assert run["events"][0]["payload"]["total_count"] == 2
    assert _episode_counts(watcher) == {
        "memories": 0,
        "agent_runs": 1,
        "run_events": 1,
        "run_outcomes": 0,
    }
    assert not notes_root.exists()

    with watcher.store._connect() as conn:
        durable = {
            "runs": [
                tuple(row)
                for row in conn.execute(
                    """
                    SELECT root_goal, thread_id, actor, source_app, source_client,
                           client_session_id, client_workspace, client_transport
                    FROM agent_runs
                    """
                )
            ],
            "events": [
                tuple(row)
                for row in conn.execute(
                    """
                    SELECT summary, payload_json, evidence_json, thread_id, actor,
                           source_app, source_client, client_session_id,
                           client_workspace, client_transport
                    FROM run_events
                    """
                )
            ],
            "outcomes": [
                tuple(row)
                for row in conn.execute(
                    """
                    SELECT evidence_json, metrics_json, termination_reason, actor,
                           source_app, source_client, client_session_id,
                           client_workspace, client_transport
                    FROM run_outcomes
                    """
                )
            ],
        }
    serialized = json.dumps(durable)
    for forbidden in (
        user_message,
        assistant_message,
        str(rollout),
        "C:\\workspaces\\demo\\mem-store",
        "transcript",
        "messages",
    ):
        assert forbidden not in serialized


def test_default_watcher_closes_an_idle_first_observation_in_one_run(tmp_path: Path) -> None:
    sessions_root = tmp_path / "sessions"
    sessions_root.mkdir()
    rollout = _write_rollout(sessions_root)
    old_time = time.time() - 120
    os.utime(rollout, (old_time, old_time))
    watcher = CodexSessionWatcher(
        WatcherConfig(
            sessions_root=sessions_root,
            notes_root=tmp_path / "notes",
            runtime_dir=tmp_path / "runtime",
            state_path=tmp_path / "runtime" / "watcher-state.json",
            idle_seconds=10,
        )
    )

    result = watcher.run_once(now_ts=time.time())

    assert [item["mode"] for item in result["processed"]] == ["closeout"]
    closeout = result["processed"][0]
    run = watcher.store.get_run(workspace_key="project:mem-store", run_id=closeout["run_id"])
    assert run["run"]["status"] == "completed"
    assert run["outcome"] is not None
    assert run["outcome"]["outcome"] == "unverified"
    assert run["outcome"]["evaluator_type"] == "system"
    assert run["outcome"]["termination_reason"] == "rollout_idle"
    assert _episode_counts(watcher) == {
        "memories": 0,
        "agent_runs": 1,
        "run_events": 1,
        "run_outcomes": 1,
    }


def test_default_watcher_migrates_legacy_closeout_state_without_a_run_id(tmp_path: Path) -> None:
    sessions_root = tmp_path / "sessions"
    sessions_root.mkdir()
    rollout = _write_rollout(sessions_root)
    old_time = time.time() - 120
    os.utime(rollout, (old_time, old_time))
    watcher = CodexSessionWatcher(
        WatcherConfig(
            sessions_root=sessions_root,
            notes_root=tmp_path / "notes",
            runtime_dir=tmp_path / "runtime",
            state_path=tmp_path / "runtime" / "watcher-state.json",
            idle_seconds=10,
        )
    )
    fingerprint = f"{rollout.stat().st_mtime_ns}:{rollout.stat().st_size}"
    _write_watcher_state(watcher, rollout, {"closeout_fingerprint": fingerprint})

    first = watcher.run_once(now_ts=time.time())
    second = watcher.run_once(now_ts=time.time())

    assert [item["mode"] for item in first["processed"]] == ["closeout"]
    assert second["processed"] == []
    closeout = first["processed"][0]
    run = watcher.store.get_run(workspace_key="project:mem-store", run_id=closeout["run_id"])
    assert run["run"]["status"] == "completed"
    assert run["outcome"]["outcome"] == "unverified"
    assert _episode_counts(watcher) == {
        "memories": 0,
        "agent_runs": 1,
        "run_events": 1,
        "run_outcomes": 1,
    }


def test_default_watcher_migrates_legacy_raw_closeout_state(tmp_path: Path) -> None:
    sessions_root = tmp_path / "sessions"
    sessions_root.mkdir()
    rollout = _write_rollout(sessions_root)
    old_time = time.time() - 120
    os.utime(rollout, (old_time, old_time))
    watcher = CodexSessionWatcher(
        WatcherConfig(
            sessions_root=sessions_root,
            notes_root=tmp_path / "notes",
            runtime_dir=tmp_path / "runtime",
            state_path=tmp_path / "runtime" / "watcher-state.json",
            idle_seconds=10,
        )
    )
    fingerprint = f"{rollout.stat().st_mtime_ns}:{rollout.stat().st_size}"
    _write_watcher_state(watcher, rollout, fingerprint)

    first = watcher.run_once(now_ts=time.time())
    second = watcher.run_once(now_ts=time.time())

    assert [item["mode"] for item in first["processed"]] == ["closeout"]
    assert second["processed"] == []
    assert _episode_counts(watcher) == {
        "memories": 0,
        "agent_runs": 1,
        "run_events": 1,
        "run_outcomes": 1,
    }


def test_default_watcher_rebinds_episode_state_when_its_run_is_gone(tmp_path: Path) -> None:
    sessions_root = tmp_path / "sessions"
    sessions_root.mkdir()
    rollout = _write_rollout(sessions_root)
    old_time = time.time() - 120
    os.utime(rollout, (old_time, old_time))
    watcher = CodexSessionWatcher(
        WatcherConfig(
            sessions_root=sessions_root,
            notes_root=tmp_path / "notes",
            runtime_dir=tmp_path / "runtime",
            state_path=tmp_path / "runtime" / "watcher-state.json",
            idle_seconds=10,
        )
    )
    fingerprint = f"{rollout.stat().st_mtime_ns}:{rollout.stat().st_size}"
    _write_watcher_state(
        watcher,
        rollout,
        {
            "episode_state_version": 1,
            "run_id": "run_missing",
            "root_work_item_id": "work_missing",
            "run_status": "active",
            "session_seen": True,
            "closeout_fingerprint": fingerprint,
            "terminal_skip_fingerprint": fingerprint,
        },
    )

    result = watcher.run_once(now_ts=time.time())

    assert [item["mode"] for item in result["processed"]] == ["closeout"]
    assert result["processed"][0]["run_id"] != "run_missing"
    assert _episode_counts(watcher)["run_outcomes"] == 1


def test_default_watcher_migrates_legacy_checkpoint_state_and_remains_idempotent(tmp_path: Path) -> None:
    sessions_root = tmp_path / "sessions"
    sessions_root.mkdir()
    rollout = _write_rollout(sessions_root)
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
    fingerprint = f"{rollout.stat().st_mtime_ns}:{rollout.stat().st_size}"
    now_ts = time.time()
    _write_watcher_state(
        watcher,
        rollout,
        {
            "session_seen": True,
            "checkpoint_fingerprint": fingerprint,
            "last_checkpoint_ts": now_ts,
        },
    )

    first = watcher.run_once(now_ts=now_ts)
    second = watcher.run_once(now_ts=now_ts + 1)
    old_time = now_ts - 7200
    os.utime(rollout, (old_time, old_time))
    third = watcher.run_once(now_ts=now_ts)
    fourth = watcher.run_once(now_ts=now_ts + 1)

    assert {item["mode"] for item in first["processed"]} == {"session-seen", "checkpoint"}
    assert second["processed"] == []
    assert [item["mode"] for item in third["processed"]] == ["closeout"]
    assert fourth["processed"] == []
    assert _episode_counts(watcher) == {
        "memories": 0,
        "agent_runs": 1,
        "run_events": 2,
        "run_outcomes": 1,
    }


def test_default_watcher_repairs_current_buggy_closeout_gate_for_an_active_run(tmp_path: Path) -> None:
    sessions_root = tmp_path / "sessions"
    sessions_root.mkdir()
    rollout = _write_rollout(sessions_root)
    old_time = time.time() - 120
    os.utime(rollout, (old_time, old_time))
    watcher = CodexSessionWatcher(
        WatcherConfig(
            sessions_root=sessions_root,
            notes_root=tmp_path / "notes",
            runtime_dir=tmp_path / "runtime",
            state_path=tmp_path / "runtime" / "watcher-state.json",
            idle_seconds=10,
        )
    )
    summary = parse_rollout_file(rollout)
    begin_request = build_watcher_episode_begin_request(summary)
    begin = watcher.store.begin_run(**begin_request)
    fingerprint = f"{rollout.stat().st_mtime_ns}:{rollout.stat().st_size}"
    _write_watcher_state(
        watcher,
        rollout,
        {
            "run_id": begin["run_id"],
            "root_work_item_id": begin["root_work_item_id"],
            "run_status": "active",
            "workspace_key": begin_request["workspace_key"],
            "session_seen": True,
            "checkpoint_fingerprint": fingerprint,
            "closeout_fingerprint": fingerprint,
            "terminal_skip_fingerprint": fingerprint,
        },
    )

    result = watcher.run_once(now_ts=time.time())

    assert [item["mode"] for item in result["processed"]] == ["closeout"]
    assert result["processed"][0]["run_id"] == begin["run_id"]
    assert _episode_counts(watcher)["run_outcomes"] == 1


def test_default_watcher_replays_after_state_loss_without_duplicate_ledger_rows(tmp_path: Path) -> None:
    sessions_root = tmp_path / "sessions"
    sessions_root.mkdir()
    _write_rollout(sessions_root)
    state_path = tmp_path / "runtime" / "watcher-state.json"
    watcher = CodexSessionWatcher(
        WatcherConfig(
            sessions_root=sessions_root,
            notes_root=tmp_path / "notes",
            runtime_dir=tmp_path / "runtime",
            state_path=state_path,
            idle_seconds=3600,
            checkpoint_seconds=1,
            checkpoint_min_messages=2,
        )
    )

    watcher.run_once(now_ts=time.time())
    initial_counts = _episode_counts(watcher)
    watcher.run_once(now_ts=time.time())
    assert _episode_counts(watcher) == initial_counts
    state_path.unlink()

    replay = watcher.run_once(now_ts=time.time())

    assert _episode_counts(watcher) == initial_counts
    checkpoint = next(item for item in replay["processed"] if item["mode"] == "checkpoint")
    assert checkpoint["begin_idempotent_replay"] is True
    assert checkpoint["event_idempotent_replay"] is True


def test_default_watcher_keeps_one_run_when_later_metadata_changes_workspace(tmp_path: Path) -> None:
    sessions_root = tmp_path / "sessions"
    sessions_root.mkdir()
    rollout = _write_rollout(sessions_root)
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
    now_ts = time.time()

    first = watcher.run_once(now_ts=now_ts)
    with rollout.open("a", encoding="utf-8") as handle:
        handle.write(
            "\n"
            + json.dumps(
                {
                    "timestamp": "2026-04-04T17:19:00.000Z",
                    "type": "session_meta",
                    "payload": {"id": THREAD_ID, "cwd": "C:\\workspaces\\demo\\renamed-workspace"},
                }
            )
        )

    second = watcher.run_once(now_ts=now_ts + 2)

    first_checkpoint = next(item for item in first["processed"] if item["mode"] == "checkpoint")
    second_checkpoint = next(item for item in second["processed"] if item["mode"] == "checkpoint")
    assert second_checkpoint["run_id"] == first_checkpoint["run_id"]
    assert _episode_counts(watcher)["agent_runs"] == 1
    run = watcher.store.get_run(workspace_key="project:mem-store", run_id=first_checkpoint["run_id"])
    assert run["events"][-1]["payload"]["workspace_key"] == "project:mem-store"
    assert run["events"][-1]["payload"]["workspace_basename"] == "mem-store"


def test_default_watcher_skips_later_growth_after_a_terminal_run_once_per_fingerprint(tmp_path: Path) -> None:
    sessions_root = tmp_path / "sessions"
    sessions_root.mkdir()
    rollout = _write_rollout(sessions_root)
    old_time = time.time() - 120
    os.utime(rollout, (old_time, old_time))
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
    counts_after_closeout = _episode_counts(watcher)
    with rollout.open("a", encoding="utf-8") as handle:
        handle.write("\n" + json.dumps({"timestamp": "2026-04-04T17:19:00.000Z", "type": "event_msg", "payload": {}}))

    second = watcher.run_once(now_ts=time.time())
    third = watcher.run_once(now_ts=time.time())

    assert [item["mode"] for item in first["processed"]] == ["closeout"]
    assert [item["mode"] for item in second["processed"]] == ["terminal-skip"]
    assert third["processed"] == []
    assert _episode_counts(watcher) == counts_after_closeout
