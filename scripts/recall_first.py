from __future__ import annotations

import argparse
import json

from agent_mem_bridge.paths import resolve_profile_namespace
from agent_mem_bridge.recall_first import recall_first
from agent_mem_bridge.storage import MemoryStore


def main() -> None:
    parser = argparse.ArgumentParser(description="Recall local bridge memory before external search.")
    parser.add_argument("query", help="Issue-like text to search for")
    parser.add_argument(
        "--namespace",
        default="project:demo",
        help="Project namespace to search first",
    )
    parser.add_argument("--limit", type=int, default=5, help="Max hits per section")
    args = parser.parse_args()

    store = MemoryStore.from_env()
    result = recall_first(
        store=store,
        query=args.query,
        project_namespace=args.namespace,
        limit=args.limit,
        global_namespace=resolve_profile_namespace(),
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

