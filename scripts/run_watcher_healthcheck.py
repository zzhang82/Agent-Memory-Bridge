from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent_mem_bridge.paths import resolve_sessions_root
from agent_mem_bridge.watcher_health import run_watcher_health_check


def main() -> None:
    args = _parse_args()
    sessions_root = args.sessions_root.resolve() if args.sessions_root else resolve_sessions_root()
    report = run_watcher_health_check(sessions_root=sessions_root, limit=args.limit)
    rendered = json.dumps(report, indent=2)
    if args.report_path is not None:
        args.report_path.parent.mkdir(parents=True, exist_ok=True)
        args.report_path.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check whether Codex rollout files still parse cleanly.")
    parser.add_argument(
        "sessions_root",
        nargs="?",
        type=Path,
        help="Path to the Codex sessions root. Defaults to the configured sessions root.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum number of recent rollout files to inspect.",
    )
    parser.add_argument(
        "--report-path",
        type=Path,
        help="Optional path to write the JSON report to disk.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
