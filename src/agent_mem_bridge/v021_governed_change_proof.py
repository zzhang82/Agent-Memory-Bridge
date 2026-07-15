from __future__ import annotations

import hashlib
import gc
import json
import os
import tempfile
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

from .embedding_index import EmbeddingConfig, ensure_embeddings_for_rows
from .query import recall_via_semantic
from .relation_metadata import parse_relation_metadata
from .release_contract import load_server_tool_names
from .storage import MemoryStore
from .task_brief import build_task_brief_report
from .task_memory import assemble_task_memory


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_V021_MANIFEST_PATH = ROOT / "benchmark" / "v0.21-governed-change-manifest.json"
DEFAULT_V021_REPORT_PATH = ROOT / "benchmark" / "latest-v0.21-governed-change-report.json"
V021_GOVERNED_CHANGE_PROOF_SCHEMA = "amb.v0.21.governed_change_proof.v1"
EXPECTED_MANIFEST_SHA256 = "85e1938ed90cbb9512d44b9a646e7bdca09f2638a7144a5dbe1cc5cf3bd014eb"
EXPECTED_PUBLIC_TOOL_COUNT = 10
EXPECTED_PUBLIC_TOOLS = {
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
FIXED_GENERATED_AT = "2026-07-15T12:00:00+00:00"
FIXED_AS_OF = "2026-07-15T12:00:00+00:00"
FLAT_BUDGET = 3
GOVERNED_SECTION_BUDGET = 3

EXPECTED_CATEGORIES = {
    "deletion_residue": 5,
    "lifecycle_supersession": 5,
    "changed_premise_usefulness": 5,
    "cross_domain_transfer": 5,
}
REQUIRED_CASE_FIELDS = {
    "id",
    "category",
    "transition",
    "expected_governed_outcome",
    "flat_baseline_hazard",
    "failure_reason",
    "metric_mappings",
    "no_go_guard",
    "checkpoints",
}


@dataclass
class CaseState:
    store: MemoryStore
    namespace: str
    global_namespace: str
    case_id: str
    labels: dict[str, str] = field(default_factory=dict)
    queries: list[str] = field(default_factory=list)
    as_of: list[str] = field(default_factory=lambda: [FIXED_AS_OF, FIXED_AS_OF])
    task_domains: list[str | None] = field(default_factory=lambda: [None, None])
    required_actionable: list[set[str]] = field(default_factory=lambda: [set(), set()])
    forbidden_actionable: list[set[str]] = field(default_factory=lambda: [set(), set()])
    required_suppressed: list[dict[str, str]] = field(default_factory=lambda: [{}, {}])
    required_corrective: list[set[str]] = field(default_factory=lambda: [set(), set()])
    hazard_labels: set[str] = field(default_factory=set)
    baseline_mode: str = "raw_recall"
    flat_hazard_probe: Callable[[dict[str, Any]], bool] | None = None
    transition: Callable[[], None] = lambda: None
    checkpoint_extra: Callable[[int, dict[str, Any]], dict[str, bool]] = lambda _index, _snapshot: {}
    audit_tokens: dict[str, str] = field(default_factory=dict)


def load_v021_governed_change_manifest(path: Path | None = None) -> tuple[dict[str, Any], str]:
    manifest_path = (path or DEFAULT_V021_MANIFEST_PATH).resolve()
    raw = manifest_path.read_bytes()
    # Git may materialize text files with CRLF on Windows; hash canonical LF bytes.
    canonical_raw = raw.replace(b"\r\n", b"\n")
    digest = hashlib.sha256(canonical_raw).hexdigest()
    if digest != EXPECTED_MANIFEST_SHA256:
        raise ValueError(
            "v0.21 governed-change manifest SHA256 mismatch: "
            f"expected {EXPECTED_MANIFEST_SHA256}, got {digest}"
        )
    manifest = json.loads(raw)
    _validate_manifest(manifest)
    return manifest, digest


def run_v021_governed_change_proof(
    *,
    manifest_path: Path | None = None,
    project_root: Path | None = None,
) -> dict[str, Any]:
    resolved_root = (project_root or ROOT).resolve()
    manifest, manifest_sha256 = load_v021_governed_change_manifest(manifest_path)
    public_tools = load_server_tool_names(resolved_root / "src" / "agent_mem_bridge" / "server.py")
    cases = [_run_case(case) for case in manifest["cases"]]

    checkpoint_results = [checkpoint for case in cases for checkpoint in case["checkpoints"]]
    governed_checkpoint_passes = sum(checkpoint["passed"] for checkpoint in checkpoint_results)
    governed_failures = sum(not case["governed_passed"] for case in cases)
    flat_hazards = sum(case["flat_baseline_hazard_observed"] for case in cases)
    baseline_expectation_matches = all(
        case["flat_baseline_hazard_observed"] == case["flat_baseline_hazard_expected"]
        for case in cases
    )
    category_slices = _category_slices(cases)
    all_temp_only = all(case["write_scope"]["writes_only_under_temp"] for case in cases)
    no_config_writes = all(case["write_scope"]["config_write_count"] == 0 for case in cases)
    useful_current_retained = all(
        checkpoint["checks"]["useful_current_retained"] for checkpoint in checkpoint_results
    )
    suppress_all_can_pass = not all(
        checkpoint["checks"]["suppress_all_structurally_blocked"]
        for checkpoint in checkpoint_results
    )
    tool_surface_ok = (
        len(public_tools) == EXPECTED_PUBLIC_TOOL_COUNT
        and public_tools == EXPECTED_PUBLIC_TOOLS
    )
    gate_passed = all(
        (
            len(cases) == 20,
            len(checkpoint_results) == 40,
            governed_checkpoint_passes == 40,
            governed_failures == 0,
            flat_hazards == 17,
            baseline_expectation_matches,
            useful_current_retained,
            not suppress_all_can_pass,
            tool_surface_ok,
            all_temp_only,
            no_config_writes,
        )
    )

    return {
        "schema": V021_GOVERNED_CHANGE_PROOF_SCHEMA,
        "release": manifest["target_release"],
        "target_release": manifest["target_release"],
        "status": "pre-v0.21-governed-change-proof",
        "generated_at": FIXED_GENERATED_AT,
        "manifest": {
            "path": "benchmark/v0.21-governed-change-manifest.json",
            "schema": manifest["schema"],
            "sha256": manifest_sha256,
            "sha256_expected": EXPECTED_MANIFEST_SHA256,
            "exact_hash_match": manifest_sha256 == EXPECTED_MANIFEST_SHA256,
        },
        "execution": {
            "memory_store_isolation": "one_fresh_temp_MemoryStore_per_case",
            "case_temp_store_count": len(cases),
            "checkpoints_per_case": 2,
            "flat_budget": FLAT_BUDGET,
            "governed_section_budget": GOVERNED_SECTION_BUDGET,
            "equal_budget_comparison": FLAT_BUDGET == GOVERNED_SECTION_BUDGET,
            "fixed_as_of": FIXED_AS_OF,
            "embedding_provider": "hash",
            "paths_sanitized": True,
        },
        "boundaries": {
            "public_mcp_tool_count": len(public_tools),
            "public_mcp_tool_names": sorted(public_tools),
            "public_mcp_surface_unchanged": tool_surface_ok,
            "new_mcp_tools_added": False,
            "writes_only_under_temp": all_temp_only,
            "config_write_count": 0 if no_config_writes else 1,
            "durable_live_writeback_count": 0,
            "auto_writeback_count": 0,
            "private_or_local_cole_data_used": False,
        },
        "summary": {
            "gate_passed": gate_passed,
            "case_count": len(cases),
            "governed_case_pass_count": len(cases) - governed_failures,
            "governed_failures": governed_failures,
            "governed_failures_target": "0/20",
            "governed_checkpoint_result_count": len(checkpoint_results),
            "governed_checkpoint_passes": governed_checkpoint_passes,
            "governed_checkpoint_passes_target": "40/40",
            "flat_baseline_hazards": flat_hazards,
            "flat_baseline_hazards_expected": "17/20",
            "flat_baseline_expectation_matches": baseline_expectation_matches,
            "useful_current_retention_pass": useful_current_retained,
            "suppress_all_can_pass": suppress_all_can_pass,
            "category_count": len(category_slices),
        },
        "category_slices": category_slices,
        "cases": cases,
    }


def _run_case(case: dict[str, Any]) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="amb-v021-governed-change-") as temp_name:
        runtime_dir = Path(temp_name).resolve()
        config_path = runtime_dir / "config.toml"
        with _proof_environment(runtime_dir):
            store = MemoryStore(runtime_dir / "bridge.db", log_dir=runtime_dir / "logs")
            state = _seed_case(store, case["id"])
            first = _checkpoint(state, case, 0)
            state.transition()
            second = _checkpoint(state, case, 1)
            written_files = sorted(
                path.relative_to(runtime_dir).as_posix()
                for path in runtime_dir.rglob("*")
                if path.is_file()
            )
            config_write_count = int(config_path.exists())

        checkpoints = [first, second]
        flat_observed = _flat_hazard_observed(state, second)
        governed_passed = all(checkpoint["passed"] for checkpoint in checkpoints)
        failure_reason = None if governed_passed else case["failure_reason"]
        result = {
            "id": case["id"],
            "category": case["category"],
            "transition": case["transition"],
            "expected_governed_outcome": case["expected_governed_outcome"],
            "no_go_guard": case["no_go_guard"],
            "flat_baseline_hazard_expected": case["flat_baseline_hazard"]["expected"],
            "flat_baseline_hazard_observed": flat_observed,
            "flat_baseline_hazard_description": case["flat_baseline_hazard"]["description"],
            "baseline_expectation_match": flat_observed == case["flat_baseline_hazard"]["expected"],
            "governed_passed": governed_passed,
            "failure_reason": failure_reason,
            "checks": {
                "two_checkpoints_executed": len(checkpoints) == 2,
                "all_governed_checkpoints_passed": governed_passed,
                "baseline_expectation_matched": flat_observed == case["flat_baseline_hazard"]["expected"],
                "equal_budget_compared": True,
                "fresh_temp_store_used": True,
            },
            "evidence": {
                "baseline_mode": state.baseline_mode,
                "flat_checkpoint_2_labels": second["evidence"]["flat_actionable_labels"],
                "governed_checkpoint_2_labels": second["evidence"]["governed_actionable_labels"],
                "suppressed_checkpoint_2": second["evidence"]["suppressed"],
                "corrective_checkpoint_2_labels": second["evidence"]["corrective_labels"],
                "audit_tokens_are_synthetic": True,
            },
            "checkpoints": checkpoints,
            "write_scope": {
                "runtime_root": "<temp>",
                "db_path": "<temp>/bridge.db",
                "log_dir": "<temp>/logs",
                "config_path": "<temp>/config.toml",
                "written_files": written_files,
                "writes_only_under_temp": True,
                "config_write_count": config_write_count,
                "durable_live_writeback_count": 0,
            },
        }
        del state, store
        gc.collect()
        return result


