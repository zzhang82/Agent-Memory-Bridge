from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Any, Callable, Literal, Mapping

from mcp import StdioServerParameters
from mcp.client import Client
from mcp.client.stdio import stdio_client
from mcp.types import Implementation

from .mcp_boundary import PUBLIC_TOOL_ORDER, package_version
from .paths import resolve_bridge_home

ConnectorClient = Literal["claude-code", "cursor", "codex", "cline", "vscode"]
ConnectorScope = Literal["project", "user"]
VSCodeFormat = Literal["servers", "copilot"]

SUPPORTED_CONNECTOR_CLIENTS: tuple[ConnectorClient, ...] = (
    "claude-code",
    "cursor",
    "codex",
    "cline",
    "vscode",
)
SERVER_ENTRY_NAME = "agent-memory-bridge"
LEGACY_SERVER_ENTRY_NAME = "agentMemoryBridge"
CONNECTOR_SCHEMA = "amb.connector-report.v1"
BRIDGE_CHECK_SCHEMA = "amb.connector-bridge-check.v1"

_CLIENT_ALIASES: dict[str, ConnectorClient] = {
    "claude-code": "claude-code",
    "claude_code": "claude-code",
    "claudecode": "claude-code",
    "cursor": "cursor",
    "codex": "codex",
    "cline": "cline",
    "vscode": "vscode",
    "vs-code": "vscode",
    "vs_code": "vscode",
    "copilot": "vscode",
}

_UNEXPANDED_VAR_RE = re.compile(r"\$\{(?:workspaceFolder|input:|env:|command:)[^}]*\}")


def normalize_connector_client(raw: str) -> ConnectorClient:
    candidate = raw.strip().lower()
    if candidate not in _CLIENT_ALIASES:
        supported = ", ".join(SUPPORTED_CONNECTOR_CLIENTS)
        raise ValueError(f"Unsupported client '{raw}'. Supported clients: {supported}.")
    return _CLIENT_ALIASES[candidate]


def resolve_connector_config_path(
    client: str,
    *,
    scope: ConnectorScope = "project",
    cwd: Path | None = None,
    home: Path | None = None,
    config_path: Path | None = None,
    vscode_format: VSCodeFormat = "servers",
    environ: Mapping[str, str] | None = None,
) -> Path:
    normalized = normalize_connector_client(client)
    if config_path is not None:
        return Path(config_path).expanduser().resolve()

    resolved_cwd = (cwd or Path.cwd()).expanduser().resolve()
    resolved_home = (home or Path.home()).expanduser().resolve()
    env = os.environ if environ is None else environ

    if normalized == "claude-code":
        return resolved_cwd / ".mcp.json" if scope == "project" else resolved_home / ".claude.json"

    if normalized == "cursor":
        return resolved_cwd / ".cursor" / "mcp.json" if scope == "project" else resolved_home / ".cursor" / "mcp.json"

    if normalized == "codex":
        return (
            resolved_cwd / ".codex" / "config.toml" if scope == "project" else resolved_home / ".codex" / "config.toml"
        )

    if normalized == "cline":
        if scope == "project":
            raise ValueError(
                "Cline does not read a standard project-local MCP config file; pass --config-path or use --scope user."
            )
        override = env.get("CLINE_MCP_SETTINGS_PATH", "").strip()
        if override:
            return Path(override).expanduser().resolve()
        cli_path = resolved_home / ".cline" / "data" / "settings" / "cline_mcp_settings.json"
        if cli_path.exists() or (resolved_home / ".cline").exists():
            return cli_path
        for candidate in _vscode_global_storage_cline_paths(resolved_home, env):
            if candidate.exists() or candidate.parent.exists():
                return candidate
        return cli_path

    # normalized == "vscode"
    if vscode_format == "copilot":
        if scope == "project":
            return resolved_cwd / ".mcp.json"
        copilot_home = env.get("COPILOT_HOME", "").strip()
        base = Path(copilot_home).expanduser().resolve() if copilot_home else resolved_home / ".copilot"
        return base / "mcp-config.json"

    if scope == "project":
        return resolved_cwd / ".vscode" / "mcp.json"

    user_dir = _vscode_user_dir(resolved_home, env)
    profiles_dir = user_dir / "profiles"
    if profiles_dir.is_dir():
        profile_configs = list(profiles_dir.glob("*/mcp.json"))
        if len(profile_configs) > 1:
            raise ValueError(
                "Multiple VS Code profile mcp.json files exist; pass --config-path to select one explicitly."
            )
        if len(profile_configs) == 1 and not (user_dir / "mcp.json").exists():
            return profile_configs[0].resolve()
    return (user_dir / "mcp.json").resolve()


def _vscode_user_dir(home: Path, env: Mapping[str, str]) -> Path:
    appdata = env.get("APPDATA", "").strip()
    if sys.platform == "win32" and appdata:
        return Path(appdata) / "Code" / "User"
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "Code" / "User"
    return home / ".config" / "Code" / "User"


def _vscode_global_storage_cline_paths(home: Path, env: Mapping[str, str]) -> tuple[Path, ...]:
    user_dir = _vscode_user_dir(home, env)
    return (user_dir / "globalStorage" / "saoudrizwan.claude-dev" / "settings" / "cline_mcp_settings.json",)


