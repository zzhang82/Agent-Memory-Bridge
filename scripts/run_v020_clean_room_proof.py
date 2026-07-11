from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent_mem_bridge.v020_clean_room_proof import (
    DEFAULT_V020_REPORT_PATH,
    DEFAULT_V020_TRANSCRIPT_PATH,
    ROOT,
    run_v020_clean_room_proof,
    write_v020_clean_room_outputs,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the v0.20 clean-room adoption proof.")
    parser.add_argument(
        "--project-root",
        type=Path,
        default=ROOT,
        help="Project root used to launch `python -m agent_mem_bridge`.",
    )
    parser.add_argument(
        "--runtime-dir",
        type=Path,
        default=None,
        help="Optional absent or empty runtime directory. Defaults to an isolated temporary directory.",
    )
    parser.add_argument(
        "--report-path",
        type=Path,
        default=DEFAULT_V020_REPORT_PATH,
        help="Path where the JSON proof report should be written.",
    )
    parser.add_argument(
        "--transcript-path",
        type=Path,
        default=DEFAULT_V020_TRANSCRIPT_PATH,
        help="Path where the Markdown proof transcript should be written.",
    )
    args = parser.parse_args()

    report = run_v020_clean_room_proof(project_root=args.project_root, runtime_dir=args.runtime_dir)
    write_v020_clean_room_outputs(
        report,
        report_path=args.report_path,
        transcript_path=args.transcript_path,
    )
    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    return 0 if report["summary"]["v020_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
