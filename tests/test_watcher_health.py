import json
from pathlib import Path

from agent_mem_bridge.watcher_health import run_watcher_health_check


def test_watcher_health_reports_ok_for_parseable_rollout(tmp_path: Path) -> None:
    sessions_root = tmp_path / "sessions"
    sessions_root.mkdir()
    rollout = sessions_root / "rollout-2026-04-06T10-00-00-019d597f-d23c-7391-9214-4c5b847d13ce.jsonl"
    lines = [
        {
            "timestamp": "2026-04-06T10:00:00.000Z",
            "type": "session_meta",
            "payload": {
                "id": "019d597f-d23c-7391-9214-4c5b847d13ce",
                "timestamp": "2026-04-06T10:00:00.000Z",
                "cwd": "D:\\playground\\MCPs\\mem-store",
                "originator": "Codex Desktop",
            },
        },
        {
            "timestamp": "2026-04-06T10:00:01.000Z",
            "type": "event_msg",
            "payload": {"type": "user_message", "message": "Check watcher health."},
        },
    ]
    rollout.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")

    report = run_watcher_health_check(sessions_root=sessions_root, limit=10)

    assert report["ok"] is True
    assert report["parse_ok_count"] == 1
    assert report["weak_summary_count"] == 0
    assert report["items"][0]["status"] == "ok"


def test_watcher_health_flags_parseable_but_empty_rollout_summary(tmp_path: Path) -> None:
    sessions_root = tmp_path / "sessions"
    sessions_root.mkdir()
    rollout = sessions_root / "rollout-2026-04-06T10-00-00-019d597f-d23c-7391-9214-4c5b847d13ce.jsonl"
    lines = [
        {
            "timestamp": "2026-04-06T10:00:00.000Z",
            "type": "session_meta",
            "payload": {
                "id": "019d597f-d23c-7391-9214-4c5b847d13ce",
                "timestamp": "2026-04-06T10:00:00.000Z",
                "cwd": "D:\\playground\\MCPs\\mem-store",
                "originator": "Codex Desktop",
            },
        },
        {
            "timestamp": "2026-04-06T10:00:01.000Z",
            "type": "event_msg",
            "payload": {"type": "tool_call", "name": "something-else"},
        },
    ]
    rollout.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")

    report = run_watcher_health_check(sessions_root=sessions_root, limit=10)

    assert report["ok"] is False
    assert report["weak_summary_count"] == 1
    assert report["items"][0]["status"] == "weak"
    assert "no-message-content" in report["items"][0]["reasons"]
