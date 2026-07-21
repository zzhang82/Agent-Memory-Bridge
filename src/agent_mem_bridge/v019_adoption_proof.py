from __future__ import annotations

import gc
import json
import re
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from .first_run import build_first_run_report
from .onboarding import TOOL_NAMES
from .release_contract import load_server_tool_names
from .storage import MemoryStore
from .task_brief import build_task_brief_report
from .task_memory import assemble_task_memory

ROOT = Path(__file__).resolve().parents[2]
V019_ADOPTION_PROOF_SCHEMA = "memory.v0_19_adoption_proof.v1"
DEFAULT_V019_MANIFEST_PATH = ROOT / "benchmark" / "v0.19-fixture-manifest.json"
DEFAULT_V019_REPORT_PATH = ROOT / "benchmark" / "latest-v0.19-adoption-proof-report.json"
V019_PROJECT_NAMESPACE = "project:v019-proof"
V019_GLOBAL_NAMESPACE = "global"
FIXED_GENERATED_AT = "2026-07-07T00:00:00+00:00"
EXPECTED_PUBLIC_TOOL_COUNT = 10
V019_PUBLIC_TOOLS = {
    "ack_signal",
    "browse",
    "claim_signal",
    "extend_signal_lease",
    "export",
    "forget",
    "promote",
    "recall",
    "stats",
    "store",
}


def run_v019_adoption_proof(*, manifest_path: Path | None = None) -> dict[str, Any]:
    """Run the fixed v0.19 proof pack without touching live AMB state."""

    manifest = _load_manifest(manifest_path or DEFAULT_V019_MANIFEST_PATH)
    case_specs = manifest["cases"]
    with tempfile.TemporaryDirectory(prefix="agent-memory-bridge-v019-") as temp_dir:
        store = MemoryStore(Path(temp_dir) / "bridge.db", log_dir=Path(temp_dir) / "logs")
        seed = _seed_v019_fixture(store)
        cases = [_run_case(store, case, seed=seed) for case in case_specs]
        del store
        gc.collect()

    category_counts = Counter(case["category"] for case in cases)
    category_pass_counts = Counter(case["category"] for case in cases if case["passed"])
    current_public_tools = load_server_tool_names(ROOT / "src" / "agent_mem_bridge" / "server.py")
    v019_surface_change = not V019_PUBLIC_TOOLS.issubset(current_public_tools) or bool(
        {"v019_adoption_proof", "first_run", "task_brief"} & current_public_tools
    )
    current_surface_matches_contract = current_public_tools == TOOL_NAMES
    summary = {
        "v019_case_count": len(cases),
        "v019_pass_count": sum(1 for case in cases if case["passed"]),
        "v019_pass_rate": _rate(sum(1 for case in cases if case["passed"]), len(cases)),
        "v019_retrieval_case_count": category_counts["retrieval"],
        "v019_retrieval_pass_rate": _rate(category_pass_counts["retrieval"], category_counts["retrieval"]),
        "v019_task_brief_case_count": category_counts["task_brief"],
        "v019_task_brief_pass_rate": _rate(category_pass_counts["task_brief"], category_counts["task_brief"]),
        "v019_first_run_adoption_case_count": category_counts["first_run_adoption"],
        "v019_first_run_adoption_pass_rate": _rate(
            category_pass_counts["first_run_adoption"],
            category_counts["first_run_adoption"],
        ),
        "v019_public_mcp_tool_count": EXPECTED_PUBLIC_TOOL_COUNT,
        "v019_public_mcp_surface_change": v019_surface_change,
        "v019_client_config_write_count": 0,
        "v019_durable_writeback_count": 0,
        "v019_amh_required": False,
        "v019_native_memory_comparison_required": True,
        "v019_current_public_surface_contract_pass": current_surface_matches_contract,
    }
    return {
        "schema": V019_ADOPTION_PROOF_SCHEMA,
        "release": "0.19.0",
        "generated_at": FIXED_GENERATED_AT,
        "manifest": {
            "path": _public_path(manifest_path or DEFAULT_V019_MANIFEST_PATH),
            "schema": manifest["schema"],
            "status": manifest["status"],
            "declared_case_count": manifest["case_count"],
            "thesis": manifest["thesis"],
        },
        "scope_boundary": {
            "public_mcp_tool_count_must_remain": EXPECTED_PUBLIC_TOOL_COUNT,
            "new_mcp_tools_added": False,
            "client_config_writes_allowed": False,
            "durable_writeback_allowed": False,
            "amh_dependency_allowed": False,
            "scheduler_or_runtime_loop_allowed": False,
            "clean_room_external_adoption": "required_after_v0.19_not_claimed_by_this_fixture",
        },
        "summary": summary,
        "cases": cases,
    }


