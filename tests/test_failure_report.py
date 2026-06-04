from pathlib import Path

from agent_mem_bridge.failure_report import build_failure_report
from agent_mem_bridge.storage import MemoryStore


def test_failure_report_aggregates_gotcha_and_procedure_failure_modes(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    store.store(
        namespace="project:alpha",
        kind="memory",
        title="[[Gotcha]] release proof skipped",
        content=(
            "record_type: gotcha\n"
            "claim: Skipping release proof causes stale release notes.\n"
            "trigger: Release is tagged before proof checks run.\n"
            "symptom: README and release contract drift.\n"
            "fix: Run release contract before tagging.\n"
            "confidence: validated\n"
        ),
        tags=["kind:gotcha", "domain:release", "topic:proof"],
    )
    store.store(
        namespace="project:alpha",
        kind="memory",
        title="[[Procedure]] release proof",
        content=(
            "record_type: procedure\n"
            "goal: Run release proof.\n"
            "steps: run pytest | run release contract\n"
            "failure_mode: Tagging without proof ships stale docs.\n"
            "rollback_path: delete tag | rerun proof\n"
            "procedure_status: validated\n"
        ),
        tags=["kind:procedure", "domain:release", "topic:proof"],
    )

    report = build_failure_report(
        store,
        query="release proof",
        project_namespace="project:alpha",
        global_namespace="global",
    )

    assert report["schema"] == "memory.failure_report.v1"
    assert report["writeback_boundary"] == "read_only_no_writeback"
    assert report["source_counts"]["gotchas"] == 1
    assert report["source_counts"]["procedures"] == 1
    sources = {item["source"] for item in report["failure_modes"]}
    assert sources == {"gotcha", "procedure"}
    assert any(item["fix"] == "Run release contract before tagging." for item in report["failure_modes"])
    assert any(item["rollback_path"] == "delete tag | rerun proof" for item in report["failure_modes"])


def test_failure_report_reuses_task_memory_suppression_without_writing(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    store.store(
        namespace="project:alpha",
        kind="memory",
        title="[[Procedure]] unsafe release shortcut",
        content=(
            "record_type: procedure\n"
            "goal: Release with unsafe shortcut.\n"
            "steps: skip proof | tag release\n"
            "failure_mode: Unsafe shortcut bypasses proof.\n"
            "procedure_status: unsafe\n"
        ),
        tags=["kind:procedure", "domain:release", "topic:proof"],
    )
    before = store.stats("project:alpha")["total_count"]

    report = build_failure_report(
        store,
        query="release proof",
        project_namespace="project:alpha",
        global_namespace="global",
    )

    after = store.stats("project:alpha")["total_count"]
    assert before == after
    assert "unsafe-procedure-suppressed" in report["warnings"]
    assert any(item["reason"] == "procedure_status:unsafe" for item in report["suppressed_items"])
