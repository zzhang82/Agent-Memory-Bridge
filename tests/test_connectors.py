from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

import pytest

from agent_mem_bridge.connectors import (
    BRIDGE_CHECK_SCHEMA,
    CONNECTOR_SCHEMA,
    SERVER_ENTRY_NAME,
    SUPPORTED_CONNECTOR_CLIENTS,
    _is_healthy_cli_mcp_line,
    check_client_connection,
    connect_client,
    normalize_connector_client,
    resolve_connector_config_path,
    run_bridge_check,
)


def test_normalize_connector_client_accepts_supported_names_and_rejects_unknown() -> None:
    assert normalize_connector_client("claude-code") == "claude-code"
    assert normalize_connector_client("claude_code") == "claude-code"
    assert normalize_connector_client("Cursor") == "cursor"
    assert normalize_connector_client("codex") == "codex"
    assert normalize_connector_client("cline") == "cline"
    assert normalize_connector_client("vscode") == "vscode"
    assert normalize_connector_client("vs-code") == "vscode"
    with pytest.raises(ValueError, match="Unsupported client"):
        normalize_connector_client("unknown-editor")


@pytest.mark.parametrize("client", SUPPORTED_CONNECTOR_CLIENTS)
def test_connect_and_check_all_five_clients_preserves_siblings(tmp_path: Path, client: str) -> None:
    workspace = tmp_path / "workspace"
    user_home = tmp_path / "home"
    bridge_home = tmp_path / "amb-home"
    workspace.mkdir()
    user_home.mkdir()
    bridge_home.mkdir()

    scope = "user" if client == "cline" else "project"
    config_path = resolve_connector_config_path(
        client,
        scope=scope,
        cwd=workspace,
        home=user_home,
    )
    config_path.parent.mkdir(parents=True, exist_ok=True)

    # Seed an unrelated sibling server before connecting AMB
    if client == "codex":
        config_path.write_text(
            '[features]\nhooks = true\n\n[mcp_servers.playwright]\ncommand = "/usr/bin/npx"\nargs = ["-y", "@playwright/mcp"]\n',
            encoding="utf-8",
        )
    elif client == "vscode":
        config_path.write_text(
            json.dumps({"servers": {"playwright": {"type": "stdio", "command": "/usr/bin/npx", "args": []}}}),
            encoding="utf-8",
        )
    else:
        config_path.write_text(
            json.dumps({"mcpServers": {"playwright": {"command": "/usr/bin/npx", "args": []}}}),
            encoding="utf-8",
        )

    report = connect_client(
        client,
        scope=scope,
        cwd=workspace,
        home=user_home,
        python_path=sys.executable,
        bridge_home=bridge_home,
        verify_spawn=False,
        host_cli_probe=lambda _c: "host_reload_required",
    )
    assert report["schema"] == CONNECTOR_SCHEMA
    assert report["ok"] is True
    assert report["unrelated_server_count"] == 1
    assert report["check"]["tier1_config_valid"] is True
    assert report["check"]["tier2_server_spawn_verified"] is True
    assert report["check"]["tier3_host_registration_status"] == "host_reload_required"
    assert report["check"]["lists_store_and_recall"] is True

    # Verify sibling server was preserved on disk
    raw_after = config_path.read_text(encoding="utf-8")
    if client == "codex":
        parsed_toml = tomllib.loads(raw_after)
        assert parsed_toml["features"]["hooks"] is True
        assert "playwright" in parsed_toml["mcp_servers"]
        assert SERVER_ENTRY_NAME in parsed_toml["mcp_servers"]
    elif client == "vscode":
        parsed_json = json.loads(raw_after)
        assert "playwright" in parsed_json["servers"]
        assert parsed_json["servers"][SERVER_ENTRY_NAME]["type"] == "stdio"
    else:
        parsed_json = json.loads(raw_after)
        assert "playwright" in parsed_json["mcpServers"]
        assert SERVER_ENTRY_NAME in parsed_json["mcpServers"]
        if client == "cline":
            assert parsed_json["mcpServers"][SERVER_ENTRY_NAME]["disabled"] is False
            assert parsed_json["mcpServers"][SERVER_ENTRY_NAME]["autoApprove"] == []


