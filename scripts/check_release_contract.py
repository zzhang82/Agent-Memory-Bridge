from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from agent_mem_bridge.current_release_contract import run_current_source_release_contract_check  # noqa: E402
from agent_mem_bridge.release_contract import run_release_contract_check  # noqa: E402


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

    canonical_root = Path(__file__).resolve().parents[1]
    if args.root.resolve() == canonical_root:
        report = run_current_source_release_contract_check(args.root)
    else:
        report = run_release_contract_check(args.root)
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
