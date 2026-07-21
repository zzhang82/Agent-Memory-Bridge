from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import tempfile
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .filesystem_safety import ensure_private_directory, ensure_private_file
from .record_projection import (
    METADATA_SCHEMA_VERSION,
    CanonicalMetadata,
    resolve_record_projection,
    sync_record_projection,
)
from .repository import delete_entry_in_transaction, normalize_content
from .schema import rotate_database_epoch
from .service_lock import ServiceFileLock
from .signals import SignalSnapshot, signal_validation_issues

CHECKPOINT_MODES = {"PASSIVE", "FULL", "RESTART", "TRUNCATE"}


def inspect_database(db_path: Path, *, full: bool = False, log_dir: Path | None = None) -> dict[str, Any]:
    path = Path(db_path)
    if not path.is_file():
        return {
            "ok": False,
            "db_path": str(path),
            "exists": False,
            "check_mode": "integrity_check" if full else "quick_check",
            "errors": ["database file does not exist"],
        }
    try:
        with closing(_read_only_connection(path)) as conn:
            pragma_name = "integrity_check" if full else "quick_check"
            integrity_rows = [str(row[0]) for row in conn.execute(f"PRAGMA {pragma_name}").fetchall()]
            foreign_key_rows = [tuple(row) for row in conn.execute("PRAGMA foreign_key_check").fetchall()]
            tables = {
                str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
            }
            content = _content_checks(conn, tables=tables)
            metrics = _database_metrics(conn, path=path, log_dir=log_dir)
    except (OSError, sqlite3.Error) as exc:
        return {
            "ok": False,
            "db_path": str(path),
            "exists": True,
            "check_mode": "integrity_check" if full else "quick_check",
            "errors": [f"{exc.__class__.__name__}: database inspection failed"],
        }

    pragma_ok = integrity_rows == ["ok"]
    content_error_count = sum(int(value) for key, value in content["counts"].items() if key.endswith("_count"))
    ok = pragma_ok and not foreign_key_rows and content_error_count == 0
    return {
        "ok": ok,
        "db_path": str(path),
        "exists": True,
        "check_mode": "integrity_check" if full else "quick_check",
        "integrity": {"ok": pragma_ok, "results": integrity_rows},
        "foreign_keys": {
            "ok": not foreign_key_rows,
            "violation_count": len(foreign_key_rows),
            "sample": [list(row) for row in foreign_key_rows[:20]],
        },
        "content": content,
        "metrics": metrics,
        "errors": [],
    }


def backup_database(source_db: Path, output: Path, *, force: bool = False, full_verify: bool = False) -> dict[str, Any]:
    source = Path(source_db).resolve()
    target = Path(output).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"source database does not exist: {source}")
    if source == target:
        raise ValueError("backup output must differ from the source database")
    if target.exists() and not force:
        raise FileExistsError(f"backup output already exists: {target}")
    ensure_private_directory(target.parent)
    temporary = _temporary_database_path(target.parent, prefix=f".{target.name}.backup-")
    try:
        _copy_database(source, temporary)
        verification = inspect_database(temporary, full=full_verify)
        if not verification["ok"]:
            raise RuntimeError("backup verification failed")
        _atomic_replace(temporary, target)
        ensure_private_file(target)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "ok": True,
        "source_db": str(source),
        "output": str(target),
        "bytes": target.stat().st_size,
        "verification": verification,
    }


def verify_backup(path: Path, *, full: bool = True) -> dict[str, Any]:
    return inspect_database(Path(path), full=full)


