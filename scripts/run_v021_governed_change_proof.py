from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent_mem_bridge.v021_governed_change_proof import (
    DEFAULT_V021_MANIFEST_PATH,
    DEFAULT_V021_REPORT_PATH,
    run_v021_governed_change_proof,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the fixed v0.21 governed-change proof.")
    parser.add_argument(
        "--manifest-path",
        type=Path,
        default=DEFAULT_V021_MANIFEST_PATH,
        help="Path to the exact governed-change manifest.",
    )
    parser.add_argument(
        "--report-path",
        type=Path,
        default=DEFAULT_V021_REPORT_PATH,
        help="Path where the deterministic JSON report is written.",
    )
    args = parser.parse_args()

    report = run_v021_governed_change_proof(manifest_path=args.manifest_path)
    args.report_path.write_bytes((json.dumps(report, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    return 0 if report["summary"]["gate_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
