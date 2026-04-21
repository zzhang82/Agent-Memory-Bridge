from __future__ import annotations

import argparse
import json

from agent_mem_bridge.storage import MemoryStore
from agent_mem_bridge.task_memory import assemble_task_memory


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Preview composed task memory for a query.")
    parser.add_argument("query", help="Task or issue query to assemble local memory for.")
    parser.add_argument(
        "--project-namespace",
        default="project:demo",
        help="Project namespace to treat as the local procedure layer.",
    )
    parser.add_argument(
        "--global-namespace",
        default="global",
        help="Global namespace for concepts, beliefs, and other supporting memory.",
    )
    parser.add_argument(
        "--format",
        choices=("json", "text"),
        default="text",
        help="Output format.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    store = MemoryStore.from_env()
    report = assemble_task_memory(
        store,
        query=args.query,
        project_namespace=args.project_namespace,
        global_namespace=args.global_namespace,
    )
    if args.format == "json":
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return
    print(report["summary"])


if __name__ == "__main__":
    main()
