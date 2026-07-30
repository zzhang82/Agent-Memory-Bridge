from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Literal

from .database_maintenance import inspect_database
from .filesystem_safety import path_storage_warnings, permission_report
from .mcp_boundary import PUBLIC_TOOL_NAMES
from .paths import (
    resolve_bridge_db_path,
    resolve_bridge_home,
    resolve_bridge_log_dir,
    resolve_config_path,
    resolve_db_warn_bytes,
    resolve_operating_profile,
    resolve_service_heartbeat_stale_seconds,
    resolve_wal_warn_bytes,
)
from .service_lock import inspect_service_lock
from .stdio_probe import run_dual_stdio_probe

TOOL_NAMES = PUBLIC_TOOL_NAMES


def run_doctor(
    *,
    include_stdio: bool = False,
    project_root: Path | None = None,
) -> dict[str, Any]:
    resolved_project_root = (project_root or Path(__file__).resolve().parents[2]).resolve()
    config_path = resolve_config_path()
    bridge_home = resolve_bridge_home()
    db_path = resolve_bridge_db_path()
    log_dir = resolve_bridge_log_dir()
    try:
        operating_profile = resolve_operating_profile()
        profile_error = None
    except ValueError as exc:
        operating_profile = "invalid"
        profile_error = str(exc)
    hardened = operating_profile == "hardened-local"

    checks = [
        _build_check(
            name="operating_profile",
            ok=profile_error is None,
            status="pass" if profile_error is None else "fail",
            detail=f"Operating profile is {operating_profile}." if profile_error is None else profile_error,
            profile=operating_profile,
        ),
        _build_check(
            name="python_version",
            ok=sys.version_info >= (3, 11),
            status="pass" if sys.version_info >= (3, 11) else "fail",
            detail=f"Running Python {sys.version.split()[0]}",
        ),
        _sqlite_fts5_check(),
        _database_health_check(db_path, log_dir=log_dir),
        _database_capacity_check(db_path, log_dir=log_dir),
        _signal_lifecycle_check(db_path),
        _config_path_check(config_path),
        _permission_check("bridge_home_permissions", bridge_home, directory=True, hardened=hardened),
        _permission_check("database_permissions", db_path, directory=False, hardened=hardened),
        _permission_check("log_directory_permissions", log_dir, directory=True, hardened=hardened),
        _storage_location_check(bridge_home, db_path, hardened=hardened),
        _service_health_check(bridge_home, hardened=hardened),
        _path_writable_check("bridge_home_parent_writable", bridge_home),
        _path_writable_check("db_parent_writable", db_path),
        _path_writable_check("log_dir_parent_writable", log_dir),
        _build_check(
            name="resolved_defaults",
            ok=True,
            status="pass",
            detail="Resolved bridge paths and defaults successfully.",
            paths={
                "project_root": str(resolved_project_root),
                "bridge_home": str(bridge_home),
                "config_path": str(config_path),
                "db_path": str(db_path),
                "log_dir": str(log_dir),
            },
        ),
    ]

    stdio_check: dict[str, Any] | None = None
    stdio_report: dict[str, Any] | None = None
    if include_stdio:
        stdio_report = run_verify(project_root=resolved_project_root)
        modern_report = stdio_report.get("modern_stdio", {})
        legacy_report = stdio_report.get("legacy_stdio", {})
        checks.extend(
            [
                _build_check(
                    name="mcp_modern_stdio",
                    ok=bool(modern_report.get("ok")),
                    status="pass" if modern_report.get("ok") else "fail",
                    detail="Modern server/discover probe passed."
                    if modern_report.get("ok")
                    else "Modern server/discover probe failed.",
                    report=modern_report,
                ),
                _build_check(
                    name="mcp_legacy_stdio",
                    ok=bool(legacy_report.get("ok")),
                    status="pass" if legacy_report.get("ok") else "fail",
                    detail="Legacy initialize probe passed."
                    if legacy_report.get("ok")
                    else "Legacy initialize probe failed.",
                    report=legacy_report,
                ),
            ]
        )
        stdio_check = _build_check(
            name="stdio_verify",
            ok=bool(stdio_report["ok"]),
            status="pass" if stdio_report["ok"] else "fail",
            detail="Ran isolated stdio verify." if stdio_report["ok"] else "Isolated stdio verify failed.",
            report=stdio_report,
        )
        checks.append(stdio_check)

    ok = all(check["status"] != "fail" for check in checks)
    report = {
        "ok": ok,
        "project_root": str(resolved_project_root),
        "checks": checks,
        "config_path": str(config_path),
        "bridge_home": str(bridge_home),
        "db_path": str(db_path),
        "log_dir": str(log_dir),
        "include_stdio": include_stdio,
        "stdio_check": stdio_check,
    }
    if stdio_report is not None:
        report.update(
            {
                "mcp_sdk_version": stdio_report.get("mcp_sdk_version"),
                "modern_stdio": stdio_report.get("modern_stdio"),
                "legacy_stdio": stdio_report.get("legacy_stdio"),
            }
        )
    return report


