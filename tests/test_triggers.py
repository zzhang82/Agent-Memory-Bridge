from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_mem_bridge.cli import main
from agent_mem_bridge.lifecycle_activation import activate
from agent_mem_bridge.service_lock import ServiceFileLock
from agent_mem_bridge.triggers import (
    MANAGED_BLOCK_START,
    SUPPORTED_TRIGGER_CLIENTS,
    TRIGGER_CAPABILITY_MATRIX,
    TRIGGER_MOMENTS,
    _hook_command_string,
    _state_file_path,
    build_trigger_matrix_report,
    install_client_triggers,
    is_scratch_path,
    render_managed_instruction_block,
    render_trigger_matrix_markdown,
    run_trigger_hook,
    verify_client_triggers,
)


def test_trigger_capability_matrix_has_all_30_cells_and_honest_support_levels() -> None:
    report = build_trigger_matrix_report()
    assert report["cell_count"] == 30
    assert tuple(report["clients"]) == SUPPORTED_TRIGGER_CLIENTS
    assert tuple(report["moments"]) == TRIGGER_MOMENTS

    # Verify honest non-native cells per Klaus audit
    assert TRIGGER_CAPABILITY_MATRIX["cursor"]["prompt_intent"]["support"] == "deferred_hook"
    assert TRIGGER_CAPABILITY_MATRIX["cursor"]["subagent_handoff"]["support"] == "instruction_rule"
    assert TRIGGER_CAPABILITY_MATRIX["vscode"]["prompt_intent"]["support"] == "deferred_hook"
    assert TRIGGER_CAPABILITY_MATRIX["codex"]["session_start"]["support"] == "trust_gated_hook"
    assert TRIGGER_CAPABILITY_MATRIX["codex"]["first_write"]["support"] == "instruction_rule"
    for moment in TRIGGER_MOMENTS:
        assert TRIGGER_CAPABILITY_MATRIX["cline"][moment]["support"] == "instruction_rule"

    markdown = render_trigger_matrix_markdown(report)
    assert "`deferred_hook`" in markdown
    assert "`trust_gated_hook`" in markdown
    assert "`instruction_rule`" in markdown


def test_vendor_hook_payloads_into_core_lifecycle_hook_stay_ignored() -> None:
    claude_payload = {
        "hook_event_name": "UserPromptSubmit",
        "session_id": "s-1",
        "prompt": "Why did we choose SQLite WAL?",
        "transcript_path": "/tmp/transcript.jsonl",
    }
    cursor_payload = {
        "hook_event_name": "sessionStart",
        "conversation_id": "c-1",
    }
    assert activate(claude_payload).kind == "ignore"
    assert activate(cursor_payload).kind == "ignore"


@pytest.mark.parametrize("client", SUPPORTED_TRIGGER_CLIENTS)
def test_all_five_clients_pass_trigger_self_test_and_leak_no_private_namespaces(tmp_path: Path, client: str) -> None:
    result = verify_client_triggers(client, bridge_home=tmp_path / "amb-home")
    assert result["ok"] is True, json.dumps(result, indent=2)
    serialized = json.dumps(result)
    assert "cole-" not in serialized


def test_trigger_state_uses_sha256_filenames_and_fails_open_on_lock_contention(tmp_path: Path) -> None:
    bridge_home = tmp_path / "amb-home"
    # Distinct session IDs that only differ in punctuation must not collide
    path_a = _state_file_path(bridge_home, "claude-code", "session:alpha/1")
    path_b = _state_file_path(bridge_home, "claude-code", "session_alpha_1")
    assert path_a != path_b

    # Hold the lock on session state and verify run_trigger_hook fails open (returns {} or {"continue": True})
    path_a.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path_a.with_suffix(".lock")
    with ServiceFileLock(lock_path):
        claude_res = run_trigger_hook(
            "claude-code",
            {
                "hook_event_name": "PreToolUse",
                "session_id": "session:alpha/1",
                "tool_name": "Write",
                "tool_input": {"file_path": "/workspace/src/app.py"},
            },
            bridge_home=bridge_home,
        )
        cursor_res = run_trigger_hook(
            "cursor",
            {
                "hook_event_name": "beforeSubmitPrompt",
                "conversation_id": "session:alpha/1",
                "prompt": "Why did this fail?",
            },
            bridge_home=bridge_home,
        )
    assert claude_res == {}
    assert cursor_res == {"continue": True}


