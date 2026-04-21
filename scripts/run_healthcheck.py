from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent_mem_bridge.healthcheck import run_health_check
from agent_mem_bridge.paths import resolve_profile_source_root


def main() -> None:
    args = _parse_args()
    source_root = args.source_root.resolve() if args.source_root else _default_profile_root()
    report = run_health_check(
        source_root=source_root,
        check_stdio=not args.skip_stdio,
        compare_mode=args.compare_mode,
    )
    rendered = json.dumps(report, indent=2)
    if args.report_path is not None:
        args.report_path.parent.mkdir(parents=True, exist_ok=True)
        args.report_path.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run bridge health checks, with optional profile-source parity validation."
    )
    parser.add_argument(
        "source_root",
        nargs="?",
        type=Path,
        help="Optional path to a profile source root. Defaults to configured [profile].source_root when parity checks are needed.",
    )
    parser.add_argument(
        "--skip-stdio",
        action="store_true",
        help="Skip the stdio MCP smoke test.",
    )
    parser.add_argument(
        "--compare-mode",
        choices=["auto", "full", "live", "snapshot-audit"],
        default="auto",
        help="Comparison mode for optional source parity checks. Defaults to auto: live if a live manifest exists, otherwise full.",
    )
    parser.add_argument(
        "--report-path",
        type=Path,
        help="Optional path to write the JSON report to disk.",
    )
    return parser.parse_args()


def _default_profile_root() -> Path:
    return resolve_profile_source_root()


if __name__ == "__main__":
    main()

