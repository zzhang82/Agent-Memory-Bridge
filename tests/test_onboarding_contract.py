from __future__ import annotations

import json
import tomllib
from pathlib import Path

import agent_mem_bridge.onboarding_contract as onboarding_contract
from agent_mem_bridge.first_run import (
    GITHUB_ARCHIVE_URL,
    PINNED_INSTALL_VERSION,
    RELEASE_INSTALL_GATE_NOTE,
    RELEASE_VERSION,
)
from agent_mem_bridge.onboarding import render_report, render_verify_success_message
from agent_mem_bridge.onboarding_contract import release_install_tool_count, run_onboarding_contract_check


def test_onboarding_contract_repository_passes() -> None:
    root = Path(__file__).resolve().parents[1]
    report = run_onboarding_contract_check(root)
    assert report["ok"] is True, json.dumps(report, indent=2, ensure_ascii=False)

    package_version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    is_version_mismatch = package_version != PINNED_INSTALL_VERSION
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

    assert package_version == RELEASE_VERSION
    assert f"archive/refs/tags/v{PINNED_INSTALL_VERSION}.zip" in GITHUB_ARCHIVE_URL
    if is_version_mismatch:
        assert f"archive/refs/tags/v{package_version}.zip" not in GITHUB_ARCHIVE_URL
    else:
        assert f"archive/refs/tags/v{package_version}.zip" in GITHUB_ARCHIVE_URL
    detailed = " ".join(guides[Path("INSTALL_FOR_AGENTS.md")].split())
    assert RELEASE_INSTALL_GATE_NOTE in detailed
    assert f"<venv-python> -m pip install agent-memory-bridge=={package_version}" in detailed
    assert "<venv-python> -m pip install -e ." in detailed
    assert "schema v12" in detailed
    assert "17 public MCP tools" in detailed
    assert "archive/refs/tags" not in detailed
    assert "candidate" not in detailed.casefold()
    assert "not yet tagged" not in detailed.casefold()
    assert "not yet published" not in detailed.casefold()
    pointer = guides[Path("llms-install.md")]
    assert "canonical agent-readable install and first-use guide" in pointer
    assert "INSTALL_FOR_AGENTS.md" in pointer
    assert "pip install" not in pointer
    navigation = guides[Path("llms.txt")]
    assert "Map:" in navigation
    assert "docs/SEMANTIC-MEMORY-POLICY.md" in navigation
    assert "pip install" not in navigation
    assert "archive/refs/tags" not in navigation
    assert "<venv-python> -m agent_mem_bridge doctor" in guides[Path("docs/INTEGRATIONS.md")]
    assert "<venv-python> -m agent_mem_bridge verify" in guides[Path("docs/INTEGRATIONS.md")]


def test_release_install_tool_count_tracks_the_release_cut() -> None:
    assert release_install_tool_count("0.26.1") == 13
    assert release_install_tool_count("0.27.0") == 17
    assert release_install_tool_count("0.27.1") == 17


def test_doctor_and_verify_do_not_claim_external_client_configuration_loaded() -> None:
    doctor_output = render_report(
        {
            "ok": True,
            "checks": [
                {
                    "name": "mcp_modern_stdio",
                    "status": "pass",
                    "detail": "Modern server/discover probe passed.",
                }
            ],
        }
    )
    verify_output = render_verify_success_message({"ok": True})

    for output in (doctor_output, verify_output):
        lowered = output.casefold()
        assert "external client" not in lowered
        assert "loaded mcp config" not in lowered
        assert "client configuration loaded" not in lowered


def test_onboarding_contract_requires_source_checkout_wording_for_version_mismatch(tmp_path: Path) -> None:
    source_version = "0.29.0"
    (tmp_path / "pyproject.toml").write_text(f'[project]\nversion = "{source_version}"\n', encoding="utf-8")
    for path in onboarding_contract.VERSIONED_INSTALL_GUIDES:
        (tmp_path / path).write_text(
            f"{RELEASE_INSTALL_GATE_NOTE}\n"
            f"<venv-python> -m pip install agent-memory-bridge=={source_version}\n"
            "<venv-python> -m pip install -e .\n"
            "17 public MCP tools\nschema v12\n",
            encoding="utf-8",
        )
    (tmp_path / "llms-install.md").write_text(
        "canonical agent-readable install and first-use guide: INSTALL_FOR_AGENTS.md\n", encoding="utf-8"
    )
    (tmp_path / "llms.txt").write_text(
        "Map: INSTALL_FOR_AGENTS.md docs/INTEGRATIONS.md docs/SEMANTIC-MEMORY-POLICY.md\n", encoding="utf-8"
    )

    report = onboarding_contract._versioned_install_tool_surface_check(tmp_path)

    assert report["ok"] is True
    assert report["source_version"] == source_version


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
    (tmp_path / "docs" / "INTEGRATIONS.md").write_text("ok\n", encoding="utf-8")
    (tmp_path / "docs" / "SEMANTIC-MEMORY-POLICY.md").write_text("ok\n", encoding="utf-8")
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
    (tmp_path / "docs" / "INTEGRATIONS.md").write_text("ok\n", encoding="utf-8")
    (tmp_path / "docs" / "SEMANTIC-MEMORY-POLICY.md").write_text("ok\n", encoding="utf-8")
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
    (tmp_path / "docs" / "INTEGRATIONS.md").write_text("ok\n", encoding="utf-8")
    (tmp_path / "docs" / "SEMANTIC-MEMORY-POLICY.md").write_text("ok\n", encoding="utf-8")
    (tmp_path / "examples" / "README.md").write_text("ok\n", encoding="utf-8")

    report = run_onboarding_contract_check(tmp_path)

    assert report["ok"] is False
    docs_check = next(check for check in report["checks"] if check["name"] == "onboarding_docs_stay_placeholder_safe")
    assert any(violation["path"] == "Dockerfile" for violation in docs_check["violations"])
