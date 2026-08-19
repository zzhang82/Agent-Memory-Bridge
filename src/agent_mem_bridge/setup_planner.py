"""Deterministic, read-only client setup planning for P2A.

This module deliberately has no configuration-writing, database, backup, package
installation, or client-launching behavior. It only observes bounded paths,
classifies an AMB-owned entry, and renders a future-action preview.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Literal, Mapping, Sequence, cast

from .client_config import (
    CLIENT_STATUSES,
    DEFAULT_SERVER_NAME,
    ClientName,
    build_client_config_options,
    render_client_config,
    supported_client_names,
)
from .paths import resolve_bridge_home, resolve_config_path

SETUP_PLAN_SCHEMA_VERSION = 1

DetectionStatus = Literal["detected", "not_detected", "path_unknown"]
ExistingAmbState = Literal[
    "absent",
    "equivalent",
    "update_required",
    "conflict",
    "unreadable",
    "inspection_unavailable",
]
RecommendedAction = Literal[
    "no_change",
    "would_create",
    "would_merge",
    "would_update",
    "manual_review",
    "unsupported_autoconfig",
]


@dataclass(frozen=True)
class ConfigInspection:
    exists: bool
    state: ExistingAmbState
    unrelated_server_count: int | None
    note: str | None = None


@dataclass(frozen=True)
class ClientSetupPlan:
    client: str
    support_status: str
    detection_status: DetectionStatus
    config_path: str | None
    config_format: str | None
    existing_amb_state: ExistingAmbState
    recommended_action: RecommendedAction
    proposed_fragment: str
    unrelated_server_count: int | None
    notes: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SetupPlan:
    schema_version: int
    write_count: int
    platform: str
    launcher: str
    bridge_home: str
    bridge_config_path: str
    clients: tuple[ClientSetupPlan, ...]
    next_commands: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "write_count": self.write_count,
            "environment": {
                "platform": self.platform,
                "launcher": self.launcher,
                "bridge_home": self.bridge_home,
                "bridge_config_path": self.bridge_config_path,
            },
            "clients": [client.as_dict() for client in self.clients],
            "next_commands": list(self.next_commands),
        }


@dataclass(frozen=True)
class _KnownClientPath:
    path: Path | None
    config_format: str | None
    detection_executable: str | None
    note: str | None = None


def build_setup_plan(
    *,
    clients: Sequence[str] | None = None,
    cwd: Path | None = None,
    python_path: str | Path | None = None,
    bridge_home: Path | None = None,
    bridge_config_path: Path | None = None,
    home: Path | None = None,
    environ: Mapping[str, str] | None = None,
    platform: str | None = None,
    which: Callable[[str], str | None] | None = None,
) -> SetupPlan:
    """Build a plan without mutating a client or any AMB runtime path."""

    requested = tuple(clients) if clients else tuple(supported_client_names())
    selected = tuple(dict.fromkeys(requested))
    unknown = [client for client in selected if client not in supported_client_names()]
    if unknown:
        supported = ", ".join(supported_client_names())
        raise ValueError(f"Unsupported client selection: {', '.join(unknown)}. Supported clients: {supported}.")

    resolved_cwd = (cwd or Path.cwd()).expanduser()
    resolved_home = (home or Path.home()).expanduser()
    resolved_environment = dict(os.environ if environ is None else environ)
    resolved_platform = platform or sys.platform
    executable_lookup = which or shutil.which
    resolved_launcher = str(python_path or sys.executable)
    resolved_bridge_home = bridge_home or resolve_bridge_home()
    resolved_bridge_config = bridge_config_path or resolve_config_path()

    client_plans = tuple(
        _build_client_plan(
            client,
            cwd=resolved_cwd,
            home=resolved_home,
            environ=resolved_environment,
            platform=resolved_platform,
            which=executable_lookup,
            python_path=resolved_launcher,
            bridge_home=resolved_bridge_home,
            bridge_config_path=resolved_bridge_config,
        )
        for client in selected
    )
    return SetupPlan(
        schema_version=SETUP_PLAN_SCHEMA_VERSION,
        write_count=0,
        platform=resolved_platform,
        launcher=resolved_launcher,
        bridge_home=str(resolved_bridge_home),
        bridge_config_path=str(resolved_bridge_config),
        clients=client_plans,
        next_commands=(
            f"{resolved_launcher} -m agent_mem_bridge doctor --include-stdio",
            f"{resolved_launcher} -m agent_mem_bridge verify",
        ),
    )


def render_setup_plan(plan: SetupPlan) -> str:
    """Render a concise human-facing preview without exposing existing config data."""

    lines = [
        "Agent Memory Bridge setup plan",
        "",
        "Environment",
        f"  OS: {plan.platform}",
        f"  AMB launcher: {plan.launcher}",
        f"  Bridge home: {plan.bridge_home}",
        f"  Bridge config: {plan.bridge_config_path}",
        "",
        "Client plans",
    ]
    for client in plan.clients:
        lines.extend(
            [
                "",
                f"  {client.client}",
                f"    support: {client.support_status}",
                f"    detection: {client.detection_status}",
                f"    config: {client.config_path or 'path unknown'}",
                f"    config format: {client.config_format or 'unknown'}",
                f"    existing AMB entry: {client.existing_amb_state}",
                f"    proposed action: {client.recommended_action}",
            ]
        )
        if client.unrelated_server_count is not None:
            lines.append(f"    unrelated server count: {client.unrelated_server_count}")
        for note in client.notes:
            lines.append(f"    note: {note}")
        lines.extend(
            ["    proposed AMB fragment:", f"```{client.config_format or ''}", client.proposed_fragment, "```"]
        )

    lines.extend(
        [
            "",
            "No changes were made.",
            "Changes written: 0",
            "",
            "Next after configuration:",
            *[f"  {command}" for command in plan.next_commands],
        ]
    )
    return "\n".join(lines)


def _build_client_plan(
    client: str,
    *,
    cwd: Path,
    home: Path,
    environ: Mapping[str, str],
    platform: str,
    which: Callable[[str], str | None],
    python_path: str,
    bridge_home: Path,
    bridge_config_path: Path,
) -> ClientSetupPlan:
    options = build_client_config_options(
        client,
        python_path=python_path,
        cwd=cwd,
        bridge_home=bridge_home,
        config_path=bridge_config_path,
    )
    rendered = render_client_config(options)
    known_path = _known_client_path(client, cwd=cwd, home=home, environ=environ, platform=platform)
    notes: list[str] = []

    if known_path.path is None:
        notes.append(known_path.note or "No stable path is encoded for read-only inspection.")
        return ClientSetupPlan(
            client=client,
            support_status=CLIENT_STATUSES[cast(ClientName, client)],
            detection_status="path_unknown",
            config_path=None,
            config_format=rendered.format,
            existing_amb_state="inspection_unavailable",
            recommended_action="unsupported_autoconfig",
            proposed_fragment=rendered.content,
            unrelated_server_count=None,
            notes=tuple(notes),
        )

    executable_present = bool(known_path.detection_executable and which(known_path.detection_executable))
    config_exists = known_path.path.is_file()
    detection_status: DetectionStatus = "detected" if executable_present or config_exists else "not_detected"
    inspection = _inspect_config(
        path=known_path.path,
        config_format=known_path.config_format or rendered.format,
        client=client,
        proposed_fragment=rendered.content,
    )
    action = _recommended_action(inspection)
    if known_path.note:
        notes.append(known_path.note)
    if detection_status == "not_detected":
        notes.append("No bounded executable or config-file marker was found at the inspected location.")
    if inspection.note:
        notes.append(inspection.note)

    return ClientSetupPlan(
        client=client,
        support_status=CLIENT_STATUSES[cast(ClientName, client)],
        detection_status=detection_status,
        config_path=str(known_path.path),
        config_format=known_path.config_format,
        existing_amb_state=inspection.state,
        recommended_action=action,
        proposed_fragment=rendered.content,
        unrelated_server_count=inspection.unrelated_server_count,
        notes=tuple(notes),
    )


def _known_client_path(
    client: str,
    *,
    cwd: Path,
    home: Path,
    environ: Mapping[str, str],
    platform: str,
) -> _KnownClientPath:
    if client == "codex":
        codex_home = Path(environ.get("CODEX_HOME", str(home / ".codex"))).expanduser()
        return _KnownClientPath(codex_home / "config.toml", "toml", "codex")
    if client == "claude-code":
        return _KnownClientPath(cwd / ".mcp.json", "json", "claude")
    if client == "vscode":
        return _KnownClientPath(cwd / ".vscode" / "mcp.json", "json", "code")
    if client == "opencode":
        if platform.startswith("win"):
            return _KnownClientPath(
                None,
                None,
                None,
                "OpenCode’s reviewed documentation does not establish a stable Windows config path for P2A.",
            )
        config_root = Path(environ.get("XDG_CONFIG_HOME", str(home / ".config"))).expanduser()
        return _KnownClientPath(config_root / "opencode" / "opencode.json", "json", "opencode")
    if client == "generic":
        return _KnownClientPath(None, None, None, "Generic is a renderer target and is never auto-detected.")
    if client == "claude-desktop":
        return _KnownClientPath(
            None,
            None,
            None,
            "Claude Desktop is currently documented around UI-managed Desktop Extensions; P2A does not guess a file path.",
        )
    return _KnownClientPath(
        None,
        None,
        None,
        "No stable primary-source config path is encoded for this supported client in P2A.",
    )


def _inspect_config(
    *,
    path: Path,
    config_format: str,
    client: str,
    proposed_fragment: str,
) -> ConfigInspection:
    if not path.exists():
        return ConfigInspection(exists=False, state="absent", unrelated_server_count=0)
    if not path.is_file():
        return ConfigInspection(
            exists=True,
            state="unreadable",
            unrelated_server_count=None,
            note="The known configuration location is not a regular file.",
        )
    if config_format == "yaml":
        return ConfigInspection(
            exists=True,
            state="inspection_unavailable",
            unrelated_server_count=None,
            note="YAML inspection is unavailable in P2A because no runtime YAML parser is added.",
        )
    try:
        raw_text = path.read_text(encoding="utf-8")
        payload = json.loads(raw_text) if config_format == "json" else tomllib.loads(raw_text)
        expected = json.loads(proposed_fragment) if config_format == "json" else tomllib.loads(proposed_fragment)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, tomllib.TOMLDecodeError):
        return ConfigInspection(
            exists=True,
            state="unreadable",
            unrelated_server_count=None,
            note=f"The existing {config_format.upper()} configuration could not be inspected safely.",
        )
    if not isinstance(payload, dict) or not isinstance(expected, dict):
        return ConfigInspection(
            exists=True,
            state="unreadable",
            unrelated_server_count=None,
            note="The existing configuration does not have the expected top-level object shape.",
        )

    container_key = _server_container_key(client)
    existing_servers = payload.get(container_key)
    expected_servers = expected.get(container_key)
    if not isinstance(existing_servers, dict) or not isinstance(expected_servers, dict):
        return ConfigInspection(exists=True, state="absent", unrelated_server_count=0)
    existing_entry = existing_servers.get(DEFAULT_SERVER_NAME)
    expected_entry = expected_servers.get(DEFAULT_SERVER_NAME)
    unrelated_count = len([name for name in existing_servers if name != DEFAULT_SERVER_NAME])
    if existing_entry is None:
        return ConfigInspection(exists=True, state="absent", unrelated_server_count=unrelated_count)
    if not isinstance(existing_entry, dict) or not isinstance(expected_entry, dict):
        return ConfigInspection(exists=True, state="conflict", unrelated_server_count=unrelated_count)
    if existing_entry == expected_entry:
        return ConfigInspection(exists=True, state="equivalent", unrelated_server_count=unrelated_count)
    if _looks_amb_owned(existing_entry):
        return ConfigInspection(exists=True, state="update_required", unrelated_server_count=unrelated_count)
    return ConfigInspection(exists=True, state="conflict", unrelated_server_count=unrelated_count)


def _server_container_key(client: str) -> str:
    if client == "codex":
        return "mcp_servers"
    if client == "vscode":
        return "servers"
    if client == "opencode":
        return "mcp"
    return "mcpServers"


def _looks_amb_owned(value: Mapping[str, Any]) -> bool:
    def visit(candidate: Any) -> bool:
        if isinstance(candidate, Mapping):
            for key, item in candidate.items():
                if str(key).startswith("AGENT_MEMORY_BRIDGE_") or visit(item):
                    return True
        elif isinstance(candidate, list):
            return any(visit(item) for item in candidate)
        elif isinstance(candidate, str):
            return "agent_mem_bridge" in candidate or "agent-memory-bridge" in candidate
        return False

    return visit(value)


def _recommended_action(inspection: ConfigInspection) -> RecommendedAction:
    if inspection.state == "equivalent":
        return "no_change"
    if inspection.state == "absent":
        return "would_create" if not inspection.exists else "would_merge"
    if inspection.state == "update_required":
        return "would_update"
    return "manual_review"
