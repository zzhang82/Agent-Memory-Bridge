from __future__ import annotations

import json
from pathlib import Path

from agent_mem_bridge.cli import main


def test_memory_policy_cli_renders_json_for_host(capsys) -> None:
    assert main(["memory-policy", "--host", "codex", "--format", "json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == "amb.semantic-memory-policy.v1"
    assert payload["host"] == "codex"
    assert payload["adapter"]["instruction_surface"] == "project AGENTS.md or AGENTS.override.md"


def test_memory_policy_cli_does_not_overwrite_export(tmp_path: Path, capsys) -> None:
    output = tmp_path / "policy.md"
    output.write_text("owner content\n", encoding="utf-8")

    assert main(["memory-policy", "--host", "opencode", "--output", str(output)]) == 3
    assert output.read_text(encoding="utf-8") == "owner content\n"
    assert "Refusing to overwrite" in capsys.readouterr().err