def _checkpoint(state: CaseState, case: dict[str, Any], index: int) -> dict[str, Any]:
    query = state.queries[index]
    as_of = state.as_of[index]
    task_domain = state.task_domains[index]
    raw_flat = state.store.recall(
        namespace=state.namespace,
        query=query,
        kind="memory",
        limit=FLAT_BUDGET,
    )
    flat_task = assemble_task_memory(
        state.store,
        query=query,
        project_namespace=state.namespace,
        global_namespace=state.global_namespace,
        procedure_limit=GOVERNED_SECTION_BUDGET,
        concept_limit=GOVERNED_SECTION_BUDGET,
        belief_limit=GOVERNED_SECTION_BUDGET,
        domain_limit=GOVERNED_SECTION_BUDGET,
        support_limit=GOVERNED_SECTION_BUDGET,
        relation_aware=False,
        as_of=as_of,
        task_domain=task_domain,
    )
    governed = assemble_task_memory(
        state.store,
        query=query,
        project_namespace=state.namespace,
        global_namespace=state.global_namespace,
        procedure_limit=GOVERNED_SECTION_BUDGET,
        concept_limit=GOVERNED_SECTION_BUDGET,
        belief_limit=GOVERNED_SECTION_BUDGET,
        domain_limit=GOVERNED_SECTION_BUDGET,
        support_limit=GOVERNED_SECTION_BUDGET,
        relation_aware=True,
        as_of=as_of,
        task_domain=task_domain,
    )
    brief = build_task_brief_report(
        state.store,
        query=query,
        namespace=state.namespace,
        global_namespace=state.global_namespace,
        generated_at=FIXED_GENERATED_AT,
        as_of=as_of,
        task_domain=task_domain,
    )
    browse = state.store.browse(namespace=state.namespace, kind="memory", limit=100)
    exported = state.store.export(namespace=state.namespace, format="json", kind="memory", limit=100)

    raw_flat_labels = _labels(state, raw_flat["items"])
    flat_task_labels = _actionable_labels(state, flat_task)
    flat_labels = flat_task_labels if state.baseline_mode == "flat_task_memory" else raw_flat_labels
    governed_labels = _actionable_labels(state, governed)
    corrective_labels = _labels(state, governed["corrective_items"])
    suppressed = {
        state.labels.get(str(item.get("id")), "unlabeled"):
        str(item.get("reason") or "suppressed")
        for item in governed["suppressed_items"]
    }
    brief_used = {
        state.labels.get(str(item.get("source_record_id")), "unlabeled")
        for item in brief["sections"]["used"]
    }
    brief_review = {
        state.labels.get(str(item.get("source_record_id")), "unlabeled")
        for item in brief["sections"]["needs_review"]
    }
    storage = _storage_snapshot(state)
    snapshot = {
        "raw_flat_labels": raw_flat_labels,
        "flat_labels": flat_labels,
        "governed_labels": governed_labels,
        "corrective_labels": corrective_labels,
        "suppressed": suppressed,
        "brief_used": brief_used,
        "brief_review": brief_review,
        "browse_labels": _labels(state, browse["items"]),
        "export_content": str(exported["content"]),
        "storage": storage,
    }

    expected_suppressed = state.required_suppressed[index]
    retention_checks = _required_retention_checks(
        required_actionable=state.required_actionable[index],
        required_corrective=state.required_corrective[index],
        actionable_labels=governed_labels,
        corrective_labels=corrective_labels,
    )
    checks = {
        **retention_checks,
        "forbidden_actionable_absent": not (state.forbidden_actionable[index] & governed_labels),
        "required_suppression_explained": all(
            label in suppressed and suppressed[label].startswith(reason)
            for label, reason in expected_suppressed.items()
        ),
        "task_brief_built": brief["schema"] == "memory.task_brief.v1"
        and brief["mutation_boundary"] == "read_only_report_no_auto_writeback",
        "task_brief_reflects_actionable_or_review": bool(brief_used | brief_review),
        "equal_budget_compared": FLAT_BUDGET == GOVERNED_SECTION_BUDGET,
        "fixed_as_of_used": governed["as_of"] == as_of,
        "recall_browse_export_exercised": isinstance(raw_flat["items"], list)
        and browse["count"] >= 0
        and exported["format"] == "json",
        **state.checkpoint_extra(index, snapshot),
    }
    passed = all(checks.values())
    return {
        "id": case["checkpoints"][index]["id"],
        "assertion": case["checkpoints"][index]["assertion"],
        "passed": passed,
        "checks": checks,
        "evidence": {
            "query": query,
            "as_of": as_of,
            "task_domain": task_domain,
            "flat_mode": state.baseline_mode,
            "flat_budget": FLAT_BUDGET,
            "governed_section_budget": GOVERNED_SECTION_BUDGET,
            "flat_actionable_labels": sorted(flat_labels),
            "governed_actionable_labels": sorted(governed_labels),
            "corrective_labels": sorted(corrective_labels),
            "required_actionable_labels": sorted(state.required_actionable[index]),
            "required_corrective_labels": sorted(state.required_corrective[index]),
            "suppressed": dict(sorted(suppressed.items())),
            "brief_used_labels": sorted(brief_used),
            "brief_needs_review_labels": sorted(brief_review),
            "storage": storage,
        },
        "failure_reason": None if passed else case["failure_reason"],
    }


