from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent_mem_bridge.memory_evolution_benchmark import (
    DEFAULT_CASES_PATH,
    DEFAULT_REPORT_PATH,
    run_memory_evolution_benchmark,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run deterministic reviewed-memory-evolution cases without querying live bridge state."
        )
    )
    parser.add_argument(
        "--cases-path",
        type=Path,
        default=DEFAULT_CASES_PATH,
        help="Path to the memory evolution cases file.",
    )
    parser.add_argument(
        "--report-path",
        type=Path,
        default=DEFAULT_REPORT_PATH,
        help="Path where the JSON report should be written.",
    )
    args = parser.parse_args()

    report = run_memory_evolution_benchmark(cases_path=args.cases_path)
    args.report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
