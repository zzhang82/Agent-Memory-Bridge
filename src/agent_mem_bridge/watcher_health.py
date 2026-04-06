from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .codex_rollout import parse_rollout_file


def run_watcher_health_check(sessions_root: Path, limit: int = 20) -> dict[str, Any]:
    root = Path(sessions_root)
    rollout_paths = sorted(root.rglob("rollout-*.jsonl"), key=lambda path: path.stat().st_mtime, reverse=True)
    recent_paths = rollout_paths[: max(1, min(limit, 200))]

    inspected: list[dict[str, Any]] = []
    parse_ok_count = 0
    parse_error_count = 0
    weak_summary_count = 0

    for rollout_path in recent_paths:
        try:
            summary = parse_rollout_file(rollout_path)
            raw_lines = _count_non_empty_lines(rollout_path)
            status_reasons: list[str] = []
            if not summary.thread_id:
                status_reasons.append("missing-thread-id")
            if not summary.cwd:
                status_reasons.append("missing-cwd")
            if raw_lines > 0 and not summary.user_messages and not summary.assistant_messages:
                status_reasons.append("no-message-content")

            status = "ok" if not status_reasons else "weak"
            if status == "ok":
                parse_ok_count += 1
            else:
                weak_summary_count += 1

            inspected.append(
                {
                    "path": str(rollout_path),
                    "status": status,
                    "reasons": status_reasons,
                    "thread_id": summary.thread_id,
                    "cwd": summary.cwd,
                    "source": summary.source,
                    "user_message_count": len(summary.user_messages),
                    "assistant_message_count": len(summary.assistant_messages),
                    "raw_line_count": raw_lines,
                    "last_updated": summary.last_updated,
                }
            )
        except (json.JSONDecodeError, OSError, UnicodeDecodeError, ValueError) as exc:
            parse_error_count += 1
            inspected.append(
                {
                    "path": str(rollout_path),
                    "status": "error",
                    "reasons": [f"{type(exc).__name__}: {exc}"],
                    "thread_id": "",
                    "cwd": "",
                    "source": "",
                    "user_message_count": 0,
                    "assistant_message_count": 0,
                    "raw_line_count": _safe_count_lines(rollout_path),
                    "last_updated": None,
                }
            )

    ok = parse_error_count == 0 and weak_summary_count == 0
    return {
        "ok": ok,
        "sessions_root": str(root.resolve()),
        "rollout_count": len(rollout_paths),
        "inspected_count": len(inspected),
        "parse_ok_count": parse_ok_count,
        "parse_error_count": parse_error_count,
        "weak_summary_count": weak_summary_count,
        "items": inspected,
    }


def _count_non_empty_lines(path: Path) -> int:
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def _safe_count_lines(path: Path) -> int:
    try:
        return _count_non_empty_lines(path)
    except OSError:
        return 0
