from __future__ import annotations

import json
from pathlib import Path

from agent_mem_bridge.cli import main
from agent_mem_bridge.onboarding import run_verify


def test_run_verify_succeeds_with_isolated_runtime(tmp_path: Path) -> None:
    report = run_verify(project_root=Path(__file__).resolve().parents[1], runtime_dir=tmp_path / "verify-runtime")

    assert report["ok"] is True
    assert report["tool_count"] == 10
    check_names = {check["name"] for check in report["checks"]}
    assert {"tool_surface", "memory_round_trip", "signal_lifecycle"} <= check_names


def test_cli_verify_json_output(tmp_path: Path, capsys) -> None:
    exit_code = main(
        [
            "verify",
            "--json",
            "--project-root",
            str(Path(__file__).resolve().parents[1]),
            "--runtime-dir",
            str(tmp_path / "verify-runtime"),
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["ok"] is True
    assert payload["tool_count"] == 10
