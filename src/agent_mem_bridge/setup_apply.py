"""Narrow, explicit P2B mutation and rollback for already-safe P2A plans.

This module deliberately consumes ``SetupPlan`` / ``ClientSetupPlan`` from P2A.  It
never detects clients, renders a second fragment, initializes AMB, or relaxes a
manual-review classification.  Automatic mutation is intentionally limited to
reviewed JSON paths; Codex TOML remains preview-only because the project has no
safe TOML serializer dependency.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Mapping

from .client_config import DEFAULT_SERVER_NAME
from .filesystem_safety import ensure_private_file
from .setup_planner import ClientSetupPlan, SetupPlan, _server_container_key
from .state_io import load_json_state, write_json_state_atomic

SETUP_APPLY_SCHEMA_VERSION = 1
SETUP_RECEIPT_SCHEMA_VERSION = 1

ApplyStatus = Literal[
    "unchanged",
    "created",
    "merged",
    "updated",
    "skipped_manual_review",
    "changed_since_plan",
    "failed",
]
RollbackStatus = Literal["restored", "removed_created", "skipped_manual_review", "failed"]

# P2B deliberately omits Codex: Python's stdlib can parse TOML but cannot safely
# serialize unrelated TOML configuration.  These names are not a detection
# registry; they are the narrow mutation allowlist for P2A's existing JSON paths.
_JSON_AUTO_APPLY_CLIENTS = frozenset({"claude-code", "vscode", "opencode"})
_WRITABLE_ACTIONS = frozenset({"would_create", "would_merge", "would_update"})


@dataclass(frozen=True)
class FileSnapshot:
    exists: bool
    digest: str | None
    mtime_ns: int | None
    size: int | None


@dataclass(frozen=True)
class SetupApplySnapshot:
    clients: Mapping[str, FileSnapshot]


@dataclass(frozen=True)
class ClientApplyResult:
    client: str
    status: ApplyStatus
    action: str
    target_path: str | None
    backup_path: str | None
    verification: Literal["passed", "not_applicable", "failed"]
    detail: str | None = None
    changed: bool = False
    rollback_available: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SetupApplyResult:
    schema_version: int
    write_count: int
    backup_count: int
    rollback_available: bool
    clients: tuple[ClientApplyResult, ...]
    next_commands: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "write_count": self.write_count,
            "backup_count": self.backup_count,
            "rollback_available": self.rollback_available,
            "clients": [client.as_dict() for client in self.clients],
            "next_commands": list(self.next_commands),
        }


@dataclass(frozen=True)
class ClientRollbackResult:
    client: str
    status: RollbackStatus
    target_path: str | None
    backup_path: str | None
    detail: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SetupRollbackResult:
    schema_version: int
    write_count: int
    clients: tuple[ClientRollbackResult, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "write_count": self.write_count,
            "clients": [client.as_dict() for client in self.clients],
        }


def capture_setup_apply_snapshot(plan: SetupPlan) -> SetupApplySnapshot:
    """Capture only bounded target metadata immediately after a P2A preview."""

    return SetupApplySnapshot(
        clients={
            client.client: _file_snapshot(Path(client.config_path)) if client.config_path else _missing_snapshot()
            for client in plan.clients
        }
    )


def apply_setup_plan(
    preview_plan: SetupPlan,
    *,
    current_plan: SetupPlan,
    snapshot: SetupApplySnapshot,
) -> SetupApplyResult:
    """Apply only currently eligible P2A JSON plans after fresh revalidation."""

    current_by_client = {client.client: client for client in current_plan.clients}
    results: list[ClientApplyResult] = []
    for preview_client in preview_plan.clients:
        current_client = current_by_client.get(preview_client.client)
        results.append(_apply_client(preview_client, current_client=current_client, snapshot=snapshot))

    writes = sum(result.changed for result in results)
    backups = sum(result.backup_path is not None for result in results)
    return SetupApplyResult(
        schema_version=SETUP_APPLY_SCHEMA_VERSION,
        write_count=writes,
        backup_count=backups,
        rollback_available=any(result.rollback_available for result in results),
        clients=tuple(results),
        next_commands=preview_plan.next_commands,
    )


def rollback_setup_plan(plan: SetupPlan) -> SetupRollbackResult:
    """Rollback only the latest P2B receipt adjacent to each selected target."""

    results: list[ClientRollbackResult] = []
    for client in plan.clients:
        if client.config_path is None:
            results.append(
                ClientRollbackResult(
                    client=client.client,
                    status="skipped_manual_review",
                    target_path=None,
                    backup_path=None,
                    detail="P2B has no stable configuration target for this client.",
                )
            )
            continue
        target = Path(client.config_path)
        receipt_path = _receipt_path(target)
        receipt = load_json_state(receipt_path)
        if not _valid_receipt_for_client(receipt, client=client.client, target=target):
            results.append(
                ClientRollbackResult(
                    client=client.client,
                    status="skipped_manual_review",
                    target_path=str(target),
                    backup_path=None,
                    detail="No matching P2B rollback receipt is available for this target.",
                )
            )
            continue
        results.append(_rollback_client(target, receipt_path=receipt_path, receipt=receipt, client=client.client))

    writes = sum(result.status in {"restored", "removed_created"} for result in results)
    return SetupRollbackResult(
        schema_version=SETUP_APPLY_SCHEMA_VERSION,
        write_count=writes,
        clients=tuple(results),
    )


def render_setup_apply_confirmation(plan: SetupPlan) -> str:
    """Render the compact human confirmation summary without config contents."""

    lines = ["Agent Memory Bridge safe setup apply", ""]
    for client in plan.clients:
        writable = _is_writable(client)
        backup = (
            "backup before modification"
            if client.config_path and client.existing_amb_state != "absent"
            else "no original-file backup"
        )
        lines.extend(
            [
                f"  Client: {client.client}",
                f"  Target: {client.config_path or 'path unknown'}",
                f"  Existing state: {client.existing_amb_state}",
                f"  Planned action: {client.recommended_action}",
                f"  Backup: {backup}",
                f"  Eligible for apply: {'yes' if writable else 'no; manual review only'}",
                "",
            ]
        )
    return "\\n".join(lines).rstrip()


def render_setup_apply_result(result: SetupApplyResult) -> str:
    lines = ["Agent Memory Bridge setup apply result", ""]
    for client in result.clients:
        lines.extend(
            [
                f"  {client.client}",
                f"    status: {client.status}",
                f"    action: {client.action}",
                f"    target: {client.target_path or 'path unknown'}",
                f"    backup: {client.backup_path or 'none'}",
                f"    configuration verification: {client.verification}",
            ]
        )
        if client.detail:
            lines.append(f"    note: {client.detail}")
    lines.extend(
        [
            "",
            f"Configuration files changed: {result.write_count}",
            f"Backups created: {result.backup_count}",
            "",
            "Next after configuration:",
            *[f"  {command}" for command in result.next_commands],
        ]
    )
    return "\n".join(lines)


def render_setup_rollback_result(result: SetupRollbackResult) -> str:
    lines = ["Agent Memory Bridge setup rollback result", ""]
    for client in result.clients:
        lines.extend(
            [
                f"  {client.client}",
                f"    status: {client.status}",
                f"    target: {client.target_path or 'path unknown'}",
                f"    backup: {client.backup_path or 'none'}",
            ]
        )
        if client.detail:
            lines.append(f"    note: {client.detail}")
    lines.extend(["", f"Configuration files changed: {result.write_count}"])
    return "\n".join(lines)


def _apply_client(
    preview: ClientSetupPlan,
    *,
    current_client: ClientSetupPlan | None,
    snapshot: SetupApplySnapshot,
) -> ClientApplyResult:
    if current_client is None or not _same_plan_identity(preview, current_client):
        return _changed_since_plan_result(preview)
    if preview.recommended_action == "no_change":
        return ClientApplyResult(
            client=preview.client,
            status="unchanged",
            action="no_change",
            target_path=preview.config_path,
            backup_path=None,
            verification="not_applicable",
        )
    if not _is_writable(current_client):
        return ClientApplyResult(
            client=preview.client,
            status="skipped_manual_review",
            action=current_client.recommended_action,
            target_path=current_client.config_path,
            backup_path=None,
            verification="not_applicable",
            detail="P2B can only mutate eligible reviewed JSON plans.",
        )
    if current_client.config_path is None:
        return ClientApplyResult(
            client=preview.client,
            status="skipped_manual_review",
            action=current_client.recommended_action,
            target_path=None,
            backup_path=None,
            verification="not_applicable",
            detail="P2B has no stable configuration target for this client.",
        )

    target = Path(current_client.config_path)
    expected_snapshot = snapshot.clients.get(preview.client, _missing_snapshot())
    if _file_snapshot(target) != expected_snapshot:
        return _changed_since_plan_result(current_client)

    try:
        expected_entry, merged_payload = _build_json_mutation(target, current_client)
        serialized = _serialize_json(merged_payload)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        return ClientApplyResult(
            client=current_client.client,
            status="failed",
            action=current_client.recommended_action,
            target_path=str(target),
            backup_path=None,
            verification="failed",
            detail="The eligible configuration could not be serialized safely.",
        )

    # The final pre-write snapshot protects the small interval after re-planning.
    if _file_snapshot(target) != expected_snapshot:
        return _changed_since_plan_result(current_client)

    created = not expected_snapshot.exists
    backup_path: Path | None = None
    created_parent = False
    try:
        if target.parent.exists():
            if not target.parent.is_dir():
                raise OSError("target parent is not a directory")
        else:
            target.parent.mkdir(parents=True, exist_ok=False)
            created_parent = True
        if not created:
            backup_path = _create_backup(target, expected_snapshot)
        _atomic_write_bytes(target, serialized, existing_mode=_file_mode(target) if not created else None)
    except OSError:
        after_failure = _file_snapshot(target)
        changed = (not expected_snapshot.exists and after_failure.exists) or (
            expected_snapshot.exists and after_failure.exists and after_failure.digest != expected_snapshot.digest
        )
        if created_parent and not changed and target.parent.exists() and not any(target.parent.iterdir()):
            target.parent.rmdir()
        return ClientApplyResult(
            client=current_client.client,
            status="failed",
            action=current_client.recommended_action,
            target_path=str(target),
            backup_path=str(backup_path) if backup_path else None,
            verification="failed",
            detail="The configuration was not safely written; inspect the target before retrying.",
            changed=changed,
        )

    applied_snapshot = _file_snapshot(target)
    if applied_snapshot.digest is None:
        return ClientApplyResult(
            client=current_client.client,
            status="failed",
            action=current_client.recommended_action,
            target_path=str(target),
            backup_path=str(backup_path) if backup_path else None,
            verification="failed",
            detail="The replacement target could not be read after write.",
            changed=True,
        )
    try:
        _write_receipt(
            target,
            client=current_client.client,
            backup_path=backup_path,
            before_digest=expected_snapshot.digest,
            applied_digest=applied_snapshot.digest,
            created=created,
            created_parent=created_parent,
        )
    except OSError:
        return ClientApplyResult(
            client=current_client.client,
            status="failed",
            action=current_client.recommended_action,
            target_path=str(target),
            backup_path=str(backup_path) if backup_path else None,
            verification="failed",
            detail="Configuration changed but rollback receipt creation failed; inspect the target manually.",
            changed=True,
        )
    verification = _verify_json_target(target, current_client, expected_entry, merged_payload)
    if not verification:
        return ClientApplyResult(
            client=current_client.client,
            status="failed",
            action=current_client.recommended_action,
            target_path=str(target),
            backup_path=str(backup_path) if backup_path else None,
            verification="failed",
            detail="Post-write verification failed; rollback information was retained.",
            changed=True,
            rollback_available=True,
        )

    status: ApplyStatus = (
        "created" if created else "merged" if current_client.recommended_action == "would_merge" else "updated"
    )
    return ClientApplyResult(
        client=current_client.client,
        status=status,
        action=current_client.recommended_action,
        target_path=str(target),
        backup_path=str(backup_path) if backup_path else None,
        verification="passed",
        changed=True,
        rollback_available=True,
    )


def _rollback_client(
    target: Path,
    *,
    receipt_path: Path,
    receipt: Mapping[str, Any],
    client: str,
) -> ClientRollbackResult:
    current = _file_snapshot(target)
    applied_digest = receipt.get("applied_digest")
    backup_text = receipt.get("backup_path")
    backup_path = Path(backup_text) if isinstance(backup_text, str) and backup_text else None
    if not current.exists or current.digest != applied_digest:
        return ClientRollbackResult(
            client=client,
            status="skipped_manual_review",
            target_path=str(target),
            backup_path=str(backup_path) if backup_path else None,
            detail="Target changed after P2B apply; rollback will not overwrite user changes.",
        )

    created = receipt.get("created") is True
    try:
        if created:
            target.unlink()
        else:
            if backup_path is None or not backup_path.is_file():
                raise OSError("P2B backup is unavailable")
            _atomic_write_bytes(target, backup_path.read_bytes(), existing_mode=_file_mode(target))
        receipt_path.unlink(missing_ok=True)
        if (
            created
            and receipt.get("created_parent") is True
            and target.parent.exists()
            and not any(target.parent.iterdir())
        ):
            target.parent.rmdir()
    except OSError:
        return ClientRollbackResult(
            client=client,
            status="failed",
            target_path=str(target),
            backup_path=str(backup_path) if backup_path else None,
            detail="Rollback could not complete safely; inspect the target and backup manually.",
        )
    return ClientRollbackResult(
        client=client,
        status="removed_created" if created else "restored",
        target_path=str(target),
        backup_path=str(backup_path) if backup_path else None,
    )


def _same_plan_identity(left: ClientSetupPlan, right: ClientSetupPlan) -> bool:
    return (
        left.client == right.client
        and left.config_path == right.config_path
        and left.config_format == right.config_format
        and left.existing_amb_state == right.existing_amb_state
        and left.recommended_action == right.recommended_action
        and left.proposed_fragment == right.proposed_fragment
    )


def _is_writable(client: ClientSetupPlan) -> bool:
    return (
        client.client in _JSON_AUTO_APPLY_CLIENTS
        and client.config_format == "json"
        and client.recommended_action in _WRITABLE_ACTIONS
        and client.existing_amb_state in {"absent", "update_required"}
        and client.config_path is not None
    )


def _changed_since_plan_result(client: ClientSetupPlan) -> ClientApplyResult:
    return ClientApplyResult(
        client=client.client,
        status="changed_since_plan",
        action=client.recommended_action,
        target_path=client.config_path,
        backup_path=None,
        verification="not_applicable",
        detail="Configuration state changed after preview; P2B will not overwrite it.",
    )


def _build_json_mutation(target: Path, client: ClientSetupPlan) -> tuple[dict[str, Any], dict[str, Any]]:
    proposed = json.loads(client.proposed_fragment)
    if not isinstance(proposed, dict):
        raise ValueError("P2A proposed JSON fragment is not an object")
    container_key = _server_container_key(client.client)
    proposed_servers = proposed.get(container_key)
    if not isinstance(proposed_servers, dict):
        raise ValueError("P2A proposed JSON server container is invalid")
    expected_entry = proposed_servers.get(DEFAULT_SERVER_NAME)
    if not isinstance(expected_entry, dict):
        raise ValueError("P2A proposed JSON server entry is invalid")

    if target.exists():
        payload = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("existing JSON config is not an object")
    else:
        payload = {}
    existing_servers = payload.get(container_key)
    if existing_servers is None:
        servers: dict[str, Any] = {}
        payload[container_key] = servers
    elif isinstance(existing_servers, dict):
        servers = dict(existing_servers)
        payload[container_key] = servers
    else:
        raise ValueError("existing JSON server container is not an object")
    servers[DEFAULT_SERVER_NAME] = expected_entry
    return expected_entry, payload


def _serialize_json(payload: Mapping[str, Any]) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def _verify_json_target(
    target: Path,
    client: ClientSetupPlan,
    expected_entry: Mapping[str, Any],
    expected_payload: Mapping[str, Any],
) -> bool:
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict):
        return False
    servers = payload.get(_server_container_key(client.client))
    return (
        isinstance(servers, dict) and servers.get(DEFAULT_SERVER_NAME) == expected_entry and payload == expected_payload
    )


def _file_snapshot(path: Path) -> FileSnapshot:
    if not path.exists():
        return _missing_snapshot()
    if not path.is_file():
        raise OSError("configuration target is not a regular file")
    raw = path.read_bytes()
    metadata = path.stat()
    return FileSnapshot(
        exists=True,
        digest=hashlib.sha256(raw).hexdigest(),
        mtime_ns=metadata.st_mtime_ns,
        size=metadata.st_size,
    )


def _missing_snapshot() -> FileSnapshot:
    return FileSnapshot(exists=False, digest=None, mtime_ns=None, size=None)


def _file_mode(path: Path) -> int | None:
    if not path.exists():
        return None
    return stat.S_IMODE(path.stat().st_mode)


def _backup_path(target: Path) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    candidate = target.with_name(f"{target.name}.amb-before-{timestamp}.bak")
    counter = 1
    while candidate.exists():
        candidate = target.with_name(f"{target.name}.amb-before-{timestamp}-{counter}.bak")
        counter += 1
    return candidate


def _create_backup(target: Path, snapshot: FileSnapshot) -> Path:
    if not snapshot.exists:
        raise ValueError("cannot back up a missing target")
    backup = _backup_path(target)
    raw = target.read_bytes()
    if hashlib.sha256(raw).hexdigest() != snapshot.digest:
        raise OSError("target changed before backup")
    try:
        with backup.open("xb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        ensure_private_file(backup)
    except BaseException:
        backup.unlink(missing_ok=True)
        raise
    return backup


def _atomic_write_bytes(target: Path, raw: bytes, *, existing_mode: int | None) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.amb-", suffix=".tmp", dir=target.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        if existing_mode is not None:
            os.chmod(temporary, existing_mode)
        os.replace(temporary, target)
        if existing_mode is None:
            ensure_private_file(target)
        _fsync_replaced_path(target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _fsync_replaced_path(target: Path) -> None:
    with target.open("r+b") as handle:
        os.fsync(handle.fileno())
    if os.name == "posix":
        directory_descriptor = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)


def _receipt_path(target: Path) -> Path:
    return target.with_name(f".{target.name}.amb-setup-receipt.json")


def _write_receipt(
    target: Path,
    *,
    client: str,
    backup_path: Path | None,
    before_digest: str | None,
    applied_digest: str,
    created: bool,
    created_parent: bool,
) -> None:
    receipt = {
        "schema_version": SETUP_RECEIPT_SCHEMA_VERSION,
        "client": client,
        "target_path": str(target),
        "backup_path": str(backup_path) if backup_path else None,
        "before_digest": before_digest,
        "applied_digest": applied_digest,
        "created": created,
        "created_parent": created_parent,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    write_json_state_atomic(_receipt_path(target), receipt)


def _valid_receipt_for_client(receipt: Mapping[str, Any], *, client: str, target: Path) -> bool:
    return (
        receipt.get("schema_version") == SETUP_RECEIPT_SCHEMA_VERSION
        and receipt.get("client") == client
        and receipt.get("target_path") == str(target)
        and isinstance(receipt.get("applied_digest"), str)
        and isinstance(receipt.get("created"), bool)
    )
