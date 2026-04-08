from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agent_mem_bridge.calibration import DEFAULT_REVIEWED_SAMPLES_PATH, write_classifier_calibration_report
from agent_mem_bridge.paths import (
    resolve_classifier_batch_size,
    resolve_classifier_command,
    resolve_classifier_minimum_confidence,
    resolve_classifier_timeout_seconds,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT_PATH = ROOT / "benchmark" / "latest-calibration-report.json"
FIXTURE_GATEWAY_PATH = ROOT / "tests" / "fixtures" / "fake_classifier_gateway.py"


def _fixture_gateway_command() -> str:
    return f'"{Path(sys.executable)}" "{FIXTURE_GATEWAY_PATH}"'


def main() -> None:
    parser = argparse.ArgumentParser(description="Run classifier-vs-fallback calibration on reviewed samples.")
    parser.add_argument("--report-path", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--samples-path", type=Path, default=DEFAULT_REVIEWED_SAMPLES_PATH)
    parser.add_argument("--command", type=str, default=None)
    parser.add_argument(
        "--fixture-gateway",
        action="store_true",
        help="Use the bundled fake classifier gateway for a deterministic local calibration run.",
    )
    parser.add_argument("--batch-size", type=int, default=resolve_classifier_batch_size())
    parser.add_argument("--timeout-seconds", type=float, default=resolve_classifier_timeout_seconds())
    parser.add_argument("--minimum-confidence", type=float, default=resolve_classifier_minimum_confidence())
    args = parser.parse_args()
    command = args.command if args.command is not None else (
        _fixture_gateway_command() if args.fixture_gateway else resolve_classifier_command()
    )
    if not command:
        print(
            "No classifier command configured; running fallback-only calibration. "
            "Use --fixture-gateway for the deterministic bundled gateway or pass --command explicitly.",
            file=sys.stderr,
        )

    report = write_classifier_calibration_report(
        report_path=args.report_path,
        reviewed_samples_path=args.samples_path,
        command=command,
        batch_size=args.batch_size,
        timeout_seconds=args.timeout_seconds,
        minimum_confidence=args.minimum_confidence,
    )
    print(
        json.dumps(
            {
                "summary": report["summary"],
                "slice_summaries": report["slice_summaries"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
