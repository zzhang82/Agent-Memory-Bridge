from __future__ import annotations

import json
import tomllib
from pathlib import Path

from agent_mem_bridge.cli import main
from agent_mem_bridge.client_config import build_client_config_options, render_client_config, render_example_client_configs


def test_rendered_example_configs_parse() -> None:
    for rendered in render_example_client_configs():
        if rendered.format == "json":
            payload = json.loads(rendered.content)
            assert "mcpServers" in payload
        else:
            payload = tomllib.loads(rendered.content)
            assert "mcp_servers" in payload


def test_non_codex_configs_do_not_reference_codex_home() -> None:
    for client in ("generic", "claude-desktop", "claude-code", "cursor", "cline", "antigravity"):
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
