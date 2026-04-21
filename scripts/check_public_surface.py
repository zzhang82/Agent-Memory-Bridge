from __future__ import annotations

import json
import sys
from pathlib import Path

from agent_mem_bridge.public_surface import run_public_surface_check


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    report = run_public_surface_check(project_root)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