def build_server_entry(
    client: ConnectorClient,
    *,
    python_path: str | Path | None = None,
    bridge_home: str | Path | None = None,
    url: str | None = None,
    bearer_token: str | None = None,
    vscode_format: VSCodeFormat = "servers",
) -> dict[str, Any]:
    if url is not None:
        cleaned_url = url.strip()
        if not cleaned_url:
            raise ValueError("Remote URL must not be empty.")
        entry: dict[str, Any] = {"url": cleaned_url}
        if client in {"claude-code", "vscode"}:
            entry["type"] = "http"
        if client == "cline":
            entry["disabled"] = False
            entry["autoApprove"] = []
        if bearer_token:
            entry["headers"] = {"Authorization": f"Bearer {bearer_token.strip()}"}
        return entry

    executable = str(Path(python_path or sys.executable).expanduser())
    if _UNEXPANDED_VAR_RE.search(executable):
        raise ValueError(f"Unexpanded editor variable in command is not allowed: {executable}")
    if "<venv-python>" in executable or not Path(executable).is_absolute():
        raise ValueError(f"Python command must be an absolute executable path, got: {executable}")

    resolved_home = str(Path(bridge_home or resolve_bridge_home()).expanduser().resolve())
    if _UNEXPANDED_VAR_RE.search(resolved_home):
        raise ValueError(f"Unexpanded editor variable in bridge home is not allowed: {resolved_home}")

    env_map = {
        "AGENT_MEMORY_BRIDGE_HOME": resolved_home,
        "AGENT_MEMORY_BRIDGE_DEFAULT_SOURCE_CLIENT": client,
        "AGENT_MEMORY_BRIDGE_DEFAULT_CLIENT_TRANSPORT": "stdio",
    }
    args = ["-I", "-m", "agent_mem_bridge"]

    if client == "cline":
        return {
            "command": executable,
            "args": args,
            "disabled": False,
            "autoApprove": [],
            "env": env_map,
        }
    if client in {"claude-code", "vscode"}:
        return {
            "type": "stdio",
            "command": executable,
            "args": args,
            "env": env_map,
        }
    # cursor and codex
    return {
        "command": executable,
        "args": args,
        "env": env_map,
    }


