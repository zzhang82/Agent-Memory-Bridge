"""Regenerate examples/demo/first-win-demo.cast from the local first-win loop."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CAST_PATH = ROOT / "examples" / "demo" / "first-win-demo.cast"
COMMAND = "python ./scripts/demo_first_win.py"
DISPLAY_ROOT = "agent-memory-bridge"
HEADER_TIMESTAMP = 1761523200
PRIVATE_PATH = re.compile(r"(?:[A-Za-z]:[\\/][^\s\"']+|/(?:home|tmp|var|private|Users)/[^\s\"']+)")


def main() -> None:
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "demo_first_win.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        sys.stderr.write(completed.stderr)
        sys.stderr.write(completed.stdout)
        raise SystemExit(completed.returncode or 1)

    transcript = scrub(completed.stdout)
    if PRIVATE_PATH.search(transcript):
        raise SystemExit("first-win transcript still contains a private path")
    if "same_record: true" not in transcript or "fresh_process: true" not in transcript:
        raise SystemExit("first-win transcript did not show a second-process recall")

    header = {
        "version": 2,
        "width": 100,
        "height": 24,
        "timestamp": HEADER_TIMESTAMP,
        "env": {"TERM": "xterm-256color", "SHELL": "sh"},
    }
    timeline: list[str] = [json.dumps(header, separators=(", ", ": "))]
    current_time = 0.2
    timeline.append(json.dumps([current_time, "o", f"{DISPLAY_ROOT}$ {COMMAND}\r\n"]))
    current_time += 0.4
    for line in transcript.splitlines(keepends=True):
        delay = 0.45 if line.startswith("# ") else 0.08
        timeline.append(json.dumps([round(current_time, 3), "o", line.replace("\n", "\r\n")]))
        current_time += delay

    CAST_PATH.parent.mkdir(parents=True, exist_ok=True)
    CAST_PATH.write_text("\n".join(timeline) + "\n", encoding="utf-8")
    print(str(CAST_PATH.relative_to(ROOT)))


def scrub(text: str) -> str:
    scrubbed = text
    for value in (str(ROOT), str(Path.home()), tempfile.gettempdir()):
        if len(value) > 1 and ("/" in value or "\\" in value):
            scrubbed = scrubbed.replace(value, "$AMB_HOME")
    return scrubbed


if __name__ == "__main__":
    main()