def test_cross_platform_scratch_path_detection() -> None:
    assert is_scratch_path("/tmp/scratch.py") is True
    assert is_scratch_path("/private/tmp/test.txt") is True
    assert is_scratch_path("C:\\Users\\alice\\AppData\\Local\\Temp\\scratch.py") is True
    assert is_scratch_path("/workspace/src/agent_mem_bridge/cli.py") is False


@pytest.mark.parametrize("client", SUPPORTED_TRIGGER_CLIENTS)
def test_install_client_triggers_preserves_existing_hooks_and_is_idempotent(tmp_path: Path, client: str) -> None:
    workspace = tmp_path / "repo"
    user_home = tmp_path / "home"
    workspace.mkdir()
    user_home.mkdir()

    if client == "claude-code":
        settings = workspace / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text(
            json.dumps(
                {
                    "model": "sonnet",
                    "hooks": {
                        "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo existing"}]}]
                    },
                }
            ),
            encoding="utf-8",
        )
    elif client == "cursor":
        hooks = workspace / ".cursor" / "hooks.json"
        hooks.parent.mkdir(parents=True)
        hooks.write_text(
            json.dumps({"version": 1, "hooks": {"sessionStart": [{"command": "echo other"}]}}),
            encoding="utf-8",
        )
    elif client == "codex":
        codex_dir = workspace / ".codex"
        codex_dir.mkdir(parents=True)
        (codex_dir / "config.toml").write_text("[features]\nhooks = false\n", encoding="utf-8")
        (codex_dir / "hooks.json").write_text(
            json.dumps(
                {"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "echo existing-codex"}]}]}}
            ),
            encoding="utf-8",
        )
    elif client == "vscode":
        vs_hooks = workspace / ".github" / "hooks" / "amb-triggers.json"
        vs_hooks.parent.mkdir(parents=True)
        vs_hooks.write_text(
            json.dumps({"hooks": {"sessionStart": [{"type": "command", "command": "echo existing-vscode"}]}}),
            encoding="utf-8",
        )

    first = install_client_triggers(client, scope="project", cwd=workspace, home=user_home)
    second = install_client_triggers(client, scope="project", cwd=workspace, home=user_home)
    assert first["ok"] is True
    assert second["ok"] is True

    for file_path in second["files"]:
        text = Path(file_path).read_text(encoding="utf-8")
        assert "cole-" not in text
        if file_path.endswith(".md"):
            assert text.count(MANAGED_BLOCK_START) == 1

    if client == "claude-code":
        parsed = json.loads((workspace / ".claude" / "settings.json").read_text(encoding="utf-8"))
        assert parsed["model"] == "sonnet"
        pre_entries = parsed["hooks"]["PreToolUse"]
        assert any("echo existing" in json.dumps(item) for item in pre_entries)
        amb_write_entries = [
            item for item in pre_entries if item.get("matcher") == "Edit|Write|NotebookEdit|MultiEdit|Bash"
        ]
        assert len(amb_write_entries) == 1
        claude_denied_1 = run_trigger_hook(
            "claude-code",
            {"hook_event_name": "PostToolUseFailure", "session_id": "c-perm", "error": "Permission denied by user"},
            bridge_home=tmp_path / "c-perm-home",
        )
        claude_empty = run_trigger_hook(
            "claude-code",
            {"hook_event_name": "PostToolUseFailure", "session_id": "c-perm", "error": "   "},
            bridge_home=tmp_path / "c-perm-home",
        )
        claude_real_1 = run_trigger_hook(
            "claude-code",
            {"hook_event_name": "PostToolUseFailure", "session_id": "c-perm", "error": "AssertionError"},
            bridge_home=tmp_path / "c-perm-home",
        )
        claude_real_2 = run_trigger_hook(
            "claude-code",
            {"hook_event_name": "PostToolUseFailure", "session_id": "c-perm", "error": "AssertionError"},
            bridge_home=tmp_path / "c-perm-home",
        )
        assert claude_denied_1 == {} and claude_empty == {} and claude_real_1 == {}
        assert "additionalContext" in (claude_real_2.get("hookSpecificOutput") or {})
    elif client == "codex":
        assert any("features" in w for w in first["warnings"])
        assert any("/hooks" in w for w in first["warnings"])
        codex_hooks = json.loads((workspace / ".codex" / "hooks.json").read_text(encoding="utf-8"))
        assert "echo existing-codex" in json.dumps(codex_hooks["hooks"]["SessionStart"])
    elif client == "cursor":
        denied_1 = run_trigger_hook(
            "cursor",
            {"hook_event_name": "postToolUseFailure", "conversation_id": "perm-1", "failure_type": "permission_denied"},
            bridge_home=tmp_path / "perm-home",
        )
        denied_2 = run_trigger_hook(
            "cursor",
            {
                "hook_event_name": "postToolUseFailure",
                "conversation_id": "perm-1",
                "error_message": "Permission denied by user",
            },
            bridge_home=tmp_path / "perm-home",
        )
        empty_err = run_trigger_hook(
            "cursor",
            {"hook_event_name": "postToolUseFailure", "conversation_id": "perm-1", "error_message": "   "},
            bridge_home=tmp_path / "perm-home",
        )
        real_fail_1 = run_trigger_hook(
            "cursor",
            {"hook_event_name": "postToolUseFailure", "conversation_id": "perm-1", "error_message": "AssertionError"},
            bridge_home=tmp_path / "perm-home",
        )
        real_fail_2 = run_trigger_hook(
            "cursor",
            {"hook_event_name": "postToolUseFailure", "conversation_id": "perm-1", "error_message": "AssertionError"},
            bridge_home=tmp_path / "perm-home",
        )
        assert denied_1 == {} and denied_2 == {} and empty_err == {} and real_fail_1 == {}
        assert "additional_context" in real_fail_2
        spaced_dir = tmp_path / "path with spaces"
        spaced_dir.mkdir()
        spaced_py = spaced_dir / "python3"
        spaced_py.write_text("", encoding="utf-8")
        assert _hook_command_string(spaced_py, "cursor").startswith(f'"{spaced_py.resolve()}"')
        with pytest.raises(ValueError, match="must not contain quotes or newline"):
            _hook_command_string(tmp_path / 'bad"quote', "cursor")
        with pytest.raises(ValueError, match="must not contain quotes or newline"):
            _hook_command_string(tmp_path / "bad\nline", "cursor")
    elif client == "vscode":
        vs_parsed = json.loads((workspace / ".github" / "hooks" / "amb-triggers.json").read_text(encoding="utf-8"))
        assert "echo existing-vscode" in json.dumps(vs_parsed["hooks"]["sessionStart"])
        vs_denied_1 = run_trigger_hook(
            "vscode",
            {"hook_event_name": "postToolUse", "session_id": "vs-perm", "isError": True, "error": "Permission denied"},
            bridge_home=tmp_path / "vs-perm-home",
        )
        vs_empty = run_trigger_hook(
            "vscode",
            {"hook_event_name": "postToolUse", "session_id": "vs-perm", "isError": True, "error": "   "},
            bridge_home=tmp_path / "vs-perm-home",
        )
        vs_real_1 = run_trigger_hook(
            "vscode",
            {"hook_event_name": "postToolUse", "session_id": "vs-perm", "isError": True, "error": "AssertionError"},
            bridge_home=tmp_path / "vs-perm-home",
        )
        vs_real_2 = run_trigger_hook(
            "vscode",
            {"hook_event_name": "postToolUse", "session_id": "vs-perm", "isError": True, "error": "AssertionError"},
            bridge_home=tmp_path / "vs-perm-home",
        )
        assert vs_denied_1 == {} and vs_empty == {} and vs_real_1 == {}
        assert "additionalContext" in vs_real_2


def test_cli_triggers_and_trigger_hook_entrypoints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["triggers", "matrix", "--json"]) == 0
    matrix_out = json.loads(capsys.readouterr().out)
    assert matrix_out["cell_count"] == 30

    assert main(["triggers", "test", "claude-code", "--json"]) == 0
    test_out = json.loads(capsys.readouterr().out)
    assert test_out["ok"] is True

    monkeypatch.setattr(
        "sys.stdin",
        type(
            "FakeStdin",
            (),
            {"read": lambda self: json.dumps({"hook_event_name": "SessionStart", "session_id": "cli-1"})},
        )(),
    )
    assert main(["trigger-hook", "--client", "claude-code"]) == 0
    hook_out = json.loads(capsys.readouterr().out)
    assert "additionalContext" in hook_out["hookSpecificOutput"]
    assert "cole-" not in render_managed_instruction_block("project:demo")