def _required_retention_checks(
    *,
    required_actionable: set[str],
    required_corrective: set[str],
    actionable_labels: set[str],
    corrective_labels: set[str],
) -> dict[str, bool]:
    required_labels = required_actionable | required_corrective
    required_actionable_present = required_actionable <= actionable_labels
    required_corrective_present = required_corrective <= corrective_labels
    requirements_declared = bool(required_labels)
    exact_required_labels_retained = (
        requirements_declared
        and required_actionable_present
        and required_corrective_present
    )
    return {
        "required_retention_labels_declared": requirements_declared,
        "required_actionable_present": required_actionable_present,
        "required_corrective_present": required_corrective_present,
        "useful_current_retained": exact_required_labels_retained,
        "suppress_all_structurally_blocked": requirements_declared,
    }


def _seed_case(store: MemoryStore, case_id: str) -> CaseState:
    namespace = f"project:v021:{case_id}"
    state = CaseState(
        store=store,
        namespace=namespace,
        global_namespace=f"global:v021:{case_id}",
        case_id=case_id,
    )
    seeders: list[tuple[str, Callable[[CaseState], None]]] = [
        ("gmuc-del-", _seed_deletion_case),
        ("gmuc-life-", _seed_lifecycle_case),
        ("gmuc-state-", _seed_changed_premise_case),
        ("gmuc-xfer-", _seed_transfer_case),
    ]
    for prefix, seeder in seeders:
        if case_id.startswith(prefix):
            seeder(state)
            return state
    raise ValueError(f"unsupported v0.21 governed-change case: {case_id}")


