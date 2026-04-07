from pathlib import Path

from agent_mem_bridge.archive_snapshot import build_default_snapshot_root, create_profile_archive_snapshot, write_live_source_manifest
from agent_mem_bridge.profile_migration import (
    build_profile_documents,
    compare_profile_migration,
    compare_profile_migration_with_mode,
    import_profile_memory,
    prune_stale_profile_imports,
)
from agent_mem_bridge.storage import MemoryStore


def test_import_profile_memory_imports_only_supported_markdown(tmp_path: Path) -> None:
    cole_root = tmp_path / "Cole"
    (cole_root / "memory" / "core").mkdir(parents=True)
    (cole_root / "memory" / "workflows").mkdir(parents=True)
    (cole_root / "memory" / "executors").mkdir(parents=True)
    (cole_root / "skills" / "obsidian-markdown").mkdir(parents=True)

    (cole_root / "HOW-TO-USE-COLE.md").write_text("# How to Use Cole\n\nBridge first.\n", encoding="utf-8")
    (cole_root / "memory" / "core" / "persona.md").write_text("# Persona\n\nCalm and direct.\n", encoding="utf-8")
    (cole_root / "memory" / "workflows" / "subagent-patterns.md").write_text(
        "# Subagent Patterns\n\nOwn the contract.\n",
        encoding="utf-8",
    )
    (cole_root / "skills" / "obsidian-markdown" / "SKILL.md").write_text(
        "---\nname: obsidian-markdown\ndescription: test\n---\n\n# Obsidian Markdown\n",
        encoding="utf-8",
    )
    (cole_root / "memory" / "executors" / "spawn_worker.py").write_text("print('skip')\n", encoding="utf-8")

    store = MemoryStore(db_path=tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    result = import_profile_memory(store, cole_root)

    assert result["document_count"] == 4
    assert result["comparison"]["missing_count"] == 0

    core_hits = store.recall(namespace="cole-core", query="Calm and direct", limit=10)
    workflow_hits = store.recall(namespace="cole-workflows", query="Own the contract", limit=10)
    skill_hits = store.recall(namespace="cole-skills", query="Obsidian Markdown", limit=10)

    assert core_hits["count"] >= 1
    assert workflow_hits["count"] == 1
    assert skill_hits["count"] == 1


def test_compare_profile_migration_detects_missing_docs(tmp_path: Path) -> None:
    cole_root = tmp_path / "Cole"
    (cole_root / "memory" / "core").mkdir(parents=True)
    persona_path = cole_root / "memory" / "core" / "persona.md"
    persona_path.write_text("# Persona\n\nCalm and direct.\n", encoding="utf-8")

    store = MemoryStore(db_path=tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    documents = build_profile_documents(cole_root)
    assert len(documents) == 1

    comparison_before = compare_profile_migration(store, cole_root)
    assert comparison_before["missing_count"] == 1

    import_profile_memory(store, cole_root)
    comparison_after = compare_profile_migration(store, cole_root)
    assert comparison_after["missing_count"] == 0
    assert comparison_after["content_mismatch_count"] == 0


def test_compare_profile_migration_detects_content_mismatch(tmp_path: Path) -> None:
    cole_root = tmp_path / "Cole"
    (cole_root / "memory" / "core").mkdir(parents=True)
    persona_path = cole_root / "memory" / "core" / "persona.md"
    persona_path.write_text("# Persona\n\nCalm and direct.\n", encoding="utf-8")

    store = MemoryStore(db_path=tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    import_profile_memory(store, cole_root)

    persona_path.write_text("# Persona\n\nCalm, direct, and more explicit.\n", encoding="utf-8")

    comparison = compare_profile_migration(store, cole_root)
    assert comparison["missing_count"] == 0
    assert comparison["content_mismatch_count"] == 1
    assert comparison["content_mismatch_paths"] == ["memory/core/persona.md"]


def test_live_compare_uses_manifest_instead_of_full_source_tree(tmp_path: Path) -> None:
    cole_root = tmp_path / "Cole"
    (cole_root / "memory" / "core").mkdir(parents=True)
    (cole_root / "memory" / "workflows").mkdir(parents=True)
    (cole_root / "memory").mkdir(exist_ok=True)

    (cole_root / "HOW-TO-USE-COLE.md").write_text("# How to Use Cole\n", encoding="utf-8")
    (cole_root / "memory" / ".claude-memory-guard.md").write_text("# Guard\n", encoding="utf-8")
    (cole_root / "memory" / "MEMORY.md").write_text("# Memory\n", encoding="utf-8")
    (cole_root / "memory" / "QUEUE.md").write_text("# Queue\n", encoding="utf-8")
    (cole_root / "memory" / "REDLINE.md").write_text("# Redline\n", encoding="utf-8")
    (cole_root / "memory" / "core" / "core.md").write_text("# Core\n", encoding="utf-8")
    (cole_root / "memory" / "core" / "decision-making.md").write_text("# Decisions\n", encoding="utf-8")
    (cole_root / "memory" / "core" / "persona.md").write_text("# Persona\n\nCalm and direct.\n", encoding="utf-8")
    workflow_path = cole_root / "memory" / "workflows" / "subagent-patterns.md"
    workflow_path.write_text("# Subagent Patterns\n\nOwn the contract.\n", encoding="utf-8")

    write_live_source_manifest(cole_root, cole_root / "live-source-manifest.json")
    store = MemoryStore(db_path=tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    import_profile_memory(store, cole_root)

    workflow_path.write_text("# Subagent Patterns\n\nChanged after import.\n", encoding="utf-8")

    live_comparison = compare_profile_migration_with_mode(store, cole_root, mode="live")
    full_comparison = compare_profile_migration_with_mode(store, cole_root, mode="full")

    assert live_comparison["content_mismatch_count"] == 0
    assert live_comparison["extra_count"] == 0
    assert live_comparison["out_of_scope_count"] == 1
    assert full_comparison["content_mismatch_count"] == 1
    assert full_comparison["content_mismatch_paths"] == ["memory/workflows/subagent-patterns.md"]


def test_snapshot_audit_compares_against_snapshot_manifest(tmp_path: Path) -> None:
    cole_root = tmp_path / "Cole"
    (cole_root / "memory" / "core").mkdir(parents=True)
    persona_path = cole_root / "memory" / "core" / "persona.md"
    persona_path.write_text("# Persona\n\nCalm and direct.\n", encoding="utf-8")

    snapshot_root = build_default_snapshot_root(cole_root)
    manifest = create_profile_archive_snapshot(
        source_root=cole_root,
        snapshot_root=snapshot_root,
        compare_report={
            "missing_count": 0,
            "extra_count": 0,
            "content_mismatch_count": 0,
            "namespace_mismatch_count": 0,
        },
    )

    store = MemoryStore(db_path=tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    import_profile_memory(store, cole_root)

    persona_path.write_text("# Persona\n\nChanged after snapshot.\n", encoding="utf-8")

    full_comparison = compare_profile_migration_with_mode(store, cole_root, mode="full")
    snapshot_comparison = compare_profile_migration_with_mode(
        store,
        cole_root,
        mode="snapshot-audit",
        snapshot_manifest_path=Path(manifest["manifest_path"]),
    )

    assert full_comparison["content_mismatch_count"] == 1
    assert snapshot_comparison["content_mismatch_count"] == 0
    assert snapshot_comparison["mode"] == "snapshot-audit"
    assert snapshot_comparison["extra_count"] == 0


def test_prune_stale_profile_imports_removes_extra_paths(tmp_path: Path) -> None:
    cole_root = tmp_path / "Cole"
    (cole_root / "memory" / "core").mkdir(parents=True)
    (cole_root / "skills" / "obsidian-markdown").mkdir(parents=True)

    (cole_root / "memory" / "core" / "persona.md").write_text("# Persona\n\nCalm and direct.\n", encoding="utf-8")
    skill_path = cole_root / "skills" / "obsidian-markdown" / "SKILL.md"
    skill_path.write_text("---\nname: obsidian-markdown\ndescription: test\n---\n\n# Obsidian Markdown\n", encoding="utf-8")

    store = MemoryStore(db_path=tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    import_profile_memory(store, cole_root)

    skill_path.unlink()

    before = compare_profile_migration(store, cole_root)
    assert before["extra_paths"] == ["skills/obsidian-markdown/SKILL.md"]

    pruned = prune_stale_profile_imports(store, cole_root)
    after = compare_profile_migration(store, cole_root)

    assert pruned["stale_paths"] == ["skills/obsidian-markdown/SKILL.md"]
    assert pruned["deleted_row_count"] == 1
    assert after["extra_count"] == 0

