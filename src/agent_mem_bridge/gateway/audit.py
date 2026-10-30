"""Structured JSONL audit logging for AMB Enterprise Gateway.

Every request passing through the HTTP gateway generates an immutable,
single-line JSONL audit event. Audit telemetry is completely isolated
from SQLite database WAL to prevent write lock contention with core memory operations.
"""

from __future__ import annotations

import json
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class AuditRecord:
    """One immutable structured audit event emitted for an MCP HTTP interaction."""

    audit_id: str
    timestamp: str
    request_id: str
    principal_id: str
    client_ip: str | None
    transport: str  # "http_mcp"
    tool: str | None
    namespace: str | None
    status: str  # "success", "denied_unauthenticated", "denied_forbidden", "denied_rate_limited", "error"
    duration_ms: float
    arguments_summary: dict[str, Any] | None = None
    output_summary: dict[str, Any] | None = None
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "audit_id": self.audit_id,
            "timestamp": self.timestamp,
            "request_id": self.request_id,
            "principal_id": self.principal_id,
            "client_ip": self.client_ip,
            "transport": self.transport,
            "tool": self.tool,
            "namespace": self.namespace,
            "status": self.status,
            "duration_ms": round(self.duration_ms, 2),
            "arguments_summary": self.arguments_summary,
            "output_summary": self.output_summary,
        }
        if self.error_message:
            data["error_message"] = self.error_message
        return data

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))


def current_iso_timestamp() -> str:
    """Return formatted UTC ISO-8601 timestamp with milliseconds."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def summarize_arguments(arguments: dict[str, Any] | None) -> dict[str, Any] | None:
    """Extract a safe, bounded summary of tool arguments without sensitive values or unbounded text."""
    if not isinstance(arguments, dict) or not arguments:
        return None

    summary: dict[str, Any] = {}

    if "kind" in arguments and isinstance(arguments["kind"], str):
        summary["kind"] = arguments["kind"]

    if "title" in arguments and isinstance(arguments["title"], str):
        title = arguments["title"].strip()
        summary["title"] = title[:80] + ("..." if len(title) > 80 else "")

    if "content" in arguments and isinstance(arguments["content"], str):
        summary["content_bytes"] = len(arguments["content"].encode("utf-8"))

    if "replacement_content" in arguments and isinstance(arguments["replacement_content"], str):
        summary["replacement_content_bytes"] = len(arguments["replacement_content"].encode("utf-8"))

    if "query" in arguments and isinstance(arguments["query"], str):
        query = arguments["query"].strip()
        summary["query"] = query[:80] + ("..." if len(query) > 80 else "")

    if "limit" in arguments and isinstance(arguments["limit"], int):
        summary["limit"] = arguments["limit"]

    if "tags" in arguments and isinstance(arguments["tags"], list):
        summary["tags_count"] = len(arguments["tags"])

    if "tags_any" in arguments and isinstance(arguments["tags_any"], list):
        summary["tags_any_count"] = len(arguments["tags_any"])

    if "correlation_id" in arguments and isinstance(arguments["correlation_id"], str):
        summary["correlation_id"] = arguments["correlation_id"][:64]

    if "goal" in arguments and isinstance(arguments["goal"], str):
        goal = arguments["goal"].strip()
        summary["goal"] = goal[:80] + ("..." if len(goal) > 80 else "")

    if "event_type" in arguments and isinstance(arguments["event_type"], str):
        summary["event_type"] = arguments["event_type"]

    if "outcome" in arguments and isinstance(arguments["outcome"], str):
        summary["outcome"] = arguments["outcome"]

    if "id" in arguments and isinstance(arguments["id"], str):
        summary["id"] = arguments["id"]

    if "signal_id" in arguments and isinstance(arguments["signal_id"], str):
        summary["signal_id"] = arguments["signal_id"]

    if "to_kind" in arguments and isinstance(arguments["to_kind"], str):
        summary["to_kind"] = arguments["to_kind"]

    return summary or None


def summarize_output(output: Any) -> dict[str, Any] | None:
    """Extract a safe summary from MCP tool response content or JSON-RPC result payload."""
    if output is None:
        return None

    if isinstance(output, dict):
        summary: dict[str, Any] = {}
        # If response wraps MCP tool content
        if "content" in output and isinstance(output["content"], list):
            summary["content_blocks"] = len(output["content"])
            # Inspect first text block for structured JSON payloads
            if output["content"] and isinstance(output["content"][0], dict):
                first_block = output["content"][0]
                text = first_block.get("text", "")
                if isinstance(text, str) and text.startswith("{"):
                    try:
                        parsed = json.loads(text)
                        if isinstance(parsed, dict):
                            for key in ("id", "stored_id", "status", "count", "outcome", "run_id"):
                                if key in parsed:
                                    summary[key] = parsed[key]
                    except ValueError:
                        pass
        for key in ("id", "stored_id", "status", "count", "item_count", "outcome", "run_id", "tools_count"):
            if key in output:
                summary[key] = output[key]
        return summary or None

    return None


class AuditEmitter:
    """Thread-safe stream emitter for single-line JSONL audit records."""

    def __init__(
        self,
        target: str = "stdout",
        *,
        sink: Callable[[AuditRecord], None] | None = None,
    ) -> None:
        self._target = target
        self._sink = sink
        self._lock = threading.Lock()
        self._file: Any = None
        if sink is None and target not in ("stdout", "stderr"):
            log_path = Path(target).expanduser().resolve()
            log_path.parent.mkdir(parents=True, exist_ok=True)
            self._file = open(log_path, "a", encoding="utf-8")

    def emit(self, record: AuditRecord) -> None:
        """Write one structured audit event."""
        if self._sink is not None:
            self._sink(record)
            return

        line = record.to_json() + "\n"
        with self._lock:
            if self._target == "stdout":
                sys.stdout.write(line)
                sys.stdout.flush()
            elif self._target == "stderr":
                sys.stderr.write(line)
                sys.stderr.flush()
            elif self._file is not None:
                self._file.write(line)
                self._file.flush()

    def close(self) -> None:
        """Close any opened file descriptors."""
        with self._lock:
            if self._file is not None:
                self._file.close()
                self._file = None