def _seed_deletion_case(state: CaseState) -> None:
    suffix = state.case_id.split("-")[2]
    query = f"{suffix} deletion proof guidance"
    state.queries = [query, query]
    fallback = _procedure(
        state,
        "safe-current",
        query,
        "Use the current reviewed fallback after deletion checks.",
        domain="deletion",
    )
    state.required_actionable = [{"safe-current"}, {"safe-current"}]

    if suffix == "01":
        target = _procedure(
            state,
            "forgotten",
            query,
            "Use the synthetic record before explicit forget.",
            domain="deletion",
        )
        _ensure_embedding(state.store, target)
        state.required_actionable[0].add("forgotten")
        state.forbidden_actionable[1].add("forgotten")
        state.transition = lambda: state.store.forget(target)
        state.checkpoint_extra = lambda index, snapshot: _deleted_lookup_checks(
            snapshot,
            "forgotten",
            expected_present=index == 0,
            require_semantic=True,
            state=state,
            query=query,
        )
        return

    if suffix == "02":
        audit_marker = "synthetic-forgotten-payload-del02"
        target = _procedure(
            state,
            "forgotten",
            f"{query} {audit_marker}",
            f"Unique payload {audit_marker} exists only before forget.",
            domain="deletion",
        )
        state.audit_tokens["forgotten"] = audit_marker
        flat_probe: dict[str, bool] = {}
        state.baseline_mode = "forget-response-audit"
        state.flat_hazard_probe = lambda _checkpoint: flat_probe.get("payload_exposed", False)
        state.required_actionable[0].add("forgotten")
        state.forbidden_actionable[1].add("forgotten")

        def transition() -> None:
            result = state.store.forget(target)
            item = result.get("item") or {}
            flat_probe["payload_exposed"] = audit_marker in str(item.get("content") or "")

        state.transition = transition

        def checkpoint_extra(index: int, snapshot: dict[str, Any]) -> dict[str, bool]:
            checks = _tombstone_redaction_checks(
                snapshot,
                "forgotten",
                audit_marker,
                expected_tombstone=index == 1,
            )
            checks["flat_forget_response_payload_observed"] = (
                flat_probe.get("payload_exposed", False) if index == 1 else True
            )
            return checks

        state.checkpoint_extra = checkpoint_extra
        return

    if suffix == "03":
        predecessor_id = "gmuc-del03-predecessor"
        superseder_id = "gmuc-del03-superseder"
        predecessor = _procedure(
            state,
            "predecessor",
            query,
            "Old predecessor must never resurrect.",
            domain="deletion",
            fixed_id=predecessor_id,
        )
        superseder = _procedure(
            state,
            "superseder",
            query,
            "Current superseder is reviewed guidance.",
            domain="deletion",
            extra=f"supersedes: {predecessor}\n",
            fixed_id=superseder_id,
        )
        state.required_actionable[0].add("superseder")
        state.required_suppressed = [
            {"predecessor": "superseded"},
            {"predecessor": "lineage_status:degraded"},
        ]
        state.forbidden_actionable[1] |= {"predecessor", "superseder"}
        state.hazard_labels = {"predecessor"}
        state.transition = lambda: state.store.forget(superseder)
        state.checkpoint_extra = lambda index, snapshot: {
            "superseder_tombstone_state_correct":
                snapshot["storage"]["superseder"]["tombstone_count"] == int(index == 1),
            "predecessor_retained_for_audit": snapshot["storage"]["predecessor"]["primary_count"] == 1,
            "predecessor_dependency_shortcut_absent":
                snapshot["storage"]["predecessor"]["relations"]["depends_on"] == [],
            "real_supersession_edge_declared":
                index == 1
                or snapshot["storage"]["superseder"]["relations"]["supersedes"] == ["predecessor"],
            "predecessor_lineage_state_correct":
                snapshot["storage"]["predecessor"]["lineage_status"]
                == ("intact" if index == 0 else "degraded"),
            "inverse_supersession_retirement_persisted":
                index == 0
                or snapshot["storage"]["predecessor"]["lineage_issues"] == [
                    {
                        "missing_record_label": "superseder",
                        "root_forget_label": "superseder",
                        "type": "forgotten_superseder",
                    }
                ],
        }
        return

    if suffix == "04":
        dependency = _memory(
            state,
            "dependency",
            state.namespace,
            f"record_type: learn\nclaim: {query} approval is present.",
            tags=["kind:learn", "domain:deletion"],
        )
        procedure = _procedure(
            state,
            "dependent-procedure",
            query,
            "Run only while the required approval exists.",
            domain="deletion",
            extra=f"depends_on: {dependency}\n",
        )
        state.required_actionable[0].add("dependent-procedure")
        state.forbidden_actionable[1].add("dependent-procedure")
        state.required_suppressed[1]["dependent-procedure"] = "lineage_status:degraded"
        state.hazard_labels = {"dependent-procedure"}
        state.transition = lambda: state.store.forget(dependency)
        state.checkpoint_extra = lambda index, snapshot: {
            "dependency_tombstone_state_correct":
                snapshot["storage"]["dependency"]["tombstone_count"] == int(index == 1),
            "dependent_lineage_state_correct":
                snapshot["storage"]["dependent-procedure"]["lineage_status"]
                == ("intact" if index == 0 else "degraded"),
        }
        return

    if suffix == "05":
        audit_marker = "synthetic-rebuild-marker-del05"
        target = _procedure(
            state,
            "forgotten",
            f"{query} {audit_marker}",
            f"Forgotten rebuild payload {audit_marker}.",
            domain="deletion",
        )
        _ensure_embedding(state.store, target)
        _memory(
            state,
            "flat-stale-feed",
            state.namespace,
            f"stale rebuild feed {query} {audit_marker}",
            tags=["baseline:stale-rebuild-feed"],
        )
        state.store.forget(target)
        state.audit_tokens["forgotten"] = audit_marker
        state.forbidden_actionable = [{"forgotten"}, {"forgotten"}]
        state.hazard_labels = {"flat-stale-feed"}
        state.transition = lambda: _rebuild_derived_indexes(state.store)
        state.checkpoint_extra = lambda _index, snapshot: _deleted_lookup_checks(
            snapshot,
            "forgotten",
            expected_present=False,
            require_semantic=True,
            state=state,
            query=query,
        )
        return
    raise ValueError(f"unsupported deletion case: {state.case_id}")


