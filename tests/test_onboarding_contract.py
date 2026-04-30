from __future__ import annotations

import json
from pathlib import Path

from agent_mem_bridge.onboarding_contract import run_onboarding_contract_check


def test_onboarding_contract_repository_passes() -> None:
    report = run_onboarding_contract_check(Path(__file__).resolve().parents[1])
    assert report["ok"] is True, json.dumps(report, indent=2, ensure_ascii=False)


def test_onboarding_contract_flags_leaked_local_paths(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir(parents=True)
    (tmp_path / "benchmark").mkdir(parents=True)
    (tmp_path / "examples").mkdir(parents=True)
    (tmp_path / "README.md").write_text("[Integrations](docs/INTEGRATIONS.md)\n", encoding="utf-8")
    (tmp_path / "README.zh-CN.md").write_text("D:/playground leaked\n", encoding="utf-8")
    (tmp_path / "CONTRIBUTING.md").write_text("ok\n", encoding="utf-8")
    (tmp_path / "Dockerfile").write_text("ENV AGENT_MEMORY_BRIDGE_HOME=/data/agent-memory-bridge\n", encoding="utf-8")
    (tmp_path / "config.example.toml").write_text("[bridge]\nhome='~/.local/share/agent-memory-bridge'\n", encoding="utf-8")
    (tmp_path / "benchmark" / "README.md").write_text("python ./scripts/run_benchmark.py\n", encoding="utf-8")
    (tmp_path / "docs" / "CONFIGURATION.md").write_text("ok\n", encoding="utf-8")
    (tmp_path / "docs" / "INTEGRATIONS.md").write_text("ok\n", encoding="utf-8")
    (tmp_path / "examples" / "README.md").write_text("ok\n", encoding="utf-8")

    report = run_onboarding_contract_check(tmp_path)

    assert report["ok"] is False
    docs_check = next(check for check in report["checks"] if check["name"] == "onboarding_docs_stay_placeholder_safe")
    assert docs_check["violations"]


def test_onboarding_contract_flags_windows_style_relative_commands(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir(parents=True)
    (tmp_path / "benchmark").mkdir(parents=True)
    (tmp_path / "examples").mkdir(parents=True)
    (tmp_path / "README.md").write_text("[Integrations](docs/INTEGRATIONS.md)\n", encoding="utf-8")
    (tmp_path / "README.zh-CN.md").write_text("ok\n", encoding="utf-8")
    (tmp_path / "CONTRIBUTING.md").write_text(".\\.venv\\Scripts\\python.exe .\\scripts\\check_release_contract.py\n", encoding="utf-8")
    (tmp_path / "Dockerfile").write_text("ENV AGENT_MEMORY_BRIDGE_HOME=/data/agent-memory-bridge\n", encoding="utf-8")
    (tmp_path / "config.example.toml").write_text("[bridge]\nhome='~/.local/share/agent-memory-bridge'\n", encoding="utf-8")
    (tmp_path / "benchmark" / "README.md").write_text("python ./scripts/run_benchmark.py\n", encoding="utf-8")
    (tmp_path / "docs" / "CONFIGURATION.md").write_text("ok\n", encoding="utf-8")
    (tmp_path / "docs" / "INTEGRATIONS.md").write_text("ok\n", encoding="utf-8")
    (tmp_path / "examples" / "README.md").write_text("ok\n", encoding="utf-8")

    report = run_onboarding_contract_check(tmp_path)

    assert report["ok"] is False
    docs_check = next(check for check in report["checks"] if check["name"] == "onboarding_docs_stay_placeholder_safe")
    assert any("Windows" in violation["reason"] for violation in docs_check["violations"])


def test_onboarding_contract_flags_codex_specific_docker_defaults(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir(parents=True)
    (tmp_path / "benchmark").mkdir(parents=True)
    (tmp_path / "examples").mkdir(parents=True)
    (tmp_path / "README.md").write_text("[Integrations](docs/INTEGRATIONS.md)\n", encoding="utf-8")
    (tmp_path / "README.zh-CN.md").write_text("ok\n", encoding="utf-8")
    (tmp_path / "CONTRIBUTING.md").write_text("ok\n", encoding="utf-8")
    (tmp_path / "Dockerfile").write_text(
        "ENV CODEX_HOME=/tmp/.codex \\\n"
        "    AGENT_MEMORY_BRIDGE_HOME=/tmp/.codex/mem-bridge\n",
        encoding="utf-8",
    )
    (tmp_path / "config.example.toml").write_text("[bridge]\nhome='~/.local/share/agent-memory-bridge'\n", encoding="utf-8")
    (tmp_path / "benchmark" / "README.md").write_text("python ./scripts/run_benchmark.py\n", encoding="utf-8")
    (tmp_path / "docs" / "CONFIGURATION.md").write_text("ok\n", encoding="utf-8")
    (tmp_path / "docs" / "INTEGRATIONS.md").write_text("ok\n", encoding="utf-8")
    (tmp_path / "examples" / "README.md").write_text("ok\n", encoding="utf-8")

    report = run_onboarding_contract_check(tmp_path)

    assert report["ok"] is False
    docs_check = next(check for check in report["checks"] if check["name"] == "onboarding_docs_stay_placeholder_safe")
    assert any(violation["path"] == "Dockerfile" for violation in docs_check["violations"])
