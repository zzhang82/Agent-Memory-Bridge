from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from agent_mem_bridge.benchmarking import run_benchmark
from agent_mem_bridge.recall_first import recall_first
from agent_mem_bridge.storage import MemoryStore


def main() -> None:
    runtime_root = Path(tempfile.gettempdir()) / "agent-memory-bridge-demo"
    shutil.rmtree(runtime_root, ignore_errors=True)
    runtime_root.mkdir(parents=True, exist_ok=True)

    store = MemoryStore(runtime_root / "demo.db", log_dir=runtime_root / "logs")
    project_namespace = "project:demo"
    global_namespace = "global"

    seed_release_memory(store, project_namespace=project_namespace, global_namespace=global_namespace)
    walkthrough_signal_lifecycle(store, namespace=project_namespace)
    show_later_recall(store, project_namespace=project_namespace, global_namespace=global_namespace)
    show_benchmark_snapshot()


def seed_release_memory(store: MemoryStore, *, project_namespace: str, global_namespace: str) -> None:
    belief = store.store(
        namespace=global_namespace,
        kind="memory",
        title="[[Belief]] release handoff pattern",
        content=(
            "record_type: belief\n"
            "claim: Verify benchmark and healthcheck before tagging a release handoff.\n"
            "support_count: 5\n"
            "distinct_session_count: 4\n"
            "contradiction_count: 0\n"
            "confidence: 0.82\n"
            "status: active\n"
        ),
        tags=["kind:belief", "domain:release", "topic:handoff"],
    )
    concept = store.store(
        namespace=global_namespace,
        kind="memory",
        title="[[Concept Note]] release handoff verification loop",
        content=(
            "record_type: concept-note\n"
            "concept: Release handoff verification loop.\n"
            "claim: Release handoff stays calmer when verification stays explicit.\n"
            "rule: Verify benchmark and healthcheck before tagging a release handoff.\n"
            f"depends_on: {belief['id']}\n"
        ),
        tags=["kind:concept-note", "domain:release", "topic:handoff"],
    )
    procedure = store.store(
        namespace=project_namespace,
        kind="memory",
        title="[[Procedure]] release handoff",
        content=(
            "record_type: procedure\n"
            "goal: Run release handoff safely.\n"
            "when_to_use: Before tagging a release or asking for final review.\n"
            "steps: run benchmark | run healthcheck | tag release | notify reviewer\n"
            f"depends_on: {concept['id']}\n"
            f"supports: {belief['id']}\n"
        ),
        tags=["kind:procedure", "domain:release", "topic:handoff"],
    )

    print_block(
        "seed durable memory",
        [
            "stored 1 belief, 1 concept note, and 1 project procedure",
            f"belief:  {belief['stored']} -> [[Belief]] release handoff pattern",
            f"concept: {concept['stored']} -> [[Concept Note]] release handoff verification loop",
            f"procedure: {procedure['stored']} -> [[Procedure]] release handoff",
        ],
    )


def walkthrough_signal_lifecycle(store: MemoryStore, *, namespace: str) -> None:
    signal = store.store(
        namespace=namespace,
        kind="signal",
        title="Review Handoff",
        content="release handoff review ready",
        tags=["handoff:review", "topic:release"],
        ttl_seconds=600,
    )
    claimed = store.claim_signal(
        namespace=namespace,
        consumer="reviewer-a",
        lease_seconds=300,
        signal_id=signal["id"],
    )
    extended = store.extend_signal_lease(
        signal["id"],
        consumer="reviewer-a",
        lease_seconds=300,
    )
    acked = store.ack_signal(signal["id"], consumer="reviewer-a")

    print_block(
        "signal lifecycle",
        [
            f"stored signal: {signal['stored']} -> Review Handoff",
            "claim_signal -> claimed by reviewer-a",
            f"extend_signal_lease -> {describe_lease_extension(claimed, extended)}",
            f"ack_signal -> acknowledged={acked.get('acked') is True}",
        ],
    )


def show_later_recall(store: MemoryStore, *, project_namespace: str, global_namespace: str) -> None:
    recall_report = recall_first(
        store=store,
        query="release handoff fix",
        project_namespace=project_namespace,
        global_namespace=global_namespace,
        limit=5,
    )

    print_block(
        "later task: recall_first('release handoff fix')",
        [
            f"recommended_action: {recall_report['recommended_action']}",
            "",
            *recall_report["task_memory_summary"].splitlines(),
        ],
    )

    print_json(
        "later task snapshot",
        {
            "procedure_hits": len(recall_report["procedure_hits"]),
            "concept_hits": len(recall_report["concept_hits"]),
            "belief_hits": len(recall_report["belief_hits"]),
            "supporting_hits": len(recall_report["supporting_hits"]),
        },
    )


def show_benchmark_snapshot() -> None:
    benchmark = run_benchmark()
    print_json(
        "benchmark snapshot",
        {
            "memory_expected_top1_accuracy": benchmark["summary"]["memory_expected_top1_accuracy"],
            "memory_mrr": benchmark["summary"]["memory_mrr"],
            "file_scan_expected_top1_accuracy": benchmark["summary"]["file_scan_expected_top1_accuracy"],
            "signal_correctness_passed": benchmark["summary"]["signal_correctness_passed"],
            "relation_metadata_passed": benchmark["summary"]["relation_metadata_passed"],
        },
    )


def describe_lease_extension(claimed: dict[str, Any], extended: dict[str, Any]) -> str:
    first_expiry = str(claimed.get("lease_expires_at") or "")
    second_expiry = str(extended.get("lease_expires_at") or "")
    if first_expiry and second_expiry and second_expiry > first_expiry:
        return "extended inside the signal lifetime window"
    return "extended up to the signal's hard expiry"


def print_block(title: str, lines: list[str]) -> None:
    print()
    print(f"# {title}")
    for line in lines:
        print(line)


def print_json(title: str, payload: dict[str, Any]) -> None:
    print()
    print(f"# {title}")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
