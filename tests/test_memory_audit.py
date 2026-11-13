from __future__ import annotations

import json
import subprocess
from pathlib import Path

from agent_mem_bridge.memory_audit import (
    build_memory_audit_report,
    render_memory_audit_markdown,
    stage_memory_audit_report,
)
from agent_mem_bridge.memory_cli import run_memory_audit
from agent_mem_bridge.review_queue import build_review_queue_report
from agent_mem_bridge.storage import MemoryStore


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", *args],
        cwd=root,
        text=True,
        stderr=subprocess.DEVNULL,
    )


def test_memory_audit_clean_repo_no_candidates(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    store.store(
        namespace="project:clean",
        title="Decision without repo dependency",
        content="record_type: decision\nclaim: Keep SQLite as single authority.",
        kind="memory",
    )
    report = build_memory_audit_report(
        store,
        namespace="project:clean",
        repository_root=tmp_path,
        limit=10,
    )
    assert report["schema"] == "memory.offline_audit.v1"
    assert report["namespace"] == "project:clean"
    assert report["scanned_count"] == 1
    assert report["needs_recheck_count"] == 0
    assert report["candidate_count"] == 0
    assert report["stage"] is False
    assert report["staged_count"] == 0

    rendered = render_memory_audit_markdown(report)
    assert "No memory drift or invalid dependencies detected." in rendered


def test_memory_audit_detects_repo_drift_and_stages_hidden_review_candidate(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    config_file = repo / "deploy.json"
    config_file.write_text('{"replica_count": 2}\n', encoding="utf-8")

    _git(repo, "init")
    _git(repo, "config", "user.email", "audit-test@example.com")
    _git(repo, "config", "user.name", "Audit Tester")
    _git(repo, "add", "deploy.json")
    _git(repo, "commit", "-m", "initial deploy config")
    commit_1 = _git(repo, "rev-parse", "HEAD").strip()

    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    stored = store.store(
        namespace="project:audit-drift",
        title="Production replica count",
        content=(f"record_type: decision\ndepends_on_repo: deploy.json@{commit_1}\nclaim: Production runs 2 replicas."),
        kind="memory",
    )
    mem_id = stored["id"]

    # Initial check before drift: no candidates
    clean_report = build_memory_audit_report(
        store,
        namespace="project:audit-drift",
        repository_root=repo,
    )
    assert clean_report["needs_recheck_count"] == 0
    assert clean_report["candidate_count"] == 0

    # Simulate drift: update file in repository
    config_file.write_text('{"replica_count": 4}\n', encoding="utf-8")
    _git(repo, "add", "deploy.json")
    _git(repo, "commit", "-m", "scale to 4 replicas")
    commit_2 = _git(repo, "rev-parse", "HEAD").strip()

    # Dry-run audit report: detects drift, stages nothing
    audit_report = build_memory_audit_report(
        store,
        namespace="project:audit-drift",
        repository_root=repo,
        limit=10,
    )
    assert audit_report["needs_recheck_count"] == 1
    assert audit_report["candidate_count"] == 1
    assert audit_report["stage"] is False
    assert audit_report["staged_count"] == 0

    item = audit_report["items"][0]
    assert item["predecessor_id"] == mem_id
    assert "depends_on_repo:changed:deploy.json" in item["recheck_reasons"]
    candidate = item["candidate"]
    assert candidate["authority_class"] == "decision"
    assert candidate["supersedes_record_ids"] == [mem_id]
    assert commit_2[:7] in candidate["claim"]
    assert candidate["recommended_action"] == "revise"
    assert item["decision"]["decision"] == "needs_review"

    # Verify dry-run markdown
    md = render_memory_audit_markdown(audit_report)
    assert "AMB Offline Memory Audit" in md
    assert "mode: `shadow / dry-run`" in md
    assert f"deploy.json@{commit_2[:7]}" in md

    # Verify normal recall and browse see NO staged candidate
    recall_res = store.recall(namespace="project:audit-drift", query="replicas", limit=10)
    assert len(recall_res["items"]) == 1
    assert recall_res["items"][0]["id"] == mem_id

    browse_res = store.browse(namespace="project:audit-drift", limit=10)
    assert len(browse_res["items"]) == 1
    assert browse_res["items"][0]["id"] == mem_id

    # Now stage the candidate
    staged_report = stage_memory_audit_report(store, audit_report)
    assert staged_report["stage"] is True
    assert staged_report["staged_count"] == 1
    assert staged_report["stage_results"][0]["disposition"] == "stored"

    # CRITICAL: Verify candidate is NOT visible in normal recall or browse
    recall_after = store.recall(namespace="project:audit-drift", query="replicas", limit=10)
    assert len(recall_after["items"]) == 1
    assert recall_after["items"][0]["id"] == mem_id

    browse_after = store.browse(namespace="project:audit-drift", limit=10)
    assert len(browse_after["items"]) == 1
    assert browse_after["items"][0]["id"] == mem_id

    # Verify candidate IS visible in operator review queue
    rq = build_review_queue_report(store, namespace="project:audit-drift", limit=10)
    open_candidates = [it for it in rq["items"] if it["item_type"] == "learning_candidate"]
    assert len(open_candidates) == 1
    assert open_candidates[0]["status"] == "open"
    assert commit_2[:7] in str(open_candidates[0]["title"])

    # Re-staging the same candidate is a duplicate, not an error
    dup_report = stage_memory_audit_report(store, audit_report)
    assert dup_report["staged_count"] == 0
    assert dup_report["stage_results"][0]["disposition"] == "duplicate"


def test_memory_audit_detects_superseded_memory_dependency(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")

    # Target dependency
    dep_orig = store.store(
        namespace="project:dep-test",
        title="Base database policy",
        content="record_type: decision\nclaim: SQLite only.",
        kind="memory",
    )

    # Dependent memory
    consumer = store.store(
        namespace="project:dep-test",
        title="Cache policy",
        content=f"record_type: decision\ndepends_on: {dep_orig['id']}\nclaim: In-memory cache over base database.",
        kind="memory",
    )
    assert consumer["id"]

    # Revise the target dependency
    store.revise(
        dep_orig["id"],
        replacement_content="record_type: decision\nclaim: SQLite with WAL mode.",
        title="Base database policy v2",
    )

    report = build_memory_audit_report(
        store,
        namespace="project:dep-test",
        repository_root=tmp_path,
        limit=10,
    )
    # The revised original memory reports depends_on:superseded
    assert report["needs_recheck_count"] >= 1
    reasons = [r for item in report["items"] for r in item["recheck_reasons"]]
    assert any("depends_on:superseded" in r for r in reasons)


def test_memory_audit_cli_runner(tmp_path: Path, monkeypatch, capsys) -> None:
    import argparse

    db_path = tmp_path / "bridge.db"
    store = MemoryStore(db_path, log_dir=tmp_path / "logs")
    store.store(
        namespace="project:cli-test",
        title="Simple decision",
        content="record_type: decision\nclaim: Simple claim.",
        kind="memory",
    )

    monkeypatch.setenv("AMB_DB_PATH", str(db_path))
    monkeypatch.chdir(tmp_path)

    args = argparse.Namespace(
        namespace="project:cli-test",
        path=tmp_path,
        limit=5,
        stage=False,
        format="json",
        json=True,
    )
    rc = run_memory_audit(args)
    assert rc == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["schema"] == "memory.offline_audit.v1"
    assert data["namespace"] == "project:cli-test"


def test_memory_audit_dirty_checkout_does_not_stage_candidates(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    config_file = repo / "deploy.json"
    config_file.write_text('{"replica_count": 2}\n', encoding="utf-8")

    _git(repo, "init")
    _git(repo, "config", "user.email", "audit-test@example.com")
    _git(repo, "config", "user.name", "Audit Tester")
    _git(repo, "add", "deploy.json")
    _git(repo, "commit", "-m", "initial deploy config")
    commit_1 = _git(repo, "rev-parse", "HEAD").strip()

    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    store.store(
        namespace="project:audit-dirty",
        title="Production replica count",
        content=(f"record_type: decision\ndepends_on_repo: deploy.json@{commit_1}\nclaim: Production runs 2 replicas."),
        kind="memory",
    )

    # Modify file without committing (dirty working tree)
    config_file.write_text('{"replica_count": 999}\n', encoding="utf-8")

    report = build_memory_audit_report(
        store,
        namespace="project:audit-dirty",
        repository_root=repo,
    )
    # Dirty repo causes recheck_status to be 'unknown', NOT 'needs_recheck'
    assert report["needs_recheck_count"] == 0
    assert report["candidate_count"] == 0


def test_memory_audit_detects_missing_dependency_target(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    missing_id = "20261113000000000000-deadbeef"
    store.store(
        namespace="project:missing-dep",
        title="Orphan dependent",
        content=f"record_type: decision\ndepends_on: {missing_id}\nclaim: Relies on missing parent.",
        kind="memory",
    )

    report = build_memory_audit_report(
        store,
        namespace="project:missing-dep",
        repository_root=tmp_path,
        limit=10,
    )
    assert report["needs_recheck_count"] == 1
    reasons = report["items"][0]["recheck_reasons"]
    assert any(f"depends_on:missing:{missing_id}" in r for r in reasons)