def test_cursor_http_entry_is_protected_until_force_is_set(tmp_path: Path) -> None:
    cursor_cfg = tmp_path / ".cursor" / "mcp.json"
    cursor_cfg.parent.mkdir(parents=True)
    original_payload = {
        "mcpServers": {
            "agent-memory-bridge": {
                "url": "http://127.0.0.1:58080/mcp",
                "headers": {"Authorization": "Bearer test-token"},
            },
            "playwright": {"command": "npx", "args": []},
        }
    }
    cursor_cfg.write_text(json.dumps(original_payload, indent=2), encoding="utf-8")

    blocked = connect_client(
        "cursor",
        scope="project",
        cwd=tmp_path,
        python_path=sys.executable,
        bridge_home=tmp_path / "amb-home",
        force=False,
        verify_spawn=False,
    )
    assert blocked["ok"] is False
    assert blocked["reason"] == "transport_conflict_requires_force"
    assert json.loads(cursor_cfg.read_text(encoding="utf-8")) == original_payload

    forced = connect_client(
        "cursor",
        scope="project",
        cwd=tmp_path,
        python_path=sys.executable,
        bridge_home=tmp_path / "amb-home",
        force=True,
        verify_spawn=False,
    )
    assert forced["ok"] is True
    updated = json.loads(cursor_cfg.read_text(encoding="utf-8"))
    assert "command" in updated["mcpServers"]["agent-memory-bridge"]
    assert "playwright" in updated["mcpServers"]


def test_cline_rejects_invented_project_scope_without_explicit_config_path(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Cline does not read a standard project-local MCP config file"):
        resolve_connector_config_path("cline", scope="project", cwd=tmp_path, home=tmp_path)


def test_vscode_copilot_format_writes_mcp_servers_key(tmp_path: Path) -> None:
    report = connect_client(
        "vscode",
        scope="project",
        cwd=tmp_path,
        vscode_format="copilot",
        python_path=sys.executable,
        bridge_home=tmp_path / "amb-home",
        verify_spawn=False,
    )
    assert report["ok"] is True
    target = tmp_path / ".mcp.json"
    assert target.is_file()
    parsed = json.loads(target.read_text(encoding="utf-8"))
    assert parsed["mcpServers"][SERVER_ENTRY_NAME]["type"] == "stdio"


def test_tier1_check_catches_relative_command_unexpanded_vars_and_disabled_cline(tmp_path: Path) -> None:
    vscode_cfg = tmp_path / ".vscode" / "mcp.json"
    vscode_cfg.parent.mkdir(parents=True)
    vscode_cfg.write_text(
        json.dumps(
            {
                "servers": {
                    SERVER_ENTRY_NAME: {
                        "type": "stdio",
                        "command": "python3",
                        "args": ["-I -m", "agent_mem_bridge"],
                        "env": {"AGENT_MEMORY_BRIDGE_HOME": "${workspaceFolder}/.amb"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    check = check_client_connection("vscode", scope="project", cwd=tmp_path, verify_spawn=False)
    assert check["ok"] is False
    assert check["tier1_config_valid"] is False
    joined_issues = " | ".join(check["issues"])
    assert "absolute path" in joined_issues
    assert "Combined flags" in joined_issues
    assert "unexpanded variable" in joined_issues

    cline_cfg = tmp_path / "cline_mcp_settings.json"
    cline_cfg.write_text(
        json.dumps(
            {
                "mcpServers": {
                    SERVER_ENTRY_NAME: {
                        "command": sys.executable,
                        "args": ["-I", "-m", "agent_mem_bridge"],
                        "disabled": True,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    cline_check = check_client_connection("cline", config_path=cline_cfg, verify_spawn=False)
    assert cline_check["ok"] is False
    assert any("disabled: true" in issue for issue in cline_check["issues"])
    assert any("autoApprove" in issue for issue in cline_check["issues"])
    assert _is_healthy_cli_mcp_line("agent-memory-bridge: connected (no errors)") is True
    assert _is_healthy_cli_mcp_line("agent-memory-bridge: connected (0 errors)") is True
    assert _is_healthy_cli_mcp_line("agent-memory-bridge: disabled") is False
    assert _is_healthy_cli_mcp_line("agent-memory-bridge: failed") is False
    assert _is_healthy_cli_mcp_line("agent-memory-bridge: disconnected") is False
    assert _is_healthy_cli_mcp_line("agent-memory-bridge: error connecting") is False
    assert _is_healthy_cli_mcp_line("playwright: connected") is False


def test_live_stdio_spawn_check_and_two_client_bridge_acceptance(tmp_path: Path) -> None:
    bridge_home = tmp_path / "shared-bridge-home"
    bridge_report = run_bridge_check(
        from_client="claude-code",
        to_client="codex",
        namespace="project:checkout-app",
        bridge_home=bridge_home,
        python_path=sys.executable,
    )
    assert bridge_report["schema"] == BRIDGE_CHECK_SCHEMA
    assert bridge_report["ok"] is True, json.dumps(bridge_report, indent=2)
    assert bridge_report["same_record"] is True
    assert bridge_report["fresh_process"] is True
    assert bridge_report["writer_source_client"] == "claude-code"
    assert bridge_report["reader_source_client"] == "codex"
    assert "Merge pull requests only after CI is green" in bridge_report["claim"]
    assert "Broken main blocked two releases" in bridge_report["reason"]
