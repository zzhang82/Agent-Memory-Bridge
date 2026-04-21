from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent_mem_bridge.activation_stress import (
    DEFAULT_ACTIVATION_STRESS_PACK_PATH,
    render_activation_stress_text,
    run_activation_stress_pack,
)


def main() -> None:
    args = _parse_args()
    report = run_activation_stress_pack(
        pack_path=args.pack_path,
        buckets=tuple(args.bucket or ()),
    )
    if args.format == "text":
        rendered = render_activation_stress_text(report)
    else:
        rendered = json.dumps(report, indent=2)
    if args.report_path is not None:
        args.report_path.parent.mkdir(parents=True, exist_ok=True)
        args.report_path.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the local belief-ladder activation stress pack without touching the live bridge database."
    )
    parser.add_argument(
        "--pack-path",
        type=Path,
        default=DEFAULT_ACTIVATION_STRESS_PACK_PATH,
        help="Path to the activation stress pack manifest.",
    )
    parser.add_argument(
        "--bucket",
        action="append",
        default=[],
        help="Optional bucket filter. Repeat to include multiple buckets.",
    )
    parser.add_argument(
        "--format",
        choices=("json", "text"),
        default="json",
        help="Render the report as JSON or a compact text summary.",
    )
    parser.add_argument("--report-path", type=Path, help="Optional path to write the rendered report.")
    return parser.parse_args()


if __name__ == "__main__":
    main()