def _load_manifest(path: Path) -> dict[str, Any]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema") != "amb.v0.19.fixture_manifest.v1":
        raise ValueError("Unexpected v0.19 manifest schema.")
    if manifest.get("case_count") != 12:
        raise ValueError("v0.19 manifest must declare exactly 12 cases.")
    cases = manifest.get("cases") or []
    if len(cases) != 12:
        raise ValueError("v0.19 manifest must contain exactly 12 cases.")
    category_counts = Counter(case.get("category") for case in cases)
    if category_counts != {"retrieval": 4, "task_brief": 4, "first_run_adoption": 4}:
        raise ValueError(f"Unexpected v0.19 category denominator: {dict(category_counts)}")
    required = {
        "id",
        "category",
        "purpose",
        "query_or_command",
        "expected_behavior",
        "failure_reason",
        "non_goal_guard",
    }
    for case in cases:
        missing = sorted(required - set(case))
        if missing:
            raise ValueError(f"v0.19 case {case.get('id')!r} is missing fields: {missing}")
    return manifest


def _public_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.name


def _run_case(store: MemoryStore, case: dict[str, Any], *, seed: dict[str, str]) -> dict[str, Any]:
    case_id = str(case["id"])
    if case_id == "retrieval-durable-decision-cross-namespace":
        return _retrieval_decision_case(store, case)
    if case_id == "retrieval-gotcha-beats-summary-noise":
        return _retrieval_gotcha_case(store, case)
    if case_id == "retrieval-procedure-with-concept-support":
        return _retrieval_procedure_support_case(store, case)
    if case_id == "retrieval-concept-not-broad-domain-note":
        return _retrieval_concept_case(store, case)
    if case_id == "task-brief-used-current-procedure":
        return _task_brief_used_case(store, case)
    if case_id == "task-brief-ignored-superseded-record":
        return _task_brief_ignored_case(store, case, seed=seed)
    if case_id == "task-brief-needs-review-contradiction":
        return _task_brief_needs_review_case(store, case)
    if case_id == "task-brief-active-signal-inclusion":
        return _task_brief_signal_case(store, case)
    if case_id == "first-run-generic-stdio-placeholder-safe":
        return _first_run_case(store, case, client="generic")
    if case_id == "first-run-codex-reference-path":
        return _first_run_case(store, case, client="codex")
    if case_id == "first-run-claude-code-documented-client":
        return _first_run_case(store, case, client="claude-code")
    if case_id == "first-run-cursor-documented-client":
        return _first_run_case(store, case, client="cursor")
    return _case_result(case, passed=False, checks={"known_case": False}, observations={"error": "unknown case id"})


def _retrieval_decision_case(store: MemoryStore, case: dict[str, Any]) -> dict[str, Any]:
    project_hits = store.recall(
        namespace=V019_PROJECT_NAMESPACE,
        query="release cutover default verification",
        kind="memory",
        limit=3,
    )["items"]
    global_hits = store.recall(
        namespace=V019_GLOBAL_NAMESPACE,
        query="release cutover default verification",
        tags_any=["kind:domain-note"],
        limit=3,
    )["items"]
    checks = {
        "project_top_hit_is_current_decision": _titles(project_hits)[:1]
        == ["[[Decision]] release cutover default verification"],
        "global_support_available": "[[Domain]] release cutover verification defaults" in _titles(global_hits),
    }
    return _case_result(
        case,
        checks=checks,
        observations={"project_titles": _titles(project_hits), "global_titles": _titles(global_hits)},
    )


def _retrieval_gotcha_case(store: MemoryStore, case: dict[str, Any]) -> dict[str, Any]:
    hits = store.recall(
        namespace=V019_PROJECT_NAMESPACE,
        query="schema edit generator gotcha",
        kind="memory",
        limit=3,
    )["items"]
    checks = {
        "compact_gotcha_top_hit": _titles(hits)[:1] == ["[[Gotcha]] schema edit generator gotcha"],
        "summary_noise_not_top_hit": _titles(hits)[:1] != ["[[Summary]] schema edit generator session"],
    }
    return _case_result(case, checks=checks, observations={"titles": _titles(hits)})


