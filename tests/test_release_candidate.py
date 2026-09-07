from __future__ import annotations

import shutil
import tomllib
from pathlib import Path

import agent_mem_bridge.release_contract as release_contract
from agent_mem_bridge.current_release_contract import run_current_source_release_contract_check
from agent_mem_bridge.mcp_boundary import PUBLIC_TOOL_ORDER, PUBLIC_TOOL_SCHEMA_SHA256
from agent_mem_bridge.schema import CURRENT_SCHEMA_VERSION

ROOT = Path(__file__).resolve().parents[1]
CURRENT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
PUBLIC_TOOL_DIGEST = "24c5c52321d61b4b6f647c0d74e2d8304ca68716c403e08a274e9badfd8dc9f8"


def _copy_release_authorities(destination: Path) -> Path:
    """Copy only the authorities used by the current-source identity check."""

    (destination / "docs").mkdir()
    shutil.copy2(ROOT / "pyproject.toml", destination / "pyproject.toml")
    shutil.copy2(ROOT / "docs" / "PRODUCTION-STATUS.md", destination / "docs" / "PRODUCTION-STATUS.md")
    version = tomllib.loads((destination / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    shutil.copy2(ROOT / "docs" / f"v{version}-announcement.md", destination / "docs" / f"v{version}-announcement.md")
    return destination


def test_current_source_contract_ignores_readme_presentation(tmp_path: Path, monkeypatch) -> None:
    """README copy and heading changes are presentation, not release authority."""

    root = _copy_release_authorities(tmp_path)
    monkeypatch.setattr(
        release_contract, "run_release_contract_check", lambda *_args, **_kwargs: {"checks": [], "ok": True}
    )
    monkeypatch.setattr(
        release_contract,
        "build_v027_episode_release_check",
        lambda *_args, **_kwargs: {"name": "historical", "ok": True},
    )
    (root / "README.md").write_text("# A different headline\n\n## Start here\n", encoding="utf-8")

    report = run_current_source_release_contract_check(root)

    assert report["ok"] is True
    assert not any(
        check["name"]
        in {
            "pyproject_version_matches_readmes",
            "public_mcp_tool_count_matches_server_surface",
        }
        for check in report["checks"]
    )


def test_current_source_contract_rejects_version_identity_drift(tmp_path: Path, monkeypatch) -> None:
    root = _copy_release_authorities(tmp_path)
    monkeypatch.setattr(
        release_contract, "run_release_contract_check", lambda *_args, **_kwargs: {"checks": [], "ok": True}
    )
    monkeypatch.setattr(
        release_contract,
        "build_v027_episode_release_check",
        lambda *_args, **_kwargs: {"name": "historical", "ok": True},
    )
    status = root / "docs" / "PRODUCTION-STATUS.md"
    status.write_text(status.read_text(encoding="utf-8").replace(f"`{CURRENT}`", "`0.32.3`", 1), encoding="utf-8")

    report = run_current_source_release_contract_check(root)

    assert report["ok"] is False
    assert any(
        check["name"] == "current_source_identity_matches_status_and_announcement" and not check["ok"]
        for check in report["checks"]
    )


def test_current_source_contract_rejects_automatic_learning_claim_drift(tmp_path: Path, monkeypatch) -> None:
    root = _copy_release_authorities(tmp_path)
    monkeypatch.setattr(
        release_contract, "run_release_contract_check", lambda *_args, **_kwargs: {"checks": [], "ok": True}
    )
    monkeypatch.setattr(
        release_contract,
        "build_v027_episode_release_check",
        lambda *_args, **_kwargs: {"name": "historical", "ok": True},
    )
    announcement = root / "docs" / f"v{CURRENT}-announcement.md"
    announcement.write_text(
        announcement.read_text(encoding="utf-8").replace("no automatic learning", "automatic learning enabled"),
        encoding="utf-8",
    )

    report = run_current_source_release_contract_check(root)

    assert report["ok"] is False
    assert any(
        check["name"] == "current_source_release_notes_match_package_version" and not check["ok"]
        for check in report["checks"]
    )


def test_current_package_and_source_docs_use_published_identity() -> None:
    package_version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    status = (ROOT / "docs" / "PRODUCTION-STATUS.md").read_text(encoding="utf-8")
    announcement = (ROOT / f"docs/v{package_version}-announcement.md").read_text(encoding="utf-8")
    assert package_version == CURRENT
    assert f"| Package/source version | `{package_version}` |" in status
    assert "GitHub Releases" in status
    assert f"agent-memory-bridge=={package_version}" in announcement
    assert "schema remains v12" in announcement
    assert "exactly 17 tools" in announcement
    assert "no MCP tool #18" in announcement
    assert "no automatic learning" in announcement


def test_historical_v0274_evidence_remains_historical() -> None:
    status = (ROOT / "docs/PRODUCTION-STATUS.md").read_text(encoding="utf-8")
    announcement = (ROOT / "docs/v0.27.4-announcement.md").read_text(encoding="utf-8")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "The `v0.27.4` tag identifies the historical source snapshot" in status
    assert "c6e3568a59852c5b589d6aba00b89ab580c228e6" in status
    assert "unpublished source candidate" in announcement
    assert "v0.27.3–v0.27.4" in changelog
    assert "v0.28.0 source/release line" in changelog
    assert "v0.29.0 source/release line" in changelog
    assert "v0.30.0 source/release line" in changelog
    assert "v0.31.0 source/release line" in changelog
    assert "v0.31.1 source/release line" in changelog
    assert "[v0.31.1 announcement](docs/v0.31.1-announcement.md)" in changelog
    assert "v0.32.0 source/release line" in changelog
    assert "[v0.32.0 announcement](docs/v0.32.0-announcement.md)" in changelog
    assert "v0.32.1 source/release line" in changelog
    assert "[v0.32.1 announcement](docs/v0.32.1-announcement.md)" in changelog
    assert "v0.32.2 source/release line" in changelog
    assert "[v0.32.2 announcement](docs/v0.32.2-announcement.md)" in changelog
    assert "v0.28.0 candidate" not in changelog


def test_install_guides_use_publication_invariant_routes() -> None:
    for name in ("INSTALL_FOR_AGENTS.md", "llms-install.md", "llms.txt", "docs/INTEGRATIONS.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "0.30.0" in text
        assert "GitHub Releases" in text
        assert "https://github.com/zzhang82/Agent-Memory-Bridge/archive/refs/tags/v0.30.0.zip" in text
        assert CURRENT in text
    assert "v0.27.0" in (ROOT / "INSTALL_FOR_AGENTS.md").read_text(encoding="utf-8")


def test_current_docs_record_published_source_without_hypothetical_wording() -> None:
    docs = (
        "INSTALL_FOR_AGENTS.md",
        "llms-install.md",
        "llms.txt",
        "docs/INTEGRATIONS.md",
        "docs/PRODUCTION-STATUS.md",
    )
    announcement = (ROOT / f"docs/v{CURRENT}-announcement.md").read_text(encoding="utf-8")
    hypothetical_phrases = (
        "if/when",
        "如果/当",
        "no release tag yet",
        "not yet tagged",
        "not yet tagged or published",
        "not yet released",
        "尚未创建标签",
        "candidate has no release tag",
        "After v0.30.0 is published",
        "This source is not a published GitHub Release yet",
        "此源码尚未作为 GitHub Release 发布",
    )
    for name in docs:
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "GitHub Releases" in text
        assert CURRENT in text
        assert "current unreleased source" not in text.casefold()
        assert "当前尚未发布的源码" not in text
        assert not any(phrase.casefold() in text.casefold() for phrase in hypothetical_phrases)
    assert CURRENT in announcement
    assert "current unreleased source" not in announcement.casefold()
    assert not any(phrase.casefold() in announcement.casefold() for phrase in hypothetical_phrases)
    first_run = (ROOT / "src/agent_mem_bridge/first_run.py").read_text(encoding="utf-8")
    assert 'RELEASE_INSTALL_GATE_NOTE = f"Current package/source version is `{RELEASE_VERSION}`."' in first_run
    assert "archive/refs/tags/v0.28.0.zip" in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")


def test_v032_release_announcements_are_publication_invariant_and_bounded() -> None:
    v0320 = (ROOT / "docs/v0.32.0-announcement.md").read_text(encoding="utf-8")
    assert "Agent Memory Bridge v0.32.0 — Project Learning UX" in v0320
    assert "Code tells AMB what the project is." in v0320
    assert "Conversations teach AMB why it is that way." in v0320
    assert "Repository WHAT refreshed; existing project WHY is unchanged." in v0320
    assert "knowledge-explorer-v1" in v0320
    assert "no MCP tool #18" in v0320
    assert "no `pip install agent-memory-bridge==0.32.0` route" in v0320

    v0321 = (ROOT / "docs/v0.32.1-announcement.md").read_text(encoding="utf-8")
    assert "Agent Memory Bridge v0.32.1 — PyPI Distribution" in v0321
    assert "pip install agent-memory-bridge==0.32.1" in v0321
    assert "PyPI Trusted Publishing" in v0321
    assert "GitHub OIDC" in v0321
    assert "Durable schema remains v12" in v0321
    assert "Public MCP surface remains exactly 17 tools" in v0321
    assert "no MCP tool #18" in v0321
    assert "no automatic learning" in v0321
    assert "v0.32.0 release notes remain historical and unchanged" in v0321

    current = (ROOT / f"docs/v{CURRENT}-announcement.md").read_text(encoding="utf-8")
    assert f"Agent Memory Bridge v{CURRENT}" in current
    assert f"pip install agent-memory-bridge=={CURRENT}" in current
    assert "Installing the package does not automatically connect every coding agent" in current
    assert "same persistent `AGENT_MEMORY_BRIDGE_HOME`" in current
    assert "PyPI Trusted Publishing" in current
    assert "GitHub OIDC" in current
    assert "schema remains v12" in current
    assert "exactly 17 tools" in current
    assert "no MCP tool #18" in current
    assert "no automatic learning" in current

    forbidden = (
        "not yet released",
        "release is pending",
        "github release is pending",
        "currently unreleased",
    )
    for announcement in (v0320, v0321, current):
        assert not any(phrase in announcement.casefold() for phrase in forbidden)


def test_project_knowledge_identity_documentation_matches_clone_isolation() -> None:
    text = (ROOT / "docs/PROJECT-KNOWLEDGE-ACTIVATION.md").read_text(encoding="utf-8")
    assert "local_repository_source_id" in text
    assert "two clones or worktrees of the same logical remote have distinct local source IDs" in text
    assert "Moving a local clone changes its local source identity" in text
    assert (
        "Multiple clones sharing a remote identity intentionally resolve to one logical local project source"
        not in text
    )


def test_changelog_durable_references_exist() -> None:
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "docs/REPOSITORY-BOOTSTRAP.md" not in changelog
    for relative in ("docs/ARCHITECTURE.md", "docs/PRODUCTION-STATUS.md"):
        assert (ROOT / relative).is_file()


def test_public_surface_and_schema_facts_remain_stable() -> None:
    status = (ROOT / "docs/PRODUCTION-STATUS.md").read_text(encoding="utf-8")
    architecture = (ROOT / "docs/ARCHITECTURE.md").read_text(encoding="utf-8")
    boundary = (ROOT / "src/agent_mem_bridge/mcp_boundary.py").read_text(encoding="utf-8")
    compiler = (ROOT / "src/agent_mem_bridge/context_manifest.py").read_text(encoding="utf-8")
    assert CURRENT_SCHEMA_VERSION == 12
    assert len(PUBLIC_TOOL_ORDER) == 17
    assert PUBLIC_TOOL_SCHEMA_SHA256 == PUBLIC_TOOL_DIGEST
    assert "Exactly 17 public MCP tools" in status
    assert "v12" in status
    assert PUBLIC_TOOL_DIGEST in status
    assert f'PUBLIC_TOOL_SCHEMA_SHA256 = "{PUBLIC_TOOL_DIGEST}"' in boundary
    assert "The Context Compiler accepts four explicit inputs:" in architecture
    assert "Repository Knowledge / WHAT" in architecture
    assert "bounded repository facts supplied as a distinct derived" in compiler


def test_v0274_compatible_schema12_database_remains_readable(tmp_path: Path, monkeypatch) -> None:
    import sqlite3

    from agent_mem_bridge import schema as schema_module
    from agent_mem_bridge.evidence_inspect import build_memory_inspect_report
    from agent_mem_bridge.first_run import build_first_run_report
    from agent_mem_bridge.schema import exact_content_hash, schema_version
    from agent_mem_bridge.storage import MemoryStore

    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_HOME", str(tmp_path / "bridge-home"))
    db_path = tmp_path / "legacy-v0274.db"
    legacy_content = "Schema v7 durable memory remains byte-for-byte stable."
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        # v0.27.4 already used schema v12; v7→v12 migration behavior is covered by
        # the existing test_v027_episode_schema migration suite.
        for raw_migration in schema_module.MIGRATIONS[:12]:
            migration = schema_module._coerce_schema_migration(raw_migration)
            migration.apply(conn)
            conn.execute(f"PRAGMA user_version = {migration.version}")
        conn.execute(
            """
            INSERT INTO memories (
                id, namespace, kind, title, content, tags_json, content_hash,
                exact_content_hash, created_at
            ) VALUES (?, ?, 'memory', ?, ?, '[]', ?, ?, ?)
            """,
            (
                "legacy-memory",
                "project:bridge",
                "Legacy evidence",
                legacy_content,
                "semantic-hash",
                exact_content_hash(legacy_content),
                "2026-07-30T12:00:00+00:00",
            ),
        )
        legacy_columns = {row[1] for row in conn.execute("PRAGMA table_info(memories)").fetchall()}
        legacy_defaults = {
            "is_learning_candidate": 0,
            "lineage_status": "intact",
            "lineage_issues_json": "[]",
            "_insertion_sequence": 1,
        }
        updates = {column: value for column, value in legacy_defaults.items() if column in legacy_columns}
        if updates:
            assignments = ", ".join(f"{column} = ?" for column in updates)
            conn.execute(
                f"UPDATE memories SET {assignments} WHERE id = 'legacy-memory'",
                tuple(updates.values()),
            )
        conn.commit()

    store = MemoryStore(db_path, log_dir=tmp_path / "logs")
    with store._connect() as conn:
        assert schema_version(conn) == CURRENT_SCHEMA_VERSION == 12
        assert conn.execute("SELECT content FROM memories WHERE id = 'legacy-memory'").fetchone()[0] == legacy_content

    recall = store.recall("project:bridge", query="byte-for-byte stable", limit=5)
    assert any(item["id"] == "legacy-memory" for item in recall["items"])
    first_run = build_first_run_report(
        store,
        client="generic",
        namespace="project:bridge",
        query="byte-for-byte stable",
        python_path=None,
        cwd=None,
        bridge_home=None,
        config_path=None,
    )
    assert first_run["schema"] == "memory.first_run.v2"
    inspect = build_memory_inspect_report(store, namespace="project:bridge", query="byte-for-byte stable")
    assert inspect["schema"] == "memory.inspect.v1"
    assert inspect["namespace"] == "project:bridge"
    assert inspect["query"] == "byte-for-byte stable"
