from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent_mem_bridge.calibration import DEFAULT_REVIEWED_SAMPLES_PATH, write_classifier_calibration_report
from agent_mem_bridge.paths import (
    resolve_classifier_batch_size,
    resolve_classifier_command,
    resolve_classifier_timeout_seconds,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT_PATH = ROOT / "benchmark" / "latest-calibration-report.json"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run classifier-vs-fallback calibration on reviewed samples.")
    parser.add_argument("--report-path", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--samples-path", type=Path, default=DEFAULT_REVIEWED_SAMPLES_PATH)
    parser.add_argument("--command", type=str, default=resolve_classifier_command())
    parser.add_argument("--batch-size", type=int, default=resolve_classifier_batch_size())
    parser.add_argument("--timeout-seconds", type=float, default=resolve_classifier_timeout_seconds())
    args = parser.parse_args()

    report = write_classifier_calibration_report(
        report_path=args.report_path,
        reviewed_samples_path=args.samples_path,
        command=args.command,
        batch_size=args.batch_size,
        timeout_seconds=args.timeout_seconds,
    )
    print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    main()