def run_verify(
    *,
    project_root: Path | None = None,
    runtime_dir: Path | None = None,
) -> dict[str, Any]:
    resolved_project_root = (project_root or Path(__file__).resolve().parents[2]).resolve()
    if runtime_dir is None:
        with TemporaryDirectory(prefix="amb-verify-") as temp_dir:
            resolved_runtime_dir = Path(temp_dir)
            return asyncio.run(run_dual_stdio_probe(resolved_project_root, resolved_runtime_dir))
    return asyncio.run(run_dual_stdio_probe(resolved_project_root, runtime_dir.resolve()))


def render_report(report: dict[str, Any]) -> str:
    lines = [
        f"overall: {'ok' if report['ok'] else 'failed'}",
    ]
    for check in report.get("checks", []):
        lines.append(f"- {check['name']}: {check['status']} - {check['detail']}")
    return "\n".join(lines)


def render_verify_success_message(report: dict[str, Any]) -> str:
    status = "works" if report["ok"] else "failed"
    return f"Your MCP server {status}."


def _config_path_check(config_path: Path) -> dict[str, Any]:
    if not config_path.exists():
        return _build_check(
            name="config_path",
            ok=True,
            status="warn",
            detail="Config file is optional and was not found.",
            path=str(config_path),
        )
    try:
        with config_path.open("rb") as handle:
            tomllib.load(handle)
    except OSError as exc:
        return _build_check(
            name="config_path",
            ok=False,
            status="fail",
            detail=f"Config file could not be read: {exc}",
            path=str(config_path),
        )
    except tomllib.TOMLDecodeError as exc:
        return _build_check(
            name="config_path",
            ok=False,
            status="fail",
            detail=f"Config file could not be parsed: {exc}",
            path=str(config_path),
        )
    return _build_check(
        name="config_path",
        ok=True,
        status="pass",
        detail="Config file exists and is readable.",
        path=str(config_path),
    )


def _sqlite_fts5_check() -> dict[str, Any]:
    try:
        connection = sqlite3.connect(":memory:")
        try:
            connection.execute("CREATE VIRTUAL TABLE verify_fts USING fts5(content)")
        finally:
            connection.close()
    except sqlite3.DatabaseError as exc:
        return _build_check(
            name="sqlite_fts5",
            ok=False,
            status="fail",
            detail=f"SQLite FTS5 unavailable: {exc}",
        )
    return _build_check(
        name="sqlite_fts5",
        ok=True,
        status="pass",
        detail="SQLite FTS5 is available.",
    )


def _database_health_check(db_path: Path, *, log_dir: Path) -> dict[str, Any]:
    if not db_path.is_file():
        return _build_check(
            name="database_health",
            ok=True,
            status="pass",
            detail="Bridge database has not been created yet.",
        )
    report = inspect_database(db_path, full=False, log_dir=log_dir)
    return _build_check(
        name="database_health",
        ok=bool(report["ok"]),
        status="pass" if report["ok"] else "fail",
        detail="SQLite quick_check, foreign keys, and persisted structures are healthy."
        if report["ok"]
        else "SQLite or persisted structure health checks failed.",
        report=report,
    )


