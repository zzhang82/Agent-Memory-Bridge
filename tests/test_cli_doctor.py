from __future__ import annotations

import json
from pathlib import Path

from agent_mem_bridge.cli import main
from agent_mem_bridge.onboarding import run_doctor


def test_run_doctor_returns_structured_checks() -> None:
    report = run_doctor(project_root=Path(__file__).resolve().parents[1])

    assert report["ok"] is True
    check_names = {check["name"] for check in report["checks"]}
    assert {"python_version", "sqlite_fts5", "config_path", "resolved_defaults"} <= check_names


def test_cli_doctor_json_output(capsys) -> None:
    exit_code = main(["doctor", "--json", "--project-root", str(Path(__file__).resolve().parents[1])])
    captured = capsys.readouterr()

    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["ok"] is True
    assert any(check["name"] == "sqlite_fts5" for check in payload["checks"])
