from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from agent_mem_bridge.benchmarking import run_benchmark
from agent_mem_bridge.storage import MemoryStore


def main() -> None:
    runtime_root = Path(tempfile.gettempdir()) / "agent-memory-bridge-demo"
    shutil.rmtree(runtime_root, ignore_errors=True)
    runtime_root.mkdir(parents=True, exist_ok=True)

    store = MemoryStore(runtime_root / "demo.db", log_dir=runtime_root / "logs")
    namespace = "project:demo"

    memory = store.store(
        namespace=namespace,
        kind="memory",
        title="Storage Decision",
        content="Use WAL mode for concurrent readers.",
        tags=["domain:storage", "topic:sqlite"],
    )
    signal = store.store(
        namespace=namespace,
        kind="signal",
        title="Review Handoff",
        content="release note review ready",
        tags=["handoff:review", "topic:release"],
        ttl_seconds=600,
    )

    print_step("store(memory)", memory)
    print_step("store(signal)", signal)
    print_step("stats(namespace)", store.stats(namespace))

    claimed = store.claim_signal(
        namespace=namespace,
        consumer="reviewer-a",
        lease_seconds=300,
        signal_id=signal["id"],
    )
    print_step("claim_signal(...)", summarize_action(claimed, "claimed"))

    extended = store.extend_signal_lease(
        signal["id"],
        consumer="reviewer-a",
        lease_seconds=300,
    )
    print_step("extend_signal_lease(...)", summarize_action(extended, "extended"))

    acked = store.ack_signal(signal["id"], consumer="reviewer-a")
    print_step("ack_signal(...)", summarize_action(acked, "acked"))

    acked_view = store.browse(namespace=namespace, kind="signal", signal_status="acked", limit=10)
    print_step("browse(..., signal_status='acked')", acked_view)

    benchmark = run_benchmark()
    print_step(
        "benchmark snapshot",
        {
            "memory_expected_top1_accuracy": benchmark["summary"]["memory_expected_top1_accuracy"],
            "file_scan_expected_top1_accuracy": benchmark["summary"]["file_scan_expected_top1_accuracy"],
            "signal_correctness_passed": benchmark["summary"]["signal_correctness_passed"],
            "duplicate_suppression_rate": benchmark["summary"]["duplicate_suppression_rate"],
        },
    )


def summarize_action(payload: dict[str, Any], key: str) -> dict[str, Any]:
    item = payload.get("item") or {}
    result = {
        key: payload.get(key),
        "reason": payload.get("reason"),
        "lease_expires_at": payload.get("lease_expires_at"),
        "item": summarize_item(item) if item else None,
    }
    return {name: value for name, value in result.items() if value is not None}


def summarize_item(item: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "id",
        "title",
        "kind",
        "signal_status",
        "claimed_by",
        "lease_expires_at",
        "expires_at",
        "acknowledged_at",
    ]
    return {key: item.get(key) for key in keys if item.get(key) is not None}


def print_step(title: str, payload: dict[str, Any]) -> None:
    print()
    print(f"# {title}")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
