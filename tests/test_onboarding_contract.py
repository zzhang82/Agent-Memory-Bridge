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
from agent_mem_bridge.onboarding_contract import release_install_tool_count, run_onboarding_contract_check


def test_onboarding_contract_repository_passes() -> None:
    root = Path(__file__).resolve().parents[1]
    report = run_onboarding_contract_check(root)
    assert report["ok"] is True, json.dumps(report, indent=2, ensure_ascii=False)

    package_version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    release_install_archive_ref = f"archive/refs/tags/v{PINNED_INSTALL_VERSION}.zip"
    source_archive_ref = f"archive/refs/tags/v{package_version}.zip"
    release_install_tool_count_value = release_install_tool_count(PINNED_INSTALL_VERSION)
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
    assert release_install_archive_ref in GITHUB_ARCHIVE_URL
    if is_version_mismatch:
        assert source_archive_ref not in GITHUB_ARCHIVE_URL
    else:
        assert source_archive_ref in GITHUB_ARCHIVE_URL
    for path in (Path("INSTALL_FOR_AGENTS.md"), Path("llms-install.md"), Path("llms.txt")):
        content = " ".join(guides[path].split())
        assert release_install_archive_ref in content, path
        assert (
            f"The pinned `v{PINNED_INSTALL_VERSION}` release-install route exposes "
            f"`{release_install_tool_count_value}` public MCP tools at client registration." in content
        ), path
        assert RELEASE_INSTALL_GATE_NOTE in content, path
        if is_version_mismatch:
            assert source_archive_ref not in content, path
            assert (
                f"Source `{package_version}` differs from pinned release-install `{PINNED_INSTALL_VERSION}`; "
                "use a source checkout until its exact-commit CI gate passes and its tag is created." in content
            ), path
        else:
            assert f"published `v{PINNED_INSTALL_VERSION}`" not in content.casefold(), path
            assert "candidate" not in content.casefold(), path
            assert "unpublished" not in content.casefold(), path
    assert "<venv-python> -m agent_mem_bridge doctor" in guides[Path("docs/INTEGRATIONS.md")]
    assert "<venv-python> -m agent_mem_bridge verify" in guides[Path("docs/INTEGRATIONS.md")]


def test_release_install_tool_count_tracks_the_release_cut() -> None:
    assert release_install_tool_count("0.26.1") == 13
    assert release_install_tool_count("0.27.0") == 17
    assert release_install_tool_count("0.27.1") == 17


def test_onboarding_contract_requires_source_checkout_wording_for_version_mismatch(tmp_path: Path, monkeypatch) -> None:
    source_version = "0.28.0"
    route_marker = (
        f"The pinned `v{PINNED_INSTALL_VERSION}` release-install route exposes "
        "`17` public MCP tools at client registration."
    )
    mismatch_marker = (
        f"Source `0.28.0` differs from pinned release-install `{PINNED_INSTALL_VERSION}`; "
        "use a source checkout until its "
        "exact-commit CI gate passes and its tag is created."
    )
    (tmp_path / "pyproject.toml").write_text(f'[project]\nversion = "{source_version}"\n', encoding="utf-8")
    for path in onboarding_contract.VERSIONED_INSTALL_GUIDES:
        (tmp_path / path).write_text(
            f"{route_marker}\n{RELEASE_INSTALL_GATE_NOTE}\n{mismatch_marker}\n", encoding="utf-8"
        )

    report = onboarding_contract._versioned_install_tool_surface_check(tmp_path)

    assert report["ok"] is True
    assert report["source_version"] == source_version
    (tmp_path / onboarding_contract.VERSIONED_INSTALL_GUIDES[0]).write_text(
        f"{route_marker}\n{RELEASE_INSTALL_GATE_NOTE}\n", encoding="utf-8"
    )
    report = onboarding_contract._versioned_install_tool_surface_check(tmp_path)
    assert report["ok"] is False
    assert "source checkout" in report["missing"][0]["markers"][0]


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
