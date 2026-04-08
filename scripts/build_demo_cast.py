from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CAST_PATH = ROOT / "examples" / "demo" / "terminal-demo.cast"
COMMAND = r".\.venv\Scripts\python.exe .\scripts\demo_terminal.py"


def main() -> None:
    completed = subprocess.run(
        [str(ROOT / ".venv" / "Scripts" / "python.exe"), str(ROOT / "scripts" / "demo_terminal.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )

    header = {
        "version": 2,
        "width": 100,
        "height": 30,
        "timestamp": int(time.time()),
        "env": {
            "TERM": "xterm-256color",
            "SHELL": "powershell.exe",
        },
    }

    timeline: list[str] = [json.dumps(header)]
    current_time = 0.2

    prompt = f"PS {ROOT}> {COMMAND}\r\n"
    timeline.append(json.dumps([round(current_time, 3), "o", prompt]))
    current_time += 0.35

    for line in completed.stdout.splitlines(keepends=True):
        delay = 0.5 if line.startswith("# ") else 0.08
        timeline.append(json.dumps([round(current_time, 3), "o", line.replace("\n", "\r\n")]))
        current_time += delay

    CAST_PATH.parent.mkdir(parents=True, exist_ok=True)
    CAST_PATH.write_text("\n".join(timeline) + "\n", encoding="utf-8")
    print(str(CAST_PATH))


if __name__ == "__main__":
    main()