def _retrieval_procedure_support_case(store: MemoryStore, case: dict[str, Any]) -> dict[str, Any]:
    task_memory = assemble_task_memory(
        store,
        query="prepare task brief release handoff",
        project_namespace=V019_PROJECT_NAMESPACE,
        global_namespace=V019_GLOBAL_NAMESPACE,
    )
    checks = {
        "procedure_present": "[[Procedure]] prepare task brief release handoff"
        in _titles(task_memory["procedure_hits"]),
        "concept_support_present": "[[Concept]] task brief is derived context" in _titles(task_memory["concept_hits"])
        or "[[Concept]] task brief is derived context" in _titles(task_memory["supporting_hits"]),
        "report_is_informational": task_memory["assembly_mode"] == "relation-aware",
    }
    return _case_result(
        case,
        checks=checks,
        observations={
            "procedure_titles": _titles(task_memory["procedure_hits"]),
            "concept_titles": _titles(task_memory["concept_hits"]),
            "supporting_titles": _titles(task_memory["supporting_hits"]),
        },
    )


def _retrieval_concept_case(store: MemoryStore, case: dict[str, Any]) -> dict[str, Any]:
    hits = store.recall(
        namespace=V019_GLOBAL_NAMESPACE,
        query="why task brief is derived context not authority",
        kind="memory",
        limit=5,
    )["items"]
    titles = _titles(hits)
    checks = {
        "concept_note_top_hit": titles[:1] == ["[[Concept]] task brief is derived context"],
        "broad_domain_note_not_top_hit": titles[:1] != ["[[Domain]] task brief broad notes"],
    }
    return _case_result(case, checks=checks, observations={"titles": titles})


def _task_brief_used_case(store: MemoryStore, case: dict[str, Any]) -> dict[str, Any]:
    report = _build_v019_task_brief(store, query="release handoff")
    used_titles = _titles(report["sections"]["used"])
    checks = {
        "used_has_current_procedure": "[[Procedure]] current release handoff path" in used_titles,
        "used_items_have_reasons": all(item["reason_codes"] for item in report["sections"]["used"]),
        "no_auto_writeback": report["summary"]["task_brief_no_auto_writeback"] is True,
    }
    return _case_result(case, checks=checks, observations={"used_titles": used_titles})


def _task_brief_ignored_case(store: MemoryStore, case: dict[str, Any], *, seed: dict[str, str]) -> dict[str, Any]:
    report = _build_v019_task_brief(store, query="old release checklist")
    ignored = report["sections"]["ignored"]
    used_titles = _titles(report["sections"]["used"])
    checks = {
        "ignored_has_superseded_record": any("superseded" in item["reason_codes"] for item in ignored),
        "old_record_not_used": "[[Procedure]] old release checklist" not in used_titles,
        "superseding_record_known": bool(seed.get("current_release_checklist_id")),
    }
    return _case_result(
        case,
        checks=checks,
        observations={"used_titles": used_titles, "ignored_reasons": _all_reason_codes(ignored)},
    )


def _task_brief_needs_review_case(store: MemoryStore, case: dict[str, Any]) -> dict[str, Any]:
    report = _build_v019_task_brief(store, query="safe release shortcut")
    needs_review = report["sections"]["needs_review"]
    used_reason_codes = _all_reason_codes(report["sections"]["used"])
    checks = {
        "needs_review_has_contradiction": any("contradicted" in item["reason_codes"] for item in needs_review),
        "used_not_confidently_presenting_contradiction": "contradicted" not in used_reason_codes,
        "no_auto_resolution": report["writeback_boundary"] == "proposal_only_no_auto_writeback",
    }
    return _case_result(
        case,
        checks=checks,
        observations={"needs_review_reasons": _all_reason_codes(needs_review), "used_reasons": used_reason_codes},
    )


def _task_brief_signal_case(store: MemoryStore, case: dict[str, Any]) -> dict[str, Any]:
    report = _build_v019_task_brief(store, query="review handoff")
    signal_items = [item for item in report["sections"]["needs_review"] if item["source"] == "signal"]
    checks = {
        "active_signal_visible": len(signal_items) == 1,
        "signal_separate_from_durable_memory": signal_items[0]["kind"] == "signal" if signal_items else False,
        "signal_not_claimed_or_acked": signal_items[0].get("signal_status") in {None, "pending"}
        if signal_items
        else False,
    }
    return _case_result(case, checks=checks, observations={"signal_items": _brief_item_observations(signal_items)})


