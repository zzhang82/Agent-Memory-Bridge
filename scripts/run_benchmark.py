from __future__ import annotations
# ruff: noqa: E402, I001

import json
import sys
from pathlib import Path

from _source_imports import ensure_source_root

ensure_source_root()


from tools.evidence.benchmarking import run_benchmark

ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = ROOT / "benchmark" / "latest-report.json"
HYBRID_REPORT_PATH = ROOT / "benchmark" / "latest-hybrid-retrieval-report.json"


def main() -> None:
    include_hybrid = "--include-hybrid" in sys.argv[1:]
    report = run_benchmark(include_hybrid=include_hybrid)
    report_path = HYBRID_REPORT_PATH if include_hybrid else REPORT_PATH
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
