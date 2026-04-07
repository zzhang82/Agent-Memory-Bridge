from __future__ import annotations

import json
from pathlib import Path

from agent_mem_bridge.benchmarking import run_benchmark


ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = ROOT / "benchmark" / "latest-report.json"


def main() -> None:
    report = run_benchmark()
    REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
