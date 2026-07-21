from __future__ import annotations

import os
import socket
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .state_io import write_json_state_atomic


@dataclass(slots=True)
class ServiceHealthWriter:
    path: Path
    version: str
    started_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    state: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.state = {
            "schema": "service.health.v1",
            "pid": os.getpid(),
            "hostname": socket.gethostname(),
            "version": self.version,
            "started_at": self.started_at,
            "status": "starting",
            "active_lane": None,
            "last_cycle_started_at": None,
            "last_cycle_completed_at": None,
            "lanes": {},
        }
        self._write()

    def cycle_started(self) -> None:
        self.state.update(
            {
                "status": "running",
                "active_lane": None,
                "last_cycle_started_at": datetime.now(UTC).isoformat(),
            }
        )
        self._write()

    def lane_started(self, name: str) -> None:
        self.state["active_lane"] = name
        lane = dict((self.state.get("lanes") or {}).get(name) or {})
        lane.update({"status": "running", "last_started_at": datetime.now(UTC).isoformat()})
        self.state.setdefault("lanes", {})[name] = lane
        self._write()

    def lane_completed(self, name: str, result: dict[str, object]) -> None:
        timestamp = datetime.now(UTC).isoformat()
        lane = dict((self.state.get("lanes") or {}).get(name) or {})
        lane.update(
            {
                "status": result.get("status", "unknown"),
                "enabled": result.get("enabled", True),
                "last_completed_at": timestamp,
                "duration_ms": result.get("duration_ms", 0.0),
                "slow": result.get("slow", False),
                "processed_count": result.get("processed_count", 0),
                "consecutive_failures": result.get("consecutive_failures", 0),
                "failure_count": result.get("failure_count", 0),
                "remaining_count": result.get("remaining_count"),
                "error_type": result.get("error_type"),
            }
        )
        if result.get("status") == "ok":
            lane["last_success_at"] = timestamp
        if result.get("status") == "failed":
            lane["last_failure_at"] = timestamp
        self.state.setdefault("lanes", {})[name] = lane
        self.state["active_lane"] = None
        self._write()

    def cycle_completed(self, result: dict[str, dict[str, object]]) -> None:
        failed = sum(item.get("status") == "failed" for item in result.values())
        backoff = sum(item.get("status") == "backoff" for item in result.values())
        slow = sum(bool(item.get("slow")) for item in result.values())
        self.state.update(
            {
                "status": "degraded" if failed or backoff else "ok",
                "active_lane": None,
                "last_cycle_completed_at": datetime.now(UTC).isoformat(),
                "failed_lane_count": failed,
                "backoff_lane_count": backoff,
                "slow_lane_count": slow,
            }
        )
        self._write()

    def _write(self) -> None:
        write_json_state_atomic(self.path, self.state)
