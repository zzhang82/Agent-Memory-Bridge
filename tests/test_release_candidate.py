from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CURRENT = "0.32.0"


def test_current_package_and_source_docs_use_v032_identity() -> None:
    package_version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    assert package_version == CURRENT
    assert "Current source version: `0.32.0`" in (ROOT / "README.md").read_text(encoding="utf-8")
    assert "当前源码版本：`0.32.0`" in (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
    assert "Published releases: see [GitHub Releases]" in (ROOT / "README.md").read_text(encoding="utf-8")
    assert "已发布版本：请见 [GitHub Releases]" in (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
    assert "| Package/source version | `0.32.0` |" in (ROOT / "docs/PRODUCTION-STATUS.md").read_text(encoding="utf-8")


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
    assert "v0.28.0 candidate" not in changelog


def test_current_quick_start_does_not_advertise_hidden_first_run_controls() -> None:
    for name in ("README.md", "README.zh-CN.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        quick_start = text.split("## Integrations" if name == "README.md" else "## 集成", 1)[0]
        assert "first-run --client" not in quick_start
        assert "first-run --example" not in quick_start
        assert "first-run --namespace" in quick_start


def test_project_learning_promotion_docs_stay_human_and_truthful() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    readme_zh = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
    agents = (ROOT / "INSTALL_FOR_AGENTS.md").read_text(encoding="utf-8")
    llms_install = (ROOT / "llms-install.md").read_text(encoding="utf-8")
    status = (ROOT / "docs/PRODUCTION-STATUS.md").read_text(encoding="utf-8")
    english_quick = readme.split("## Quick Start", 1)[1].split("## Integrations", 1)[0]
    chinese_quick = readme_zh.split("## 快速开始", 1)[1].split("## 集成", 1)[0]

    assert "Code tells AMB WHAT the project is." in english_quick
    assert "Conversations teach AMB WHY it is that way." in english_quick
    assert "conceptual view, not verbatim CLI output" in english_quick
    assert "代码告诉 AMB 项目“是什么”（WHAT）" in chinese_quick
    assert "对话告诉 AMB 项目“为什么这样”（WHY）" in chinese_quick
    assert "record_type: decision" not in english_quick
    assert "record_type: constraint" not in english_quick
    assert "record_type: decision" not in chinese_quick
    assert "not the modern Project Learning entrypoint" in english_quick
    assert "不是现代 Project Learning 的入口" in chinese_quick
    assert "Refresh is not automatic." in english_quick
    assert "刷新不是自动发生的" in chinese_quick
    assert "bootstrap-repo . \\\n  --namespace project:<name>" in english_quick
    assert "agent_mem_bridge project init" in english_quick
    assert "does not automatically learn decisions" in english_quick
    assert "CLI-only, not MCP tool #18" in english_quick
    assert "不是 MCP 工具 #18" in chinese_quick
    assert "schema v12" in readme
    assert "17 public MCP tools" in readme
    assert "Exactly 17 public MCP tools" in status
    assert "pip install agent-memory-bridge==0.32.0" in english_quick
    assert "there is no `pip install agent-memory-bridge==0.32.0` route" in english_quick
    assert "automatically remembers repository decisions" not in readme.casefold()
    assert "automatic learning" not in english_quick.casefold()
    assert "record_type: decision" in agents
    assert "existing public MCP `store` contract" in agents
    assert "Do not silently infer a durable decision" in agents
    assert "record_type: decision" in llms_install
    assert "not the modern Project Learning" in llms_install
    assert "not required before bootstrap" in agents

    for quick_start in (english_quick, chinese_quick):
        after_venv = quick_start.split("python -m venv .amb-venv", 1)[1]
        assert not any(line.strip().startswith("agent-memory-bridge ") for line in after_venv.splitlines())
        step1 = quick_start.split("### 1.", 1)[1].split("### 2.", 1)[0]
        assert "doctor" not in step1
        assert "verify" not in step1
        assert "agent_mem_bridge project init" in quick_start.split("### 2.", 1)[1]
        assert "agent_mem_bridge bootstrap-repo" in quick_start.split("### 2.", 1)[1]
        assert quick_start.index("agent_mem_bridge project init") < quick_start.index("agent_mem_bridge bootstrap-repo")
        assert quick_start.index("agent_mem_bridge bootstrap-repo") < quick_start.index("agent_mem_bridge doctor")
        assert "agent_mem_bridge verify" in quick_start.split("### 2.", 1)[1]


def test_install_guides_use_publication_invariant_routes() -> None:
    for name in ("INSTALL_FOR_AGENTS.md", "llms-install.md", "llms.txt", "docs/INTEGRATIONS.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "0.30.0" in text
        assert "GitHub Releases" in text
        assert "https://github.com/zzhang82/Agent-Memory-Bridge/archive/refs/tags/v0.30.0.zip" in text
    assert "v0.27.0" in (ROOT / "INSTALL_FOR_AGENTS.md").read_text(encoding="utf-8")


def test_current_docs_record_v032_source_without_hypothetical_wording() -> None:
    docs = (
        "README.md",
        "README.zh-CN.md",
        "INSTALL_FOR_AGENTS.md",
        "llms-install.md",
        "llms.txt",
        "docs/INTEGRATIONS.md",
    )
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
        assert "current unreleased source" not in text.casefold()
        assert "当前尚未发布的源码" not in text
        assert not any(phrase.casefold() in text.casefold() for phrase in hypothetical_phrases)
    english_readme = (ROOT / "README.md").read_text(encoding="utf-8")
    chinese_readme = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
    english_quick = english_readme.split("## Quick Start", 1)[1].split("## Integrations", 1)[0]
    chinese_quick = chinese_readme.split("## 快速开始", 1)[1].split("## 集成", 1)[0]
    assert "Published releases: see [GitHub Releases]" in english_readme
    assert "已发布版本：请见 [GitHub Releases]" in chinese_readme
    assert "live Releases page" in english_quick
    assert "实时状态" in chinese_quick
    assert "This source is not a published GitHub Release yet" not in english_readme
    assert "此源码尚未作为 GitHub Release 发布" not in chinese_readme
    assert "Current package/source version is `0.32.0`." in (ROOT / "src/agent_mem_bridge/first_run.py").read_text(
        encoding="utf-8"
    )
    assert "archive/refs/tags/v0.28.0.zip" in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")


def test_v032_announcement_is_publication_invariant_and_bounded() -> None:
    announcement = (ROOT / "docs/v0.32.0-announcement.md").read_text(encoding="utf-8")
    assert "Agent Memory Bridge v0.32.0 — Project Learning UX" in announcement
    assert "Code tells AMB what the project is." in announcement
    assert "Conversations teach AMB why it is that way." in announcement
    assert "Repository WHAT refreshed; existing project WHY is unchanged." in announcement
    assert "knowledge-explorer-v1" in announcement
    assert "no MCP tool #18" in announcement
    assert "no `pip install agent-memory-bridge==0.32.0` route" in announcement
    forbidden = (
        "not yet released",
        "release is pending",
        "github release is pending",
        "currently unreleased",
    )
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
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    text_zh = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
    architecture = (ROOT / "docs/ARCHITECTURE.md").read_text(encoding="utf-8")
    status = (ROOT / "docs/PRODUCTION-STATUS.md").read_text(encoding="utf-8")
    boundary = (ROOT / "src/agent_mem_bridge/mcp_boundary.py").read_text(encoding="utf-8")
    compiler = (ROOT / "src/agent_mem_bridge/context_manifest.py").read_text(encoding="utf-8")
    digest = "24c5c52321d61b4b6f647c0d74e2d8304ca68716c403e08a274e9badfd8dc9f8"
    assert "17 public MCP tools" in text
    assert "schema v12" in text
    assert "Exactly 17 public MCP tools" in status
    assert "v12" in status
    assert digest in status
    assert f'PUBLIC_TOOL_SCHEMA_SHA256 = "{digest}"' in boundary
    assert "The Context Compiler accepts four explicit inputs:" in architecture
    assert "Repository Knowledge / WHAT" in architecture
    assert "bounded repository facts supplied as a distinct derived" in compiler
    for readme in (text, text_zh):
        diagram = re.search(r"```mermaid\n(?P<body>.*?)\n```", readme, re.DOTALL)
        assert diagram is not None
        body = diagram.group("body")
        assert "A --> D" not in body
        assert "A[" in body and "--> C[" in body
        assert "C --> E[" in body
        assert "E --> D" in body
        assert "B[" in body and "--> D[Context Compiler]" in body


def test_v0274_compatible_schema12_database_remains_readable(tmp_path: Path, monkeypatch) -> None:
    import sqlite3

    from agent_mem_bridge import schema as schema_module
    from agent_mem_bridge.evidence_inspect import build_memory_inspect_report
    from agent_mem_bridge.first_run import build_first_run_report
    from agent_mem_bridge.schema import CURRENT_SCHEMA_VERSION, exact_content_hash, schema_version
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
