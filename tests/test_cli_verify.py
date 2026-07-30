from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_mem_bridge.cli import main
from agent_mem_bridge.onboarding import run_verify


def test_run_verify_succeeds_with_isolated_runtime(tmp_path: Path) -> None:
    report = run_verify(project_root=Path(__file__).resolve().parents[1], runtime_dir=tmp_path / "verify-runtime")

    assert report["ok"] is True
    assert report["mcp_sdk_version"] == "2.0.0"
    assert report["tool_count"] == 13
    check_names = {check["name"] for check in report["checks"]}
    assert check_names == {"mcp_modern_stdio", "mcp_legacy_stdio"}
    assert report["modern_stdio"]["protocol_version"] == "2026-07-28"
    assert report["modern_stdio"]["discover"] is True
    assert report["modern_stdio"]["result_type"] == "complete"
    assert report["modern_stdio"]["business_flow"]["ok"] is True
    assert "complete_results" in {check["name"] for check in report["modern_stdio"]["checks"]}
    assert report["legacy_stdio"]["protocol_version"] == "2025-11-25"
    assert report["legacy_stdio"]["initialize"] is True
    assert report["legacy_stdio"]["business_flow"]["ok"] is True
    assert "complete_results" not in {check["name"] for check in report["legacy_stdio"]["checks"]}


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
    assert payload["tool_count"] == 13


def test_cli_verify_exits_nonzero_when_modern_probe_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def failed_modern_probe(*_: object, **__: object) -> dict[str, object]:
        return {
            "ok": False,
            "mcp_sdk_version": "2.0.0",
            "checks": [
                {"name": "mcp_modern_stdio", "status": "fail", "detail": "Modern probe failed."},
                {"name": "mcp_legacy_stdio", "status": "pass", "detail": "Legacy probe passed."},
            ],
            "modern_stdio": {
                "ok": False,
                "error_type": "ProtocolError",
                "error": "isolated stdio protocol probe failed",
            },
            "legacy_stdio": {"ok": True, "protocol_version": "2025-11-25"},
            "tool_count": 13,
        }

    monkeypatch.setattr("agent_mem_bridge.onboarding.run_dual_stdio_probe", failed_modern_probe)

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
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert payload["ok"] is False
    assert payload["modern_stdio"]["error"] == "isolated stdio protocol probe failed"
