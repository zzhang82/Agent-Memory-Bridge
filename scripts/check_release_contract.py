from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agent_mem_bridge.release_contract import run_release_contract_check


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify that the release-facing surface stays aligned with local reports and server surface."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Project root to validate. Defaults to the repository root.",
    )
    args = parser.parse_args()

    report = run_release_contract_check(args.root)
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