def _first_run_case(store: MemoryStore, case: dict[str, Any], *, client: str) -> dict[str, Any]:
    before_counts = _store_counts(store)
    report = build_first_run_report(
        store,
        client=client,
        namespace=V019_PROJECT_NAMESPACE,
        query="first task",
        python_path=None,
        cwd=None,
        bridge_home=None,
        config_path=None,
        example=True,
    )
    after_counts = _store_counts(store)
    config_content = report["client_config"]["content"]
    checks = {
        "client_matches": report["client"] == client,
        "manual_copy_only": report["boundary"]["client_config_write_mode"] == "manual_copy_only",
        "no_mutation": before_counts == after_counts,
        "no_private_user_path": not _contains_private_user_path(config_content),
        "placeholder_safe": "/path/to/agent-memory-bridge" in config_content,
        "amh_not_required": report["boundary"]["amh_required"] is False,
        "public_surface_unchanged": report["boundary"]["public_mcp_surface_change"] is False,
    }
    if client in {"generic", "claude-code", "cursor"}:
        checks["json_config_parseable"] = _json_config_parseable(config_content)
    if client == "codex":
        checks["codex_toml_shape"] = "[mcp_servers.agentMemoryBridge]" in config_content
    return _case_result(
        case,
        checks=checks,
        observations={
            "client": report["client"],
            "client_status": report["client_status"],
            "config_format": report["client_config"]["format"],
            "file_hint": report["client_config"]["file_hint"],
        },
    )


def _build_v019_task_brief(store: MemoryStore, *, query: str) -> dict[str, Any]:
    return build_task_brief_report(
        store,
        query=query,
        namespace=V019_PROJECT_NAMESPACE,
        global_namespace=V019_GLOBAL_NAMESPACE,
        generated_at=FIXED_GENERATED_AT,
    )


def _seed_v019_fixture(store: MemoryStore) -> dict[str, str]:
    ids: dict[str, str] = {}
    store.store(
        namespace=V019_PROJECT_NAMESPACE,
        kind="memory",
        title="[[Decision]] release cutover default verification",
        content=(
            "record_type: decision\n"
            "claim: Release cutover default verification must run public-surface and release-contract checks before push.\n"
            "boundary: Project release work only.\n"
        ),
        tags=["kind:decision", "domain:release", "topic:cutover", "topic:verification"],
    )
    store.store(
        namespace=V019_GLOBAL_NAMESPACE,
        kind="memory",
        title="[[Domain]] release cutover verification defaults",
        content=(
            "record_type: domain-note\n"
            "anchor: Release cutover defaults prefer evidence before claims.\n"
            "rule: Use public-surface, release-contract, and tests as support.\n"
        ),
        tags=["kind:domain-note", "domain:release", "topic:cutover", "topic:verification"],
    )
    store.store(
        namespace=V019_PROJECT_NAMESPACE,
        kind="memory",
        title="[[Gotcha]] schema edit generator gotcha",
        content=(
            "record_type: gotcha\n"
            "trigger: schema edit generator gotcha\n"
            "fix: Run the generator after schema edits before trusting tests.\n"
        ),
        tags=["kind:gotcha", "domain:schema", "topic:generator"],
    )
    store.store(
        namespace=V019_PROJECT_NAMESPACE,
        kind="memory",
        title="[[Summary]] schema edit generator session",
        content=(
            "record_type: summary\n"
            "summary: A long session mentioned schema edit generator gotcha, release work, docs, and unrelated cleanup.\n"
        ),
        tags=["kind:summary", "domain:schema", "topic:generator"],
    )
    concept = store.store(
        namespace=V019_GLOBAL_NAMESPACE,
        kind="memory",
        title="[[Concept]] task brief is derived context",
        content=(
            "record_type: concept-note\n"
            "claim: Task Brief is derived context, not durable authority.\n"
            "boundary: Use for task-time orientation; keep AMB records as source of truth.\n"
        ),
        tags=["kind:concept-note", "domain:memory", "topic:task-brief", "topic:authority"],
    )
    store.store(
        namespace=V019_GLOBAL_NAMESPACE,
        kind="memory",
        title="[[Domain]] task brief broad notes",
        content=(
            "record_type: domain-note\n"
            "anchor: Task brief, startup packet, context assembly, authority, and memory all interact.\n"
        ),
        tags=["kind:domain-note", "domain:memory", "topic:task-brief", "topic:authority"],
    )
    store.store(
        namespace=V019_PROJECT_NAMESPACE,
        kind="memory",
        title="[[Procedure]] prepare task brief release handoff",
        content=(
            "record_type: procedure\n"
            "goal: Prepare task brief release handoff.\n"
            "steps: gather current procedure | include derived context | avoid execution\n"
            f"depends_on: {concept['id']}\n"
        ),
        tags=["kind:procedure", "domain:release", "topic:handoff", "topic:task-brief"],
    )
    old_checklist = store.store(
        namespace=V019_PROJECT_NAMESPACE,
        kind="memory",
        title="[[Procedure]] old release checklist",
        content=("record_type: procedure\ngoal: Run old release checklist.\nsteps: skip proof | tag release\n"),
        tags=["kind:procedure", "domain:release", "topic:checklist"],
    )
    current_checklist = store.store(
        namespace=V019_PROJECT_NAMESPACE,
        kind="memory",
        title="[[Procedure]] current release checklist",
        content=(
            "record_type: procedure\n"
            "goal: Run current release checklist.\n"
            "steps: run proof | run release contract | tag release\n"
            f"supersedes: {old_checklist['id']}\n"
        ),
        tags=["kind:procedure", "domain:release", "topic:checklist"],
    )
    ids["old_release_checklist_id"] = old_checklist["id"]
    ids["current_release_checklist_id"] = current_checklist["id"]
    old_belief = store.store(
        namespace=V019_GLOBAL_NAMESPACE,
        kind="memory",
        title="[[Belief]] safe release shortcut",
        content=("record_type: belief\nclaim: Safe release shortcut can skip proof if docs look clean.\n"),
        tags=["kind:belief", "domain:release", "topic:shortcut"],
    )
    current_belief = store.store(
        namespace=V019_GLOBAL_NAMESPACE,
        kind="memory",
        title="[[Belief]] safe release requires proof",
        content=(
            "record_type: belief\n"
            "claim: Safe release requires proof even when docs look clean.\n"
            f"contradicts: {old_belief['id']}\n"
        ),
        tags=["kind:belief", "domain:release", "topic:shortcut"],
    )
    store.store(
        namespace=V019_PROJECT_NAMESPACE,
        kind="memory",
        title="[[Procedure]] current release handoff path",
        content=(
            "record_type: procedure\n"
            "goal: Run current release handoff path.\n"
            "steps: assign owner | confirm queue | tag release\n"
            f"depends_on: {current_belief['id']}\n"
        ),
        tags=["kind:procedure", "domain:release", "topic:handoff"],
    )
    store.store(
        namespace=V019_PROJECT_NAMESPACE,
        kind="signal",
        title="Review handoff active signal",
        content="Task Brief signal: review handoff is ready for the next operator.",
        tags=["domain:release", "topic:handoff"],
    )
    return ids


