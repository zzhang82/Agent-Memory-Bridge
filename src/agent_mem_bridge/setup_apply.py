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
from typing import Any, Callable, Literal, Mapping

from .client_config import DEFAULT_SERVER_NAME
from .setup_planner import ClientSetupPlan, SetupPlan, _server_container_key
from .state_io import load_json_state

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


class _ChangedSincePlan(OSError):
    """Internal bounded abort used when a target changes before publication."""


PublicationHook = Callable[[Path], None] | None
# Test-only deterministic seams immediately before final snapshot revalidation and
# immediately before publication. Production leaves both hooks unset.
_before_publication_revalidation: PublicationHook = None
_before_publication: PublicationHook = None


@dataclass(frozen=True)
class FileSnapshot:
    exists: bool
    digest: str | None
    mtime_ns: int | None
    size: int | None
    device: int | None
    inode: int | None
    regular: bool
    readable: bool


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
    return "\n".join(lines).rstrip()


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
        return _manual_review_result(current_client, "P2B can only mutate eligible reviewed JSON plans.")
    if current_client.config_path is None:
        return _manual_review_result(current_client, "P2B has no stable configuration target for this client.")

    target = Path(current_client.config_path)
    expected_snapshot = snapshot.clients.get(preview.client, _missing_snapshot())
    observed_snapshot = _file_snapshot(target)
    if observed_snapshot != expected_snapshot:
        return _changed_since_plan_result(current_client)
    if expected_snapshot.exists and not _safe_regular_snapshot(expected_snapshot):
        return _manual_review_result(current_client, "The configuration target is not a readable regular file.")

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

    created = not expected_snapshot.exists
    applied_digest = hashlib.sha256(serialized).hexdigest()
    backup_path: Path | None = None
    temporary: Path | None = None
    created_parent = False
    receipt_path = _receipt_path(target)
    try:
        previous_receipt = _receipt_bytes(receipt_path)
    except OSError:
        return _manual_review_result(current_client, "The existing P2B receipt is not a readable regular file.")
    receipt_written = False
    published = False
    try:
        if target.parent.exists():
            if not target.parent.is_dir():
                raise OSError("target parent is not a directory")
        else:
            target.parent.mkdir(parents=True, exist_ok=False)
            created_parent = True
        if not created:
            backup_path = _create_backup(target, expected_snapshot)
        temporary = _prepare_temporary_bytes(
            target,
            serialized,
            existing_mode=_file_mode(target) if not created else None,
        )

        _run_publication_hook(_before_publication_revalidation, target)
        if _file_snapshot(target) != expected_snapshot:
            raise _ChangedSincePlan()

        _write_receipt(
            target,
            client=current_client.client,
            backup_path=backup_path,
            before_digest=expected_snapshot.digest,
            applied_digest=applied_digest,
            created=created,
            created_parent=created_parent,
        )
        receipt_written = True

        _run_publication_hook(_before_publication, target)
        _publish_prepared_bytes(temporary, target, expected_snapshot=expected_snapshot)
        temporary = None
        published = True
        _fsync_replaced_path(target)
    except _ChangedSincePlan:
        _cleanup_unpublished_apply(
            target,
            temporary=temporary,
            backup_path=backup_path,
            receipt_path=receipt_path,
            previous_receipt=previous_receipt,
            created_parent=created_parent,
        )
        return _changed_since_plan_result(current_client)
    except OSError:
        after_failure = _file_snapshot(target)
        changed = published or _snapshot_represents_applied(after_failure, applied_digest)
        if not changed:
            _cleanup_unpublished_apply(
                target,
                temporary=temporary,
                backup_path=backup_path,
                receipt_path=receipt_path,
                previous_receipt=previous_receipt,
                created_parent=created_parent,
            )
        return ClientApplyResult(
            client=current_client.client,
            status="failed",
            action=current_client.recommended_action,
            target_path=str(target),
            backup_path=str(backup_path) if changed and backup_path else None,
            verification="failed",
            detail=(
                "Configuration changed after rollback metadata was recorded; rollback remains available."
                if changed and receipt_written
                else "The configuration was not safely written; inspect the target before retrying."
            ),
            changed=changed,
            rollback_available=changed and receipt_written,
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
    applied_digest = receipt.get("applied_digest")
    backup_text = receipt.get("backup_path")
    if not isinstance(applied_digest, str):
        return ClientRollbackResult(
            client=client,
            status="skipped_manual_review",
            target_path=str(target),
            backup_path=None,
            detail="P2B receipt is incomplete; rollback was refused.",
        )
    backup_path = Path(backup_text) if isinstance(backup_text, str) and backup_text else None
    current = _file_snapshot(target)
    if not _snapshot_represents_applied(current, applied_digest):
        return ClientRollbackResult(
            client=client,
            status="skipped_manual_review",
            target_path=str(target),
            backup_path=str(backup_path) if backup_path else None,
            detail="Target changed after P2B apply; rollback will not overwrite user changes.",
        )

    created = receipt.get("created") is True
    raw_backup: bytes | None = None
    if not created:
        before_digest = receipt.get("before_digest")
        if not isinstance(before_digest, str) or backup_path is None or not _expected_backup_path(target, backup_path):
            return ClientRollbackResult(
                client=client,
                status="skipped_manual_review",
                target_path=str(target),
                backup_path=str(backup_path) if backup_path else None,
                detail="P2B backup integrity validation failed; rollback will not overwrite the target.",
            )
        try:
            raw_backup = backup_path.read_bytes()
        except OSError:
            return ClientRollbackResult(
                client=client,
                status="skipped_manual_review",
                target_path=str(target),
                backup_path=str(backup_path),
                detail="P2B backup could not be read safely; rollback was refused.",
            )
        if hashlib.sha256(raw_backup).hexdigest() != before_digest:
            return ClientRollbackResult(
                client=client,
                status="skipped_manual_review",
                target_path=str(target),
                backup_path=str(backup_path),
                detail="P2B backup digest does not match its receipt; rollback was refused.",
            )

    if _file_snapshot(target) != current:
        return ClientRollbackResult(
            client=client,
            status="skipped_manual_review",
            target_path=str(target),
            backup_path=str(backup_path) if backup_path else None,
            detail="Target changed during rollback preparation; rollback was refused.",
        )
    try:
        if created:
            target.unlink()
            _fsync_directory(target.parent)
        else:
            assert raw_backup is not None
            _atomic_write_bytes(target, raw_backup, existing_mode=_file_mode(target))
        receipt_path.unlink(missing_ok=True)
        _fsync_directory(receipt_path.parent)
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


def _manual_review_result(client: ClientSetupPlan, detail: str) -> ClientApplyResult:
    return ClientApplyResult(
        client=client.client,
        status="skipped_manual_review",
        action=client.recommended_action,
        target_path=client.config_path,
        backup_path=None,
        verification="not_applicable",
        detail=detail,
    )


def _safe_regular_snapshot(snapshot: FileSnapshot) -> bool:
    return snapshot.exists and snapshot.regular and snapshot.readable and snapshot.digest is not None


def _snapshot_represents_applied(snapshot: FileSnapshot, applied_digest: str) -> bool:
    return _safe_regular_snapshot(snapshot) and snapshot.digest == applied_digest


def _run_publication_hook(hook: PublicationHook, target: Path) -> None:
    if hook is not None:
        hook(target)


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
    """Return bounded metadata without allowing an unusual target to crash apply."""

    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return _missing_snapshot()
    except OSError:
        return FileSnapshot(True, None, None, None, None, None, False, False)
    if not stat.S_ISREG(metadata.st_mode):
        return FileSnapshot(
            exists=True,
            digest=None,
            mtime_ns=metadata.st_mtime_ns,
            size=metadata.st_size,
            device=metadata.st_dev,
            inode=metadata.st_ino,
            regular=False,
            readable=False,
        )
    try:
        raw = path.read_bytes()
    except OSError:
        return FileSnapshot(
            exists=True,
            digest=None,
            mtime_ns=metadata.st_mtime_ns,
            size=metadata.st_size,
            device=metadata.st_dev,
            inode=metadata.st_ino,
            regular=True,
            readable=False,
        )
    return FileSnapshot(
        exists=True,
        digest=hashlib.sha256(raw).hexdigest(),
        mtime_ns=metadata.st_mtime_ns,
        size=metadata.st_size,
        device=metadata.st_dev,
        inode=metadata.st_ino,
        regular=True,
        readable=True,
    )


def _missing_snapshot() -> FileSnapshot:
    return FileSnapshot(False, None, None, None, None, None, False, False)


def _file_mode(path: Path) -> int | None:
    snapshot = _file_snapshot(path)
    if not _safe_regular_snapshot(snapshot):
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
    if not _safe_regular_snapshot(snapshot):
        raise ValueError("cannot back up a missing or unreadable target")
    raw = target.read_bytes()
    if hashlib.sha256(raw).hexdigest() != snapshot.digest:
        raise _ChangedSincePlan()
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    for _ in range(100):
        backup = _backup_path(target)
        try:
            descriptor = os.open(backup, flags, 0o600)
        except FileExistsError:
            continue
        try:
            if os.name == "posix":
                os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            _fsync_directory(backup.parent)
            return backup
        except BaseException:
            try:
                os.close(descriptor)
            except OSError:
                pass
            backup.unlink(missing_ok=True)
            raise
    raise OSError("could not allocate a unique P2B backup")


def _prepare_temporary_bytes(target: Path, raw: bytes, *, existing_mode: int | None) -> Path:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.amb-", suffix=".tmp", dir=target.parent)
    temporary = Path(temporary_name)
    try:
        if os.name == "posix":
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        if existing_mode is not None:
            os.chmod(temporary, existing_mode)
        return temporary
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise


def _publish_prepared_bytes(temporary: Path, target: Path, *, expected_snapshot: FileSnapshot) -> None:
    if _file_snapshot(target) != expected_snapshot:
        raise _ChangedSincePlan()
    if expected_snapshot.exists:
        os.replace(temporary, target)
        return
    try:
        os.link(temporary, target)
    except FileExistsError as exc:
        raise _ChangedSincePlan() from exc
    temporary.unlink()


def _atomic_write_bytes(target: Path, raw: bytes, *, existing_mode: int | None) -> None:
    """Use only for rollback restoration after its own applied-state revalidation."""

    temporary = _prepare_temporary_bytes(target, raw, existing_mode=existing_mode)
    try:
        os.replace(temporary, target)
        _fsync_replaced_path(target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _fsync_replaced_path(target: Path) -> None:
    with target.open("r+b") as handle:
        os.fsync(handle.fileno())
    _fsync_directory(target.parent)


def _fsync_directory(directory: Path) -> None:
    if os.name != "posix":
        return
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _receipt_path(target: Path) -> Path:
    return target.with_name(f".{target.name}.amb-setup-receipt.json")


def _receipt_bytes(path: Path) -> bytes | None:
    snapshot = _file_snapshot(path)
    if not snapshot.exists:
        return None
    if not _safe_regular_snapshot(snapshot):
        raise OSError("existing P2B receipt is not a readable regular file")
    return path.read_bytes()


def _write_private_atomic(path: Path, raw: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        if os.name == "posix":
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_replaced_path(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


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
    raw = (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode("utf-8")
    _write_private_atomic(_receipt_path(target), raw)


def _restore_previous_receipt(path: Path, previous_receipt: bytes | None) -> None:
    if previous_receipt is None:
        path.unlink(missing_ok=True)
        _fsync_directory(path.parent)
        return
    _write_private_atomic(path, previous_receipt)


def _cleanup_unpublished_apply(
    target: Path,
    *,
    temporary: Path | None,
    backup_path: Path | None,
    receipt_path: Path,
    previous_receipt: bytes | None,
    created_parent: bool,
) -> None:
    if temporary is not None:
        temporary.unlink(missing_ok=True)
    if backup_path is not None:
        backup_path.unlink(missing_ok=True)
    _restore_previous_receipt(receipt_path, previous_receipt)
    if created_parent and target.parent.exists() and not any(target.parent.iterdir()):
        target.parent.rmdir()


def _expected_backup_path(target: Path, backup_path: Path) -> bool:
    expected_prefix = f"{target.name}.amb-before-"
    if not backup_path.is_absolute() or backup_path.parent != target.parent:
        return False
    if not backup_path.name.startswith(expected_prefix) or not backup_path.name.endswith(".bak"):
        return False
    if backup_path.is_symlink():
        return False
    snapshot = _file_snapshot(backup_path)
    return _safe_regular_snapshot(snapshot)


def _valid_receipt_for_client(receipt: Mapping[str, Any], *, client: str, target: Path) -> bool:
    created = receipt.get("created")
    before_digest = receipt.get("before_digest")
    backup_path = receipt.get("backup_path")
    return (
        receipt.get("schema_version") == SETUP_RECEIPT_SCHEMA_VERSION
        and receipt.get("client") == client
        and receipt.get("target_path") == str(target)
        and isinstance(receipt.get("applied_digest"), str)
        and len(receipt["applied_digest"]) == 64
        and isinstance(created, bool)
        and (created or (isinstance(before_digest, str) and len(before_digest) == 64 and isinstance(backup_path, str)))
        and (not created or (before_digest is None and backup_path is None))
    )
