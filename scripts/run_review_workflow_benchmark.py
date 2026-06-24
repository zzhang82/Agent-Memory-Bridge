from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent_mem_bridge.review_workflow import DEFAULT_REVIEW_WORKFLOW_REPORT_PATH, run_review_workflow_benchmark


def main() -> int:
    parser = argparse.ArgumentParser(description="Run deterministic review-workflow fixture proof.")
    parser.add_argument(
        "--report-path",
        type=Path,
        default=DEFAULT_REVIEW_WORKFLOW_REPORT_PATH,
        help="Path where the JSON report should be written.",
    )
    args = parser.parse_args()

    report = run_review_workflow_benchmark()
    args.report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
