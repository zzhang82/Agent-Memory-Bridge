from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent_mem_bridge.log_audit import (
    build_log_audit_report,
    extract_candidates_from_log_text,
    render_log_audit_markdown,
    stage_log_audit_report,
)
from agent_mem_bridge.memory_cli import run_log_audit
from agent_mem_bridge.review_queue import build_review_queue_report
from agent_mem_bridge.storage import MemoryStore


def test_extract_candidates_from_log_text() -> None:
    raw_log = """
2026-11-13T10:00:00Z INFO user: Let us debug the issue
2026-11-13T10:01:00Z DEBUG {"type": "tool_call", "command": "pytest"}
2026-11-13T10:02:00Z ERROR AssertionError: WAL checkpoint timed out
2026-11-13T10:05:00Z INFO gotcha: Call checkpoint_database with TRUNCATE mode before asserting bytes
2026-11-13T10:06:00Z INFO - **decision**: Pin all hook interpreters to python3.11+
2026-11-13T10:07:00Z INFO {"event": "resolution", "claim": "Strict 0600 permissions required on local config file"}
2026-11-13T10:08:00Z INFO gotcha: Call checkpoint_database with TRUNCATE mode before asserting bytes
"""
    items = extract_candidates_from_log_text(
        raw_log,
        source_name="session.log",
        namespace="project:log-test",
        limit=10,
    )

    # 3 unique claims extracted (duplicate gotcha omitted)
    assert len(items) == 3

    claims = [it["candidate"]["claim"] for it in items]
    assert any("TRUNCATE" in c for c in claims)
    assert any("hook interpreters" in c for c in claims)
    assert any("0600 permissions" in c for c in claims)

    for it in items:
        cand = it["candidate"]
        assert cand["schema"] == "memory.candidate.v1"
        assert cand["authority_class"] == "decision"
        assert cand["namespace"] == "project:log-test"
        assert it["evidence_ref"].startswith("log:session.log:")
        assert it["decision"]["decision"] == "needs_review"
        assert it["decision"]["would_write"] is False


def test_log_audit_report_and_hidden_staging(tmp_path: Path) -> None:
    log_file = tmp_path / "debug_session.jsonl"
    log_content = """{"role": "user", "content": "How do we handle token permissions?"}
{"role": "assistant", "content": "Here is the finding:\\ngotcha: Config file requires strict 0600 POSIX permissions or HTTP server rejects it."}
{"role": "assistant", "content": "Also note:\\ndecision: Use SQLite WAL mode as the single durable authority."}
"""
    log_file.write_text(log_content, encoding="utf-8")

    db_path = tmp_path / "bridge.db"
    store = MemoryStore(db_path, log_dir=tmp_path / "logs")

    # 1. Dry run report: does NOT mutate database
    dry_report = build_log_audit_report(
        log_file,
        store,
        namespace="project:log-audit",
        limit=5,
    )
    assert dry_report["schema"] == "memory.log_audit.v1"
    assert dry_report["candidate_count"] == 2
    assert dry_report["stage"] is False
    assert dry_report["staged_count"] == 0

    rendered = render_log_audit_markdown(dry_report)
    assert "AMB Session Log Audit" in rendered
    assert "mode: `shadow / preview`" in rendered

    # Verify no memory written to database
    recalled = store.recall(namespace="project:log-audit", limit=10)
    assert recalled["count"] == 0

    # 2. Stage into review queue
    staged_report = stage_log_audit_report(store, dry_report)
    assert staged_report["stage"] is True
    assert staged_report["staged_count"] == 2
    assert all(res["disposition"] == "stored" for res in staged_report["stage_results"])

    # CRITICAL: Verify candidate is NOT visible in normal recall or browse
    recall_after = store.recall(namespace="project:log-audit", query="permissions", limit=10)
    assert recall_after["count"] == 0

    browse_after = store.browse(namespace="project:log-audit", limit=10)
    assert browse_after["count"] == 0

    # CRITICAL: Verify candidate IS visible in review queue as needs_review
    rq = build_review_queue_report(store, namespace="project:log-audit", limit=10)
    open_candidates = [it for it in rq["items"] if it["item_type"] == "learning_candidate"]
    assert len(open_candidates) == 2
    assert all(c["status"] == "open" for c in open_candidates)
    assert any("0600" in str(c["title"]) for c in open_candidates)


def test_log_audit_cli_runner(tmp_path: Path, monkeypatch, capsys) -> None:
    log_file = tmp_path / "run.log"
    log_file.write_text("INFO lesson: Always verify release contracts before git tag.\n", encoding="utf-8")

    db_path = tmp_path / "bridge.db"
    store = MemoryStore(db_path, log_dir=tmp_path / "logs")
    store.database_epoch()  # initialize database

    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_DB_PATH", str(db_path))
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_HOME", str(tmp_path))

    args = argparse.Namespace(
        log_file=str(log_file),
        namespace="project:cli-log",
        limit=5,
        stage=False,
        format="json",
        json=True,
    )
    rc = run_log_audit(args)
    assert rc == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["schema"] == "memory.log_audit.v1"
    assert data["candidate_count"] == 1
    assert data["items"][0]["candidate"]["claim"] == "Always verify release contracts before git tag."
