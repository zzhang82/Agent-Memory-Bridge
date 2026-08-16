from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from agent_mem_bridge.storage import MemoryStore


def _procedure(status: str, label: str) -> str:
    return (
        "record_type: procedure\n"
        f"goal: Execute the {label} release cutover procedure.\n"
        "when_to_use: Before production release cutover.\n"
        "steps: verify release | deploy release | validate release\n"
        f"procedure_status: {status}\n"
    )


def _ids(payload: dict[str, Any]) -> list[str]:
    return [str(item["id"]) for item in payload["items"]]


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="amb-default-recall-eligibility-", ignore_cleanup_errors=True) as directory:
        root = Path(directory)
        store = MemoryStore(root / "bridge.db", log_dir=root / "logs")
        predecessor = store.store(
            namespace="project:checkout",
            content="The checkout production deploy procedure is: run legacy-deploy --force.",
        )
        revision = store.revise(
            str(predecessor["id"]),
            replacement_content=(
                "The checkout production deploy procedure is: run safe-deploy --verified. "
                "The legacy command must not be used."
            ),
        )
        procedures = {
            status: store.store(
                namespace="project:checkout",
                content=_procedure(status, status),
                tags=["kind:procedure", "domain:release"],
            )
            for status in ("unsafe", "stale", "replaced", "validated")
        }

        default_revision = store.recall(namespace="project:checkout", query="legacy deploy force", limit=5)
        historical_revision = store.recall(
            namespace="project:checkout",
            query="legacy deploy force",
            limit=5,
            eligibility="historical",
        )
        default_procedures = store.recall(
            namespace="project:checkout",
            query="release cutover procedure",
            tags_any=["kind:procedure"],
            limit=10,
        )
        historical_procedures = store.recall(
            namespace="project:checkout",
            query="release cutover procedure",
            tags_any=["kind:procedure"],
            limit=10,
            eligibility="historical",
        )

        print(
            json.dumps(
                {
                    "revision": {
                        "predecessor_id": predecessor["id"],
                        "successor_id": revision["successor_id"],
                        "default_result_ids": _ids(default_revision),
                        "historical_result_ids": _ids(historical_revision),
                        "default_suppression": default_revision.get("retrieval", {}).get("suppression_reason_counts"),
                    },
                    "procedures": {
                        "ids_by_status": {status: item["id"] for status, item in procedures.items()},
                        "default_result_ids": _ids(default_procedures),
                        "historical_result_ids": _ids(historical_procedures),
                        "default_suppression": default_procedures.get("retrieval", {}).get("suppression_reason_counts"),
                    },
                },
                sort_keys=True,
            )
        )


if __name__ == "__main__":
    main()