def rebuild_database_projections(
    db_path: Path,
    *,
    service_lock_path: Path | None = None,
) -> dict[str, Any]:
    path = Path(db_path).expanduser().resolve()
    lock_path = Path(service_lock_path).expanduser().resolve() if service_lock_path else path.parent / "service.lock"
    with ServiceFileLock(lock_path):
        with closing(sqlite3.connect(path, timeout=5.0)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("BEGIN IMMEDIATE")
            repaired_insertion_sequence_count = 0
            rotated_epoch: str | None = None
            try:
                rows = conn.execute(
                    """
                    SELECT id, namespace, kind, title, content, tags_json,
                           actor, source_app, is_learning_candidate
                    FROM memories
                    ORDER BY rowid
                    """
                ).fetchall()
                missing_insertions = conn.execute(
                    """
                    SELECT m.id
                    FROM memories m
                    LEFT JOIN memory_insertions i ON i.memory_id = m.id
                    WHERE i.memory_id IS NULL
                    ORDER BY m.rowid
                    """
                ).fetchall()
                for missing in missing_insertions:
                    conn.execute(
                        "INSERT OR IGNORE INTO memory_insertions (memory_id) VALUES (?)",
                        (missing["id"],),
                    )
                repaired_insertion_sequence_count = len(missing_insertions)
                for row in rows:
                    try:
                        tags = json.loads(str(row["tags_json"] or "[]"))
                    except json.JSONDecodeError as exc:
                        raise RuntimeError(f"cannot rebuild malformed tags_json for {row['id']}") from exc
                    if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
                        raise RuntimeError(f"cannot rebuild malformed tags_json for {row['id']}")
                    content = str(row["content"])
                    content_hash = hashlib.sha256(normalize_content(content).encode("utf-8")).hexdigest()
                    conn.execute("UPDATE memories SET content_hash = ? WHERE id = ?", (content_hash, row["id"]))
                    sync_record_projection(
                        conn,
                        memory_id=str(row["id"]),
                        namespace=str(row["namespace"]),
                        content=content,
                        tags=tags,
                        kind=str(row["kind"]),
                        actor=str(row["actor"]) if row["actor"] is not None else None,
                        source_app=str(row["source_app"]) if row["source_app"] is not None else None,
                        is_learning_candidate=bool(row["is_learning_candidate"]),
                    )
                    conn.execute("DELETE FROM memories_fts WHERE memory_id = ?", (row["id"],))
                    conn.execute(
                        "INSERT INTO memories_fts(memory_id, title, content) VALUES (?, ?, ?)",
                        (row["id"], row["title"] or "", content),
                    )
                if repaired_insertion_sequence_count:
                    rotated_epoch = rotate_database_epoch(conn)
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
    health = inspect_database(path, full=False)
    return {
        "ok": health["ok"],
        "db_path": str(path),
        "rebuilt_count": len(rows),
        "repaired_insertion_sequence_count": repaired_insertion_sequence_count,
        "database_epoch": rotated_epoch,
        "service_lock_path": str(lock_path),
        "health": health,
    }


def restore_database(
    source_backup: Path,
    target_db: Path,
    *,
    force: bool = False,
    service_lock_path: Path | None = None,
) -> dict[str, Any]:
    source = Path(source_backup).expanduser().resolve()
    target = Path(target_db).expanduser().resolve()
    lock_path = Path(service_lock_path).expanduser().resolve() if service_lock_path else target.parent / "service.lock"
    with ServiceFileLock(lock_path):
        return _restore_database_locked(source, target, force=force, service_lock_path=lock_path)


def _restore_database_locked(
    source: Path,
    target: Path,
    *,
    force: bool,
    service_lock_path: Path,
) -> dict[str, Any]:
    source_verification = inspect_database(source, full=True)
    if not source_verification["ok"]:
        raise RuntimeError("restore source failed integrity verification")
    if source == target:
        raise ValueError("restore source and target must differ")
    if target.exists() and not force:
        raise FileExistsError(f"restore target already exists: {target}")

    ensure_private_directory(target.parent)
    temporary = _temporary_database_path(target.parent, prefix=f".{target.name}.restore-")
    recovery_path: Path | None = None
    checkpoint: dict[str, Any] | None = None
    restored_epoch: str | None = None
    try:
        _copy_database(source, temporary)
        temporary_verification = inspect_database(temporary, full=True)
        if not temporary_verification["ok"]:
            raise RuntimeError("restored temporary database failed integrity verification")

        if target.exists():
            checkpoint = checkpoint_database(target, mode="TRUNCATE")
            if int(checkpoint["busy"]) != 0:
                raise RuntimeError("restore target is busy; stop bridge clients before restoring")
            recovery_path = _recovery_path(target)
            _copy_database(target, recovery_path)
            ensure_private_file(recovery_path)

        # Restore through SQLite's backup API so already-open connections do not
        # retain a split view of an unlinked database inode.
        _copy_database(temporary, target)
        with closing(sqlite3.connect(target, timeout=5.0)) as restored_conn:
            restored_conn.execute("BEGIN IMMEDIATE")
            restored_epoch = rotate_database_epoch(restored_conn)
            restored_conn.commit()
        checkpoint_database(target, mode="TRUNCATE")
        ensure_private_file(target)
        restored_verification = inspect_database(target, full=True)
        if not restored_verification["ok"]:
            raise RuntimeError("restored database failed final integrity verification")
    except BaseException:
        temporary.unlink(missing_ok=True)
        if recovery_path is not None and recovery_path.exists():
            _copy_database(recovery_path, target)
            checkpoint_database(target, mode="TRUNCATE")
        elif target.exists():
            target.unlink(missing_ok=True)
        raise
    finally:
        temporary.unlink(missing_ok=True)

    return {
        "ok": True,
        "source_backup": str(source),
        "target_db": str(target),
        "recovery_backup": str(recovery_path) if recovery_path is not None else None,
        "database_epoch": restored_epoch,
        "service_lock_path": str(service_lock_path),
        "pre_restore_checkpoint": checkpoint,
        "verification": restored_verification,
    }


def checkpoint_database(db_path: Path, *, mode: str = "PASSIVE") -> dict[str, Any]:
    normalized = mode.strip().upper()
    if normalized not in CHECKPOINT_MODES:
        raise ValueError(f"checkpoint mode must be one of {sorted(CHECKPOINT_MODES)}")
    path = Path(db_path)
    if not path.is_file():
        raise FileNotFoundError(f"database does not exist: {path}")
    with closing(sqlite3.connect(path, timeout=5.0)) as conn:
        row = conn.execute(f"PRAGMA wal_checkpoint({normalized})").fetchone()
    busy, log_frames, checkpointed_frames = (int(value) for value in (row or (0, 0, 0)))
    return {
        "ok": busy == 0,
        "db_path": str(path),
        "mode": normalized.lower(),
        "busy": busy,
        "log_frames": log_frames,
        "checkpointed_frames": checkpointed_frames,
        "wal_bytes": _file_size(Path(f"{path}-wal")),
    }


def cleanup_signals(
    db_path: Path,
    *,
    acked_older_than_days: float,
    expired_older_than_days: float,
    limit: int = 1_000,
    apply: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    if acked_older_than_days < 0 or expired_older_than_days < 0:
        raise ValueError("signal retention days must not be negative")
    bounded_limit = max(1, min(int(limit), 10_000))
    timestamp = now or datetime.now(UTC)
    acked_cutoff = (timestamp - timedelta(days=acked_older_than_days)).isoformat()
    expired_cutoff = (timestamp - timedelta(days=expired_older_than_days)).isoformat()
    path = Path(db_path)
    with closing(sqlite3.connect(path, timeout=5.0)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        rows = conn.execute(
            """
            SELECT id, namespace, kind
            FROM memories
            WHERE kind = 'signal'
              AND (
                (signal_status = 'acked' AND acknowledged_at IS NOT NULL AND acknowledged_at <= ?)
                OR (expires_at IS NOT NULL AND expires_at <= ?)
              )
            ORDER BY COALESCE(acknowledged_at, expires_at, created_at) ASC, id ASC
            LIMIT ?
            """,
            (acked_cutoff, expired_cutoff, bounded_limit),
        ).fetchall()
        candidate_ids = [str(row["id"]) for row in rows]
        if not apply or not rows:
            return {
                "ok": True,
                "applied": False,
                "candidate_count": len(rows),
                "deleted_count": 0,
                "candidate_ids": candidate_ids,
                "acked_cutoff": acked_cutoff,
                "expired_cutoff": expired_cutoff,
                "limit": bounded_limit,
            }

        conn.execute("BEGIN IMMEDIATE")
        try:
            placeholders = ",".join("?" for _ in candidate_ids)
            confirmed = conn.execute(
                f"""
                SELECT id, namespace, kind
                FROM memories
                WHERE id IN ({placeholders})
                  AND kind = 'signal'
                  AND (
                    (signal_status = 'acked' AND acknowledged_at IS NOT NULL AND acknowledged_at <= ?)
                    OR (expires_at IS NOT NULL AND expires_at <= ?)
                  )
                """,
                (*candidate_ids, acked_cutoff, expired_cutoff),
            ).fetchall()
            deleted_at = timestamp.isoformat()
            confirmed_ids: list[str] = []
            cascade_deleted_ids: list[str] = []
            for row in confirmed:
                deletion = delete_entry_in_transaction(
                    conn,
                    root_id=str(row["id"]),
                    deleted_at=deleted_at,
                    root_cause="signal-retention",
                )
                if deletion is None:
                    continue
                confirmed_ids.append(str(row["id"]))
                cascade_deleted_ids.extend(deletion["cascade_deleted_ids"])
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    return {
        "ok": True,
        "applied": True,
        "candidate_count": len(rows),
        "deleted_count": len(confirmed_ids),
        "candidate_ids": candidate_ids,
        "deleted_ids": confirmed_ids,
        "cascade_deleted_ids": cascade_deleted_ids,
        "acked_cutoff": acked_cutoff,
        "expired_cutoff": expired_cutoff,
        "limit": bounded_limit,
    }


def _read_only_connection(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _copy_database(source: Path, target: Path) -> None:
    with closing(_read_only_connection(source)) as source_conn:
        with closing(sqlite3.connect(target)) as target_conn:
            source_conn.backup(target_conn)
            target_conn.commit()


def _temporary_database_path(parent: Path, *, prefix: str) -> Path:
    descriptor, name = tempfile.mkstemp(prefix=prefix, suffix=".db", dir=parent)
    os.close(descriptor)
    return Path(name)


def _atomic_replace(source: Path, target: Path) -> None:
    os.replace(source, target)
    with target.open("rb") as handle:
        os.fsync(handle.fileno())
    if os.name == "posix":
        directory_fd = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)


def _recovery_path(target: Path) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    candidate = target.with_name(f"{target.name}.before-restore-{timestamp}.bak")
    counter = 1
    while candidate.exists():
        candidate = target.with_name(f"{target.name}.before-restore-{timestamp}-{counter}.bak")
        counter += 1
    return candidate


def _content_checks(conn: sqlite3.Connection, *, tables: set[str]) -> dict[str, Any]:
    counts = {
        "malformed_tags_json_count": 0,
        "malformed_lineage_json_count": 0,
        "malformed_embedding_count": 0,
        "embedding_dimension_mismatch_count": 0,
        "non_finite_embedding_count": 0,
        "invalid_signal_state_count": 0,
        "stale_content_hash_count": 0,
        "invalid_metadata_value_count": 0,
        "missing_metadata_projection_count": 0,
        "stale_metadata_projection_count": 0,
        "stale_tag_projection_count": 0,
        "stale_edge_projection_count": 0,
        "stale_fts_projection_count": 0,
        "missing_insertion_sequence_count": 0,
        "edge_target_state_mismatch_count": 0,
    }
    samples: dict[str, list[str]] = {key.removesuffix("_count"): [] for key in counts}
    if "memories" not in tables:
        counts["missing_metadata_projection_count"] = 1
        samples["missing_metadata_projection"].append("memories-table-missing")
        return {"ok": False, "counts": counts, "samples": samples}

    memory_rows = conn.execute(
        """
        SELECT id, namespace, kind, title, content, tags_json, lineage_issues_json, content_hash,
               actor, source_app, is_learning_candidate
        FROM memories
        """
    ).fetchall()
    for row in memory_rows:
        memory_id = str(row["id"])
        if not _json_container(row["tags_json"], list):
            _record_issue(counts, samples, "malformed_tags_json", memory_id)
        if not _json_container(row["lineage_issues_json"], list):
            _record_issue(counts, samples, "malformed_lineage_json", memory_id)
        expected_content_hash = hashlib.sha256(normalize_content(str(row["content"])).encode("utf-8")).hexdigest()
        if str(row["content_hash"]) != expected_content_hash:
            _record_issue(counts, samples, "stale_content_hash", memory_id)

    if {"memory_metadata", "memory_tags", "memory_edges", "memories_fts"}.issubset(tables):
        for row in memory_rows:
            memory_id = str(row["id"])
            try:
                tags = json.loads(str(row["tags_json"] or "[]"))
            except json.JSONDecodeError:
                continue
            if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
                continue
            resolved = resolve_record_projection(
                conn,
                memory_id=memory_id,
                namespace=str(row["namespace"]),
                content=str(row["content"]),
                tags=tags,
                kind=str(row["kind"]),
                actor=str(row["actor"]) if row["actor"] is not None else None,
                source_app=str(row["source_app"]) if row["source_app"] is not None else None,
                is_learning_candidate=bool(row["is_learning_candidate"]),
            )
            if resolved.projection.metadata.validation_issues:
                _record_issue(counts, samples, "invalid_metadata_value", memory_id)
            metadata_row = conn.execute(
                "SELECT * FROM memory_metadata WHERE memory_id = ?",
                (memory_id,),
            ).fetchone()
            if metadata_row is not None and not _metadata_projection_matches(
                metadata_row, resolved.projection.metadata
            ):
                _record_issue(counts, samples, "stale_metadata_projection", memory_id)
            actual_tags = [
                str(item["tag"])
                for item in conn.execute(
                    "SELECT tag FROM memory_tags WHERE memory_id = ? ORDER BY tag",
                    (memory_id,),
                ).fetchall()
            ]
            if actual_tags != sorted(resolved.projection.tags):
                _record_issue(counts, samples, "stale_tag_projection", memory_id)
            actual_edges = [
                (
                    memory_id,
                    str(item["target_id"]),
                    str(item["relation"]),
                    int(item["position"]),
                    int(item["machine_owned"]),
                    str(item["target_namespace"]) if item["target_namespace"] is not None else None,
                    int(item["target_exists"]),
                )
                for item in conn.execute(
                    """
                    SELECT target_id, relation, position, machine_owned, target_namespace, target_exists
                    FROM memory_edges
                    WHERE source_id = ?
                    ORDER BY position, relation, target_id
                    """,
                    (memory_id,),
                ).fetchall()
            ]
            expected_edges = sorted(resolved.edge_rows, key=lambda item: (item[3], item[2], item[1]))
            if actual_edges != expected_edges:
                _record_issue(counts, samples, "stale_edge_projection", memory_id)
            fts_rows = conn.execute(
                "SELECT title, content FROM memories_fts WHERE memory_id = ?",
                (memory_id,),
            ).fetchall()
            if (
                len(fts_rows) != 1
                or str(fts_rows[0]["title"]) != str(row["title"] or "")
                or str(fts_rows[0]["content"]) != str(row["content"])
            ):
                _record_issue(counts, samples, "stale_fts_projection", memory_id)

    invalid_signal_rows = conn.execute(
        """
        SELECT id, kind, signal_status, claimed_by, claimed_at,
               lease_expires_at, expires_at, acknowledged_at
        FROM memories
        WHERE kind = 'signal'
        """
    ).fetchall()
    for row in invalid_signal_rows:
        if signal_validation_issues(SignalSnapshot.from_row(row)):
            _record_issue(counts, samples, "invalid_signal_state", str(row["id"]))

    if "memory_embeddings" in tables:
        for row in conn.execute("SELECT memory_id, embedding_dim, vector_json FROM memory_embeddings"):
            memory_id = str(row["memory_id"])
            try:
                vector = json.loads(str(row["vector_json"]))
            except (TypeError, json.JSONDecodeError):
                _record_issue(counts, samples, "malformed_embedding", memory_id)
                continue
            if not isinstance(vector, list):
                _record_issue(counts, samples, "malformed_embedding", memory_id)
                continue
            if len(vector) != int(row["embedding_dim"]):
                _record_issue(counts, samples, "embedding_dimension_mismatch", memory_id)
            try:
                finite = all(math.isfinite(float(value)) for value in vector)
            except (TypeError, ValueError):
                finite = False
            if not finite:
                _record_issue(counts, samples, "non_finite_embedding", memory_id)

    if "memory_metadata" in tables:
        rows = conn.execute(
            """
            SELECT m.id
            FROM memories m
            LEFT JOIN memory_metadata mm ON mm.memory_id = m.id
            WHERE mm.memory_id IS NULL
            """
        ).fetchall()
        for row in rows:
            _record_issue(counts, samples, "missing_metadata_projection", str(row["id"]))
    else:
        _record_issue(counts, samples, "missing_metadata_projection", "memory_metadata-table-missing")

    if "memory_insertions" in tables:
        rows = conn.execute(
            """
            SELECT m.id
            FROM memories m
            LEFT JOIN memory_insertions mi ON mi.memory_id = m.id
            WHERE mi.memory_id IS NULL
            """
        ).fetchall()
        for row in rows:
            _record_issue(counts, samples, "missing_insertion_sequence", str(row["id"]))
    else:
        _record_issue(counts, samples, "missing_insertion_sequence", "memory_insertions-table-missing")

    if "memory_edges" in tables:
        rows = conn.execute(
            """
            SELECT e.source_id || '->' || e.target_id AS edge_id
            FROM memory_edges e
            LEFT JOIN memories target ON target.id = e.target_id
            WHERE e.target_exists != CASE WHEN target.id IS NULL THEN 0 ELSE 1 END
               OR (target.id IS NOT NULL AND e.target_namespace IS NOT NULL
                   AND e.target_namespace != target.namespace)
            """
        ).fetchall()
        for row in rows:
            _record_issue(counts, samples, "edge_target_state_mismatch", str(row["edge_id"]))

    total = sum(counts.values())
    return {"ok": total == 0, "counts": counts, "samples": samples}


def _metadata_projection_matches(row: sqlite3.Row, expected: CanonicalMetadata) -> bool:
    try:
        validation_issues = json.loads(str(row["validation_issues_json"] or "[]"))
    except json.JSONDecodeError:
        return False
    return (
        row["record_type"] == expected.record_type
        and row["status"] == expected.status
        and row["confidence"] == expected.confidence
        and row["confidence_label"] == expected.confidence_label
        and row["valid_from"] == expected.valid_from
        and row["valid_until"] == expected.valid_until
        and int(row["metadata_schema_version"]) == METADATA_SCHEMA_VERSION
        and validation_issues == list(expected.validation_issues)
    )


def _database_metrics(conn: sqlite3.Connection, *, path: Path, log_dir: Path | None) -> dict[str, Any]:
    page_count = int(conn.execute("PRAGMA page_count").fetchone()[0])
    page_size = int(conn.execute("PRAGMA page_size").fetchone()[0])
    freelist_count = int(conn.execute("PRAGMA freelist_count").fetchone()[0])
    journal_mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0])
    memory_count = _table_count(conn, "memories")
    signal_count = 0
    acked_signal_count = 0
    expired_signal_count = 0
    if memory_count is not None:
        signal_count = int(conn.execute("SELECT COUNT(*) FROM memories WHERE kind = 'signal'").fetchone()[0])
        acked_signal_count = int(
            conn.execute("SELECT COUNT(*) FROM memories WHERE kind = 'signal' AND signal_status = 'acked'").fetchone()[
                0
            ]
        )
        expired_signal_count = int(
            conn.execute(
                "SELECT COUNT(*) FROM memories WHERE kind = 'signal' AND expires_at IS NOT NULL AND expires_at <= ?",
                (datetime.now(UTC).isoformat(),),
            ).fetchone()[0]
        )
    return {
        "database_bytes": _file_size(path),
        "wal_bytes": _file_size(Path(f"{path}-wal")),
        "shm_bytes": _file_size(Path(f"{path}-shm")),
        "log_bytes": _directory_size(log_dir) if log_dir is not None else None,
        "page_count": page_count,
        "page_size": page_size,
        "freelist_count": freelist_count,
        "freelist_bytes": freelist_count * page_size,
        "logical_database_bytes": page_count * page_size,
        "journal_mode": journal_mode,
        "memory_count": memory_count,
        "signal_count": signal_count,
        "acked_signal_count": acked_signal_count,
        "expired_signal_count": expired_signal_count,
    }


def _table_count(conn: sqlite3.Connection, table: str) -> int | None:
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    if exists is None:
        return None
    return int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])


def _json_container(value: Any, expected_type: type[list[Any]]) -> bool:
    try:
        parsed = json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return False
    return isinstance(parsed, expected_type)


def _record_issue(
    counts: dict[str, int],
    samples: dict[str, list[str]],
    name: str,
    identifier: str,
) -> None:
    counts[f"{name}_count"] += 1
    if len(samples[name]) < 20:
        samples[name].append(identifier)


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _directory_size(path: Path | None) -> int:
    if path is None or not path.exists():
        return 0
    total = 0
    for item in path.rglob("*"):
        if item.is_file():
            total += _file_size(item)
    return total
