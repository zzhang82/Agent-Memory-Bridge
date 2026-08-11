from __future__ import annotations

import os
import shlex
from pathlib import Path
from typing import Any

from .client_config import (
    DEFAULT_SERVER_NAME,
    build_client_config_options,
    render_client_config,
)
from .mcp_boundary import package_version
from .storage import MemoryStore
from .task_brief import build_task_brief_report, render_task_brief_markdown

FIRST_RUN_SCHEMA = "memory.first_run.v1"
FIRST_RUN_BOUNDARY = "manual_config_copy_no_auto_mutation"
PYTHON_LAUNCHER_NOTE = (
    "Use the available Python 3.11+ launcher: examples use `python`; on many "
    "Linux systems use `python3`; on Windows `py -3` may be appropriate. "
    "Generated Windows verification commands use PowerShell syntax."
)
RELEASE_VERSION = package_version()
PINNED_INSTALL_VERSION = "0.27.0"
GITHUB_ARCHIVE_URL = f"https://github.com/zzhang82/Agent-Memory-Bridge/archive/refs/tags/v{PINNED_INSTALL_VERSION}.zip"
RELEASE_INSTALL_GATE_NOTE = (
    "The pinned GitHub tag route is the published `v0.27.0` baseline. "
    "The local source candidate has no release tag; use a source checkout with "
    "`<venv-python> -m pip install -e .` to evaluate it."
)
VENV_INTERPRETER_COMMAND = (
    'python -c "import os; from pathlib import Path; '
    "print((Path('.amb-venv') / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')).absolute())\""
)


def build_first_run_report(
    store: MemoryStore,
    *,
    client: str,
    namespace: str,
    query: str,
    python_path: str | Path | None,
    cwd: str | Path | None,
    bridge_home: str | Path | None,
    config_path: str | Path | None,
    example: bool = False,
) -> dict[str, Any]:
    """Build a copy/paste first-run guide without writing client config."""

    options = build_client_config_options(
        client,
        python_path=python_path,
        cwd=cwd,
        bridge_home=bridge_home,
        config_path=config_path,
        example=example,
    )
    rendered = render_client_config(options)
    task_brief = build_task_brief_report(store, query=query, namespace=namespace)
    baseline_install = [
        "python -m venv .amb-venv",
        VENV_INTERPRETER_COMMAND,
        f'<venv-python> -m pip install "{GITHUB_ARCHIVE_URL}"',
    ]
    verify_commands = [
        _render_python_module_command(options.command, "doctor"),
        _render_python_module_command(options.command, "verify"),
    ]
    version_mismatch_note = None
    if RELEASE_VERSION != PINNED_INSTALL_VERSION:
        version_mismatch_note = (
            f"Package/source version {RELEASE_VERSION} differs from pinned release-install version "
            f"{PINNED_INSTALL_VERSION}. Continue to use a source checkout until its exact-commit CI gate "
            "passes and its tag is created."
        )
    return {
        "schema": FIRST_RUN_SCHEMA,
        "client": rendered.client,
        "client_status": rendered.status,
        "client_config_format": rendered.format,
        "client_config_file_hint": rendered.file_hint,
        "namespace": namespace,
        "query": query,
        "python_launcher_note": PYTHON_LAUNCHER_NOTE,
        "mutation_boundary": FIRST_RUN_BOUNDARY,
        "boundary": {
            "mutation_allowed": False,
            "client_config_write_mode": "manual_copy_only",
            "public_mcp_surface_change": False,
            "amh_required": False,
            "task_brief_is_read_only": True,
        },
        "install": {
            "package_version": RELEASE_VERSION,
            "release_install_version": PINNED_INSTALL_VERSION,
            "release_install_gate_note": RELEASE_INSTALL_GATE_NOTE,
            "version_mismatch_note": version_mismatch_note,
            "baseline": baseline_install,
            "editable_install": [
                "python -m venv .amb-venv",
                VENV_INTERPRETER_COMMAND,
                "<venv-python> -m pip install -e .",
            ],
            "github_install": baseline_install,
            "smoke_test": verify_commands[1],
            "optional_uv_smoke_test": (
                "uvx --from git+https://github.com/zzhang82/Agent-Memory-Bridge"
                f"@v{PINNED_INSTALL_VERSION} agent-memory-bridge verify"
            ),
        },
        "verify": verify_commands,
        "client_config": {
            "server_name": DEFAULT_SERVER_NAME,
            "file_hint": rendered.file_hint,
            "format": rendered.format,
            "content": rendered.content,
        },
        "first_task_brief": task_brief,
    }


def render_first_run_markdown(report: dict[str, Any]) -> str:
    boundary = report["boundary"]
    config = report["client_config"]
    task_brief = report["first_task_brief"]
    lines = [
        "# AMB First Run",
        "",
        f"- schema: `{report['schema']}`",
        f"- client: `{report['client']}`",
        f"- client_status: `{report['client_status']}`",
        f"- namespace: `{report['namespace']}`",
        f"- query: {report['query']}",
        f"- mutation_allowed: `{str(boundary['mutation_allowed']).lower()}`",
        f"- public_mcp_surface_change: `{str(boundary['public_mcp_surface_change']).lower()}`",
        f"- amh_required: `{str(boundary['amh_required']).lower()}`",
        "",
        "## Install",
        "",
        report["python_launcher_note"],
        "",
        (f"Version-pinned GitHub tag install (`v{report['install']['release_install_version']}`) in an isolated venv:"),
        "",
        report["install"]["release_install_gate_note"],
        "",
        "```bash",
        *report["install"]["baseline"],
        "```",
        "",
        "Optional `uvx` shortcut (requires `uv`):",
        "",
        "```bash",
        report["install"]["optional_uv_smoke_test"],
        "```",
        "",
        "## Client Config",
        "",
        f"- file_hint: `{config['file_hint']}`",
        f"- format: `{config['format']}`",
        "- write_mode: `manual_copy_only`",
        "",
        f"```{_code_fence_language(config['format'])}",
        config["content"],
        "```",
        "",
        "## Verify",
        "",
        "Run these after pasting the config and restarting the MCP client if needed:",
        "`doctor` checks local prerequisites and paths; `verify` launches an isolated",
        "AMB stdio runtime. Inspect the client's MCP status/tool visibility to prove",
        "that the client loaded the config.",
        "",
        "```bash",
        *report["verify"],
        "```",
        "",
        "## First Task Brief",
        "",
        render_task_brief_markdown(task_brief),
    ]
    version_mismatch_note = report["install"]["version_mismatch_note"]
    if version_mismatch_note is not None:
        client_config_index = lines.index("## Client Config")
        lines[client_config_index:client_config_index] = [version_mismatch_note, ""]
    return "\n".join(lines)


def _code_fence_language(config_format: str) -> str:
    if config_format == "toml":
        return "toml"
    if config_format == "yaml":
        return "yaml"
    return "json"


def _render_python_module_command(
    command: str,
    subcommand: str,
    *,
    platform: str | None = None,
) -> str:
    args = [command, "-m", "agent_mem_bridge", subcommand]
    if (platform or os.name) == "nt":
        quoted_command = "'" + command.replace("'", "''") + "'"
        return " ".join(["&", quoted_command, *args[1:]])
    return shlex.join(args)
