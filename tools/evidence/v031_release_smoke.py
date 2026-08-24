#!/usr/bin/env python3
"""Run an artifact-first clean-room smoke proof for the v0.31 release family."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
NAMESPACE = "project:v031-proof"
EXPECTED_TOOL_COUNT = 17
EXPECTED_TOOL_DIGEST = "24c5c52321d61b4b6f647c0d74e2d8304ca68716c403e08a274e9badfd8dc9f8"
EXPECTED_WHAT = {"key": "python_requires", "value": ">=3.11", "source": "pyproject.toml"}
WHY_TITLE = "Keep the clean-room fixture local-first"
WHY_CONTENT = "\n".join(
    (
        "record_type: decision",
        "claim: Do not introduce Redis.",
        "reason: This clean-room fixture is intentionally local-first and single-node.",
        f"scope: {NAMESPACE}",
        "confidence: observed",
    )
)
QUERY = "How should this Python 3.11 project implement a queue without Redis?"
REPORT_SCHEMA = "agent-memory-bridge.v031-clean-room-release-proof.v1"
STAGES = (
    "installed_artifact",
    "bootstrap_repository_what",
    "public_mcp_store_why",
    "fresh_process_recall",
    "knowledge_explorer",
    "relation_governance",
    "inspect_provenance",
    "public_surface",
    "read_only_contamination",
)


class ProofFailure(RuntimeError):
    """A bounded release-proof failure suitable for human or JSON output."""


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise ProofFailure(reason)


def _run(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    label: str,
    timeout: int = 180,
) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProofFailure(f"{label} could not complete ({type(exc).__name__})") from None
    if completed.returncode != 0:
        raise ProofFailure(f"{label} exited with status {completed.returncode}")
    return completed


def _run_json(command: list[str], *, cwd: Path, env: dict[str, str], label: str, timeout: int = 180) -> dict[str, Any]:
    completed = _run(command, cwd=cwd, env=env, label=label, timeout=timeout)
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        raise ProofFailure(f"{label} did not return JSON") from None
    if not isinstance(payload, dict):
        raise ProofFailure(f"{label} returned an unexpected JSON shape")
    return payload


def _venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _venv_console(venv: Path) -> Path:
    return venv / ("Scripts/agent-memory-bridge.exe" if os.name == "nt" else "bin/agent-memory-bridge")


SENSITIVE_ENV_PREFIXES = (
    "AGENT_MEMORY_BRIDGE_",
    "ANTHROPIC_",
    "AZURE_OPENAI_",
    "COHERE_",
    "CODEX_",
    "GEMINI_",
    "GOOGLE_",
    "GROQ_",
    "LANGCHAIN_",
    "LANGSMITH_",
    "MISTRAL_",
    "OPENAI_",
    "OTEL_",
    "SENTRY_",
)


def _build_env(root: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.update(
        {
            "HOME": str(root / "user-home"),
            "USERPROFILE": str(root / "user-home"),
            "XDG_CONFIG_HOME": str(root / "xdg-config"),
            "PYTHONNOUSERSITE": "1",
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PIP_NO_INPUT": "1",
        }
    )
    return env


def _runtime_env(root: Path) -> dict[str, str]:
    bridge_home = root / "bridge-home"
    config = root / "bridge-config.toml"
    config.write_text('[retrieval]\nmode = "lexical"\n', encoding="utf-8")
    env = {
        key: value
        for key, value in os.environ.items()
        if key != "PYTHONPATH" and not key.upper().startswith(SENSITIVE_ENV_PREFIXES)
    }
    env.update(
        {
            "AGENT_MEMORY_BRIDGE_HOME": str(bridge_home),
            "AGENT_MEMORY_BRIDGE_DB_PATH": str(bridge_home / "bridge.db"),
            "AGENT_MEMORY_BRIDGE_LOG_DIR": str(root / "logs"),
            "AGENT_MEMORY_BRIDGE_REPOSITORY_SNAPSHOT_ROOT": str(root / "repository-snapshots"),
            "AGENT_MEMORY_BRIDGE_CONFIG": str(config),
            "AGENT_MEMORY_BRIDGE_RETRIEVAL_MODE": "lexical",
            "HOME": str(root / "user-home"),
            "USERPROFILE": str(root / "user-home"),
            "XDG_CONFIG_HOME": str(root / "xdg-config"),
            "PYTHONNOUSERSITE": "1",
            "HTTP_PROXY": "http://127.0.0.1:9",
            "HTTPS_PROXY": "http://127.0.0.1:9",
            "ALL_PROXY": "http://127.0.0.1:9",
            "NO_PROXY": "",
        }
    )
    return env


def _create_fixture(root: Path) -> Path:
    fixture = root / "fixture"
    fixture.mkdir()
    (fixture / "AGENTS.md").write_text("# Fixture instructions\n\nKeep this fixture local-first.\n", encoding="utf-8")
    (fixture / "Makefile").write_text("test:\n\tpython -m pytest\n", encoding="utf-8")
    (fixture / "pyproject.toml").write_text(
        '[project]\nname = "v031-proof-fixture"\nrequires-python = ">=3.11"\n', encoding="utf-8"
    )
    (fixture / "src").mkdir()
    (fixture / "src" / "app.py").write_text('def status() -> str:\n    return "ready"\n', encoding="utf-8")
    (fixture / "tests").mkdir()
    (fixture / "tests" / "test_app.py").write_text(
        'from src.app import status\n\n\ndef test_status() -> None:\n    assert status() == "ready"\n', encoding="utf-8"
    )
    git_env = os.environ.copy()
    git_env.update(
        {
            "GIT_AUTHOR_DATE": "2000-01-01T00:00:00+00:00",
            "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+00:00",
        }
    )
    for command in (
        ["git", "-C", str(fixture), "init", "-q"],
        ["git", "-C", str(fixture), "config", "user.email", "proof@example.invalid"],
        ["git", "-C", str(fixture), "config", "user.name", "AMB release proof"],
        ["git", "-C", str(fixture), "add", "."],
        ["git", "-C", str(fixture), "commit", "-qm", "Create clean-room fixture"],
    ):
        _run(command, cwd=root, env=git_env, label="fixture Git setup", timeout=30)
    return fixture


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    if not root.exists():
        return digest.hexdigest()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "missing"


def _db_state(db_path: Path) -> dict[str, Any]:
    with sqlite3.connect(db_path) as conn:
        memory_rows = conn.execute(
            "SELECT id, content, is_learning_candidate FROM memories WHERE namespace = ? ORDER BY id", (NAMESPACE,)
        ).fetchall()
        feedback_count = conn.execute("SELECT COUNT(*) FROM retrieval_feedback").fetchone()[0]
    return {
        "memory_ids": [str(row[0]) for row in memory_rows],
        "memory_count": len(memory_rows),
        "learning_candidate_count": sum(int(row[2]) for row in memory_rows),
        "feedback_count": int(feedback_count),
        "repository_fact_in_durable_rows": any(
            "python_requires" in str(row[1]) or ">=3.11" in str(row[1]) for row in memory_rows
        ),
        "database_digest": _file_digest(db_path),
    }


def _matching_fact(items: object, *, inspect_shape: bool = False) -> dict[str, Any] | None:
    if not isinstance(items, list):
        return None
    key_name = "fact_kind" if inspect_shape else "key"
    return next(
        (
            item
            for item in items
            if isinstance(item, dict)
            and item.get(key_name) == EXPECTED_WHAT["key"]
            and item.get("value") == EXPECTED_WHAT["value"]
            and item.get("source") == EXPECTED_WHAT["source"]
        ),
        None,
    )


MCP_CLIENT = r"""
import asyncio
import hashlib
import json
import os
import sys

