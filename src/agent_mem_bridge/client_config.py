from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


ClientName = Literal[
    "generic",
    "codex",
    "claude-desktop",
    "claude-code",
    "cursor",
    "cline",
    "antigravity",
]
ConfigFormat = Literal["json", "toml"]

DEFAULT_SERVER_NAME = "agentMemoryBridge"
PLACEHOLDER_PYTHON = "/path/to/agent-memory-bridge/.venv/bin/python"
PLACEHOLDER_REPO_ROOT = "/path/to/agent-memory-bridge"
PLACEHOLDER_BRIDGE_HOME = "/path/to/bridge-home"
PLACEHOLDER_CONFIG_PATH = "/path/to/agent-memory-bridge-config.toml"

CLIENT_ALIASES: dict[str, ClientName] = {
    "generic": "generic",
    "generic-json": "generic",
    "codex": "codex",
    "claude": "claude-desktop",
    "claude-desktop": "claude-desktop",
    "claude_desktop": "claude-desktop",
    "claude-code": "claude-code",
    "claude_code": "claude-code",
    "cursor": "cursor",
    "cline": "cline",
    "antigravity": "antigravity",
}

CLIENT_STATUSES: dict[ClientName, str] = {
    "generic": "supported",
    "codex": "verified",
    "claude-desktop": "documented",
    "claude-code": "documented",
    "cursor": "documented",
    "cline": "documented",
    "antigravity": "locally-tested",
}

CLIENT_FILE_HINTS: dict[ClientName, str] = {
    "generic": "client MCP config",
    "codex": "~/.codex/config.toml",
    "claude-desktop": "claude_desktop_config.json",
    "claude-code": ".mcp.json",
    "cursor": ".cursor/mcp.json",
    "cline": "cline_mcp_settings.json",
    "antigravity": "mcp_config.json",
}


@dataclass(frozen=True)
class ClientConfigOptions:
    client: ClientName
    command: str
    args: tuple[str, ...]
    cwd: str | None
    bridge_home: str | None
    config_path: str | None
    source_client: str
    client_transport: str = "stdio"
    server_name: str = DEFAULT_SERVER_NAME


@dataclass(frozen=True)
class RenderedClientConfig:
    client: ClientName
    format: ConfigFormat
    content: str
    file_hint: str
    status: str


def normalize_client_name(raw: str) -> ClientName:
    candidate = raw.strip().lower()
    if candidate not in CLIENT_ALIASES:
        supported = ", ".join(supported_client_names())
        raise ValueError(f"Unsupported client '{raw}'. Supported clients: {supported}.")
    return CLIENT_ALIASES[candidate]


def supported_client_names() -> list[str]:
    return [
        "generic",
        "codex",
        "claude-desktop",
        "claude-code",
        "cursor",
        "cline",
        "antigravity",
    ]


def build_client_config_options(
    client: str,
    *,
    python_path: str | Path | None,
    cwd: str | Path | None,
    bridge_home: str | Path | None,
    config_path: str | Path | None,
    example: bool = False,
    server_name: str = DEFAULT_SERVER_NAME,
) -> ClientConfigOptions:
    normalized = normalize_client_name(client)
    if example:
        command = PLACEHOLDER_PYTHON
        resolved_cwd = PLACEHOLDER_REPO_ROOT
        resolved_bridge_home = PLACEHOLDER_BRIDGE_HOME
        resolved_config_path = PLACEHOLDER_CONFIG_PATH
    else:
        command = _normalize_path(python_path)
        resolved_cwd = _normalize_optional_path(cwd)
        resolved_bridge_home = _normalize_optional_path(bridge_home)
        resolved_config_path = _normalize_optional_path(config_path)

    if not command:
        raise ValueError("A Python command path is required to render client config.")

    return ClientConfigOptions(
        client=normalized,
        command=command,
        args=("-m", "agent_mem_bridge"),
        cwd=resolved_cwd,
        bridge_home=resolved_bridge_home,
        config_path=resolved_config_path,
        source_client=normalized,
        server_name=server_name,
    )


def render_client_config(options: ClientConfigOptions) -> RenderedClientConfig:
    if options.client == "codex":
        return RenderedClientConfig(
            client=options.client,
            format="toml",
            content=_render_codex_config(options),
            file_hint=CLIENT_FILE_HINTS[options.client],
            status=CLIENT_STATUSES[options.client],
        )

    return RenderedClientConfig(
        client=options.client,
        format="json",
        content=_render_json_config(options),
        file_hint=CLIENT_FILE_HINTS[options.client],
        status=CLIENT_STATUSES[options.client],
    )


def render_example_client_configs() -> list[RenderedClientConfig]:
    rendered: list[RenderedClientConfig] = []
    for client in supported_client_names():
        options = build_client_config_options(
            client,
            python_path=PLACEHOLDER_PYTHON,
            cwd=PLACEHOLDER_REPO_ROOT,
            bridge_home=PLACEHOLDER_BRIDGE_HOME,
            config_path=PLACEHOLDER_CONFIG_PATH,
            example=True,
        )
        rendered.append(render_client_config(options))
    return rendered


def _render_json_config(options: ClientConfigOptions) -> str:
    server: dict[str, object] = {
        "command": options.command,
        "args": list(options.args),
    }
    if options.client in {"claude-desktop", "claude-code", "cursor"}:
        server["type"] = "stdio"
    if options.cwd:
        server["cwd"] = options.cwd
    env = _build_env(options)
    if env:
        server["env"] = env
    payload = {"mcpServers": {options.server_name: server}}
    return json.dumps(payload, indent=2)


def _render_codex_config(options: ClientConfigOptions) -> str:
    lines = [
        f"[mcp_servers.{options.server_name}]",
        f'command = "{_escape_toml_string(options.command)}"',
        f"args = [{', '.join(_quote_toml(item) for item in options.args)}]",
    ]
    if options.cwd:
        lines.append(f'cwd = "{_escape_toml_string(options.cwd)}"')
    env = _build_env(options)
    if env:
        lines.append("")
        lines.append(f"[mcp_servers.{options.server_name}.env]")
        for key, value in env.items():
            lines.append(f'{key} = "{_escape_toml_string(value)}"')
    return "\n".join(lines)


def _build_env(options: ClientConfigOptions) -> dict[str, str]:
    env: dict[str, str] = {}
    if options.bridge_home:
        env["AGENT_MEMORY_BRIDGE_HOME"] = options.bridge_home
    if options.config_path:
        env["AGENT_MEMORY_BRIDGE_CONFIG"] = options.config_path
    env["AGENT_MEMORY_BRIDGE_DEFAULT_SOURCE_CLIENT"] = options.source_client
    env["AGENT_MEMORY_BRIDGE_DEFAULT_CLIENT_TRANSPORT"] = options.client_transport
    return env


def _normalize_path(value: str | Path | None) -> str:
    if value is None:
        return ""
    return _path_to_string(value)


def _normalize_optional_path(value: str | Path | None) -> str | None:
    normalized = _normalize_path(value)
    return normalized or None


def _path_to_string(value: str | Path) -> str:
    if isinstance(value, Path):
        return str(value.expanduser())
    return str(Path(value).expanduser())


def _quote_toml(value: str) -> str:
    return f'"{_escape_toml_string(value)}"'


def _escape_toml_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')
