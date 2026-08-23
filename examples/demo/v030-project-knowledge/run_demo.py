#!/usr/bin/env python3
"""Run the v0.30 Project Knowledge Activation adoption demo locally."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from mcp import StdioServerParameters
from mcp.client import Client
from mcp.client.stdio import stdio_client
from mcp.types import Implementation

PROJECT_ROOT = Path(__file__).resolve().parents[3]
NAMESPACE = "project:demo"
WRITER_CLIENT = "v030-demo-writer"
WHY_TITLE = "Why this fixture remains local-first"
WHY_CLAIM = "Do not introduce Redis."
WHY_REASON = "This committed single-node demo has no shared-cache need; keep the project local-first."
WHY_CONTENT = "\n".join(
    (
        "record_type: decision",
        f"claim: {WHY_CLAIM}",
        f"reason: {WHY_REASON}",
        f"scope: {NAMESPACE}",
        "confidence: observed",
    )
)
QUERY = "What Python 3.11 requirement applies, and why should this project stay local-first without Redis?"
EXPECTED_WHAT = {"key": "python_requires", "value": ">=3.11", "source": "pyproject.toml"}
STAGE_NAMES = (
    "bootstrap_repository_what",
    "stdio_writer_why",
    "fresh_stdio_reader_what_and_why",
    "inspect_repository_what_provenance_and_boundary",
)
REPORT_SCHEMA = "agent-memory-bridge.v030-project-knowledge-demo.v1"
NON_CLAIMS = [
    "This is a local temporary-fixture proof, not external client adoption or identity certification.",
    "It does not prove automatic learning or automatic durable repository writeback.",
    "It does not prove a model applied recalled memory or that recall caused an outcome.",
    "It does not prove agent productivity, recall quality, repository completeness, production readiness, or multi-user coordination.",
]


class DemoFailure(RuntimeError):
    """A stable, reader-facing failure for an unmet demo assertion."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise DemoFailure(message)


def _run_checked(command: list[str], *, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if completed.returncode != 0:
        raise DemoFailure("command failed")
    return completed


def _run_json_command(arguments: list[str], *, environment: dict[str, str]) -> dict[str, Any]:
    completed = _run_checked([sys.executable, "-m", "agent_mem_bridge", *arguments], environment=environment)
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise DemoFailure("command did not return JSON") from exc
    if not isinstance(payload, dict):
        raise DemoFailure("command returned an unexpected JSON shape")
    return payload


def _create_committed_fixture(root: Path) -> Path:
    fixture = root / "fixture"
    fixture.mkdir()
    (fixture / "AGENTS.md").write_text(
        "# Demo fixture instructions\n\nKeep this fixture local-first.\n", encoding="utf-8"
    )
    (fixture / "Makefile").write_text("test:\n\tpython -m pytest\n", encoding="utf-8")
    (fixture / "pyproject.toml").write_text(
        '[project]\nname = "v030-demo-fixture"\nrequires-python = ">=3.11"\n',
        encoding="utf-8",
    )
    (fixture / "src").mkdir()
    (fixture / "src" / "app.py").write_text('def status() -> str:\n    return "ready"\n', encoding="utf-8")
    (fixture / "tests").mkdir()
    (fixture / "tests" / "test_app.py").write_text(
        'from src.app import status\n\n\ndef test_status() -> None:\n    assert status() == "ready"\n',
        encoding="utf-8",
    )
    git_environment = os.environ.copy()
    git_environment.update(
        {
            "GIT_AUTHOR_DATE": "2000-01-01T00:00:00+00:00",
            "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+00:00",
        }
    )
    for command in (
        ["git", "-C", str(fixture), "init", "-q"],
        ["git", "-C", str(fixture), "config", "user.email", "demo@example.invalid"],
        ["git", "-C", str(fixture), "config", "user.name", "AMB v0.30 Demo"],
        ["git", "-C", str(fixture), "add", "."],
        ["git", "-C", str(fixture), "commit", "-qm", "Create committed demo fixture"],
    ):
        completed = subprocess.run(
            command,
            env=git_environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        if completed.returncode != 0:
            raise DemoFailure("temporary Git fixture could not be committed")
    return fixture


def _isolated_environment(root: Path) -> dict[str, str]:
    home = root / "bridge-home"
    config = root / "bridge-config.toml"
    config.write_text('[retrieval]\nmode = "lexical"\n', encoding="utf-8")
    environment = os.environ.copy()
    source_root = str(PROJECT_ROOT / "src")
    inherited_pythonpath = environment.get("PYTHONPATH")
    environment.update(
        {
            "AGENT_MEMORY_BRIDGE_HOME": str(home),
            "AGENT_MEMORY_BRIDGE_DB_PATH": str(home / "bridge.db"),
            "AGENT_MEMORY_BRIDGE_LOG_DIR": str(home / "logs"),
            "AGENT_MEMORY_BRIDGE_REPOSITORY_SNAPSHOT_ROOT": str(root / "repository-snapshots"),
            "AGENT_MEMORY_BRIDGE_CONFIG": str(config),
            "AGENT_MEMORY_BRIDGE_RETRIEVAL_MODE": "lexical",
            "PYTHONPATH": source_root if not inherited_pythonpath else source_root + os.pathsep + inherited_pythonpath,
        }
    )
    return environment


def _matching_repository_fact(items: object, *, inspect_shape: bool = False) -> dict[str, Any] | None:
    if not isinstance(items, list):
        return None
    fact_key = "fact_kind" if inspect_shape else "key"
    for item in items:
        if not isinstance(item, dict):
            continue
        if (
            item.get(fact_key) == EXPECTED_WHAT["key"]
            and item.get("value") == EXPECTED_WHAT["value"]
            and item.get("source") == EXPECTED_WHAT["source"]
        ):
            return item
    return None


def _server_parameters(environment: dict[str, str]) -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "agent_mem_bridge"],
        cwd=str(PROJECT_ROOT),
        env=environment,
    )


