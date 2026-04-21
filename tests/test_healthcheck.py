from pathlib import Path

from agent_mem_bridge.archive_snapshot import write_live_source_manifest
from agent_mem_bridge.profile_migration import import_profile_memory
from agent_mem_bridge.healthcheck import run_health_check
from agent_mem_bridge.storage import MemoryStore


def test_run_health_check_reports_ok_for_imported_profile_docs(tmp_path: Path, monkeypatch) -> None:
    profile_root = tmp_path / "Profile"
    (profile_root / "memory" / "core").mkdir(parents=True)
    (profile_root / "memory" / "workflows").mkdir(parents=True)
    (profile_root / "skills" / "obsidian-markdown").mkdir(parents=True)

    (profile_root / "memory" / "core" / "persona.md").write_text(
        "# Persona\n\nsimplicity over feature count reliability over cleverness\n\nIf in doubt, stop and ask\n",
        encoding="utf-8",
    )
    (profile_root / "memory" / "workflows" / "subagent-patterns.md").write_text(
        "# Subagent Orchestration Patterns\n\nOwn the contract.\n",
        encoding="utf-8",
    )
    (profile_root / "skills" / "obsidian-markdown" / "SKILL.md").write_text(
        "# Obsidian Flavored Markdown Skill\n\nUse when working with Obsidian.\n",
        encoding="utf-8",
    )

    bridge_home = tmp_path / "bridge-home"
    db_path = bridge_home / "bridge.db"
    log_dir = bridge_home / "logs"
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_HOME", str(bridge_home))
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_DB_PATH", str(db_path))
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_LOG_DIR", str(log_dir))
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_SESSIONS_ROOT", str(tmp_path / "sessions"))
    (tmp_path / "sessions").mkdir()

    store = MemoryStore(db_path=db_path, log_dir=log_dir)
    import_profile_memory(store, profile_root)

    report = run_health_check(source_root=profile_root, check_stdio=False)

    assert report["ok"] is True
    assert report["compare"]["missing_count"] == 0
    assert report["compare"]["content_mismatch_count"] == 0
    assert all(item["ok"] for item in report["recall_checks"])
    assert report["relation_metadata_smoke"]["ok"] is True
    assert report["watcher_health"]["ok"] is True


def test_run_health_check_auto_uses_live_compare_when_manifest_exists(tmp_path: Path, monkeypatch) -> None:
    profile_root = tmp_path / "Profile"
    (profile_root / "memory" / "core").mkdir(parents=True)
    (profile_root / "memory" / "workflows").mkdir(parents=True)
    (profile_root / "memory").mkdir(exist_ok=True)
    (profile_root / "skills" / "obsidian-markdown").mkdir(parents=True)

    (profile_root / "HOW-TO-USE-PROFILE.md").write_text("# How to Use This Profile\n", encoding="utf-8")
    (profile_root / "memory" / ".claude-memory-guard.md").write_text("# Guard\n", encoding="utf-8")
    (profile_root / "memory" / "MEMORY.md").write_text("# Memory\n", encoding="utf-8")
    (profile_root / "memory" / "QUEUE.md").write_text("# Queue\n", encoding="utf-8")
    (profile_root / "memory" / "REDLINE.md").write_text("# Redline\n", encoding="utf-8")
    (profile_root / "memory" / "core" / "core.md").write_text("# Core\n", encoding="utf-8")
    (profile_root / "memory" / "core" / "decision-making.md").write_text("# Decisions\n", encoding="utf-8")
    (profile_root / "memory" / "core" / "persona.md").write_text(
        "# Persona\n\nsimplicity over feature count reliability over cleverness\n\nIf in doubt, stop and ask\n",
        encoding="utf-8",
    )
    (profile_root / "memory" / "workflows" / "subagent-patterns.md").write_text(
        "# Subagent Orchestration Patterns\n\nOwn the contract.\n",
        encoding="utf-8",
    )
    (profile_root / "skills" / "obsidian-markdown" / "SKILL.md").write_text(
        "# Obsidian Flavored Markdown Skill\n\nUse when working with Obsidian.\n",
        encoding="utf-8",
    )
    write_live_source_manifest(profile_root, profile_root / "live-source-manifest.json")

    bridge_home = tmp_path / "bridge-home"
    db_path = bridge_home / "bridge.db"
    log_dir = bridge_home / "logs"
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_HOME", str(bridge_home))
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_DB_PATH", str(db_path))
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_LOG_DIR", str(log_dir))
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_SESSIONS_ROOT", str(tmp_path / "sessions"))
    (tmp_path / "sessions").mkdir()

    store = MemoryStore(db_path=db_path, log_dir=log_dir)
    import_profile_memory(store, profile_root)

    report = run_health_check(source_root=profile_root, check_stdio=False)

    assert report["ok"] is True
    assert report["resolved_compare_mode"] == "live"
    assert all(item["ok"] for item in report["recall_checks"])
    assert report["relation_metadata_smoke"]["ok"] is True
    assert report["watcher_health"]["ok"] is True

