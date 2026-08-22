from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CURRENT = "0.29.0"


def test_current_package_and_source_docs_use_v029_identity() -> None:
    package_version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    assert package_version == CURRENT
    assert "Current source release: `0.29.0`" in (ROOT / "README.md").read_text(encoding="utf-8")
    assert "当前源码发布版本：`0.29.0`" in (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
    assert "| Package/source version | `0.29.0` |" in (ROOT / "docs/PRODUCTION-STATUS.md").read_text(encoding="utf-8")


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
    assert "v0.28.0 candidate" not in changelog


def test_current_quick_start_does_not_advertise_hidden_first_run_controls() -> None:
    for name in ("README.md", "README.zh-CN.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        quick_start = text.split("## Integrations" if name == "README.md" else "## 集成", 1)[0]
        assert "first-run --client" not in quick_start
        assert "first-run --example" not in quick_start
        assert "first-run --namespace" in quick_start


def test_install_guides_use_publication_invariant_routes() -> None:
    for name in ("INSTALL_FOR_AGENTS.md", "llms-install.md", "llms.txt", "docs/INTEGRATIONS.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "0.29.0" in text
        assert "GitHub Releases" in text
        assert (
            "https://github.com/zzhang82/Agent-Memory-Bridge/archive/refs/tags/v0.29.0.zip" in text or "v0.29.0" in text
        )
    assert "v0.27.0" in (ROOT / "INSTALL_FOR_AGENTS.md").read_text(encoding="utf-8")


def test_current_docs_do_not_claim_live_publication() -> None:
    docs = (
        "README.md",
        "README.zh-CN.md",
        "INSTALL_FOR_AGENTS.md",
        "llms-install.md",
        "llms.txt",
        "docs/INTEGRATIONS.md",
    )
    files = (*docs, "src/agent_mem_bridge/first_run.py")
    transient_phrases = (
        "no release tag yet",
        "not yet tagged",
        "not yet tagged or published",
        "尚未创建标签",
        "candidate has no release tag",
        "After v0.29.0 is published",
    )
    for name in files:
        text = (ROOT / name).read_text(encoding="utf-8")
        assert not re.search(r"latest (?:published )?GitHub Release is v0\.29\.0", text, re.IGNORECASE)
        assert not any(phrase in text for phrase in transient_phrases)
    for name in docs:
        assert "GitHub Releases" in (ROOT / name).read_text(encoding="utf-8")
    assert "Current package/source version is `0.29.0`." in (ROOT / "src/agent_mem_bridge/first_run.py").read_text(
        encoding="utf-8"
    )
    assert "archive/refs/tags/v0.28.0.zip" in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")


def test_public_surface_and_schema_facts_remain_stable() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    status = (ROOT / "docs/PRODUCTION-STATUS.md").read_text(encoding="utf-8")
    assert "17 public MCP tools" in text
    assert "schema v12" in text
    assert "Exactly 17 public MCP tools" in status
    assert "v12" in status


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
