from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent_mem_bridge.v019_adoption_proof import DEFAULT_V019_REPORT_PATH, run_v019_adoption_proof


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the fixed v0.19 adoption-proof fixture pack.")
    parser.add_argument(
        "--manifest-path",
        type=Path,
        default=None,
        help="Path to the reviewed v0.19 fixture manifest.",
    )
    parser.add_argument(
        "--report-path",
        type=Path,
        default=DEFAULT_V019_REPORT_PATH,
        help="Path where the JSON report should be written.",
    )
    args = parser.parse_args()

    report = run_v019_adoption_proof(manifest_path=args.manifest_path)
    args.report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