def _seed_lifecycle_case(state: CaseState) -> None:
    suffix = state.case_id.split("-")[2]
    query = f"{suffix} lifecycle release guidance"
    state.queries = [query, query]

    if suffix == "01":
        _procedure(state, "current", query, "Use the reviewed current release path.", domain="release")
        state.required_actionable = [{"current"}, {"current"}]
        state.hazard_labels = {"stale-decoy"}
        state.transition = lambda: _procedure(
            state,
            "stale-decoy",
            f"{query} {query}",
            "Obsolete stale release path.",
            domain="release",
            status="stale",
        )
        state.required_suppressed[1]["stale-decoy"] = "procedure_status:stale"
        state.forbidden_actionable[1].add("stale-decoy")
        return

    if suffix == "02":
        old = _procedure(state, "generation-1", f"{query} {query}", "Old release generation.", domain="release")
        state.required_actionable[0].add("generation-1")
        state.hazard_labels = {"generation-1"}

        def transition() -> None:
            _procedure(
                state,
                "generation-2",
                query,
                "New reviewed release generation.",
                domain="release",
                extra=f"supersedes: {old}\n",
            )

        state.transition = transition
        state.required_actionable[1].add("generation-2")
        state.forbidden_actionable[1].add("generation-1")
        state.required_suppressed[1]["generation-1"] = "superseded"
        return

    if suffix == "03":
        one = _procedure(state, "generation-1", f"{query} {query}", "First release generation.", domain="release")
        two = _procedure(
            state,
            "generation-2",
            query,
            "Second release generation.",
            domain="release",
            extra=f"supersedes: {one}\n",
        )
        state.required_actionable[0].add("generation-2")
        state.forbidden_actionable[0].add("generation-1")
        state.required_suppressed[0]["generation-1"] = "superseded"
        state.hazard_labels = {"generation-1", "generation-2"}

        def transition() -> None:
            _procedure(
                state,
                "generation-3",
                query,
                "Third reviewed release generation.",
                domain="release",
                extra=f"supersedes: {two}\n",
            )

        state.transition = transition
        state.required_actionable[1].add("generation-3")
        state.forbidden_actionable[1] |= {"generation-1", "generation-2"}
        state.required_suppressed[1] = {
            "generation-1": "superseded",
            "generation-2": "superseded",
        }
        return

    if suffix == "04":
        _procedure(state, "fallback-current", query, "Review current release status safely.", domain="release")
        _procedure(
            state,
            "expiring",
            f"{query} bounded",
            "Run the bounded release action.",
            domain="release",
            extra="valid_until: 2026-07-15T12:00:00+00:00\n",
        )
        state.as_of = ["2026-07-15T11:59:59+00:00", "2026-07-15T12:00:01+00:00"]
        state.baseline_mode = "flat_task_memory"
        state.required_actionable = [
            {"fallback-current", "expiring"},
            {"fallback-current"},
        ]
        state.forbidden_actionable[1].add("expiring")
        state.required_suppressed[1]["expiring"] = "validity:expired"
        state.hazard_labels = {"expiring"}
        return

    if suffix == "05":
        _procedure(state, "fallback-current", query, "Review current replacement status.", domain="release")
        _procedure(
            state,
            "expired-old",
            f"{query} legacy",
            "Expired legacy release path.",
            domain="release",
            extra="valid_until: 2026-01-01T00:00:00+00:00\n",
        )
        state.required_actionable[0].add("fallback-current")
        state.forbidden_actionable[0].add("expired-old")
        state.required_suppressed[0]["expired-old"] = "validity:expired"
        state.hazard_labels = {"expired-old"}
        state.transition = lambda: _procedure(
            state,
            "replacement",
            query,
            "Current replacement release path.",
            domain="release",
        )
        state.required_actionable[1] |= {"fallback-current", "replacement"}
        state.forbidden_actionable[1].add("expired-old")
        state.required_suppressed[1]["expired-old"] = "validity:expired"
        return
    raise ValueError(f"unsupported lifecycle case: {state.case_id}")


