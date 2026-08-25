#!/usr/bin/env python3
"""Run the v0.32 artifact-first Project Learning UX release proof."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import v031_release_smoke as base

NAMESPACE = "project:fixture"
DECISION_TITLE = "Keep the disposable project local-first"
DECISION_CONTENT = "\n".join(
    (
        "record_type: decision",
        "claim: Do not introduce Redis.",
        "reason: This disposable project is intentionally local-first and single-node.",
        f"scope: {NAMESPACE}",
        "confidence: observed",
    )
)
CONSTRAINT_TITLE = "Keep repository WHAT rebuildable"
CONSTRAINT_CONTENT = "\n".join(
    (
        "record_type: constraint",
        "claim: Repository-derived WHAT must remain rebuildable.",
        "reason: Repository facts must not become human decision authority.",
        f"scope: {NAMESPACE}",
        "confidence: observed",
    )
)
QUERY = "Should this project add Redis?"
REPORT_SCHEMA = "agent-memory-bridge.v032-clean-room-release-proof.v1"


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise base.ProofFailure(reason)


def _run(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    label: str,
    input_text: str | None = None,
    expected_status: int = 0,
    timeout: int = 180,
) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            input=input_text,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise base.ProofFailure(f"{label} could not complete ({type(exc).__name__})") from None
    _require(completed.returncode == expected_status, f"{label} exited with status {completed.returncode}")
    return completed


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    if not root.exists():
        return digest.hexdigest()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _db_rows(db_path: Path) -> dict[str, Any]:
    if not db_path.exists():
        return {"items": [], "total_durable_memory_rows": 0, "learning_candidates": 0, "feedback": 0}
    with sqlite3.connect(db_path) as conn:
        items = conn.execute(
            "SELECT id, title, content FROM memories WHERE namespace = ? ORDER BY id",
            (NAMESPACE,),
        ).fetchall()
        total_durable_memory_rows = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        learning = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE namespace = ? AND is_learning_candidate = 1",
            (NAMESPACE,),
        ).fetchone()[0]
        feedback = conn.execute("SELECT COUNT(*) FROM retrieval_feedback").fetchone()[0]
    return {
        "items": [{"id": str(row[0]), "title": str(row[1]), "content": str(row[2])} for row in items],
        "total_durable_memory_rows": int(total_durable_memory_rows),
        "learning_candidates": int(learning),
        "feedback": int(feedback),
    }


def _project_init_first(console: Path, fixture: Path, root: Path, env: dict[str, str]) -> dict[str, Any]:
    completed = _run(
        [str(console), "project", "init", ".", "--yes"],
        cwd=fixture,
        env=env,
        label="first Project Init",
    )
    output = completed.stdout
    for marker in (
        "Project detected: fixture",
        f"Suggested namespace: {NAMESPACE}",
        f"Chosen namespace: {NAMESPACE}",
        "Initialized project: fixture",
        "Repository WHAT initialized.",
        "CODE / WHAT",
        "CONVERSATION / WHY",
        "Tell your connected coding agent:",
    ):
        _require(marker in output, f"first Project Init output missed {marker}")
    _require("Refreshed project:" not in output, "first Project Init used refresh wording")
    state = _db_rows(Path(env["AGENT_MEMORY_BRIDGE_DB_PATH"]))
    _require(state["total_durable_memory_rows"] == 0, "Project Init created durable memory")
    _require(state["learning_candidates"] == 0, "Project Init created a learning candidate")
    _require(state["feedback"] == 0, "Project Init created retrieval feedback")
    snapshot_root = Path(env["AGENT_MEMORY_BRIDGE_REPOSITORY_SNAPSHOT_ROOT"])
    _require((snapshot_root / "bindings.json").exists(), "Project Init did not create a binding")
    _require(bool(list(snapshot_root.rglob("current.json"))), "Project Init did not store a snapshot")
    return {
        "repository_detected": True,
        "namespace_proposed_and_chosen": True,
        "repository_what_initialized": True,
        "human_first_explore": True,
        "empty_why_guidance": True,
        "total_durable_memory_rows": 0,
        "learning_candidates": 0,
        "feedback_rows": 0,
    }


def _store_why(python: Path, root: Path, env: dict[str, str]) -> tuple[str, str]:
    ids: list[str] = []
    for title, content in ((DECISION_TITLE, DECISION_CONTENT), (CONSTRAINT_TITLE, CONSTRAINT_CONTENT)):
        result = base._mcp_call(
            python,
            cwd=root,
            env=env,
            action="store",
            arguments={"namespace": NAMESPACE, "kind": "memory", "title": title, "content": content},
        )
        payload = result.get("payload") or {}
        _require(payload.get("stored") is True and bool(payload.get("id")), "public MCP store failed")
        ids.append(str(payload["id"]))
    return ids[0], ids[1]


def _human_explorer(console: Path, root: Path, env: dict[str, str]) -> dict[str, Any]:
    human = _run(
        [str(console), "explore", "--namespace", NAMESPACE],
        cwd=root,
        env=env,
        label="human Explore",
    ).stdout
    for marker in (
        "CODE / WHAT",
        "CONVERSATION / WHY",
        "Do not introduce Redis.",
        "This disposable project is intentionally local-first and single-node.",
        "Repository-derived WHAT must remain rebuildable.",
        "Repository facts must not become human decision authority.",
    ):
        _require(marker in human, f"human Explore missed {marker}")
    for forbidden in (
        "derived_repository",
        "governed_durable_memory",
        "memory:",
        "source_ref",
        "has_decision",
        "inputSchema",
        "structured_content",
    ):
        _require(forbidden not in human, f"human Explore leaked {forbidden}")

    technical = _run(
        [str(console), "explore", "--namespace", NAMESPACE, "--format", "markdown", "--technical"],
        cwd=root,
        env=env,
        label="technical Explore",
    ).stdout
    _require("# AMB Knowledge Explorer" in technical, "technical Explore heading changed")
    _require("## Relationships" in technical, "technical Explore lost relationships")
    _require("`has_decision`" in technical and "`has_constraint`" in technical, "technical graph lost WHY edges")
    first_json = _run(
        [str(console), "explore", "--namespace", NAMESPACE, "--format", "json"],
        cwd=root,
        env=env,
        label="first JSON Explore",
    ).stdout
    second_json = _run(
        [str(console), "explore", "--namespace", NAMESPACE, "--format", "json"],
        cwd=root,
        env=env,
        label="second JSON Explore",
    ).stdout
    _require(first_json == second_json, "repeated Explorer JSON was not byte-identical")
    projection = json.loads(first_json)
    _require(projection.get("schema") == "knowledge-explorer-v1", "Explorer JSON schema changed")
    relations = {edge.get("relation") for edge in projection.get("edges") or [] if isinstance(edge, dict)}
    _require({"bound_to", "has_decision", "has_constraint"}.issubset(relations), "Explorer JSON lost relations")
    return {
        "human_markdown": True,
        "technical_markdown": True,
        "json_schema": "knowledge-explorer-v1",
        "json_byte_identical": True,
        "relations_preserved": True,
    }


def _consistency(python: Path, console: Path, root: Path, env: dict[str, str], decision_id: str) -> dict[str, Any]:
    base.NAMESPACE = NAMESPACE
    base.QUERY = QUERY
    base.WHY_CONTENT = DECISION_CONTENT
    governed = base._governed_context_path(python, console, root, env, active_id=decision_id)
    _require(governed["recall_active"], "Recall did not see the active decision")
    _require(governed["task_memory_decision_hits"], "Task Memory missed decision_hits")
    _require(governed["inspect_selected"], "Inspect did not select the decision")
    _require(governed["context_compiler_project_decision"], "Context Compiler missed [Project Decision]")
    _require(governed["explore_governed_durable_memory"], "Explore missed governed WHY")
    return governed


def _repeat_init(
    console: Path,
    fixture: Path,
    root: Path,
    env: dict[str, str],
    expected_items: list[dict[str, str]],
) -> dict[str, Any]:
    before = _db_rows(Path(env["AGENT_MEMORY_BRIDGE_DB_PATH"]))
    snapshot_root = Path(env["AGENT_MEMORY_BRIDGE_REPOSITORY_SNAPSHOT_ROOT"])
    bindings_before = (snapshot_root / "bindings.json").read_bytes()
    completed = _run(
        [str(console), "project", "init", str(fixture), "--namespace", NAMESPACE, "--yes"],
        cwd=root,
        env=env,
        label="repeat Project Init",
    )
    _require("Refreshed project: fixture" in completed.stdout, "repeat init missed refresh heading")
    _require(
        "Repository WHAT refreshed; existing project WHY is unchanged." in completed.stdout,
        "repeat init missed authority-split refresh copy",
    )
    _require("Initialized project:" not in completed.stdout, "repeat init used initialization wording")
    after = _db_rows(Path(env["AGENT_MEMORY_BRIDGE_DB_PATH"]))
    _require(after["items"] == expected_items == before["items"], "repeat init changed existing WHY")
    _require(after["learning_candidates"] == 0, "repeat init created learning candidates")
    _require(after["feedback"] == 0, "repeat init created feedback")
    _require((snapshot_root / "bindings.json").read_bytes() == bindings_before, "repeat init changed the binding")
    return {
        "refresh_wording": True,
        "same_why_ids_and_content": True,
        "duplicate_memory": False,
        "duplicate_binding": False,
        "learning_candidates": 0,
        "feedback_rows": 0,
    }


def _safety(console: Path, fixture: Path, root: Path, env: dict[str, str]) -> dict[str, Any]:
    safety_root = root / "safety"
    safety_root.mkdir()
    safety_env = dict(env)
    safety_env.update(
        {
            "AGENT_MEMORY_BRIDGE_HOME": str(safety_root / "home"),
            "AGENT_MEMORY_BRIDGE_DB_PATH": str(safety_root / "home" / "bridge.db"),
            "AGENT_MEMORY_BRIDGE_LOG_DIR": str(safety_root / "logs"),
            "AGENT_MEMORY_BRIDGE_REPOSITORY_SNAPSHOT_ROOT": str(safety_root / "snapshots"),
        }
    )
    before = _tree_digest(safety_root)
    dry = _run(
        [str(console), "project", "init", str(fixture), "--namespace", NAMESPACE, "--dry-run"],
        cwd=root,
        env=safety_env,
        label="Project Init dry-run",
    )
    _require("No changes have been made." in dry.stdout, "dry-run missed no-change copy")
    _require(_tree_digest(safety_root) == before, "dry-run mutated state")
    declined = _run(
        [str(console), "project", "init", str(fixture), "--namespace", NAMESPACE],
        cwd=root,
        env=safety_env,
        label="Project Init decline",
        input_text="\n",
    )
    _require("No changes were made." in declined.stdout, "default NO did not decline")
    _require(_tree_digest(safety_root) == before, "decline mutated state")

    dirty_before = _tree_digest(safety_root)
    (fixture / "src" / "app.py").write_text('def status() -> str:\n    return "dirty"\n', encoding="utf-8")
    dirty = _run(
        [str(console), "project", "init", str(fixture), "--namespace", NAMESPACE, "--yes"],
        cwd=root,
        env=safety_env,
        label="dirty Project Init",
        expected_status=1,
    )
    _require("worktree is dirty" in dirty.stdout, "dirty Project Init did not fail closed")
    _require(_tree_digest(safety_root) == dirty_before, "dirty Project Init mutated state")
    _run(["git", "-C", str(fixture), "checkout", "--", "src/app.py"], cwd=root, env=env, label="fixture reset")

    _run(
        [str(console), "project", "init", str(fixture), "--namespace", NAMESPACE, "--yes"],
        cwd=root,
        env=safety_env,
        label="safety namespace bind",
    )
    other = root / "other-fixture"
    other.mkdir()
    (other / "README.md").write_text("Other fixture\n", encoding="utf-8")
    (other / "pyproject.toml").write_text(
        '[project]\nname = "other-fixture"\nrequires-python = ">=3.11"\n',
        encoding="utf-8",
    )
    for command in (
        ["git", "-C", str(other), "init", "-q"],
        ["git", "-C", str(other), "config", "user.email", "proof@example.invalid"],
        ["git", "-C", str(other), "config", "user.name", "AMB release proof"],
        ["git", "-C", str(other), "add", "."],
        ["git", "-C", str(other), "commit", "-qm", "Create conflicting fixture"],
    ):
        _run(command, cwd=root, env=env, label="conflicting fixture Git setup", timeout=30)
    conflict_before = _tree_digest(safety_root)
    conflict = _run(
        [str(console), "project", "init", str(other), "--namespace", NAMESPACE, "--yes"],
        cwd=root,
        env=safety_env,
        label="namespace-conflict Project Init",
        expected_status=1,
    )
    _require("already bound to a different repository" in conflict.stdout, "namespace conflict did not fail closed")
    for forbidden in ("Initialized project:", "Refreshed project:", "Repository WHAT initialized."):
        _require(forbidden not in conflict.stdout + conflict.stderr, "namespace conflict printed success wording")
    _require(_tree_digest(safety_root) == conflict_before, "namespace conflict mutated state")

    source = _run(
        [
            str(_venv_python_from_console(console)),
            "-c",
            "import inspect; from agent_mem_bridge.project_init import apply_project_init; print(inspect.getsource(apply_project_init))",
        ],
        cwd=root,
        env=safety_env,
        label="installed post-confirm source check",
    ).stdout
    _require("current = plan_project_init" in source, "installed apply omitted post-confirm revalidation")
    _require("save_snapshot(current.snapshot)" in source, "installed apply did not persist fresh snapshot")
    _require("save_snapshot(plan.snapshot)" not in source, "installed apply persisted stale plan snapshot")
    return {
        "dry_run_zero_write": True,
        "decline_zero_write": True,
        "dirty_fail_closed": True,
        "namespace_conflict_fail_closed": True,
        "post_confirm_revalidation_present": True,
    }


def _venv_python_from_console(console: Path) -> Path:
    return console.parent / ("python.exe" if os.name == "nt" else "python")


def run_proof(*, expected_version: str, artifact_kind: str) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "result": "fail",
        "expected_version": expected_version,
        "artifact": artifact_kind,
        "stages": [],
    }
    with tempfile.TemporaryDirectory(prefix="amb-v032-release-proof-") as temporary:
        root = Path(temporary).resolve()
        build_env = base._build_env(root)
        env = base._runtime_env(root)
        base.NAMESPACE = NAMESPACE
        base.QUERY = QUERY
        base.WHY_CONTENT = DECISION_CONTENT
        wheel, sdist = base._build_distributions(root, build_env)
        artifact = wheel if artifact_kind == "wheel" else sdist
        python, console = base._install_artifact(root, artifact, build_env)

        def stage(name: str, evidence: Any) -> Any:
            report["stages"].append({"name": name, "status": "pass", "evidence": evidence})
            return evidence

        stage("installed_artifact", base._artifact_stage(python, console, root, env, expected_version, artifact_kind))
        fixture = base._create_fixture(root)
        stage("project_init", _project_init_first(console, fixture, root, env))
        decision_id, constraint_id = _store_why(python, root, env)
        stored_state = _db_rows(Path(env["AGENT_MEMORY_BRIDGE_DB_PATH"]))
        expected_items = stored_state["items"]
        _require(
            {item["id"] for item in expected_items} == {decision_id, constraint_id},
            "Project Learning state contained rows beyond the two explicit WHY writes",
        )
        _require(
            {item["content"] for item in expected_items} == {DECISION_CONTENT, CONSTRAINT_CONTENT},
            "Project Learning state did not preserve the exact explicit WHY content",
        )
        _require(stored_state["learning_candidates"] == 0, "explicit WHY created a learning candidate")
        _require(stored_state["feedback"] == 0, "explicit WHY created retrieval feedback")
        stage("project_learning", {"decision_id": decision_id, "constraint_id": constraint_id})
        stage("explorer_contract", _human_explorer(console, root, env))
        stage("inspect_compiler_consistency", _consistency(python, console, root, env, decision_id))
        stage("repeat_init", _repeat_init(console, fixture, root, env, expected_items))
        stage("safety", _safety(console, fixture, root, env))
        surface = base._mcp_call(python, cwd=root, env=env, action="list_tools")
        stage("public_surface", base._surface_stage(surface))
    report["result"] = "pass"
    return report


def render_text(report: dict[str, Any]) -> str:
    lines = ["AMB v0.32 clean-room release proof", ""]
    if report.get("result") == "pass":
        for index, item in enumerate(report.get("stages") or [], start=1):
            lines.append(f"[{index}] {item['name']:<30} PASS")
        lines.extend(("", "RESULT: PASS"))
    else:
        lines.extend((f"FAIL: {report.get('reason', 'proof failed')}", "", "RESULT: FAIL"))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--artifact", choices=("wheel", "sdist"), default="wheel")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = run_proof(expected_version=args.expected_version, artifact_kind=args.artifact)
    except Exception as exc:
        reason = str(exc) if isinstance(exc, base.ProofFailure) else type(exc).__name__
        report = {
            "schema": REPORT_SCHEMA,
            "result": "fail",
            "expected_version": args.expected_version,
            "artifact": args.artifact,
            "reason": reason[:240],
        }
    print(json.dumps(report, sort_keys=True, separators=(",", ":")) if args.json else render_text(report))
    return 0 if report["result"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