def connect_client(
    client: str,
    *,
    scope: ConnectorScope = "project",
    cwd: Path | None = None,
    home: Path | None = None,
    config_path: Path | None = None,
    python_path: str | Path | None = None,
    bridge_home: str | Path | None = None,
    url: str | None = None,
    token_file: Path | None = None,
    vscode_format: VSCodeFormat = "servers",
    force: bool = False,
    allow_project_token: bool = False,
    dry_run: bool = False,
    verify_spawn: bool = True,
    host_cli_probe: Callable[[ConnectorClient], str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    normalized = normalize_connector_client(client)
    target_path = resolve_connector_config_path(
        normalized,
        scope=scope,
        cwd=cwd,
        home=home,
        config_path=config_path,
        vscode_format=vscode_format,
        environ=environ,
    )
    if url is not None and token_file is not None and scope == "project" and not allow_project_token:
        raise ValueError(
            "Refusing to write a bearer token from --token-file into a project-scoped config file. "
            "Use --scope user or pass --allow-project-token and ensure the config file is gitignored."
        )

    bearer_token: str | None = None
    if token_file is not None:
        bearer_token = Path(token_file).expanduser().read_text(encoding="utf-8").strip()
        if not bearer_token:
            raise ValueError(f"Token file is empty: {token_file}")
        if len(bearer_token) > 8192 or any(ord(ch) < 33 or ord(ch) > 126 for ch in bearer_token):
            raise ValueError(
                f"Token file must contain one non-empty printable ASCII token without whitespace: {token_file}"
            )

    desired_entry = build_server_entry(
        normalized,
        python_path=python_path,
        bridge_home=bridge_home,
        url=url,
        bearer_token=bearer_token,
        vscode_format=vscode_format,
    )

    existed_before = target_path.exists()
    original_bytes = target_path.read_bytes() if existed_before else None

    merge_result = _merge_client_config(
        normalized,
        target_path=target_path,
        existing_bytes=original_bytes,
        desired_entry=desired_entry,
        vscode_format=vscode_format,
        force=force,
    )
    if not merge_result["ok"]:
        return {
            "schema": CONNECTOR_SCHEMA,
            "ok": False,
            "client": normalized,
            "scope": scope,
            "config_path": str(target_path),
            "action": "conflict",
            "reason": merge_result["reason"],
            "detail": merge_result["detail"],
            "unrelated_server_count": merge_result.get("unrelated_server_count", 0),
        }

    rendered_text: str = merge_result["rendered_text"]
    if dry_run:
        preview = _redact_bearer_token(rendered_text, bearer_token)
        dry_report: dict[str, Any] = {
            "schema": CONNECTOR_SCHEMA,
            "ok": True,
            "dry_run": True,
            "client": normalized,
            "scope": scope,
            "config_path": str(target_path),
            "action": merge_result["action"],
            "unrelated_server_count": merge_result["unrelated_server_count"],
            "preview": preview,
        }
        if token_file is not None:
            dry_report["warnings"] = _bearer_token_warnings(scope)
        return dry_report

    backup_path = target_path.with_name(f"{target_path.name}.amb-backup.bak") if existed_before else None
    if merge_result["action"] == "unchanged":
        check_report = check_client_connection(
            normalized,
            scope=scope,
            cwd=cwd,
            home=home,
            config_path=target_path,
            vscode_format=vscode_format,
            verify_spawn=verify_spawn,
            host_cli_probe=host_cli_probe,
            environ=environ,
        )
        return {
            "schema": CONNECTOR_SCHEMA,
            "ok": bool(check_report.get("ok")),
            "dry_run": False,
            "rolled_back": False,
            "client": normalized,
            "scope": scope,
            "config_path": str(target_path),
            "backup_path": str(backup_path) if (backup_path and backup_path.exists()) else None,
            "action": "unchanged",
            "unrelated_server_count": merge_result["unrelated_server_count"],
            "check": check_report,
        } | ({"warnings": _bearer_token_warnings(scope)} if token_file is not None else {})

    target_path.parent.mkdir(parents=True, exist_ok=True)
    if backup_path is not None and original_bytes is not None:
        _atomic_write_bytes(backup_path, original_bytes)

    try:
        _atomic_write_bytes(target_path, rendered_text.encode("utf-8"))
        check_report = check_client_connection(
            normalized,
            scope=scope,
            cwd=cwd,
            home=home,
            config_path=target_path,
            vscode_format=vscode_format,
            verify_spawn=verify_spawn,
            host_cli_probe=host_cli_probe,
            environ=environ,
        )
        if not check_report["ok"]:
            _rollback_file(target_path, original_bytes=original_bytes)
            return {
                "schema": CONNECTOR_SCHEMA,
                "ok": False,
                "rolled_back": True,
                "client": normalized,
                "scope": scope,
                "config_path": str(target_path),
                "backup_path": str(backup_path) if backup_path else None,
                "action": "rolled_back",
                "check": check_report,
            }
    except Exception as exc:
        _rollback_file(target_path, original_bytes=original_bytes)
        return {
            "schema": CONNECTOR_SCHEMA,
            "ok": False,
            "rolled_back": True,
            "client": normalized,
            "scope": scope,
            "config_path": str(target_path),
            "action": "rolled_back",
            "error": str(exc),
        }

    written: dict[str, Any] = {
        "schema": CONNECTOR_SCHEMA,
        "ok": True,
        "dry_run": False,
        "rolled_back": False,
        "client": normalized,
        "scope": scope,
        "config_path": str(target_path),
        "backup_path": str(backup_path) if backup_path else None,
        "action": merge_result["action"],
        "unrelated_server_count": merge_result["unrelated_server_count"],
        "check": check_report,
    }
    if token_file is not None:
        written["warnings"] = _bearer_token_warnings(scope)
    return written


def _merge_client_config(
    client: ConnectorClient,
    *,
    target_path: Path,
    existing_bytes: bytes | None,
    desired_entry: dict[str, Any],
    vscode_format: VSCodeFormat,
    force: bool,
) -> dict[str, Any]:
    if client == "codex":
        return _merge_codex_toml(existing_bytes=existing_bytes, desired_entry=desired_entry, force=force)

    root_key = _json_root_key(client, target_path=target_path, vscode_format=vscode_format)
    if existing_bytes is None or not existing_bytes.strip():
        payload: dict[str, Any] = {root_key: {SERVER_ENTRY_NAME: desired_entry}}
        return {
            "ok": True,
            "action": "created",
            "unrelated_server_count": 0,
            "rendered_text": json.dumps(payload, indent=2) + "\n",
        }

    try:
        parsed = json.loads(existing_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {
            "ok": False,
            "reason": "unreadable_existing_config",
            "detail": f"Existing JSON config could not be parsed: {exc}",
        }
    if not isinstance(parsed, dict):
        return {
            "ok": False,
            "reason": "invalid_root_type",
            "detail": "Existing JSON config root is not an object.",
        }

    # Detect if user passed a file that already uses the other VS Code root key
    if client == "vscode" and root_key not in parsed:
        alt_key = "mcpServers" if root_key == "servers" else "servers"
        if isinstance(parsed.get(alt_key), dict):
            root_key = alt_key

    servers_obj = parsed.get(root_key)
    if servers_obj is None:
        servers_map: dict[str, Any] = {}
        parsed[root_key] = servers_map
    elif isinstance(servers_obj, dict):
        servers_map = dict(servers_obj)
        parsed[root_key] = servers_map
    else:
        return {
            "ok": False,
            "reason": "invalid_servers_container",
            "detail": f"Existing `{root_key}` field is not a JSON object.",
        }

    existing_key = (
        SERVER_ENTRY_NAME
        if SERVER_ENTRY_NAME in servers_map
        else (LEGACY_SERVER_ENTRY_NAME if LEGACY_SERVER_ENTRY_NAME in servers_map else None)
    )
    existing_entry = servers_map.get(existing_key) if existing_key else None
    if isinstance(existing_entry, dict):
        existing_is_http = "url" in existing_entry
        desired_is_http = "url" in desired_entry
        if existing_is_http != desired_is_http and not force:
            return {
                "ok": False,
                "reason": "transport_conflict_requires_force",
                "detail": (
                    "Existing AMB entry uses a different transport "
                    f"({'http' if existing_is_http else 'stdio'}). Pass --force to replace it."
                ),
                "unrelated_server_count": len(
                    [k for k in servers_map if k not in {SERVER_ENTRY_NAME, LEGACY_SERVER_ENTRY_NAME}]
                ),
            }
        if existing_entry != desired_entry and not force:
            return {
                "ok": False,
                "reason": "existing_entry_requires_force",
                "detail": (
                    f"Existing AMB {'http' if existing_is_http else 'stdio'} entry differs "
                    "(for example AGENT_MEMORY_BRIDGE_HOME, command, or url). Pass --force to replace it."
                ),
                "unrelated_server_count": len(
                    [k for k in servers_map if k not in {SERVER_ENTRY_NAME, LEGACY_SERVER_ENTRY_NAME}]
                ),
            }

    unrelated_count = len([k for k in servers_map if k not in {SERVER_ENTRY_NAME, LEGACY_SERVER_ENTRY_NAME}])
    if existing_key == LEGACY_SERVER_ENTRY_NAME and SERVER_ENTRY_NAME not in servers_map:
        servers_map.pop(LEGACY_SERVER_ENTRY_NAME, None)
    action = "updated" if existing_entry is not None else "merged"
    if existing_entry == desired_entry:
        action = "unchanged"
    servers_map[SERVER_ENTRY_NAME] = desired_entry
    return {
        "ok": True,
        "action": action,
        "unrelated_server_count": unrelated_count,
        "rendered_text": json.dumps(parsed, indent=2) + "\n",
    }


def _redact_bearer_token(text: str, bearer_token: str | None) -> str:
    if not bearer_token:
        return text
    redacted = text.replace(bearer_token, "<REDACTED>")
    escaped = _escape_toml(bearer_token)
    if escaped != bearer_token:
        redacted = redacted.replace(escaped, "<REDACTED>")
    json_escaped = json.dumps(bearer_token)[1:-1]
    if json_escaped != bearer_token:
        redacted = redacted.replace(json_escaped, "<REDACTED>")
    return redacted


def _bearer_token_warnings(scope: ConnectorScope) -> list[str]:
    if scope == "project":
        return [
            "Bearer token from --token-file was written into a project-scoped config file; "
            "keep it untracked/gitignored and never commit it."
        ]
    return ["Bearer token from --token-file was written into your user-level config file (mode 0600); keep it private."]


def _json_root_key(client: ConnectorClient, *, target_path: Path, vscode_format: VSCodeFormat) -> str:
    if client == "vscode":
        if target_path.name == ".mcp.json" or target_path.name == "mcp-config.json" or vscode_format == "copilot":
            return "mcpServers"
        return "servers"
    return "mcpServers"


def _merge_codex_toml(
    *,
    existing_bytes: bytes | None,
    desired_entry: dict[str, Any],
    force: bool,
) -> dict[str, Any]:
    existing_text = existing_bytes.decode("utf-8") if existing_bytes is not None else ""
    if existing_text.strip():
        try:
            parsed = tomllib.loads(existing_text)
        except tomllib.TOMLDecodeError as exc:
            return {
                "ok": False,
                "reason": "unreadable_existing_config",
                "detail": f"Existing TOML config could not be parsed: {exc}",
            }
    else:
        parsed = {}

    mcp_servers = parsed.get("mcp_servers")
    if mcp_servers is not None and not isinstance(mcp_servers, dict):
        return {
            "ok": False,
            "reason": "invalid_servers_container",
            "detail": "`mcp_servers` in config.toml is not a table.",
        }
    servers_map = mcp_servers if isinstance(mcp_servers, dict) else {}
    existing_entry = servers_map.get(SERVER_ENTRY_NAME) or servers_map.get(LEGACY_SERVER_ENTRY_NAME)
    if isinstance(existing_entry, dict):
        existing_is_http = "url" in existing_entry
        desired_is_http = "url" in desired_entry
        if existing_is_http != desired_is_http and not force:
            return {
                "ok": False,
                "reason": "transport_conflict_requires_force",
                "detail": (
                    "Existing Codex AMB entry uses a different transport "
                    f"({'http' if existing_is_http else 'stdio'}). Pass --force to replace it."
                ),
            }
        if existing_entry != desired_entry and not force:
            unrelated_count = len([k for k in servers_map if k not in {SERVER_ENTRY_NAME, LEGACY_SERVER_ENTRY_NAME}])
            return {
                "ok": False,
                "reason": "existing_entry_requires_force",
                "detail": (
                    f"Existing AMB {'http' if existing_is_http else 'stdio'} entry differs "
                    "(for example AGENT_MEMORY_BRIDGE_HOME, command, or url). Pass --force to replace it."
                ),
                "unrelated_server_count": unrelated_count,
            }

    unrelated_count = len([k for k in servers_map if k not in {SERVER_ENTRY_NAME, LEGACY_SERVER_ENTRY_NAME}])
    if (
        existing_entry == desired_entry
        and SERVER_ENTRY_NAME in servers_map
        and LEGACY_SERVER_ENTRY_NAME not in servers_map
    ):
        return {
            "ok": True,
            "action": "unchanged",
            "unrelated_server_count": unrelated_count,
            "rendered_text": existing_text,
        }
    stripped_text = _strip_codex_amb_sections(existing_text)
    new_section = _render_codex_amb_section(desired_entry)
    if stripped_text.strip():
        merged_text = stripped_text.rstrip() + "\n\n" + new_section + "\n"
    else:
        merged_text = new_section + "\n"

    # Re-parse with tomllib to guarantee valid TOML output
    tomllib.loads(merged_text)
    action = "created" if not existing_text.strip() else ("updated" if existing_entry is not None else "merged")
    return {
        "ok": True,
        "action": action,
        "unrelated_server_count": unrelated_count,
        "rendered_text": merged_text,
    }


def _toml_multiline_state(line: str, delim: str | None) -> str | None:
    """Advance a `'''` / `\"\"\"` multiline-string state across one physical line.

    Backslash escapes count only inside basic `\"\"\"` strings. A comment marker
    outside a string ends the scan, so a `#` cannot open or close a string.
    """

    index = 0
    length = len(line)
    while index < length:
        if delim is None and line[index] == "#":
            return None
        if delim is not None:
            if delim == '"""' and line[index] == "\\":
                index += 2
                continue
            if line.startswith(delim, index):
                delim = None
                index += 3
                continue
            index += 1
            continue
        if line.startswith('"""', index) or line.startswith("'''", index):
            delim = line[index : index + 3]
            index += 3
            continue
        index += 1
    return delim


def _toml_statement_is_complete(statement: str) -> bool:
    try:
        tomllib.loads(statement if statement.endswith("\n") else statement + "\n")
    except tomllib.TOMLDecodeError:
        return False
    return True


def _toml_header_state(line: str) -> tuple[bool, bool] | None:
    """Classify one single-line table header outside a multiline string.

    Returns `(skip_section, in_mcp_servers_root)`, or `None` when the line is
    not a TOML header.
    """

    stripped = line.strip()
    if not stripped.startswith("["):
        return None
    if stripped.startswith("[["):
        if not _toml_statement_is_complete(line):
            return None
        return (False, False)
    if not _toml_statement_is_complete(line):
        return None
    try:
        parsed = tomllib.loads(line + "\n")
    except tomllib.TOMLDecodeError:
        return None
    if len(parsed) != 1:
        return None
    top_key, sub = next(iter(parsed.items()))
    if top_key == "mcp_servers" and isinstance(sub, dict):
        if len(sub) == 0:
            return (False, True)
        if SERVER_ENTRY_NAME in sub or LEGACY_SERVER_ENTRY_NAME in sub:
            return (True, False)
        return (False, False)
    return (False, False)


def _statement_defines_amb_key(statement: str) -> bool:
    if not _toml_statement_is_complete(statement):
        return False
    parsed = tomllib.loads(statement if statement.endswith("\n") else statement + "\n")
    return SERVER_ENTRY_NAME in parsed or LEGACY_SERVER_ENTRY_NAME in parsed


def _strip_codex_amb_sections(text: str) -> str:
    lines = text.splitlines()
    kept_lines: list[str] = []
    skip_section = False
    in_mcp_servers_root = False
    multiline_delim: str | None = None
    index = 0
    while index < len(lines):
        line = lines[index]
        if multiline_delim is not None:
            if not skip_section:
                kept_lines.append(line)
            multiline_delim = _toml_multiline_state(line, multiline_delim)
            index += 1
            continue

        header_state = _toml_header_state(line)
        if header_state is not None:
            skip_section, in_mcp_servers_root = header_state
            if not skip_section:
                kept_lines.append(line)
            multiline_delim = _toml_multiline_state(line, None)
            index += 1
            continue

        stripped = line.strip()
        if in_mcp_servers_root and stripped and not stripped.startswith("#"):
            statement_lines = [line]
            while not _toml_statement_is_complete("\n".join(statement_lines)) and index + 1 < len(lines):
                index += 1
                statement_lines.append(lines[index])
            statement = "\n".join(statement_lines)
            if _statement_defines_amb_key(statement):
                multiline_delim = None
                for statement_line in statement_lines:
                    multiline_delim = _toml_multiline_state(statement_line, multiline_delim)
                index += 1
                continue
            if not skip_section:
                kept_lines.extend(statement_lines)
            multiline_delim = None
            for statement_line in statement_lines:
                multiline_delim = _toml_multiline_state(statement_line, multiline_delim)
            index += 1
            continue

        if not skip_section:
            kept_lines.append(line)
        multiline_delim = _toml_multiline_state(line, None)
        index += 1
    return "\n".join(kept_lines).rstrip()


def _render_codex_amb_section(entry: dict[str, Any]) -> str:
    lines = [f"[mcp_servers.{SERVER_ENTRY_NAME}]"]
    if "url" in entry:
        lines.append(f'url = "{_escape_toml(str(entry["url"]))}"')
        headers = entry.get("headers")
        if isinstance(headers, dict) and headers:
            lines.append("")
            lines.append(f"[mcp_servers.{SERVER_ENTRY_NAME}.headers]")
            for key, value in headers.items():
                lines.append(f'{key} = "{_escape_toml(str(value))}"')
        return "\n".join(lines)

    command = str(entry["command"])
    args = [str(item) for item in entry.get("args") or []]
    lines.append(f'command = "{_escape_toml(command)}"')
    rendered_args = ", ".join(f'"{_escape_toml(arg)}"' for arg in args)
    lines.append(f"args = [{rendered_args}]")
    env = entry.get("env")
    if isinstance(env, dict) and env:
        lines.append("")
        lines.append(f"[mcp_servers.{SERVER_ENTRY_NAME}.env]")
        for key, value in env.items():
            lines.append(f'{key} = "{_escape_toml(str(value))}"')
    return "\n".join(lines)


def _escape_toml(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def check_client_connection(
    client: str,
    *,
    scope: ConnectorScope = "project",
    cwd: Path | None = None,
    home: Path | None = None,
    config_path: Path | None = None,
    vscode_format: VSCodeFormat = "servers",
    verify_spawn: bool = True,
    host_cli_probe: Callable[[ConnectorClient], str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    normalized = normalize_connector_client(client)
    try:
        resolved_path = resolve_connector_config_path(
            normalized,
            scope=scope,
            cwd=cwd,
            home=home,
            config_path=config_path,
            vscode_format=vscode_format,
            environ=environ,
        )
    except ValueError as exc:
        return _failed_check_report(normalized, scope, None, str(exc))

    if not resolved_path.is_file():
        return _failed_check_report(
            normalized,
            scope,
            resolved_path,
            f"Config file does not exist: {resolved_path}",
        )

    validation = _validate_client_config_file(normalized, resolved_path, vscode_format=vscode_format)
    if not validation["tier1_config_valid"]:
        return {
            "schema": CONNECTOR_SCHEMA,
            "ok": False,
            "client": normalized,
            "scope": scope,
            "config_path": str(resolved_path),
            "tier1_config_valid": False,
            "tier2_server_spawn_verified": False,
            "tier3_host_registration_status": "not_checked",
            "lists_store_and_recall": False,
            "tool_count": 0,
            "tool_names": [],
            "issues": validation["issues"],
            "note": "Connection check failed; see issues above.",
        }

    entry: dict[str, Any] = validation["entry"]
    transport = "http" if "url" in entry else "stdio"
    spawn_ok = False
    lists_store_and_recall = False
    tool_names: list[str] = []
    spawn_error: str | None = None

    if verify_spawn:
        if transport == "stdio":
            probe = asyncio.run(_probe_stdio_entry(entry, cwd=resolved_path.parent))
        else:
            probe = _probe_http_entry(entry)
        spawn_ok = bool(probe.get("ok"))
        tool_names = list(probe.get("tool_names") or [])
        lists_store_and_recall = "store" in tool_names and "recall" in tool_names
        if not spawn_ok or not lists_store_and_recall:
            spawn_error = str(probe.get("error") or "Server did not list both `store` and `recall`.")
    else:
        spawn_ok = True
        tool_names = list(PUBLIC_TOOL_ORDER)
        lists_store_and_recall = True

    tier3_status = host_cli_probe(normalized) if host_cli_probe is not None else _default_host_cli_probe(normalized)
    issues = list(validation["issues"])
    if spawn_error:
        issues.append(spawn_error)

    ok = bool(validation["tier1_config_valid"] and spawn_ok and lists_store_and_recall)
    return {
        "schema": CONNECTOR_SCHEMA,
        "ok": ok,
        "client": normalized,
        "scope": scope,
        "config_path": str(resolved_path),
        "transport": transport,
        "tier1_config_valid": True,
        "tier2_server_spawn_verified": spawn_ok and lists_store_and_recall,
        "tier3_host_registration_status": tier3_status,
        "lists_store_and_recall": lists_store_and_recall,
        "tool_count": len(tool_names),
        "tool_names": tool_names,
        "bridge_home": (entry.get("env") or {}).get("AGENT_MEMORY_BRIDGE_HOME") if transport == "stdio" else None,
        "issues": issues,
        "note": (
            (
                "Host CLI confirmed MCP registration."
                if tier3_status == "host_cli_verified"
                else "Config and MCP server spawn verified (`store` and `recall` listed); reload the client app to load config changes."
            )
            if ok
            else (
                "Config is valid, but MCP server spawn probe failed; see issues above."
                if validation["tier1_config_valid"]
                else "Connection check failed; see issues above."
            )
        ),
    }


def _failed_check_report(
    client: ConnectorClient,
    scope: ConnectorScope,
    config_path: Path | None,
    issue: str,
) -> dict[str, Any]:
    return {
        "schema": CONNECTOR_SCHEMA,
        "ok": False,
        "client": client,
        "scope": scope,
        "config_path": str(config_path) if config_path else None,
        "tier1_config_valid": False,
        "tier2_server_spawn_verified": False,
        "tier3_host_registration_status": "not_checked",
        "lists_store_and_recall": False,
        "tool_count": 0,
        "tool_names": [],
        "issues": [issue],
    }


def _validate_client_config_file(
    client: ConnectorClient,
    config_path: Path,
    *,
    vscode_format: VSCodeFormat,
) -> dict[str, Any]:
    issues: list[str] = []
    raw_text = config_path.read_text(encoding="utf-8")
    if client == "codex":
        try:
            parsed = tomllib.loads(raw_text)
        except tomllib.TOMLDecodeError as exc:
            return {"tier1_config_valid": False, "issues": [f"Invalid TOML: {exc}"], "entry": {}}
        servers = parsed.get("mcp_servers")
        if not isinstance(servers, dict):
            return {"tier1_config_valid": False, "issues": ["Missing `[mcp_servers]` table."], "entry": {}}
    else:
        try:
            parsed = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            return {"tier1_config_valid": False, "issues": [f"Invalid JSON: {exc}"], "entry": {}}
        if not isinstance(parsed, dict):
            return {"tier1_config_valid": False, "issues": ["Root JSON value must be an object."], "entry": {}}
        expected_key = _json_root_key(client, target_path=config_path, vscode_format=vscode_format)
        if client == "vscode" and expected_key not in parsed:
            alt_key = "mcpServers" if expected_key == "servers" else "servers"
            if isinstance(parsed.get(alt_key), dict):
                expected_key = alt_key
        servers = parsed.get(expected_key)
        if not isinstance(servers, dict):
            return {
                "tier1_config_valid": False,
                "issues": [f"Missing `{expected_key}` object in {config_path.name}."],
                "entry": {},
            }

    entry = servers.get(SERVER_ENTRY_NAME) or servers.get(LEGACY_SERVER_ENTRY_NAME)
    if not isinstance(entry, dict):
        return {
            "tier1_config_valid": False,
            "issues": [f"Missing `{SERVER_ENTRY_NAME}` entry in {config_path.name}."],
            "entry": {},
        }

    if client == "cline":
        if entry.get("disabled") is True:
            issues.append("Cline entry has `disabled: true`.")
        if "autoApprove" not in entry or not isinstance(entry.get("autoApprove"), list):
            issues.append("Cline entry must include `autoApprove` list.")

    if "url" in entry:
        url_val = str(entry.get("url") or "").strip()
        if not url_val.startswith(("http://", "https://")):
            issues.append(f"Invalid HTTP URL in connector entry: {url_val}")
        return {"tier1_config_valid": not issues, "issues": issues, "entry": entry}

    if client in {"claude-code", "vscode"} and entry.get("type") != "stdio":
        issues.append(f'{client} stdio config requires `"type": "stdio"`.')

    command = entry.get("command")
    if not isinstance(command, str) or not command.strip():
        issues.append("Missing `command` string in connector entry.")
    else:
        if _UNEXPANDED_VAR_RE.search(command) or "<venv-python>" in command:
            issues.append(f"Unexpanded placeholder or editor variable in command: {command}")
        elif not Path(command).is_absolute():
            issues.append(f"Command must be an absolute path so client cwd does not break launch: {command}")
        elif not Path(command).is_file():
            issues.append(f"Configured command executable does not exist: {command}")

    args = entry.get("args")
    if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
        issues.append("`args` must be a list of strings.")
    elif any(" " in item and item.startswith("-") for item in args):
        issues.append(
            "Combined flags in a single `args` element break non-shell spawn; split flags into separate strings."
        )

    env = entry.get("env")
    if env is not None:
        if not isinstance(env, dict):
            issues.append("`env` must be an object/table of string values.")
        else:
            for k, v in env.items():
                if not isinstance(v, str) or _UNEXPANDED_VAR_RE.search(v):
                    issues.append(f"Invalid or unexpanded variable in env.{k}.")

    return {"tier1_config_valid": not issues, "issues": issues, "entry": entry}


async def _probe_stdio_entry(entry: Mapping[str, Any], *, cwd: Path) -> dict[str, Any]:
    command = str(entry["command"])
    args = [str(item) for item in entry.get("args") or []]
    env_obj = entry.get("env")
    raw_env: Mapping[str, Any] = env_obj if isinstance(env_obj, Mapping) else {}
    merged_env = {**os.environ, **{str(k): str(v) for k, v in raw_env.items()}}
    entry_cwd = Path(str(entry["cwd"])).expanduser().resolve() if entry.get("cwd") else cwd

    server_params = StdioServerParameters(
        command=command,
        args=args,
        cwd=entry_cwd,
        env=merged_env,
    )
    client_info = Implementation(name="amb-connector-check", version=package_version())
    try:
        async with Client(stdio_client(server_params), mode="auto", client_info=client_info) as client:
            tools_result = await client.list_tools(cache_mode="bypass")
            tool_names = [tool.name for tool in tools_result.tools]
        return {
            "ok": "store" in tool_names and "recall" in tool_names and len(tool_names) == len(PUBLIC_TOOL_ORDER),
            "tool_names": tool_names,
            "tool_count": len(tool_names),
        }
    except Exception as exc:
        return {
            "ok": False,
            "tool_names": [],
            "tool_count": 0,
            "error": f"{type(exc).__name__}: {exc}",
        }


def _probe_http_entry(entry: Mapping[str, Any]) -> dict[str, Any]:
    from .http_probe import run_http_probe

    url = str(entry.get("url") or "").strip()
    headers_obj = entry.get("headers")
    headers: Mapping[str, Any] = headers_obj if isinstance(headers_obj, Mapping) else {}
    auth_header = str(headers.get("Authorization") or "").strip()
    token_file: Path | None = None
    temp_token_path: Path | None = None
    try:
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()
            fd, name = tempfile.mkstemp(prefix="amb-http-check-", suffix=".token")
            temp_token_path = Path(name)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(token)
            token_file = temp_token_path
        report = asyncio.run(run_http_probe(url, token_file=token_file, timeout=10.0))
        ok = bool(report.get("ok"))
        tool_names = list(PUBLIC_TOOL_ORDER) if ok else []
        return {
            "ok": ok,
            "tool_names": tool_names,
            "tool_count": len(tool_names),
            "error": None if ok else "HTTP authority probe failed.",
        }
    except Exception as exc:
        return {
            "ok": False,
            "tool_names": [],
            "tool_count": 0,
            "error": f"{type(exc).__name__}: {exc}",
        }
    finally:
        if temp_token_path is not None:
            temp_token_path.unlink(missing_ok=True)


def _is_healthy_cli_mcp_line(line: str) -> bool:
    if SERVER_ENTRY_NAME not in line and LEGACY_SERVER_ENTRY_NAME not in line:
        return False
    lowered = re.sub(r"\bno\s+errors?\b|\b0\s+errors?\b", "", line.lower())
    return not bool(re.search(r"\b(?:disabled|failed|disconnected|error)\b", lowered))


def _default_host_cli_probe(client: ConnectorClient) -> str:
    cli_bin = "claude" if client == "claude-code" else ("codex" if client == "codex" else None)
    if cli_bin is None or shutil.which(cli_bin) is None:
        return "host_reload_required"
    try:
        completed = subprocess.run(
            [cli_bin, "mcp", "list"],
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
        if completed.returncode == 0 and any(_is_healthy_cli_mcp_line(line) for line in completed.stdout.splitlines()):
            return "host_cli_verified"
    except (OSError, subprocess.SubprocessError):
        pass
    return "host_reload_required"


def run_bridge_check(
    *,
    from_client: str = "claude-code",
    to_client: str = "cursor",
    namespace: str = "project:bridge-check",
    bridge_home: Path | None = None,
    python_path: str | Path | None = None,
) -> dict[str, Any]:
    writer_client = normalize_connector_client(from_client)
    reader_client = normalize_connector_client(to_client)
    if writer_client == reader_client:
        raise ValueError("--from-client and --to-client must be two distinct clients.")

    with tempfile.TemporaryDirectory(prefix="amb-bridge-check-") as temp_root_str:
        temp_root = Path(temp_root_str).resolve()
        shared_home = (bridge_home or (temp_root / "shared-home")).expanduser().resolve()
        shared_home.mkdir(parents=True, exist_ok=True)

        writer_dir = temp_root / "writer-workspace"
        reader_dir = temp_root / "reader-workspace"
        writer_dir.mkdir(parents=True, exist_ok=True)
        reader_dir.mkdir(parents=True, exist_ok=True)

        writer_cfg_override = writer_dir / "cline_mcp_settings.json" if writer_client == "cline" else None
        reader_cfg_override = reader_dir / "cline_mcp_settings.json" if reader_client == "cline" else None

        writer_connect = connect_client(
            writer_client,
            scope="user" if writer_client == "cline" else "project",
            cwd=writer_dir,
            home=writer_dir,
            config_path=writer_cfg_override,
            python_path=python_path,
            bridge_home=shared_home,
            verify_spawn=True,
        )
        reader_connect = connect_client(
            reader_client,
            scope="user" if reader_client == "cline" else "project",
            cwd=reader_dir,
            home=reader_dir,
            config_path=reader_cfg_override,
            python_path=python_path,
            bridge_home=shared_home,
            verify_spawn=True,
        )
        if not writer_connect["ok"] or not reader_connect["ok"]:
            return {
                "schema": BRIDGE_CHECK_SCHEMA,
                "ok": False,
                "from_client": writer_client,
                "to_client": reader_client,
                "writer_connect": writer_connect,
                "reader_connect": reader_connect,
            }

        writer_validation = _validate_client_config_file(
            writer_client,
            Path(writer_connect["config_path"]),
            vscode_format="servers",
        )
        reader_validation = _validate_client_config_file(
            reader_client,
            Path(reader_connect["config_path"]),
            vscode_format="servers",
        )
        round_trip = asyncio.run(
            _execute_bridge_round_trip(
                writer_client=writer_client,
                writer_entry=writer_validation["entry"],
                writer_cwd=writer_dir,
                reader_client=reader_client,
                reader_entry=reader_validation["entry"],
                reader_cwd=reader_dir,
                namespace=namespace,
            )
        )
        return {
            "schema": BRIDGE_CHECK_SCHEMA,
            "ok": bool(round_trip.get("ok")),
            "from_client": writer_client,
            "to_client": reader_client,
            "shared_bridge_home": str(shared_home),
            "namespace": namespace,
            **round_trip,
        }


async def _execute_bridge_round_trip(
    *,
    writer_client: ConnectorClient,
    writer_entry: Mapping[str, Any],
    writer_cwd: Path,
    reader_client: ConnectorClient,
    reader_entry: Mapping[str, Any],
    reader_cwd: Path,
    namespace: str,
) -> dict[str, Any]:
    claim_text = "Merge pull requests only after CI is green on the target branch."
    reason_text = "Broken main blocked two releases this month."
    content = f"record_type: decision\nclaim: {claim_text}\nreason: {reason_text}\n"

    writer_params = StdioServerParameters(
        command=str(writer_entry["command"]),
        args=[str(a) for a in writer_entry.get("args") or []],
        cwd=writer_cwd,
        env={**os.environ, **{str(k): str(v) for k, v in (writer_entry.get("env") or {}).items()}},
    )
    async with Client(
        stdio_client(writer_params),
        mode="auto",
        client_info=Implementation(name=writer_client, version=package_version()),
    ) as client_a:
        store_res = await client_a.call_tool(
            "store",
            {
                "namespace": namespace,
                "content": content,
                "kind": "memory",
                "title": "CI green before merge",
            },
        )
        stored_payload = store_res.structured_content
        if store_res.is_error or not isinstance(stored_payload, dict):
            return {"ok": False, "error": "Writer store call failed."}
        stored_id = str(stored_payload.get("id") or "")

    reader_params = StdioServerParameters(
        command=str(reader_entry["command"]),
        args=[str(a) for a in reader_entry.get("args") or []],
        cwd=reader_cwd,
        env={**os.environ, **{str(k): str(v) for k, v in (reader_entry.get("env") or {}).items()}},
    )
    async with Client(
        stdio_client(reader_params),
        mode="auto",
        client_info=Implementation(name=reader_client, version=package_version()),
    ) as client_b:
        recall_res = await client_b.call_tool(
            "recall",
            {
                "namespace": namespace,
                "query": "What is required before we merge a pull request?",
                "kind": "memory",
                "limit": 5,
            },
        )
        recall_payload = recall_res.structured_content
        if recall_res.is_error or not isinstance(recall_payload, dict):
            return {"ok": False, "error": "Reader recall call failed."}

    items = recall_payload.get("items") or []
    matched = next((item for item in items if isinstance(item, dict) and str(item.get("id")) == stored_id), None)
    if matched is None:
        return {
            "ok": False,
            "stored_id": stored_id,
            "same_record": False,
            "fresh_process": True,
            "error": "Recalled items in second client did not contain the record stored by the first client.",
        }

    recalled_content = str(matched.get("content") or "")
    writer_source = str(matched.get("source_client") or "")
    same_record = (
        str(matched.get("id")) == stored_id
        and claim_text in recalled_content
        and reason_text in recalled_content
        and writer_source == writer_client
    )
    return {
        "ok": same_record,
        "stored_id": stored_id,
        "recalled_id": str(matched.get("id") or ""),
        "claim": claim_text,
        "reason": reason_text,
        "writer_source_client": writer_source,
        "reader_source_client": reader_client,
        "same_record": same_record,
        "fresh_process": True,
    }


def render_connector_report_markdown(report: Mapping[str, Any]) -> str:
    if report.get("schema") == BRIDGE_CHECK_SCHEMA:
        status = "PASS" if report.get("ok") else "FAIL"
        return "\n".join(
            [
                f"# AMB Cross-Client Bridge Check ({status})",
                "",
                f"- Writer client: `{report.get('from_client')}`",
                f"- Reader client: `{report.get('to_client')}`",
                f"- Shared AMB home: `{report.get('shared_bridge_home')}`",
                f"- Namespace: `{report.get('namespace')}`",
                f"- Stored record ID: `{report.get('stored_id', 'none')}`",
                f"- Same record recalled in fresh process: `{str(bool(report.get('same_record'))).lower()}`",
            ]
        )

    check = report.get("check") if isinstance(report.get("check"), Mapping) else report
    lines = [
        f"# AMB Client Connector: {report.get('client')}",
        "",
        f"- Status: `{'ok' if report.get('ok') else 'failed'}`",
        f"- Scope: `{report.get('scope')}`",
        f"- Config path: `{report.get('config_path')}`",
    ]
    if "action" in report:
        lines.append(f"- Action: `{report.get('action')}`")
    if report.get("reason"):
        lines.append(f"- Reason: `{report['reason']}`")
    if report.get("detail"):
        lines.append(f"- Detail: {report['detail']}")
    if report.get("error"):
        lines.append(f"- Error: {report['error']}")
    if isinstance(check, Mapping) and "tier1_config_valid" in check:
        lines.extend(
            [
                f"- Tier 1 (`config_valid`): `{str(bool(check.get('tier1_config_valid'))).lower()}`",
                f"- Tier 2 (`server_spawn_verified`): `{str(bool(check.get('tier2_server_spawn_verified'))).lower()}`",
                f"- Tier 3 (`host_registration_status`): `{check.get('tier3_host_registration_status')}`",
                f"- Lists `store` and `recall`: `{str(bool(check.get('lists_store_and_recall'))).lower()}` "
                f"(`{check.get('tool_count', 0)}` tools)",
            ]
        )
        for issue in check.get("issues") or []:
            lines.append(f"- Issue: {issue}")
        if check.get("note"):
            lines.extend(["", str(check["note"])])
    for warning in report.get("warnings") or []:
        lines.append(f"- Warning: {warning}")
    if report.get("preview"):
        lines.extend(["", "## Preview", "", "```", str(report["preview"]).rstrip(), "```"])
    return "\n".join(lines)


def _atomic_write_bytes(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    temp_path = Path(temp_name)
    try:
        if os.name == "posix":
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, target)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise


def _rollback_file(target: Path, *, original_bytes: bytes | None) -> None:
    if original_bytes is None:
        target.unlink(missing_ok=True)
    else:
        _atomic_write_bytes(target, original_bytes)