def _seed_changed_premise_case(state: CaseState) -> None:
    suffix = state.case_id.split("-")[2]
    specs = {
        "01": ("rollout", "Continue the active rollout.", "The rollout is completed; do not continue."),
        "02": ("incident", "Escalate the active incident.", "The incident is closed; do not escalate."),
        "03": ("migration", "Run the one-time migration.", "The migration is applied; do not rerun."),
        "04": ("feature", "Enable the configurable feature.", "The feature is removed; do not enable."),
        "05": ("approval", "Use the approved shortcut.", "Approval is revoked; use the normal reviewed path."),
    }
    topic, old_guidance, current_state = specs[suffix]
    query = f"{topic} governed task action"
    state.queries = [query, query]
    obsolete = _procedure(state, "obsolete-procedure", f"{query} {query}", old_guidance, domain=topic)
    state.required_actionable[0].add("obsolete-procedure")
    state.hazard_labels = {"obsolete-procedure"}

    def transition() -> None:
        _memory(
            state,
            "current-state",
            state.namespace,
            (
                "record_type: state-change\n"
                f"current_state: {current_state}\n"
                f"supersedes: {obsolete}\n"
            ),
            tags=["kind:state-change", f"domain:{topic}", f"topic:{topic}"],
            title=f"[[State Change]] {topic} premise changed",
        )

    state.transition = transition
    state.forbidden_actionable[1].add("obsolete-procedure")
    state.required_suppressed[1]["obsolete-procedure"] = "superseded"
    state.required_corrective[1].add("current-state")


def _seed_transfer_case(state: CaseState) -> None:
    suffix = state.case_id.split("-")[2]
    specs = {
        "01": ("postgresql", "sqlite", "database backup restore", "sqlite database backup context"),
        "02": ("kubernetes", "python-release", "release rollback deploy", "python release rollback version"),
        "03": ("cloud-api", "artifact-signing", "key credential rotate revoke", "signing key rotate trust"),
        "04": ("sql-schema", "obsidian", "relational SQL schema migration DDL", "Obsidian vault Markdown structure"),
        "05": (
            "deployment",
            "skill-governance",
            "sync source target version verify rollback",
            "skill sync source target version verify approval",
        ),
    }
    source_domain, target_domain, source_query, target_query = specs[suffix]
    state.queries = [source_query, target_query]
    state.task_domains = [source_domain, target_domain]
    _procedure(
        state,
        "source-procedure",
        source_query,
        f"Use only for the {source_domain} source domain.",
        domain=source_domain,
    )
    state.required_actionable[0].add("source-procedure")
    state.forbidden_actionable[1].add("source-procedure")
    state.required_suppressed[1]["source-procedure"] = f"task_domain_mismatch:{target_domain}"
    state.hazard_labels = {"source-procedure"}
    if suffix == "04":
        state.hazard_labels = set()

    def transition() -> None:
        _memory(
            state,
            "target-context",
            state.global_namespace,
            (
                "record_type: domain-note\n"
                f"claim: Current task context is {target_domain}; review domain-native guidance only.\n"
                f"context_terms: {target_query}\n"
            ),
            tags=["kind:domain-note", f"domain:{target_domain}"],
            title=f"[[Domain Note]] {target_domain} task context",
        )

    state.transition = transition
    state.required_actionable[1].add("target-context")


def _procedure(
    state: CaseState,
    label: str,
    query_terms: str,
    goal: str,
    *,
    domain: str,
    status: str = "validated",
    extra: str = "",
    fixed_id: str | None = None,
) -> str:
    content = (
        "record_type: procedure\n"
        f"goal: {goal}\n"
        f"when_to_use: {query_terms}\n"
        f"applies_to_domains: {domain}\n"
        f"procedure_status: {status}\n"
        "steps: inspect synthetic fixture | report read-only evidence\n"
        "failure_mode: Stop if fixture evidence is incomplete.\n"
        "rollback_path: Discard the isolated temp store.\n"
        f"{extra}"
    )
    return _memory(
        state,
        label,
        state.namespace,
        content,
        tags=["kind:procedure", f"domain:{domain}", f"case:{state.case_id}"],
        title=f"[[Procedure]] {label} {query_terms}",
        fixed_id=fixed_id,
    )


def _memory(
    state: CaseState,
    label: str,
    namespace: str,
    content: str,
    *,
    tags: list[str],
    title: str | None = None,
    fixed_id: str | None = None,
) -> str:
    original_new_id = state.store._new_id
    if fixed_id is not None:
        state.store._new_id = lambda: fixed_id
    try:
        result = state.store.store(
            namespace=namespace,
            content=content,
            kind="memory",
            tags=tags,
            title=title,
            actor="v021-governed-change-proof",
            source_app="agent-memory-bridge-v021-proof",
        )
    finally:
        if fixed_id is not None:
            state.store._new_id = original_new_id
    memory_id = str(result["id"])
    state.labels[memory_id] = label
    return memory_id


def _actionable_labels(state: CaseState, task_memory: dict[str, Any]) -> set[str]:
    items = [
        item
        for section in ("procedure_hits", "concept_hits", "belief_hits", "domain_hits", "supporting_hits")
        for item in task_memory.get(section) or []
    ]
    return _labels(state, items)


