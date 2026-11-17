from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

import pytest

from agent_mem_bridge.cli import main
from agent_mem_bridge.connectors import (
    BRIDGE_CHECK_SCHEMA,
    CONNECTOR_SCHEMA,
    SERVER_ENTRY_NAME,
    SUPPORTED_CONNECTOR_CLIENTS,
    _is_healthy_cli_mcp_line,
    check_client_connection,
    connect_client,
    normalize_connector_client,
    render_connector_report_markdown,
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


def test_codex_toml_merge_keeps_following_sections_and_noop_skips_backup(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    config_path = workspace / ".codex" / "config.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        "\n".join(
            [
                "[mcp_servers.agent-memory-bridge]",
                'command = "/old/python"',
                'args = ["-I", "-m", "agent_mem_bridge"]',
                "",
                "[profiles.fast] # my fast profile",
                'model = "fast"',
                "",
                "[[skills]]",
                'name = "kept-skill"',
                "",
                "[mcp_servers.other]",
                'command = "/usr/bin/other"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    first = connect_client(
        "codex",
        scope="project",
        cwd=workspace,
        python_path=sys.executable,
        bridge_home=tmp_path / "amb-home",
        force=True,
        verify_spawn=False,
    )
    assert first["ok"] is True
    rendered = config_path.read_text(encoding="utf-8")
    assert "[profiles.fast] # my fast profile" in rendered
    assert "[[skills]]" in rendered
    assert 'name = "kept-skill"' in rendered
    assert "[mcp_servers.other]" in rendered
    parsed = tomllib.loads(rendered)
    assert parsed["mcp_servers"]["other"]["command"] == "/usr/bin/other"
    assert parsed["skills"][0]["name"] == "kept-skill"

    backup_path = config_path.with_name(f"{config_path.name}.amb-backup.bak")
    backup_bytes = backup_path.read_bytes()
    backup_mtime = backup_path.stat().st_mtime_ns
    second = connect_client(
        "codex",
        scope="project",
        cwd=workspace,
        python_path=sys.executable,
        bridge_home=tmp_path / "amb-home",
        verify_spawn=False,
    )
    assert second["action"] == "unchanged"
    assert second["ok"] is True
    assert backup_path.read_bytes() == backup_bytes
    assert backup_path.stat().st_mtime_ns == backup_mtime
    assert config_path.read_text(encoding="utf-8") == rendered


def _write_codex_config(config_path: Path, body: str) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(body, encoding="utf-8")


def _connect_codex_force(workspace: Path, bridge_home: Path) -> str:
    report = connect_client(
        "codex",
        scope="project",
        cwd=workspace,
        python_path=sys.executable,
        bridge_home=bridge_home,
        force=True,
        verify_spawn=False,
    )
    assert report["ok"] is True, report
    rendered = (workspace / ".codex" / "config.toml").read_text(encoding="utf-8")
    tomllib.loads(rendered)
    return rendered


def test_codex_toml_strip_preserves_multiline_strings(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    config_path = workspace / ".codex" / "config.toml"
    _write_codex_config(
        config_path,
        "\n".join(
            [
                "[profile]",
                'instructions = """',
                "[mcp_servers.agent-memory-bridge]",
                'command = "/inside-string"',
                '"""',
                "",
                "[mcp_servers.agent-memory-bridge]",
                'command = "/old"',
                "",
                "[mcp_servers.other]",
                'command = "/usr/bin/other"',
                "",
            ]
        ),
    )
    rendered = _connect_codex_force(workspace, tmp_path / "amb-home")
    embedded = rendered.split('"""', 2)[1]
    assert "[mcp_servers.agent-memory-bridge]" in embedded
    assert 'command = "/inside-string"' in embedded
    assert 'command = "/old"' not in rendered
    parsed = tomllib.loads(rendered)
    assert parsed["mcp_servers"]["other"]["command"] == "/usr/bin/other"
    assert parsed["mcp_servers"]["agent-memory-bridge"]["command"] == sys.executable


@pytest.mark.parametrize(
    "header",
    [
        "[mcp_servers.'agent-memory-bridge']",
        "[ mcp_servers . agent-memory-bridge ]",
    ],
)
def test_codex_toml_strip_replaces_quoted_and_spaced_headers(tmp_path: Path, header: str) -> None:
    workspace = tmp_path / "repo"
    _write_codex_config(
        workspace / ".codex" / "config.toml",
        "\n".join(
            [
                header,
                'command = "/old"',
                "",
                "[mcp_servers.other]",
                'command = "/usr/bin/other"',
                "",
            ]
        ),
    )
    rendered = _connect_codex_force(workspace, tmp_path / "amb-home")
    assert 'command = "/old"' not in rendered
    parsed = tomllib.loads(rendered)
    assert parsed["mcp_servers"]["other"]["command"] == "/usr/bin/other"
    assert parsed["mcp_servers"]["agent-memory-bridge"]["command"] == sys.executable


def test_codex_toml_strip_replaces_inline_root_entry(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    _write_codex_config(
        workspace / ".codex" / "config.toml",
        "\n".join(
            [
                "[mcp_servers]",
                'playwright = { command = "/usr/bin/npx" }',
                'agent-memory-bridge = { command = "/old" }',
                "",
            ]
        ),
    )
    rendered = _connect_codex_force(workspace, tmp_path / "amb-home")
    assert 'command = "/old"' not in rendered
    parsed = tomllib.loads(rendered)
    assert parsed["mcp_servers"]["playwright"]["command"] == "/usr/bin/npx"
    assert parsed["mcp_servers"]["agent-memory-bridge"]["command"] == sys.executable


def test_different_stdio_home_requires_force_but_identical_rerun_is_unchanged(tmp_path: Path) -> None:
    cursor_cfg = tmp_path / ".cursor" / "mcp.json"
    cursor_cfg.parent.mkdir(parents=True)
    cursor_cfg.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "agent-memory-bridge": {
                        "command": sys.executable,
                        "args": ["-I", "-m", "agent_mem_bridge"],
                        "env": {
                            "AGENT_MEMORY_BRIDGE_HOME": "/data/team-home",
                            "AGENT_MEMORY_BRIDGE_DEFAULT_SOURCE_CLIENT": "cursor",
                            "AGENT_MEMORY_BRIDGE_DEFAULT_CLIENT_TRANSPORT": "stdio",
                        },
                    }
                }
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
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
    assert blocked["reason"] == "existing_entry_requires_force"
    assert "unrelated_server_count" in blocked
    blocked_report = render_connector_report_markdown(blocked)
    assert "existing_entry_requires_force" in blocked_report
    assert "Pass --force to replace it." in blocked_report

    same = connect_client(
        "cursor",
        scope="project",
        cwd=tmp_path,
        python_path=sys.executable,
        bridge_home="/data/team-home",
        force=False,
        verify_spawn=False,
    )
    assert same["ok"] is True
    assert same["action"] == "unchanged"


def test_failed_spawn_note_does_not_claim_verification(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "agent_mem_bridge.connectors._probe_http_entry",
        lambda _entry: {"ok": False, "tool_names": [], "error": "connection refused"},
    )
    config_path = tmp_path / ".cursor" / "mcp.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps({"mcpServers": {"agent-memory-bridge": {"url": "http://127.0.0.1:9/mcp"}}}),
        encoding="utf-8",
    )
    check = check_client_connection("cursor", scope="project", cwd=tmp_path, verify_spawn=True)
    assert check["ok"] is False
    assert check["tier1_config_valid"] is True
    assert check["tier2_server_spawn_verified"] is False
    assert "Config and MCP server spawn verified" not in check["note"]
    assert "spawn probe failed" in check["note"]

    rolled = connect_client(
        "cursor",
        scope="project",
        cwd=tmp_path / "fresh",
        url="http://127.0.0.1:9/mcp",
        verify_spawn=True,
    )
    assert rolled["ok"] is False
    assert rolled["rolled_back"] is True
    rendered = render_connector_report_markdown(rolled)
    assert "Config and MCP server spawn verified" not in rendered
    assert "spawn probe failed" in rendered


def test_project_token_file_is_refused_unless_allowed_and_dry_run_redacts(
    tmp_path: Path,
) -> None:
    token_file = tmp_path / "token.txt"
    token_file.write_text("super-secret-token\n", encoding="utf-8")
    with pytest.raises(ValueError, match="allow-project-token"):
        connect_client(
            "cursor",
            scope="project",
            cwd=tmp_path,
            url="http://127.0.0.1:58080/mcp",
            token_file=token_file,
            dry_run=True,
            verify_spawn=False,
        )

    preview = connect_client(
        "cursor",
        scope="project",
        cwd=tmp_path,
        url="http://127.0.0.1:58080/mcp",
        token_file=token_file,
        allow_project_token=True,
        dry_run=True,
        verify_spawn=False,
    )
    assert preview["ok"] is True
    assert "super-secret-token" not in preview["preview"]
    assert "<REDACTED>" in preview["preview"]
    assert any("project-scoped" in warning for warning in preview["warnings"])

    user_home = tmp_path / "home"
    user_home.mkdir()
    user_preview = connect_client(
        "cursor",
        scope="user",
        cwd=tmp_path,
        home=user_home,
        url="http://127.0.0.1:58080/mcp",
        token_file=token_file,
        dry_run=True,
        verify_spawn=False,
    )
    assert user_preview["ok"] is True
    assert "super-secret-token" not in user_preview["preview"]
    assert any("user-level" in warning for warning in user_preview["warnings"])


@pytest.mark.parametrize(
    "token_text",
    ["secret token\n", "secret\ntoken\n", "密钥\n"],
)
def test_token_file_rejects_whitespace_and_non_ascii(tmp_path: Path, token_text: str) -> None:
    token_file = tmp_path / "token.txt"
    token_file.write_text(token_text, encoding="utf-8")
    with pytest.raises(ValueError, match="printable ASCII token without whitespace"):
        connect_client(
            "cursor",
            scope="user",
            cwd=tmp_path,
            home=tmp_path / "home",
            url="http://127.0.0.1:58080/mcp",
            token_file=token_file,
            dry_run=True,
            verify_spawn=False,
        )


def test_cli_connect_project_token_requires_allow_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    token_file = tmp_path / "token.txt"
    token_file.write_text("super-secret-token\n", encoding="utf-8")
    config_path = tmp_path / ".cursor" / "mcp.json"
    monkeypatch.chdir(tmp_path)
    base = [
        "connect",
        "cursor",
        "--yes",
        "--url",
        "http://127.0.0.1:58080/mcp",
        "--token-file",
        str(token_file),
        "--config-path",
        str(config_path),
        "--dry-run",
    ]
    assert main(base) == 2
    refused = capsys.readouterr().err
    assert "allow-project-token" in refused
    assert not config_path.exists()

    assert main([*base, "--allow-project-token"]) == 0
    allowed = capsys.readouterr().out
    assert "super-secret-token" not in allowed
    assert not config_path.exists()
