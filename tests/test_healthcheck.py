from pathlib import Path

from agent_mem_bridge.archive_snapshot import write_live_source_manifest
from agent_mem_bridge.cole_migration import import_cole_memory
from agent_mem_bridge.healthcheck import run_health_check
from agent_mem_bridge.storage import MemoryStore


def test_run_health_check_reports_ok_for_imported_cole_docs(tmp_path: Path, monkeypatch) -> None:
    cole_root = tmp_path / "Cole"
    (cole_root / "memory" / "core").mkdir(parents=True)
    (cole_root / "memory" / "workflows").mkdir(parents=True)
    (cole_root / "skills" / "obsidian-markdown").mkdir(parents=True)

    (cole_root / "memory" / "core" / "persona.md").write_text(
        "# Persona\n\nsimplicity over feature count reliability over cleverness\n\nIf in doubt, stop and ask\n",
        encoding="utf-8",
    )
    (cole_root / "memory" / "workflows" / "subagent-patterns.md").write_text(
        "# Subagent Orchestration Patterns\n\nOwn the contract.\n",
        encoding="utf-8",
    )
    (cole_root / "skills" / "obsidian-markdown" / "SKILL.md").write_text(
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
    import_cole_memory(store, cole_root)

    report = run_health_check(source_root=cole_root, check_stdio=False)

    assert report["ok"] is True
    assert report["compare"]["missing_count"] == 0
    assert report["compare"]["content_mismatch_count"] == 0
    assert all(item["ok"] for item in report["recall_checks"])
    assert report["watcher_health"]["ok"] is True


def test_run_health_check_auto_uses_live_compare_when_manifest_exists(tmp_path: Path, monkeypatch) -> None:
    cole_root = tmp_path / "Cole"
    (cole_root / "memory" / "core").mkdir(parents=True)
    (cole_root / "memory" / "workflows").mkdir(parents=True)
    (cole_root / "memory").mkdir(exist_ok=True)
    (cole_root / "skills" / "obsidian-markdown").mkdir(parents=True)

    (cole_root / "HOW-TO-USE-COLE.md").write_text("# How to Use Cole\n", encoding="utf-8")
    (cole_root / "memory" / ".claude-memory-guard.md").write_text("# Guard\n", encoding="utf-8")
    (cole_root / "memory" / "MEMORY.md").write_text("# Memory\n", encoding="utf-8")
    (cole_root / "memory" / "QUEUE.md").write_text("# Queue\n", encoding="utf-8")
    (cole_root / "memory" / "REDLINE.md").write_text("# Redline\n", encoding="utf-8")
    (cole_root / "memory" / "core" / "core.md").write_text("# Core\n", encoding="utf-8")
    (cole_root / "memory" / "core" / "decision-making.md").write_text("# Decisions\n", encoding="utf-8")
    (cole_root / "memory" / "core" / "persona.md").write_text(
        "# Persona\n\nsimplicity over feature count reliability over cleverness\n\nIf in doubt, stop and ask\n",
        encoding="utf-8",
    )
    (cole_root / "memory" / "workflows" / "subagent-patterns.md").write_text(
        "# Subagent Orchestration Patterns\n\nOwn the contract.\n",
        encoding="utf-8",
    )
    (cole_root / "skills" / "obsidian-markdown" / "SKILL.md").write_text(
        "# Obsidian Flavored Markdown Skill\n\nUse when working with Obsidian.\n",
        encoding="utf-8",
    )
    write_live_source_manifest(cole_root, cole_root / "live-source-manifest.json")

    bridge_home = tmp_path / "bridge-home"
    db_path = bridge_home / "bridge.db"
    log_dir = bridge_home / "logs"
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_HOME", str(bridge_home))
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_DB_PATH", str(db_path))
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_LOG_DIR", str(log_dir))
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_SESSIONS_ROOT", str(tmp_path / "sessions"))
    (tmp_path / "sessions").mkdir()

    store = MemoryStore(db_path=db_path, log_dir=log_dir)
    import_cole_memory(store, cole_root)

    report = run_health_check(source_root=cole_root, check_stdio=False)

    assert report["ok"] is True
    assert report["resolved_compare_mode"] == "live"
    assert report["watcher_health"]["ok"] is True