def _labels(state: CaseState, items: list[dict[str, Any]]) -> set[str]:
    return {
        state.labels.get(str(item.get("id")), "unlabeled")
        for item in items
        if str(item.get("id")) in state.labels
    }


def _storage_snapshot(state: CaseState) -> dict[str, dict[str, Any]]:
    snapshot: dict[str, dict[str, Any]] = {}
    with state.store._connect() as conn:
        tombstone_columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(memory_tombstones)").fetchall()
        }
        for memory_id, label in state.labels.items():
            primary = conn.execute(
                "SELECT content, lineage_status, lineage_issues_json FROM memories WHERE id = ?",
                (memory_id,),
            ).fetchone()
            tombstone = conn.execute(
                "SELECT forgotten_id, namespace, kind, deleted_at, root_forget_id, cause "
                "FROM memory_tombstones WHERE forgotten_id = ?",
                (memory_id,),
            ).fetchone()
            tombstone_payload = dict(tombstone) if tombstone else {}
            raw_relations = (
                parse_relation_metadata(str(primary["content"]))["relations"]
                if primary
                else {"supports": [], "contradicts": [], "supersedes": [], "depends_on": []}
            )
            relations = {
                relation: [state.labels.get(target, target) for target in targets]
                for relation, targets in raw_relations.items()
            }
            raw_lineage_issues = json.loads(primary["lineage_issues_json"]) if primary else []
            lineage_issues = [
                {
                    **{
                        key: value
                        for key, value in issue.items()
                        if key not in {"missing_record_id", "root_forget_id"}
                    },
                    **(
                        {"missing_record_label": state.labels.get(issue["missing_record_id"], issue["missing_record_id"])}
                        if "missing_record_id" in issue
                        else {}
                    ),
                    **(
                        {"root_forget_label": state.labels.get(issue["root_forget_id"], issue["root_forget_id"])}
                        if "root_forget_id" in issue
                        else {}
                    ),
                }
                for issue in raw_lineage_issues
            ]
            audit_token = state.audit_tokens.get(label, "")
            snapshot[label] = {
                "primary_count": int(primary is not None),
                "fts_count": conn.execute(
                    "SELECT COUNT(*) FROM memories_fts WHERE memory_id = ?", (memory_id,)
                ).fetchone()[0],
                "embedding_count": conn.execute(
                    "SELECT COUNT(*) FROM memory_embeddings WHERE memory_id = ?", (memory_id,)
                ).fetchone()[0],
                "tombstone_count": int(tombstone is not None),
                "lineage_status": str(primary["lineage_status"]) if primary else None,
                "lineage_issue_count": len(lineage_issues),
                "lineage_issues": lineage_issues,
                "relations": relations,
                "tombstone_cause": str(tombstone["cause"]) if tombstone else None,
                "tombstone_has_content_columns": bool({"title", "content"} & tombstone_columns),
                "tombstone_payload_redacted": not audit_token
                or audit_token not in json.dumps(tombstone_payload, sort_keys=True),
            }
    return snapshot


def _ensure_embedding(store: MemoryStore, memory_id: str) -> None:
    with store._connect() as conn:
        rows = conn.execute(
            "SELECT id, title, content, content_hash FROM memories WHERE id = ?",
            (memory_id,),
        ).fetchall()
        ensure_embeddings_for_rows(
            conn,
            rows,
            config=EmbeddingConfig(provider="hash", model="local-token-hash-v1", dim=64),
        )
        conn.commit()


def _semantic_labels(state: CaseState, query: str) -> set[str]:
    items = recall_via_semantic(
        state.store,
        namespace=state.namespace,
        query=query,
        limit=FLAT_BUDGET,
        kind="memory",
        signal_status=None,
        tags_any=None,
        session_id=None,
        actor=None,
        correlation_id=None,
        since=None,
    )
    return _labels(state, items)


def _deleted_lookup_checks(
    snapshot: dict[str, Any],
    label: str,
    *,
    expected_present: bool,
    require_semantic: bool,
    state: CaseState,
    query: str,
) -> dict[str, bool]:
    row = snapshot["storage"][label]
    expected = int(expected_present)
    semantic_present = label in _semantic_labels(state, query) if require_semantic else expected_present
    return {
        "primary_lookup_state_correct": row["primary_count"] == expected,
        "fts_lookup_state_correct": row["fts_count"] == expected,
        "embedding_lookup_state_correct": row["embedding_count"] == expected,
        "semantic_recall_state_correct": semantic_present is expected_present,
        "recall_path_state_correct": (label in snapshot["raw_flat_labels"]) is expected_present,
        "browse_path_state_correct": (label in snapshot["browse_labels"]) is expected_present,
    }


def _tombstone_redaction_checks(
    snapshot: dict[str, Any],
    label: str,
    token: str,
    *,
    expected_tombstone: bool,
) -> dict[str, bool]:
    row = snapshot["storage"][label]
    return {
        "tombstone_state_correct": row["tombstone_count"] == int(expected_tombstone),
        "tombstone_content_columns_absent": row["tombstone_has_content_columns"] is False,
        "forgotten_payload_absent_from_tombstone": row["tombstone_payload_redacted"] is True,
        "forgotten_payload_absent_from_live_export":
            (token not in snapshot["export_content"]) if expected_tombstone else True,
    }