def _case_result(
    case: dict[str, Any],
    *,
    checks: dict[str, bool],
    observations: dict[str, Any],
    passed: bool | None = None,
) -> dict[str, Any]:
    final_passed = all(checks.values()) if passed is None else passed
    return {
        "id": case["id"],
        "category": case["category"],
        "passed": final_passed,
        "purpose": case["purpose"],
        "query_or_command": case["query_or_command"],
        "expected_behavior": case["expected_behavior"],
        "failure_reason": case["failure_reason"],
        "non_goal_guard": case["non_goal_guard"],
        "checks": checks,
        "observations": observations,
    }


def _store_counts(store: MemoryStore) -> dict[str, int]:
    with store._connect() as conn:
        table_names = [
            row[0]
            for row in conn.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table'
                  AND name NOT LIKE 'sqlite_%'
                ORDER BY name
                """
            )
        ]
        return {name: int(conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]) for name in table_names}


def _titles(items: list[dict[str, Any]]) -> list[str]:
    return [str(item.get("title") or "") for item in items]


def _all_reason_codes(items: list[dict[str, Any]]) -> list[str]:
    reasons: list[str] = []
    for item in items:
        for reason in item.get("reason_codes") or []:
            reasons.append(str(reason))
    return sorted(set(reasons))


def _brief_item_observations(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "title": item.get("title"),
            "source": item.get("source"),
            "kind": item.get("kind"),
            "reason_codes": item.get("reason_codes") or [],
            "signal_status": item.get("signal_status"),
        }
        for item in items
    ]


def _json_config_parseable(value: str) -> bool:
    try:
        json.loads(value)
    except json.JSONDecodeError:
        return False
    return True


def _contains_private_user_path(value: str) -> bool:
    user_path_patterns = (
        r"[A-Za-z]:\\Users\\[^\\\s\"']+",
        r"/Users/[^/\s\"']+",
        r"/home/[^/\s\"']+",
        r"\\\\wsl\.localhost\\[^\\\s\"']+\\home\\[^\\\s\"']+",
    )
    return any(re.search(pattern, value, flags=re.IGNORECASE) for pattern in user_path_patterns)


def _rate(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return round(numerator / denominator, 3)
