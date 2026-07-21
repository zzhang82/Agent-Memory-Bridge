from __future__ import annotations

import json
import tomllib
from pathlib import Path

from agent_mem_bridge.first_run import GITHUB_ARCHIVE_URL
from agent_mem_bridge.onboarding_contract import run_onboarding_contract_check


def test_onboarding_contract_repository_passes() -> None:
    root = Path(__file__).resolve().parents[1]
    report = run_onboarding_contract_check(root)
    assert report["ok"] is True, json.dumps(report, indent=2, ensure_ascii=False)

    version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    archive_ref = f"archive/refs/tags/v{version}.zip"
    guide_paths = (
        Path("INSTALL_FOR_AGENTS.md"),
        Path("llms-install.md"),
        Path("llms.txt"),
        Path("docs/INTEGRATIONS.md"),
    )
    guides = {path: (root / path).read_text(encoding="utf-8") for path in guide_paths}

    for path, content in guides.items():
        assert "pip --python .amb-venv" not in content, path
        assert "refs/heads/main.zip" not in content, path
        assert ".venv/bin/python" not in content, path

    assert archive_ref in GITHUB_ARCHIVE_URL
    for path in (Path("INSTALL_FOR_AGENTS.md"), Path("llms-install.md"), Path("llms.txt")):
        assert archive_ref in guides[path], path
    assert "<venv-python> -m agent_mem_bridge doctor" in guides[Path("docs/INTEGRATIONS.md")]
    assert "<venv-python> -m agent_mem_bridge verify" in guides[Path("docs/INTEGRATIONS.md")]


def test_onboarding_contract_flags_leaked_local_paths(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir(parents=True)
    (tmp_path / "benchmark").mkdir(parents=True)
    (tmp_path / "examples").mkdir(parents=True)
    (tmp_path / "README.md").write_text("[Integrations](docs/INTEGRATIONS.md)\n", encoding="utf-8")
    (tmp_path / "INSTALL_FOR_AGENTS.md").write_text("ok\n", encoding="utf-8")
    (tmp_path / "llms-install.md").write_text("ok\n", encoding="utf-8")
    (tmp_path / "llms.txt").write_text("ok\n", encoding="utf-8")
    (tmp_path / "README.zh-CN.md").write_text("C:/workspace/private-project leaked\n", encoding="utf-8")
    (tmp_path / "CONTRIBUTING.md").write_text("ok\n", encoding="utf-8")
    (tmp_path / "Dockerfile").write_text("ENV AGENT_MEMORY_BRIDGE_HOME=/data/agent-memory-bridge\n", encoding="utf-8")
    (tmp_path / "config.example.toml").write_text(
        "[bridge]\nhome='~/.local/share/agent-memory-bridge'\n", encoding="utf-8"
    )
    (tmp_path / "benchmark" / "README.md").write_text("python ./scripts/run_benchmark.py\n", encoding="utf-8")
    (tmp_path / "docs" / "CONFIGURATION.md").write_text("ok\n", encoding="utf-8")
    (tmp_path / "docs" / "HARNESS-DESIGN.md").write_text("ok\n", encoding="utf-8")
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
    (tmp_path / "INSTALL_FOR_AGENTS.md").write_text("ok\n", encoding="utf-8")
    (tmp_path / "llms-install.md").write_text("ok\n", encoding="utf-8")
    (tmp_path / "llms.txt").write_text("ok\n", encoding="utf-8")
    (tmp_path / "README.zh-CN.md").write_text("ok\n", encoding="utf-8")
    (tmp_path / "CONTRIBUTING.md").write_text(
        ".\\.venv\\Scripts\\python.exe .\\scripts\\check_release_contract.py\n", encoding="utf-8"
    )
    (tmp_path / "Dockerfile").write_text("ENV AGENT_MEMORY_BRIDGE_HOME=/data/agent-memory-bridge\n", encoding="utf-8")
    (tmp_path / "config.example.toml").write_text(
        "[bridge]\nhome='~/.local/share/agent-memory-bridge'\n", encoding="utf-8"
    )
    (tmp_path / "benchmark" / "README.md").write_text("python ./scripts/run_benchmark.py\n", encoding="utf-8")
    (tmp_path / "docs" / "CONFIGURATION.md").write_text("ok\n", encoding="utf-8")
    (tmp_path / "docs" / "HARNESS-DESIGN.md").write_text("ok\n", encoding="utf-8")
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
    (tmp_path / "INSTALL_FOR_AGENTS.md").write_text("ok\n", encoding="utf-8")
    (tmp_path / "llms-install.md").write_text("ok\n", encoding="utf-8")
    (tmp_path / "llms.txt").write_text("ok\n", encoding="utf-8")
    (tmp_path / "README.zh-CN.md").write_text("ok\n", encoding="utf-8")
    (tmp_path / "CONTRIBUTING.md").write_text("ok\n", encoding="utf-8")
    (tmp_path / "Dockerfile").write_text(
        "ENV CODEX_HOME=/tmp/.codex \\\n    AGENT_MEMORY_BRIDGE_HOME=/tmp/.codex/mem-bridge\n",
        encoding="utf-8",
    )
    (tmp_path / "config.example.toml").write_text(
        "[bridge]\nhome='~/.local/share/agent-memory-bridge'\n", encoding="utf-8"
    )
    (tmp_path / "benchmark" / "README.md").write_text("python ./scripts/run_benchmark.py\n", encoding="utf-8")
    (tmp_path / "docs" / "CONFIGURATION.md").write_text("ok\n", encoding="utf-8")
    (tmp_path / "docs" / "HARNESS-DESIGN.md").write_text("ok\n", encoding="utf-8")
    (tmp_path / "docs" / "INTEGRATIONS.md").write_text("ok\n", encoding="utf-8")
    (tmp_path / "examples" / "README.md").write_text("ok\n", encoding="utf-8")

    report = run_onboarding_contract_check(tmp_path)

    assert report["ok"] is False
    docs_check = next(check for check in report["checks"] if check["name"] == "onboarding_docs_stay_placeholder_safe")
    assert any(violation["path"] == "Dockerfile" for violation in docs_check["violations"])
