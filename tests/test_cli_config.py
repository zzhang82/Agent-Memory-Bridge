from __future__ import annotations

import json
import tomllib
from pathlib import Path

from agent_mem_bridge.cli import main
from agent_mem_bridge.client_config import (
    build_client_config_options,
    render_client_config,
    render_example_client_configs,
    supported_client_names,
)


def test_rendered_example_configs_parse() -> None:
    for rendered in render_example_client_configs():
        if rendered.format == "json":
            payload = json.loads(rendered.content)
            assert "mcpServers" in payload or "mcp" in payload or "servers" in payload
        else:
            if rendered.format == "toml":
                payload = tomllib.loads(rendered.content)
                assert "mcp_servers" in payload
            else:
                assert rendered.format == "yaml"
                assert "mcp_servers:" in rendered.content


def test_non_codex_configs_do_not_reference_codex_home() -> None:
    for client in (
        "generic",
        "claude-desktop",
        "claude-code",
        "vscode",
        "cursor",
        "cline",
        "antigravity",
        "opencode",
        "hermes",
    ):
        rendered = render_client_config(
            build_client_config_options(
                client,
                python_path="/path/to/python",
                cwd="/path/to/repo",
                bridge_home="/path/to/bridge-home",
                config_path="/path/to/config.toml",
                example=False,
            )
        )
        assert "CODEX_HOME" not in rendered.content


def test_vscode_opencode_and_hermes_are_supported_clients() -> None:
    names = supported_client_names()

    assert "vscode" in names
    assert "opencode" in names
    assert "hermes" in names


def test_vscode_config_uses_vscode_servers_shape() -> None:
    rendered = render_client_config(
        build_client_config_options(
            "copilot",
            python_path="/path/to/python",
            cwd="/path/to/repo",
            bridge_home="/path/to/bridge-home",
            config_path="/path/to/config.toml",
            example=True,
        )
    )
    payload = json.loads(rendered.content)
    server = payload["servers"]["agentMemoryBridge"]

    assert rendered.client == "vscode"
    assert rendered.file_hint == ".vscode/mcp.json"
    assert server["type"] == "stdio"
    assert server["command"] == "/path/to/agent-memory-bridge/.venv/bin/python"
    assert server["env"]["AGENT_MEMORY_BRIDGE_DEFAULT_SOURCE_CLIENT"] == "vscode"


def test_opencode_config_uses_opencode_mcp_shape() -> None:
    rendered = render_client_config(
        build_client_config_options(
            "opencode",
            python_path="/path/to/python",
            cwd="/path/to/repo",
            bridge_home="/path/to/bridge-home",
            config_path="/path/to/config.toml",
            example=True,
        )
    )
    payload = json.loads(rendered.content)
    server = payload["mcp"]["agentMemoryBridge"]

    assert rendered.format == "json"
    assert server["type"] == "local"
    assert server["command"] == ["/path/to/agent-memory-bridge/.venv/bin/python", "-m", "agent_mem_bridge"]
    assert "cwd" not in server
    assert "env" not in server
    assert server["environment"]["AGENT_MEMORY_BRIDGE_DEFAULT_SOURCE_CLIENT"] == "opencode"


def test_hermes_config_uses_yaml_mcp_servers_shape() -> None:
    rendered = render_client_config(
        build_client_config_options(
            "hermes",
            python_path="/path/to/python",
            cwd="/path/to/repo",
            bridge_home="/path/to/bridge-home",
            config_path="/path/to/config.toml",
        )
    )

    assert rendered.format == "yaml"
    assert "mcp_servers:" in rendered.content
    assert "agentMemoryBridge:" in rendered.content
    assert "cwd:" not in rendered.content
    assert "AGENT_MEMORY_BRIDGE_DEFAULT_SOURCE_CLIENT: 'hermes'" in rendered.content


def test_config_output_refuses_overwrite_without_force(tmp_path: Path) -> None:
    output_path = tmp_path / "cursor.json"
    output_path.write_text("already here\n", encoding="utf-8")

    exit_code = main(
        [
            "config",
            "--client",
            "cursor",
            "--example",
            "--output",
            str(output_path),
        ]
    )

    assert exit_code == 3
    assert output_path.read_text(encoding="utf-8") == "already here\n"
