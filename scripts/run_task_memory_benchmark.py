from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent_mem_bridge.task_memory_benchmark import (
    DEFAULT_CASES_PATH,
    DEFAULT_REPORT_PATH,
    run_task_memory_benchmark,
)


def main() -> None:
    args = _parse_args()
    report = run_task_memory_benchmark(cases_path=args.cases_path)
    rendered = json.dumps(report, indent=2)
    if args.report_path is not None:
        args.report_path.parent.mkdir(parents=True, exist_ok=True)
        args.report_path.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the reviewed task-memory packet benchmark comparing the current flat "
            "assembler with relation-aware assembly."
        )
    )
    parser.add_argument(
        "--cases-path",
        type=Path,
        default=DEFAULT_CASES_PATH,
        help="Path to the reviewed task-memory cases file.",
    )
    parser.add_argument(
        "--report-path",
        type=Path,
        default=DEFAULT_REPORT_PATH,
        help="Path to write the JSON report.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
