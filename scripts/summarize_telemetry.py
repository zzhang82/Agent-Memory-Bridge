from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent_mem_bridge.paths import resolve_telemetry_log_dir
from agent_mem_bridge.telemetry_summary import (
    load_telemetry_spans,
    render_telemetry_summary_text,
    summarize_telemetry,
)


def main() -> None:
    args = _parse_args()
    log_path = args.log_path.resolve() if args.log_path else resolve_telemetry_log_dir() / "spans.jsonl"
    spans = load_telemetry_spans(log_path)
    summary = summarize_telemetry(
        spans,
        log_path=log_path,
        hours=args.hours,
    )
    rendered = json.dumps(summary, indent=2) if args.json else render_telemetry_summary_text(summary)
    if args.report_path is not None:
        args.report_path.parent.mkdir(parents=True, exist_ok=True)
        args.report_path.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize local telemetry spans for dogfood and benchmark checks.")
    parser.add_argument(
        "--log-path",
        type=Path,
        help="Optional spans.jsonl path. Defaults to configured [telemetry].log_dir/spans.jsonl.",
    )
    parser.add_argument(
        "--hours",
        type=float,
        default=None,
        help="Optional trailing time window in hours. Defaults to all available spans.",
    )
    parser.add_argument("--json", action="store_true", help="Render the summary as JSON.")
    parser.add_argument("--report-path", type=Path, help="Optional path to write the rendered summary to disk.")
    return parser.parse_args()


if __name__ == "__main__":
    main()