def _database_capacity_check(db_path: Path, *, log_dir: Path) -> dict[str, Any]:
    if not db_path.is_file():
        return _build_check(
            name="database_capacity",
            ok=True,
            status="pass",
            detail="Bridge database has not been created yet.",
        )
    report = inspect_database(db_path, full=False, log_dir=log_dir)
    metrics = report.get("metrics") or {}
    db_warn_bytes = resolve_db_warn_bytes()
    wal_warn_bytes = resolve_wal_warn_bytes()
    warnings: list[str] = []
    if int(metrics.get("database_bytes") or 0) >= db_warn_bytes:
        warnings.append("database-size-threshold")
    if int(metrics.get("wal_bytes") or 0) >= wal_warn_bytes:
        warnings.append("wal-size-threshold")
    return _build_check(
        name="database_capacity",
        ok=True,
        status="warn" if warnings else "pass",
        detail="Database and WAL sizes are below configured warning thresholds."
        if not warnings
        else "Database or WAL size reached a configured warning threshold.",
        warnings=warnings,
        metrics=metrics,
        thresholds={"database_bytes": db_warn_bytes, "wal_bytes": wal_warn_bytes},
    )


def _permission_check(name: str, path: Path, *, directory: bool, hardened: bool) -> dict[str, Any]:
    report = permission_report(path, directory=directory)
    if not report["exists"] or not report["applicable"]:
        return _build_check(
            name=name,
            ok=True,
            status="pass",
            detail="POSIX permission check is not applicable or the managed path does not exist yet.",
            report=report,
        )
    private = bool(report["private"])
    status: Literal["pass", "warn", "fail"] = "pass" if private else "fail" if hardened else "warn"
    return _build_check(
        name=name,
        ok=private or not hardened,
        status=status,
        detail="Managed path is private to the current POSIX user."
        if private
        else f"Managed path mode {report['mode']} allows group/world access; expected {report['expected_mode']}.",
        report=report,
    )


def _storage_location_check(bridge_home: Path, db_path: Path, *, hardened: bool) -> dict[str, Any]:
    warnings = sorted({*path_storage_warnings(bridge_home), *path_storage_warnings(db_path)})
    status: Literal["pass", "warn", "fail"] = "fail" if warnings and hardened else "warn" if warnings else "pass"
    return _build_check(
        name="storage_location",
        ok=not warnings or not hardened,
        status=status,
        detail="Bridge paths do not look like sync or network-share locations."
        if not warnings
        else "Bridge paths appear to use a sync/network location; SQLite WAL is safest on a local filesystem.",
        warnings=warnings,
    )