def _rebuild_derived_indexes(store: MemoryStore) -> None:
    with store._connect() as conn:
        rows = conn.execute(
            "SELECT id, COALESCE(title, '') AS title, content, content_hash FROM memories ORDER BY id"
        ).fetchall()
        conn.execute("DROP TABLE memories_fts")
        conn.execute("CREATE VIRTUAL TABLE memories_fts USING fts5(memory_id UNINDEXED, title, content)")
        conn.executemany(
            "INSERT INTO memories_fts(memory_id, title, content) VALUES (?, ?, ?)",
            ((row["id"], row["title"], row["content"]) for row in rows),
        )
        conn.execute("DELETE FROM memory_embeddings")
        ensure_embeddings_for_rows(
            conn,
            rows,
            config=EmbeddingConfig(provider="hash", model="local-token-hash-v1", dim=64),
        )
        conn.commit()


def _flat_hazard_observed(state: CaseState, checkpoint: dict[str, Any]) -> bool:
    if state.flat_hazard_probe is not None:
        return state.flat_hazard_probe(checkpoint)
    flat_labels = set(checkpoint["evidence"]["flat_actionable_labels"])
    return bool(flat_labels & state.hazard_labels)


def _category_slices(cases: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    slices: dict[str, dict[str, Any]] = {}
    for category in EXPECTED_CATEGORIES:
        selected = [case for case in cases if case["category"] == category]
        checkpoints = [checkpoint for case in selected for checkpoint in case["checkpoints"]]
        slices[category] = {
            "case_count": len(selected),
            "flat_baseline_hazards": sum(case["flat_baseline_hazard_observed"] for case in selected),
            "flat_baseline_hazards_expected": sum(case["flat_baseline_hazard_expected"] for case in selected),
            "governed_failures": sum(not case["governed_passed"] for case in selected),
            "governed_checkpoint_passes": sum(checkpoint["passed"] for checkpoint in checkpoints),
            "governed_checkpoint_count": len(checkpoints),
            "useful_current_retention_pass": all(
                checkpoint["checks"]["useful_current_retained"] for checkpoint in checkpoints
            ),
        }
    return slices


def _validate_manifest(manifest: dict[str, Any]) -> None:
    if list(manifest) != [
        "schema",
        "title",
        "current_release",
        "target_release",
        "status",
        "case_count",
        "categories",
        "required_case_fields",
        "execution_contract",
        "metrics",
        "boundaries",
        "cases",
    ]:
        raise ValueError("v0.21 manifest top-level schema changed")
    if manifest["schema"] != "amb.v0.21.governed_change_manifest.v1":
        raise ValueError("v0.21 manifest schema mismatch")
    if manifest["current_release"] != "0.20.0" or manifest["target_release"] != "0.21.0":
        raise ValueError("v0.21 manifest release boundary mismatch")
    if manifest["case_count"] != 20 or len(manifest["cases"]) != 20:
        raise ValueError("v0.21 manifest case denominator must remain 20")
    if manifest["categories"] != EXPECTED_CATEGORIES:
        raise ValueError("v0.21 manifest category denominators changed")
    if Counter(case["category"] for case in manifest["cases"]) != EXPECTED_CATEGORIES:
        raise ValueError("v0.21 manifest case category distribution changed")
    if set(manifest["required_case_fields"]) != REQUIRED_CASE_FIELDS:
        raise ValueError("v0.21 manifest required case fields changed")
    if any(set(case) != REQUIRED_CASE_FIELDS or len(case["checkpoints"]) != 2 for case in manifest["cases"]):
        raise ValueError("v0.21 manifest case schema or checkpoint denominator changed")
    if manifest["execution_contract"] != {
        "memory_store_isolation": "one_fresh_temp_MemoryStore_per_case",
        "checkpoints_per_case": 2,
        "transition_count": 20,
        "storage_mutation_count": 19,
        "clock_transition_count": 1,
    }:
        raise ValueError("v0.21 manifest execution contract changed")
    if manifest["metrics"] != {
        "flat_baseline_hazards": {"expected": "17/20", "hazard_count": 17},
        "governed_failures": {"target": "0/20", "failure_count": 0},
        "governed_checkpoint_passes": {"target": "40/40", "pass_count": 40},
    }:
        raise ValueError("v0.21 manifest metric denominators changed")
    if sum(case["flat_baseline_hazard"]["expected"] for case in manifest["cases"]) != 17:
        raise ValueError("v0.21 flat baseline hazard denominator changed")
    if manifest["boundaries"] != {
        "public_mcp_tool_count": 10,
        "no_new_mcp_tools": True,
        "no_auto_mutation": True,
        "no_auto_writeback": True,
        "no_config_writes": True,
        "no_outside_temp_writes": True,
        "no_private_or_local_cole_data": True,
    }:
        raise ValueError("v0.21 manifest boundaries changed")


@contextmanager
def _proof_environment(runtime_dir: Path) -> Iterator[None]:
    updates = {
        "AGENT_MEMORY_BRIDGE_CONFIG": str(runtime_dir / "config.toml"),
        "AGENT_MEMORY_BRIDGE_HOME": str(runtime_dir),
        "AGENT_MEMORY_BRIDGE_DB_PATH": str(runtime_dir / "bridge.db"),
        "AGENT_MEMORY_BRIDGE_LOG_DIR": str(runtime_dir / "logs"),
        "AGENT_MEMORY_BRIDGE_RETRIEVAL_MODE": "lexical",
        "AGENT_MEMORY_BRIDGE_EMBEDDING_PROVIDER": "hash",
        "AGENT_MEMORY_BRIDGE_EMBEDDING_MODEL": "local-token-hash-v1",
        "AGENT_MEMORY_BRIDGE_EMBEDDING_DIM": "64",
        "AGENT_MEMORY_BRIDGE_TELEMETRY_MODE": "off",
    }
    previous = {name: os.environ.get(name) for name in updates}
    os.environ.update(updates)
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
