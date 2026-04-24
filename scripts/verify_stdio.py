from __future__ import annotations

import json
from pathlib import Path

from agent_mem_bridge.onboarding import run_verify


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    report = run_verify(project_root=project_root, runtime_dir=project_root / ".runtime" / "verify-stdio")
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
