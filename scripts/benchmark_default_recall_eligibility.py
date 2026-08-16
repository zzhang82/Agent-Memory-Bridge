from __future__ import annotations

import argparse
import json
import tempfile
import time
from pathlib import Path
from typing import Any

from agent_mem_bridge.storage import MemoryStore


def _result(count: int) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="amb-default-recall-benchmark-", ignore_cleanup_errors=True) as directory:
        root = Path(directory)
        store = MemoryStore(root / "bridge.db", log_dir=root / "logs")
        for index in range(count):
            store.store(
                namespace="project:benchmark",
                content=f"distractor memory {index} for unrelated engineering notes",
            )
        predecessor = store.store(
            namespace="project:benchmark",
            content="checkout lifecycle benchmark target legacy deploy force highrank highrank highrank",
        )
        revision = store.revise(
            str(predecessor["id"]),
            replacement_content="checkout lifecycle benchmark target safe deploy verified",
        )
        started = time.perf_counter()
        recalled = store.recall(
            namespace="project:benchmark",
            query="checkout lifecycle benchmark target legacy deploy force",
            limit=5,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000
        ids = [str(item["id"]) for item in recalled["items"]]
        successor_id = str(revision["successor_id"])
        return {
            "memory_count": count + 2,
            "recall_latency_ms": round(elapsed_ms, 3),
            "predecessor_absent": str(predecessor["id"]) not in ids,
            "successor_rank": ids.index(successor_id) + 1 if successor_id in ids else None,
            "returned_result_count": len(ids),
            "duplicate_ids": len(ids) != len(set(ids)),
            "suppression_reason_counts": recalled.get("retrieval", {}).get("suppression_reason_counts", {}),
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--counts", type=int, nargs="+", default=[100, 1000, 10000])
    arguments = parser.parse_args()
    print(json.dumps([_result(count) for count in arguments.counts], sort_keys=True))


if __name__ == "__main__":
    main()
