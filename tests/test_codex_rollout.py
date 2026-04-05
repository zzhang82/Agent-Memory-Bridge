import json
from pathlib import Path

from agent_mem_bridge.codex_rollout import build_checkpoint_payload, build_closeout_payload, parse_rollout_file


def test_parse_rollout_and_build_payload(tmp_path: Path) -> None:
    rollout = tmp_path / "rollout-2026-04-04T13-17-22-019d597f-d23c-7391-9214-4c5b847d13ce.jsonl"
    lines = [
        {
            "timestamp": "2026-04-04T17:18:07.854Z",
            "type": "session_meta",
            "payload": {
                "id": "019d597f-d23c-7391-9214-4c5b847d13ce",
                "timestamp": "2026-04-04T17:17:22.372Z",
                "cwd": "D:\\playground\\MCPs\\mem-store",
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

    summary = parse_rollout_file(rollout)
    payload = build_closeout_payload(summary)

    assert summary.thread_id == "019d597f-d23c-7391-9214-4c5b847d13ce"
    assert summary.cwd.endswith("mem-store")
    assert summary.user_messages == ["Build a memory bridge."]
    assert payload["namespace"] == "project:mem-store"
    assert payload["session_id"] == summary.thread_id
    assert payload["kind"] == "memory"
    assert payload["source_app"] == "codex-session-watcher"
    assert "kind:summary" in payload["tags"]
    assert "project:mem-store" in payload["tags"]


def test_parse_rollout_keeps_subagent_thread_and_parent_lineage(tmp_path: Path) -> None:
    rollout = tmp_path / "rollout-2026-04-04T13-32-45-019d598d-e7c7-7c61-95e7-dba18b42dde8.jsonl"
    lines = [
        {
            "timestamp": "2026-04-04T17:32:50.427Z",
            "type": "session_meta",
            "payload": {
                "id": "019d598d-e7c7-7c61-95e7-dba18b42dde8",
                "forked_from_id": "019d597f-d23c-7391-9214-4c5b847d13ce",
                "timestamp": "2026-04-04T17:32:45.389Z",
                "cwd": "D:\\playground\\MCPs\\mem-store",
                "originator": "Codex Desktop",
                "agent_nickname": "Plato",
                "agent_role": "default",
            },
        },
        {
            "timestamp": "2026-04-04T17:32:50.429Z",
            "type": "session_meta",
            "payload": {
                "id": "019d597f-d23c-7391-9214-4c5b847d13ce",
                "timestamp": "2026-04-04T17:17:22.372Z",
                "cwd": "D:\\playground\\MCPs\\mem-store",
                "originator": "Codex Desktop",
            },
        },
        {
            "timestamp": "2026-04-04T17:32:58.000Z",
            "type": "event_msg",
            "payload": {"type": "user_message", "message": "Check the watcher output."},
        },
    ]
    rollout.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")

    summary = parse_rollout_file(rollout)
    payload = build_closeout_payload(summary)

    assert summary.thread_id == "019d598d-e7c7-7c61-95e7-dba18b42dde8"
    assert summary.forked_from_id == "019d597f-d23c-7391-9214-4c5b847d13ce"
    assert summary.agent_nickname == "Plato"
    assert payload["session_id"] == "019d598d-e7c7-7c61-95e7-dba18b42dde8"
    assert payload["correlation_id"] == "019d597f-d23c-7391-9214-4c5b847d13ce"
    assert "agent:plato" in payload["tags"]
    assert "parent-thread:019d597f-d23c-7391-9214-4c5b847d13ce" in payload["tags"]
    assert payload["namespace"] == "project:mem-store"


def test_build_checkpoint_payload_prefers_typed_durable_bullets(tmp_path: Path) -> None:
    rollout = tmp_path / "rollout-2026-04-04T19-39-29-019d597f-d23c-7391-9214-4c5b847d13ce.jsonl"
    lines = [
        {
            "timestamp": "2026-04-04T19:39:20.000Z",
            "type": "session_meta",
            "payload": {
                "id": "019d597f-d23c-7391-9214-4c5b847d13ce",
                "timestamp": "2026-04-04T19:17:22.372Z",
                "cwd": "D:\\playground\\MCPs\\mem-store",
                "originator": "Codex Desktop",
            },
        },
        {
            "timestamp": "2026-04-04T19:39:21.000Z",
            "type": "event_msg",
            "payload": {"type": "user_message", "message": "Decision: use checkpoint sync before closeout so important fixes do not wait."},
        },
        {
            "timestamp": "2026-04-04T19:39:22.000Z",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": "Problem: later work can be missing until closeout. Fix: write checkpoint summaries during active rollouts.",
                    }
                ],
            },
        },
    ]
    rollout.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")

    summary = parse_rollout_file(rollout)
    payload = build_checkpoint_payload(summary)

    assert payload["source_app"] == "codex-session-checkpointer"
    assert "auto-checkpoint" in payload["tags"]
    assert "auto-closeout" not in payload["tags"]
    assert any(item.startswith("Decision:") for item in payload["bullets"])
    assert any(item.startswith("Problem:") for item in payload["bullets"])
    assert any(item.startswith("Fix:") for item in payload["bullets"])


def test_build_checkpoint_payload_skips_conversational_confirmations(tmp_path: Path) -> None:
    rollout = tmp_path / "rollout-2026-04-04T19-50-00-019d597f-d23c-7391-9214-4c5b847d13ce.jsonl"
    lines = [
        {
            "timestamp": "2026-04-04T19:50:00.000Z",
            "type": "session_meta",
            "payload": {
                "id": "019d597f-d23c-7391-9214-4c5b847d13ce",
                "timestamp": "2026-04-04T19:17:22.372Z",
                "cwd": "D:\\playground\\MCPs\\mem-store",
                "originator": "Codex Desktop",
            },
        },
        {
            "timestamp": "2026-04-04T19:50:01.000Z",
            "type": "event_msg",
            "payload": {"type": "user_message", "message": "check if agentMemoryBridge loaded"},
        },
        {
            "timestamp": "2026-04-04T19:50:02.000Z",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": "Yes, agentMemoryBridge is loaded and responding. If you want, I can also test recall(namespace=\"project:ç®€åŽ†\", ...) right now. Fix: checkpoint important fixes before closeout.",
                    }
                ],
            },
        },
    ]
    rollout.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")

    summary = parse_rollout_file(rollout)
    payload = build_checkpoint_payload(summary)

    assert any(item.startswith("Fix:") for item in payload["bullets"])
    assert not any("If you want" in item for item in payload["bullets"])
    assert not any("loaded and responding" in item for item in payload["bullets"])