def _service_health_check(bridge_home: Path, *, hardened: bool) -> dict[str, Any]:
    lock_path = bridge_home / "service.lock"
    health_path = bridge_home / "service-health.json"
    lock = inspect_service_lock(lock_path)
    health: dict[str, Any] = {}
    health_error: str | None = None
    if health_path.is_file():
        try:
            parsed = json.loads(health_path.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                health = parsed
            else:
                health_error = "non-object-health-state"
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            health_error = exc.__class__.__name__

    stale_seconds = resolve_service_heartbeat_stale_seconds()
    heartbeat_at = _parse_health_datetime(
        health.get("last_cycle_completed_at") or health.get("last_cycle_started_at") or health.get("started_at")
    )
    age_seconds = max(0.0, (datetime.now(UTC) - heartbeat_at).total_seconds()) if heartbeat_at is not None else None
    process_count = _related_service_process_count()
    warnings: list[str] = []
    failures: list[str] = []
    if lock.get("exists") and lock.get("held") is False:
        warnings.append("stale-unheld-lock-file")
    if process_count is not None and process_count > 1:
        (failures if hardened else warnings).append("multiple-service-processes")
    if lock.get("held") is True and not health:
        failures.append("active-service-missing-heartbeat")
    if lock.get("held") is True and age_seconds is not None and stale_seconds > 0 and age_seconds > stale_seconds:
        failures.append("active-service-heartbeat-stale")
    if health_error is not None:
        failures.append("malformed-service-health")

    status: Literal["pass", "warn", "fail"] = "fail" if failures else "warn" if warnings else "pass"
    return _build_check(
        name="service_health",
        ok=not failures,
        status=status,
        detail="Service lock and heartbeat state are healthy."
        if status == "pass"
        else "Service lock/heartbeat state needs attention.",
        lock=lock,
        health=health,
        health_error=health_error,
        heartbeat_age_seconds=age_seconds,
        heartbeat_stale_seconds=stale_seconds,
        related_service_process_count=process_count,
        warnings=warnings,
        failures=failures,
    )


def _parse_health_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _related_service_process_count() -> int | None:
    proc_root = Path("/proc")
    if os.name != "posix" or not proc_root.is_dir():
        return None
    count = 0
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            command = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", errors="ignore")
        except OSError:
            continue
        normalized = " ".join(command.lower().split())
        if "service" in normalized and ("agent_mem_bridge" in normalized or "agent-memory-bridge" in normalized):
            count += 1
    return count


def _signal_lifecycle_check(db_path: Path) -> dict[str, Any]:
    if not db_path.is_file():
        return _build_check(
            name="signal_lifecycle_state",
            ok=True,
            status="pass",
            detail="Bridge database has not been created yet.",
            invalid_count=0,
            invalid_ids=[],
        )
    try:
        with sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True) as conn:
            table_exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'memories'"
            ).fetchone()
            if table_exists is None:
                return _build_check(
                    name="signal_lifecycle_state",
                    ok=True,
                    status="pass",
                    detail="Bridge database has no memories table yet.",
                    invalid_count=0,
                    invalid_ids=[],
                )
            invalid_count = int(
                conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM memories
                    WHERE kind = 'signal'
                      AND signal_status = 'claimed'
                      AND (
                        COALESCE(TRIM(claimed_by), '') = ''
                        OR COALESCE(TRIM(claimed_at), '') = ''
                        OR COALESCE(TRIM(lease_expires_at), '') = ''
                      )
                    """
                ).fetchone()[0]
            )
            rows = conn.execute(
                """
                SELECT id
                FROM memories
                WHERE kind = 'signal'
                  AND signal_status = 'claimed'
                  AND (
                    COALESCE(TRIM(claimed_by), '') = ''
                    OR COALESCE(TRIM(claimed_at), '') = ''
                    OR COALESCE(TRIM(lease_expires_at), '') = ''
                  )
                ORDER BY created_at ASC
                LIMIT 20
                """
            ).fetchall()
    except (OSError, sqlite3.Error) as exc:
        return _build_check(
            name="signal_lifecycle_state",
            ok=False,
            status="fail",
            detail=f"Could not inspect signal lifecycle state: {type(exc).__name__}.",
            invalid_count=None,
            invalid_ids=[],
        )

    invalid_ids = [str(row[0]) for row in rows]
    return _build_check(
        name="signal_lifecycle_state",
        ok=invalid_count == 0,
        status="pass" if invalid_count == 0 else "fail",
        detail=(
            "No claimed signals have missing ownership or lease fields."
            if invalid_count == 0
            else f"Found {invalid_count} claimed signal(s) with missing ownership or lease fields."
        ),
        invalid_count=invalid_count,
        invalid_ids=invalid_ids,
    )


def _path_writable_check(name: str, target: Path) -> dict[str, Any]:
    candidate = _path_check_target(target)
    ok = os.access(candidate, os.W_OK)
    return _build_check(
        name=name,
        ok=ok,
        status="pass" if ok else "fail",
        detail=f"Nearest existing parent is {'writable' if ok else 'not writable'}.",
        target=str(target),
        checked_parent=str(candidate),
    )


def _nearest_existing_parent(path: Path) -> Path:
    candidate = path if path.suffix == "" else path.parent
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate


def _path_check_target(path: Path) -> Path:
    candidate = path if path.is_dir() else path.parent
    if candidate.exists():
        return candidate
    return _nearest_existing_parent(candidate)


def _build_check(
    *,
    name: str,
    ok: bool,
    status: Literal["pass", "warn", "fail"],
    detail: str,
    **extra: Any,
) -> dict[str, Any]:
    payload = {
        "name": name,
        "ok": ok,
        "status": status,
        "detail": detail,
    }
    payload.update(extra)
    return payload