async def _call_public_tool(
    environment: dict[str, str], name: str, arguments: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    parameters = _server_parameters(environment)
    client_info = Implementation(name="v030-project-knowledge-demo", version="0.30.0")
    async with Client(
        stdio_client(parameters), mode="auto", client_info=client_info, read_timeout_seconds=30
    ) as client:
        listed = await client.list_tools()
        tool_names = [tool.name for tool in listed.tools]
        _require(name in tool_names, "required public MCP tool is unavailable")
        result = await client.call_tool(name, arguments)
    if result.is_error or not isinstance(result.structured_content, dict):
        raise DemoFailure("public MCP tool call failed")
    return result.structured_content, tool_names


async def _store_why(environment: dict[str, str]) -> dict[str, Any]:
    payload, tool_names = await _call_public_tool(
        environment,
        "store",
        {
            "namespace": NAMESPACE,
            "content": WHY_CONTENT,
            "kind": "memory",
            "title": WHY_TITLE,
            "actor": "v030-demo",
            "source_app": "agent-memory-bridge-v030-demo",
            "source_client": WRITER_CLIENT,
            "client_workspace": "v030-demo-fixture",
            "client_transport": "stdio",
        },
    )
    _require(payload.get("stored") is True, "public MCP store did not persist the WHY record")
    return {"stored": True, "tool_surface_contains_store": "store" in tool_names}


async def _recall_what_and_why(environment: dict[str, str]) -> tuple[dict[str, Any], dict[str, Any]]:
    payload, tool_names = await _call_public_tool(
        environment,
        "recall",
        {"namespace": NAMESPACE, "query": QUERY, "kind": "memory", "limit": 5},
    )
    items = payload.get("items")
    if not isinstance(items, list):
        raise DemoFailure("public MCP recall returned no item list")
    durable_why = next(
        (
            item
            for item in items
            if isinstance(item, dict)
            and item.get("title") == WHY_TITLE
            and item.get("content") == WHY_CONTENT
            and item.get("record_type") == "decision"
            and item.get("tags") == []
            and item.get("source_client") == WRITER_CLIENT
            and item.get("client_transport") == "stdio"
        ),
        None,
    )
    _require(durable_why is not None, "fresh reader did not recall the stored WHY record")
    repository = payload.get("repository_knowledge")
    if not isinstance(repository, dict):
        raise DemoFailure("public MCP recall returned no repository knowledge")
    _require(repository.get("authority") == "derived_repository", "repository authority was not derived")
    _require(repository.get("binding_state") == "current", "repository snapshot was not current")
    fact = _matching_repository_fact(repository.get("selected"))
    _require(fact is not None, "fresh reader did not receive the expected repository WHAT fact")
    _require("recall" in tool_names, "public recall tool was not listed")
    return durable_why, fact


def _bootstrap_stage(fixture: Path, environment: dict[str, str]) -> dict[str, Any]:
    bootstrap = _run_json_command(
        ["bootstrap-repo", str(fixture), "--namespace", NAMESPACE, "--format", "json"], environment=environment
    )
    _require(bootstrap.get("binding") == "git_commit", "bootstrap did not create a commit-bound snapshot")
    binding = bootstrap.get("binding_action")
    _require(
        isinstance(binding, dict) and binding.get("namespace") == NAMESPACE, "bootstrap did not bind the namespace"
    )
    fact = _matching_repository_fact(bootstrap.get("facts"))
    _require(fact is not None, "bootstrap did not extract the expected fixture fact")
    facts = bootstrap.get("facts")
    if not isinstance(facts, list):
        raise DemoFailure("bootstrap did not return repository facts")
    structure = next(
        (item.get("value") for item in facts if isinstance(item, dict) and item.get("key") == "top_level_structure"),
        None,
    )
    task_runner = next(
        (item.get("value") for item in facts if isinstance(item, dict) and item.get("key") == "task_runner"),
        None,
    )
    _require(isinstance(structure, list), "bootstrap did not extract fixture structure")
    _require(task_runner == "Makefile", "bootstrap did not extract the fixture task runner")
    return {
        "command": "bootstrap-repo --format json",
        "binding": bootstrap["binding"],
        "repository_what": EXPECTED_WHAT,
        "fixture_structure": structure,
        "task_runner": task_runner,
    }


def _inspect_stage(environment: dict[str, str]) -> dict[str, Any]:
    inspect = _run_json_command(
        ["inspect", "--namespace", NAMESPACE, "--query", QUERY, "--format", "json", "--technical"],
        environment=environment,
    )
    _require(
        inspect.get("mutation_boundary") == "read_only_with_respect_to_user_memory_state_and_configuration",
        "inspect did not report its read-only boundary",
    )
    repository = inspect.get("repository_knowledge")
    if not isinstance(repository, dict):
        raise DemoFailure("inspect did not return repository knowledge")
    snapshot = repository.get("snapshot")
    _require(isinstance(snapshot, dict), "inspect did not return repository snapshot metadata")
    _require(snapshot.get("authority") == "derived_repository", "inspect did not preserve repository authority")
    _require(snapshot.get("binding_state") == "current", "inspect did not use a current repository snapshot")
    fact = _matching_repository_fact(repository.get("selected"), inspect_shape=True)
    _require(fact is not None, "inspect did not return the expected repository WHAT provenance")
    return {
        "command": "inspect --format json --technical",
        "mutation_boundary": inspect["mutation_boundary"],
        "repository_what_provenance": {
            "key": fact.get("fact_kind"),
            "source": fact.get("source"),
            "authority": fact.get("authority"),
        },
        "repository_authority": snapshot["authority"],
        "repository_binding": snapshot.get("binding"),
        "repository_binding_state": snapshot["binding_state"],
    }


def run_demo() -> dict[str, Any]:
    report = _base_report()
    with tempfile.TemporaryDirectory(prefix="amb-v030-project-knowledge-") as temporary:
        temporary_root = Path(temporary)
        environment = _isolated_environment(temporary_root)
        fixture = _create_committed_fixture(temporary_root)
        stage_actions = (
            lambda: _bootstrap_stage(fixture, environment),
            lambda: asyncio.run(_store_why(environment)),
            lambda: _fresh_reader_stage(environment),
            lambda: _inspect_stage(environment),
        )
        blocked = False
        for name, action in zip(STAGE_NAMES, stage_actions, strict=True):
            if blocked:
                report["stages"].append({"name": name, "status": "fail", "error": "not_run_after_prior_failure"})
                continue
            try:
                evidence = action()
            except Exception as exc:  # The report intentionally hides machine-specific paths and subprocess text.
                report["stages"].append({"name": name, "status": "fail", "error": type(exc).__name__})
                blocked = True
            else:
                report["stages"].append({"name": name, "status": "pass", "evidence": evidence})
    report["ok"] = all(stage["status"] == "pass" for stage in report["stages"])
    return report


def _base_report() -> dict[str, Any]:
    return {
        "schema": REPORT_SCHEMA,
        "ok": False,
        "retrieval_mode": "lexical",
        "stages": [],
        "non_claims": NON_CLAIMS,
    }


def _setup_failure_report(exc: Exception) -> dict[str, Any]:
    report = _base_report()
    report["stages"].append({"name": STAGE_NAMES[0], "status": "fail", "error": type(exc).__name__})
    report["stages"].extend(
        {"name": name, "status": "fail", "error": "not_run_after_prior_failure"} for name in STAGE_NAMES[1:]
    )
    return report


def _fresh_reader_stage(environment: dict[str, str]) -> dict[str, Any]:
    durable_why, fact = asyncio.run(_recall_what_and_why(environment))
    return {
        "entrypoint": "python -m agent_mem_bridge",
        "tool": "recall",
        "fresh_process": True,
        "durable_why_provenance": {
            "source_app": durable_why.get("source_app"),
            "source_client": durable_why.get("source_client"),
            "client_transport": durable_why.get("client_transport"),
        },
        "repository_what": {
            "key": fact.get("key"),
            "value": fact.get("value"),
            "source": fact.get("source"),
            "authority": fact.get("authority"),
        },
    }


def _render_text(report: dict[str, Any]) -> str:
    lines = ["v0.30 Project Knowledge Activation demo"]
    if report["ok"]:
        bootstrap = report["stages"][0]["evidence"]
        reader = report["stages"][2]["evidence"]
        inspect = report["stages"][3]["evidence"]
        what = reader["repository_what"]
        provenance = reader["durable_why_provenance"]
        lines.extend(
            (
                "Code tells WHAT; conversations teach WHY.",
                f"Observed repository WHAT: {what['key']} {what['value']} from {what['source']} "
                f"({what['authority']}; derived, rebuildable, and commit-bound).",
                f"Observed fixture extraction: {', '.join(bootstrap['fixture_structure'])}; task runner {bootstrap['task_runner']}.",
                "Observed explicit project decision (WHY): "
                f"{WHY_CLAIM} Reason: {WHY_REASON} "
                f"({provenance['source_client']} via {provenance['client_transport']}).",
                "Fresh public MCP recall returned both the repository WHAT and project-decision WHY.",
                "Inspect boundary: supported repository WHAT provenance is "
                f"{inspect['repository_what_provenance']['source']} "
                f"({inspect['repository_authority']}; {inspect['repository_binding']}/"
                f"{inspect['repository_binding_state']}) and inspect is read-only.",
            )
        )
    for index, stage in enumerate(report["stages"], start=1):
        lines.append(f"{index}. {stage['name']}: {str(stage['status']).upper()}")
    lines.append(f"Overall: {'PASS' if report['ok'] else 'FAIL'}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Print the bounded deterministic proof report as JSON.")
    arguments = parser.parse_args(argv)
    try:
        report = run_demo()
    except Exception as exc:  # Keep prerequisite/setup failures bounded and path-free.
        report = _setup_failure_report(exc)
    if arguments.json:
        print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    else:
        print(_render_text(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