from mcp import StdioServerParameters
from mcp.client import Client
from mcp.client.stdio import stdio_client
from mcp.types import Implementation


async def main():
    request = json.loads(sys.stdin.read())
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "agent_mem_bridge"],
        cwd=os.getcwd(),
        env=dict(os.environ),
    )
    async with Client(
        stdio_client(server),
        mode="auto",
        client_info=Implementation(name="v031-clean-room-proof", version="1"),
        read_timeout_seconds=30,
    ) as client:
        listed = await client.list_tools()
        tools = listed.tools
        snapshot = sorted(
            (
                {"name": tool.name, "inputSchema": tool.input_schema, "outputSchema": tool.output_schema}
                for tool in tools
            ),
            key=lambda item: item["name"],
        )
        encoded = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
        response = {
            "tool_names": [tool.name for tool in tools],
            "tool_digest": hashlib.sha256(encoded).hexdigest(),
        }
        if request["action"] != "list_tools":
            result = await client.call_tool(request["action"], request.get("arguments", {}))
            if result.is_error or not isinstance(result.structured_content, dict):
                raise RuntimeError("MCP tool call failed")
            response["payload"] = result.structured_content
    print(json.dumps(response, sort_keys=True, separators=(",", ":")))


asyncio.run(main())
"""


def _mcp_call(
    python: Path,
    *,
    cwd: Path,
    env: dict[str, str],
    action: str,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            [str(python), "-c", MCP_CLIENT],
            input=json.dumps({"action": action, "arguments": arguments or {}}),
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProofFailure(f"MCP {action} could not complete ({type(exc).__name__})") from None
    if completed.returncode != 0:
        raise ProofFailure(f"MCP {action} exited with status {completed.returncode}")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        raise ProofFailure(f"MCP {action} did not return JSON") from None
    if not isinstance(payload, dict):
        raise ProofFailure(f"MCP {action} returned an unexpected JSON shape")
    return payload


def _build_distributions(root: Path, env: dict[str, str]) -> tuple[Path, Path]:
    dist = root / "dist"
    _run(
        [sys.executable, "-m", "build", "--wheel", "--sdist", "--outdir", str(dist)],
        cwd=ROOT,
        env=env,
        label="distribution build",
        timeout=300,
    )
    artifacts = sorted((*dist.glob("*.whl"), *dist.glob("*.tar.gz")))
    _require(len(artifacts) == 2, "distribution build did not produce one wheel and one sdist")
    _run(
        [sys.executable, "-m", "twine", "check", *(str(path) for path in artifacts)],
        cwd=root,
        env=env,
        label="twine check",
        timeout=120,
    )
    return next(dist.glob("*.whl")), next(dist.glob("*.tar.gz"))


def _install_artifact(root: Path, artifact: Path, env: dict[str, str]) -> tuple[Path, Path]:
    venv = root / "venv"
    _run([sys.executable, "-m", "venv", str(venv)], cwd=root, env=env, label="virtual environment creation")
    python = _venv_python(venv)
    _run(
        [str(python), "-m", "pip", "install", str(artifact)],
        cwd=root,
        env=env,
        label="artifact installation",
        timeout=300,
    )
    console = _venv_console(venv)
    _require(console.exists(), "installed console entrypoint is missing")
    return python, console


def _artifact_stage(
    python: Path, console: Path, root: Path, env: dict[str, str], expected_version: str, artifact_kind: str
) -> dict[str, Any]:
    code = textwrap.dedent(
        """
        import importlib.metadata
        import json
        import pathlib
        import agent_mem_bridge
        from agent_mem_bridge.schema import CURRENT_SCHEMA_VERSION

        print(json.dumps({
            "version": importlib.metadata.version("agent-memory-bridge"),
            "origin": str(pathlib.Path(agent_mem_bridge.__file__).resolve()),
            "schema": CURRENT_SCHEMA_VERSION,
        }))
        """
    )
    payload = _run_json([str(python), "-c", code], cwd=root, env=env, label="installed package origin check")
    origin = Path(str(payload.get("origin") or "")).resolve()
    _require(payload.get("version") == expected_version, "installed package version did not match --expected-version")
    _require(payload.get("schema") == 12, "installed package schema was not v12")
    _require(
        origin.is_relative_to(python.parent.parent.resolve()), "runtime module did not come from the isolated venv"
    )
    _require(not origin.is_relative_to((ROOT / "src").resolve()), "runtime module resolved from checkout src")
    version = _run([str(console), "--version"], cwd=root, env=env, label="installed console version check")
    _require(version.stdout.strip() == expected_version, "installed console reported the wrong version")
    return {
        "artifact": artifact_kind,
        "version": expected_version,
        "schema": 12,
        "module_from_isolated_environment": True,
        "module_from_checkout_src": False,
        "console_entrypoint_verified": True,
        "wheel_evidence_tools_excluded": artifact_kind == "wheel",
        "source_distribution_selected": artifact_kind == "sdist",
    }


def _bootstrap_stage(console: Path, fixture: Path, root: Path, env: dict[str, str]) -> dict[str, Any]:
    payload = _run_json(
        [str(console), "bootstrap-repo", str(fixture), "--namespace", NAMESPACE, "--format", "json"],
        cwd=root,
        env=env,
        label="repository bootstrap",
    )
    _require(payload.get("binding") == "git_commit", "repository bootstrap was not commit-bound")
    binding = payload.get("binding_action")
    _require(isinstance(binding, dict) and binding.get("namespace") == NAMESPACE, "namespace binding was not created")
    fact = _matching_fact(payload.get("facts"))
    _require(fact is not None, "repository bootstrap did not extract python_requires >=3.11")
    facts = payload.get("facts") or []
    task_runner = next(
        (item.get("value") for item in facts if isinstance(item, dict) and item.get("key") == "task_runner"), None
    )
    _require(task_runner == "Makefile", "repository bootstrap did not extract the Makefile task runner")
    return {
        "binding": "git_commit",
        "namespace_bound": True,
        "repository_what": EXPECTED_WHAT,
        "additional_fact": {"key": "task_runner", "value": "Makefile"},
        "derived_repository_provenance": True,
    }


def _store_stage(python: Path, root: Path, env: dict[str, str]) -> tuple[str, dict[str, Any]]:
    result = _mcp_call(
        python,
        cwd=root,
        env=env,
        action="store",
        arguments={
            "namespace": NAMESPACE,
            "kind": "memory",
            "title": WHY_TITLE,
            "content": WHY_CONTENT,
            "actor": "v031-release-proof",
            "source_app": "v031-clean-room-release-proof",
            "source_client": "artifact-proof-writer",
            "client_workspace": "v031-proof-fixture",
            "client_transport": "stdio",
        },
    )
    payload = result.get("payload") or {}
    stored_id = str(payload.get("id") or "")
    _require(payload.get("stored") is True and bool(stored_id), "public MCP store did not persist the decision")
    return stored_id, {
        "record_type": "decision",
        "stored": True,
        "distinct_mcp_client_and_server_launch_completed": True,
    }


def _recall_stage(
    python: Path, root: Path, env: dict[str, str], stored_id: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    result = _mcp_call(
        python,
        cwd=root,
        env=env,
        action="recall",
        arguments={"namespace": NAMESPACE, "query": QUERY, "kind": "memory", "limit": 10},
    )
    payload = result.get("payload") or {}
    items = payload.get("items") or []
    decision = next(
        (
            item
            for item in items
            if isinstance(item, dict)
            and item.get("id") == stored_id
            and item.get("record_type") == "decision"
            and item.get("content") == WHY_CONTENT
        ),
        None,
    )
    _require(decision is not None, "fresh-process recall did not return the durable decision")
    repository = payload.get("repository_knowledge")
    _require(isinstance(repository, dict), "fresh-process recall did not return repository knowledge")
    assert isinstance(repository, dict)
    _require(repository.get("authority") == "derived_repository", "repository authority was not derived_repository")
    _require(repository.get("binding_state") == "current", "repository binding was not current")
    fact = _matching_fact(repository.get("selected"))
    _require(fact is not None, "fresh-process recall did not return repository WHAT")
    return (
        {
            "distinct_fresh_mcp_client_and_server_launch_completed": True,
            "durable_decision_recalled": True,
            "repository_what_recalled": True,
            "repository_authority": "derived_repository",
            "binding_state": "current",
            "writer_launch_completed_before_recall_launch": True,
        },
        {"tool_names": result.get("tool_names") or [], "tool_digest": result.get("tool_digest")},
    )


def _explore(console: Path, root: Path, env: dict[str, str]) -> dict[str, Any]:
    return _run_json(
        [str(console), "explore", "--namespace", NAMESPACE, "--format", "json"],
        cwd=root,
        env=env,
        label="Knowledge Explorer",
    )


def _explorer_stage(console: Path, root: Path, env: dict[str, str], stored_id: str) -> dict[str, Any]:
    projection = _explore(console, root, env)
    _require(projection.get("schema") == "knowledge-explorer-v1", "Explorer schema was not knowledge-explorer-v1")
    _require(projection.get("read_only") is True, "Explorer did not declare read_only")
    _require(projection.get("rebuildable") is True, "Explorer did not declare rebuildable")
    nodes = projection.get("nodes") or []
    edges = projection.get("edges") or []
    project_id = f"project:{NAMESPACE}"
    node_by_id = {str(node.get("id")): node for node in nodes if isinstance(node, dict)}
    _require(node_by_id.get(project_id, {}).get("authority") == "derived_projection", "project authority mismatch")
    _require(
        any(node.get("authority") == "derived_repository" and node.get("type") == "repository" for node in nodes),
        "Explorer did not project the repository authority",
    )
    _require(
        node_by_id.get(f"memory:{stored_id}", {}).get("authority") == "governed_durable_memory",
        "Explorer did not project the durable decision authority",
    )
    _require(
        any(edge.get("source") == project_id and edge.get("relation") == "bound_to" for edge in edges),
        "Explorer did not project project-to-repository binding",
    )
    _require(
        any(
            edge.get("source") == project_id
            and edge.get("relation") == "has_decision"
            and edge.get("target") == f"memory:{stored_id}"
            for edge in edges
        ),
        "Explorer did not project project-to-decision knowledge",
    )
    return {
        "schema": "knowledge-explorer-v1",
        "read_only": True,
        "rebuildable": True,
        "project_authority": "derived_projection",
        "repository_authority": "derived_repository",
        "decision_authority": "governed_durable_memory",
        "bound_to_edge": True,
        "has_decision_edge": True,
    }


def _relation_stage(
    python: Path, console: Path, root: Path, env: dict[str, str], target_id: str
) -> tuple[dict[str, Any], list[str]]:
    source_content = "\n".join(
        (
            "record_type: constraint",
            "claim: Queue implementations must remain single-node.",
            f"supports: {target_id}",
            f"scope: {NAMESPACE}",
            "confidence: observed",
        )
    )
    stored = _mcp_call(
        python,
        cwd=root,
        env=env,
        action="store",
        arguments={
            "namespace": NAMESPACE,
            "kind": "memory",
            "title": "Single-node queue constraint",
            "content": source_content,
            "actor": "v031-release-proof",
            "source_app": "v031-clean-room-release-proof",
            "source_client": "artifact-proof-governance",
            "client_transport": "stdio",
        },
    )
    source_id = str((stored.get("payload") or {}).get("id") or "")
    _require(bool(source_id), "relation source was not stored through public MCP")
    active = _explore(console, root, env)
    _require(
        any(
            edge.get("source") == f"memory:{source_id}"
            and edge.get("relation") == "supports"
            and edge.get("target") == f"memory:{target_id}"
            for edge in active.get("edges") or []
        ),
        "Explorer did not show the active structured relation",
    )
    replacement = "\n".join(
        (
            "record_type: decision",
            "claim: Do not introduce Redis or another network queue.",
            "reason: This clean-room fixture remains local-first and single-node.",
            f"scope: {NAMESPACE}",
            "confidence: observed",
        )
    )
    revised = _mcp_call(
        python,
        cwd=root,
        env=env,
        action="revise",
        arguments={
            "id": target_id,
            "replacement_content": replacement,
            "title": "Keep the clean-room fixture local-first",
            "actor": "v031-release-proof",
            "reason": "Exercise existing supersession governance",
        },
    )
    successor_id = str((revised.get("payload") or {}).get("successor_id") or "")
    _require(bool(successor_id), "public MCP revise did not create a successor")
    governed = _explore(console, root, env)
    node_ids = {str(node.get("id")) for node in governed.get("nodes") or [] if isinstance(node, dict)}
    _require(f"memory:{target_id}" not in node_ids, "Explorer kept a superseded decision active")
    _require(f"memory:{successor_id}" in node_ids, "Explorer did not project the active successor")
    _require(
        not any(edge.get("target") == f"memory:{target_id}" for edge in governed.get("edges") or []),
        "Explorer kept an active edge to the superseded target",
    )
    _require(
        any(
            diagnostic.get("memory_id") == target_id or diagnostic.get("target_memory_id") == target_id
            for diagnostic in governed.get("diagnostics") or []
        ),
        "Explorer did not diagnose the governed relation suppression",
    )
    return (
        {
            "active_relation_observed": True,
            "superseded_target_withheld": True,
            "successor_projected": True,
            "governed_mutation_path": "public_mcp_revise",
        },
        [target_id, source_id, successor_id],
    )


def _inspect_report(console: Path, root: Path, env: dict[str, str]) -> dict[str, Any]:
    return _run_json(
        [str(console), "inspect", "--namespace", NAMESPACE, "--query", QUERY, "--format", "json", "--technical"],
        cwd=root,
        env=env,
        label="inspect provenance",
    )


def _governed_context_path(
    python: Path,
    console: Path,
    root: Path,
    env: dict[str, str],
    *,
    active_id: str,
    inactive_ids: tuple[str, ...] = (),
) -> dict[str, Any]:
    recall = _mcp_call(
        python,
        cwd=root,
        env=env,
        action="recall",
        arguments={"namespace": NAMESPACE, "query": QUERY, "kind": "memory", "limit": 10},
    )
    recall_ids = {
        str(item.get("id")) for item in ((recall.get("payload") or {}).get("items") or []) if isinstance(item, dict)
    }
    _require(active_id in recall_ids, "fresh-process recall did not return the active decision")
    _require(
        all(memory_id not in recall_ids for memory_id in inactive_ids),
        "fresh-process recall kept an inactive predecessor",
    )

    inspect_payload = _inspect_report(console, root, env)
    selected = {
        str(item.get("memory_id")): item for item in inspect_payload.get("selected") or [] if isinstance(item, dict)
    }
    _require(active_id in selected, "inspect did not select the active decision")
    _require(
        (selected[active_id].get("technical") or {}).get("section") == "decision_hits",
        "inspect did not select the decision through task memory",
    )
    _require(
        all(memory_id not in selected for memory_id in inactive_ids), "inspect kept an inactive predecessor selected"
    )
    repository = inspect_payload.get("repository_knowledge") or {}
    snapshot = repository.get("snapshot") or {}
    _require(snapshot.get("authority") == "derived_repository", "inspect repository authority mismatch")

    db_path = env["AGENT_MEMORY_BRIDGE_DB_PATH"]
    log_dir = env["AGENT_MEMORY_BRIDGE_LOG_DIR"]
    code = textwrap.dedent(
        f"""
        import json
        from pathlib import Path
        from agent_mem_bridge.context_manifest import compile_context, render_context
        from agent_mem_bridge.schema import CURRENT_SCHEMA_VERSION, schema_version
        from agent_mem_bridge.storage import MemoryStore
        from agent_mem_bridge.task_memory import assemble_task_memory

        store = MemoryStore(Path({db_path!r}), log_dir=Path({log_dir!r}))
        with store._connect() as conn:
            current_schema = schema_version(conn)
        report = assemble_task_memory(store, query={QUERY!r}, project_namespace={NAMESPACE!r})
        manifest = compile_context(task_memory=report, budget_tokens=2048)
        rendered = render_context(manifest)
        print(json.dumps({{
            "schema": current_schema,
            "expected_schema": CURRENT_SCHEMA_VERSION,
            "decision_ids": [item.get("id") for item in report.get("decision_hits") or []],
            "item_ids": [item.item_id for item in manifest.items],
            "rendered": rendered,
        }}))
        """
    )
    compiled = _run_json([str(python), "-c", code], cwd=root, env=env, label="task memory and context compiler")
    _require(compiled.get("schema") == 12, "installed database schema was not v12")
    _require(compiled.get("expected_schema") == 12, "installed package schema constant was not v12")
    decision_ids = [str(item) for item in compiled.get("decision_ids") or []]
    _require(active_id in decision_ids, "task memory decision_hits did not contain the active decision")
    _require(
        all(memory_id not in decision_ids for memory_id in inactive_ids), "task memory kept an inactive predecessor"
    )
    rendered = str(compiled.get("rendered") or "")
    _require("[Project Decision]" in rendered, "context compiler did not render [Project Decision]")
    item_ids = {str(item) for item in compiled.get("item_ids") or []}
    _require(active_id in item_ids, "context compiler did not include the active decision")
    _require(
        all(memory_id not in item_ids for memory_id in inactive_ids), "context compiler kept an inactive predecessor"
    )

    projection = _explore(console, root, env)
    node_ids = {str(node.get("id")) for node in projection.get("nodes") or [] if isinstance(node, dict)}
    _require(f"memory:{active_id}" in node_ids, "Explorer did not project the active decision")
    _require(
        all(f"memory:{memory_id}" not in node_ids for memory_id in inactive_ids),
        "Explorer kept an inactive predecessor",
    )
    return {
        "recall_active": True,
        "task_memory_decision_hits": True,
        "inspect_selected": True,
        "context_compiler_project_decision": True,
        "explore_governed_durable_memory": True,
        "inactive_predecessor_withheld": True,
        "schema": 12,
    }


def _inspect_stage(python: Path, console: Path, root: Path, env: dict[str, str], stored_id: str) -> dict[str, Any]:
    payload = _inspect_report(console, root, env)
    _require(
        payload.get("mutation_boundary") == "read_only_with_respect_to_user_memory_state_and_configuration",
        "inspect did not report its read-only boundary",
    )
    repository = payload.get("repository_knowledge") or {}
    snapshot = repository.get("snapshot") or {}
    fact = _matching_fact(repository.get("selected"), inspect_shape=True)
    _require(snapshot.get("authority") == "derived_repository", "inspect repository authority mismatch")
    _require(snapshot.get("binding_state") == "current", "inspect repository binding was not current")
    _require(fact is not None, "inspect did not expose repository WHAT provenance")
    governed = _governed_context_path(python, console, root, env, active_id=stored_id)
    return {
        "repository_authority": "derived_repository",
        "binding_state": "current",
        "fact_source": EXPECTED_WHAT["source"],
        "mutation_boundary": payload["mutation_boundary"],
        "arbitrary_decision_selection_claimed": False,
        "selected_decision": stored_id,
        "governed_context_path": governed,
    }


def _surface_stage(surface: dict[str, Any]) -> dict[str, Any]:
    names = surface.get("tool_names") or []
    digest = str(surface.get("tool_digest") or "")
    _require(len(names) == EXPECTED_TOOL_COUNT, "public MCP tool count was not 17")
    _require("explore" not in names, "Explorer unexpectedly appeared as a public MCP tool")
    _require(digest == EXPECTED_TOOL_DIGEST, "public tool-schema digest changed")
    return {"tool_count": 17, "explore_is_cli_only": True, "tool_schema_digest": digest, "digest_unchanged": True}


def run_proof(*, expected_version: str, artifact_kind: str) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "result": "fail",
        "expected_version": expected_version,
        "artifact": artifact_kind,
        "stages": [],
    }
    with tempfile.TemporaryDirectory(prefix="amb-v031-release-proof-") as temporary:
        root = Path(temporary).resolve()
        build_env = _build_env(root)
        env = _runtime_env(root)
        stage_index = 0

        def stage(name: str, action: Any) -> Any:
            nonlocal stage_index
            _require(name == STAGES[stage_index], "internal stage order mismatch")
            try:
                evidence = action()
            except Exception as exc:
                reason = str(exc) if isinstance(exc, ProofFailure) else type(exc).__name__
                report["stages"].append({"name": name, "status": "fail", "reason": reason})
                raise ProofFailure(reason) from None
            report["stages"].append({"name": name, "status": "pass", "evidence": evidence})
            stage_index += 1
            return evidence

        wheel, sdist = _build_distributions(root, build_env)
        artifact = wheel if artifact_kind == "wheel" else sdist
        if artifact_kind == "wheel":
            import zipfile

            with zipfile.ZipFile(wheel) as archive:
                _require(
                    not any(name.startswith("tools/") for name in archive.namelist()), "wheel contains evidence tools"
                )
        python, console = _install_artifact(root, artifact, build_env)
        stage(
            STAGES[0],
            lambda: _artifact_stage(python, console, root, env, expected_version, artifact_kind),
        )
        fixture = _create_fixture(root)
        stage(STAGES[1], lambda: _bootstrap_stage(console, fixture, root, env))
        stored_id, store_evidence = _store_stage(python, root, env)
        stage(STAGES[2], lambda: store_evidence)
        recall_evidence, surface = _recall_stage(python, root, env, stored_id)
        stage(STAGES[3], lambda: recall_evidence)
        db_path = Path(env["AGENT_MEMORY_BRIDGE_DB_PATH"])
        snapshot_root = Path(env["AGENT_MEMORY_BRIDGE_REPOSITORY_SNAPSHOT_ROOT"])
        before_explorer_db = _file_digest(db_path)
        before_explorer_snapshots = _tree_digest(snapshot_root / "snapshots")
        before_explorer_bindings = _file_digest(snapshot_root / "bindings.json")
        stage(STAGES[4], lambda: _explorer_stage(console, root, env, stored_id))
        after_explorer_db = _file_digest(db_path)
        after_explorer_snapshots = _tree_digest(snapshot_root / "snapshots")
        after_explorer_bindings = _file_digest(snapshot_root / "bindings.json")
        _require(before_explorer_db == after_explorer_db, "Explorer mutated the durable database")
        _require(before_explorer_snapshots == after_explorer_snapshots, "Explorer mutated repository snapshots")
        _require(before_explorer_bindings == after_explorer_bindings, "Explorer mutated repository bindings")
        initial_governed = _governed_context_path(python, console, root, env, active_id=stored_id)
        _require(initial_governed["task_memory_decision_hits"], "initial project WHY did not reach task memory")
        relation_evidence, intended_ids = _relation_stage(python, console, root, env, stored_id)
        successor_id = intended_ids[-1]
        relation_evidence["governed_context_path"] = _governed_context_path(
            python,
            console,
            root,
            env,
            active_id=successor_id,
            inactive_ids=(stored_id,),
        )
        stage(STAGES[5], lambda: relation_evidence)
        stage(STAGES[6], lambda: _inspect_stage(python, console, root, env, successor_id))
        stage(STAGES[7], lambda: _surface_stage(surface))
        state = _db_state(db_path)
        _require(set(state["memory_ids"]) == set(intended_ids), "unexpected durable memory rows were created")
        _require(state["learning_candidate_count"] == 0, "a learning candidate was created automatically")
        _require(state["feedback_count"] == 0, "retrieval feedback or ranking evidence was mutated")
        _require(not state["repository_fact_in_durable_rows"], "repository facts contaminated durable memory")
        stage(
            STAGES[8],
            lambda: {
                "repository_facts_in_durable_memory": False,
                "durable_memory_count": state["memory_count"],
                "durable_rows_are_only_explicit_store_or_revise_results": True,
                "learning_candidate_count": 0,
                "feedback_count": 0,
                "explorer_database_unchanged": True,
                "explorer_snapshot_content_unchanged": True,
                "automatic_promotion": False,
                "model_or_provider_configuration_inherited": False,
                "runtime_http_proxy_is_local_failure_endpoint": True,
                "product_operations_used": ["local_cli", "local_mcp_stdio", "local_git", "local_sqlite"],
                "model_api_operation_in_proof": False,
                "remote_product_service_operation_in_proof": False,
                "temporary_state_only": True,
            },
        )
    report["result"] = "pass"
    return report


def _failure_report(*, expected_version: str, artifact_kind: str, reason: str) -> dict[str, Any]:
    safe_reason = reason
    if ROOT.as_posix() in safe_reason or str(ROOT) in safe_reason or "/tmp/" in safe_reason or "\\" in safe_reason:
        safe_reason = "proof failed without exposing machine-specific details"
    return {
        "schema": REPORT_SCHEMA,
        "result": "fail",
        "expected_version": expected_version,
        "artifact": artifact_kind,
        "reason": safe_reason[:240],
    }


def render_text(report: dict[str, Any]) -> str:
    lines = ["AMB v0.31 clean-room release proof", ""]
    if report.get("result") == "pass":
        labels = {
            "installed_artifact": "installed artifact",
            "bootstrap_repository_what": "bootstrap WHAT",
            "public_mcp_store_why": "public MCP store WHY",
            "fresh_process_recall": "fresh-process recall",
            "knowledge_explorer": "Knowledge Explorer",
            "relation_governance": "relation governance",
            "inspect_provenance": "inspect provenance",
            "public_surface": "public surface",
            "read_only_contamination": "read-only boundaries",
        }
        for index, item in enumerate(report.get("stages") or [], start=1):
            lines.append(f"[{index}] {labels.get(item['name'], item['name']):<28} PASS")
        lines.extend(("", "RESULT: PASS"))
    else:
        lines.extend((f"FAIL: {report.get('reason', 'proof failed')}", "", "RESULT: FAIL"))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--expected-version", required=True, help="Exact installed package metadata version to require."
    )
    parser.add_argument("--artifact", choices=("wheel", "sdist"), default="wheel")
    parser.add_argument("--json", action="store_true", help="Emit bounded deterministic JSON evidence.")
    args = parser.parse_args(argv)
    try:
        report = run_proof(expected_version=args.expected_version, artifact_kind=args.artifact)
    except Exception as exc:
        report = _failure_report(
            expected_version=args.expected_version,
            artifact_kind=args.artifact,
            reason=str(exc) if isinstance(exc, ProofFailure) else type(exc).__name__,
        )
    print(json.dumps(report, sort_keys=True, separators=(",", ":")) if args.json else render_text(report))
    return 0 if report["result"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
