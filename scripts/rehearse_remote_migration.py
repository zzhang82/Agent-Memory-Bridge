from __future__ import annotations
# ruff: noqa: E402, I001

import argparse
import hashlib
import json
import os
import re
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from _source_imports import ensure_source_root

ensure_source_root()

from agent_mem_bridge.database_maintenance import backup_database, restore_database, verify_backup
from agent_mem_bridge.filesystem_safety import ensure_private_directory, ensure_private_file
from agent_mem_bridge.index_health import inspect_indexes
from agent_mem_bridge.paths import resolve_bridge_db_path
from agent_mem_bridge.schema import CURRENT_SCHEMA_VERSION, database_epoch, schema_version
from agent_mem_bridge.storage import MemoryStore


DERIVED_TABLES = frozenset(
    {
        "memories_fts",
        "memories_fts_config",
        "memories_fts_content",
        "memories_fts_data",
        "memories_fts_docsize",
        "memories_fts_idx",
        "memory_embeddings",
        "memory_utility_shadow",
        "run_state_projection",
        "run_work_item_state_projection",
    }
)


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _canonical_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"type": "bytes", "sha256": hashlib.sha256(value).hexdigest(), "length": len(value)}
    if isinstance(value, float):
        return {"type": "float", "value": repr(value)}
    if value is None:
        return {"type": "null"}
    return {"type": type(value).__name__, "value": value}


def _hash_payload(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_only_connection(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)


def _table_manifest(conn: sqlite3.Connection, table: str, *, omit_epoch: bool = False) -> dict[str, Any]:
    quoted_table = _quote_identifier(table)
    cursor = conn.execute(f"SELECT * FROM {quoted_table}")
    columns = [str(column[0]) for column in cursor.description or ()]
    row_hashes: list[str] = []
    for row in cursor.fetchall():
        payload = {
            column: _canonical_value(value)
            for column, value in zip(columns, row, strict=True)
            if not (omit_epoch and table == "bridge_metadata" and column == "value" and row[0] == "database_epoch")
        }
        if omit_epoch and table == "bridge_metadata" and row[0] == "database_epoch":
            continue
        row_hashes.append(_hash_payload(payload))
    row_hashes.sort()
    return {
        "row_count": len(row_hashes),
        "row_content_sha256": row_hashes,
        "fingerprint": _hash_payload(row_hashes),
    }


def _table_fingerprint(tables: dict[str, dict[str, Any]]) -> str:
    return _hash_payload({name: table["fingerprint"] for name, table in sorted(tables.items())})


def build_database_manifest(path: Path) -> dict[str, Any]:
    """Return a content-sensitive manifest without exporting database contents."""

    database_path = Path(path).expanduser().resolve()
    with _read_only_connection(database_path) as conn:
        tables = sorted(
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            if str(row[0]) != "sqlite_sequence"
        )
        durable_names = [table for table in tables if table not in DERIVED_TABLES]
        durable_tables = {table: _table_manifest(conn, table) for table in durable_names}
        durable_comparison_tables = {
            table: _table_manifest(conn, table, omit_epoch=table == "bridge_metadata") for table in durable_names
        }
        derived_tables = {table: _table_manifest(conn, table) for table in tables if table in DERIVED_TABLES}
        namespaces = [
            {"namespace": str(row[0]), "record_count": int(row[1])}
            for row in conn.execute("SELECT namespace, COUNT(*) FROM memories GROUP BY namespace ORDER BY namespace")
        ]
        signal_count = int(conn.execute("SELECT COUNT(*) FROM memories WHERE kind = 'signal'").fetchone()[0])
        run_count = int(conn.execute("SELECT COUNT(*) FROM agent_runs").fetchone()[0])
        return {
            "schema_version": schema_version(conn),
            "database_epoch": database_epoch(conn),
            "namespaces": namespaces,
            "signals": {"count": signal_count},
            "runs": {"count": run_count},
            "durable_tables": durable_tables,
            "durable_fingerprint": _table_fingerprint(durable_tables),
            "durable_comparison_fingerprint": _table_fingerprint(durable_comparison_tables),
            "derived_tables": derived_tables,
            "derived_fingerprint": _table_fingerprint(derived_tables),
        }


def _index_health(path: Path) -> dict[str, Any]:
    with _read_only_connection(path) as conn:
        conn.row_factory = sqlite3.Row
        return inspect_indexes(conn)


def durable_manifests_equivalent(first: dict[str, Any], second: dict[str, Any]) -> bool:
    """Compare durable contents while allowing the documented restore epoch rotation."""

    return first["durable_comparison_fingerprint"] == second["durable_comparison_fingerprint"]


def _validate_source_snapshot(source_snapshot: Path) -> tuple[Path, dict[str, Any]]:
    source = Path(source_snapshot).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"source snapshot does not exist: {source}")
    if source == resolve_bridge_db_path().expanduser().resolve():
        raise ValueError("source snapshot must not be the configured live bridge database")
    sidecars = [Path(f"{source}{suffix}") for suffix in ("-wal", "-shm")]
    if any(sidecar.exists() for sidecar in sidecars):
        raise ValueError("source snapshot must be a consistent standalone backup, not a database/WAL file set")
    verification = verify_backup(source, full=True)
    if not verification["ok"]:
        raise RuntimeError("source snapshot failed full integrity verification")
    with _read_only_connection(source) as conn:
        version = schema_version(conn)
    if version != CURRENT_SCHEMA_VERSION:
        raise RuntimeError(f"source snapshot schema {version} does not match required schema {CURRENT_SCHEMA_VERSION}")
    return source, verification


def _prepare_runtime_dir(runtime_dir: Path | None) -> Path:
    if runtime_dir is None:
        path = Path(tempfile.mkdtemp(prefix="amb-migration-rehearsal-"))
    else:
        path = Path(runtime_dir).expanduser().resolve()
        if path.exists() and (not path.is_dir() or any(path.iterdir())):
            raise ValueError("runtime directory must be absent or empty")
    ensure_private_directory(path, tighten_existing=True)
    return path


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    ensure_private_file(path)


@contextmanager
def _isolated_store_environment(
    runtime_dir: Path,
    label: str,
    *,
    db_path: Path,
    log_dir: Path,
) -> Iterator[None]:
    config_path = runtime_dir / f"{label}-config.toml"
    bridge_home = runtime_dir / f"{label}-home"
    receipt_secret_path = runtime_dir / f"{label}-receipt-secret.json"
    telemetry_log_dir = runtime_dir / f"{label}-telemetry"
    ensure_private_directory(bridge_home, tighten_existing=True)
    ensure_private_directory(db_path.parent, tighten_existing=True)
    ensure_private_directory(log_dir, tighten_existing=True)
    ensure_private_directory(receipt_secret_path.parent, tighten_existing=True)
    config_path.write_text("", encoding="utf-8")
    ensure_private_file(config_path)
    previous = {name: value for name, value in os.environ.items() if name.startswith("AGENT_MEMORY_BRIDGE_")}
    for name in previous:
        os.environ.pop(name, None)
    variables = {
        "AGENT_MEMORY_BRIDGE_HOME": str(bridge_home),
        "AGENT_MEMORY_BRIDGE_CONFIG": str(config_path),
        "AGENT_MEMORY_BRIDGE_DB_PATH": str(db_path),
        "AGENT_MEMORY_BRIDGE_LOG_DIR": str(log_dir),
        "AGENT_MEMORY_BRIDGE_RECALL_RECEIPT_SECRET_PATH": str(receipt_secret_path),
        "AGENT_MEMORY_BRIDGE_TELEMETRY_MODE": "off",
        "AGENT_MEMORY_BRIDGE_TELEMETRY_LOG_DIR": str(telemetry_log_dir),
        "AGENT_MEMORY_BRIDGE_RETRIEVAL_MODE": "lexical",
        "AGENT_MEMORY_BRIDGE_EMBEDDING_PROVIDER": "hash",
        "AGENT_MEMORY_BRIDGE_EMBEDDING_CAPABILITY": "hashed_lexical",
        "AGENT_MEMORY_BRIDGE_CLASSIFIER_MODE": "off",
        "AGENT_MEMORY_BRIDGE_EMBEDDING_SCHEDULER_ENABLED": "false",
        "AGENT_MEMORY_BRIDGE_WATCHER_ENABLED": "false",
        "AGENT_MEMORY_BRIDGE_REFLEX_ENABLED": "false",
        "AGENT_MEMORY_BRIDGE_CONSOLIDATION_ENABLED": "false",
    }
    os.environ.update(variables)
    try:
        yield
    finally:
        for name in tuple(os.environ):
            if name.startswith("AGENT_MEMORY_BRIDGE_"):
                os.environ.pop(name, None)
        os.environ.update(previous)


def _item_version(item: dict[str, Any]) -> dict[str, str]:
    return {
        "id": str(item["id"]),
        "version_sha256": _hash_payload(
            {
                "created_at": item.get("created_at"),
                "content": item.get("content"),
                "id": item.get("id"),
                "title": item.get("title"),
            }
        ),
    }


def _lexical_query(item: dict[str, Any]) -> str:
    terms = re.findall(r"[A-Za-z0-9_]+", f"{item.get('title') or ''} {item.get('content') or ''}")
    if not terms:
        raise RuntimeError("representative memory has no supported lexical query term")
    return max(terms, key=len)


def _representative_memory(
    source_manifest: dict[str, Any], source_store: MemoryStore, candidate_store: MemoryStore
) -> dict[str, Any]:
    for namespace in source_manifest["namespaces"]:
        value = str(namespace["namespace"])
        browsed = source_store.browse(namespace=value, kind="memory", limit=1)
        if browsed["items"]:
            expected = browsed["items"][0]
            query = _lexical_query(expected)
            source_recall = source_store.recall(namespace=value, query=query, kind="memory", limit=20)
            candidate_recall = candidate_store.recall(namespace=value, query=query, kind="memory", limit=20)
            source_versions = [_item_version(item) for item in source_recall["items"]]
            candidate_versions = [_item_version(item) for item in candidate_recall["items"]]
            if source_versions != candidate_versions:
                raise RuntimeError("candidate lexical recall differs from the source clone")
            expected_version = _item_version(expected)
            expected_visible = expected_version in source_versions
            if expected_visible != (expected_version in candidate_versions):
                raise RuntimeError("candidate changed a governed lexical recall exclusion")
            return {
                "namespace_sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
                "query_sha256": hashlib.sha256(query.encode("utf-8")).hexdigest(),
                "expected_id_sha256": hashlib.sha256(str(expected["id"]).encode("utf-8")).hexdigest(),
                "expected_version_sha256": expected_version["version_sha256"],
                "source_result_versions": source_versions,
                "candidate_result_versions": candidate_versions,
                "expected_visible": expected_visible,
            }
    raise RuntimeError("source snapshot has no memory available for representative read and recall")


def rehearse_remote_migration(source_snapshot: Path, runtime_dir: Path | None = None) -> dict[str, Any]:
    """Run an isolated migration and rollback rehearsal against a consistent backup snapshot."""

    source, source_verification = _validate_source_snapshot(source_snapshot)
    source_bytes_before = _file_sha256(source)
    source_manifest = build_database_manifest(source)
    runtime = _prepare_runtime_dir(runtime_dir)
    source_manifest_path = runtime / "pre-manifest.json"
    candidate_manifest_path = runtime / "post-manifest.json"
    rollback_manifest_path = runtime / "rollback-manifest.json"
    _write_json(source_manifest_path, source_manifest)

    staged_snapshot = runtime / "source-snapshot.db"
    staged_backup = backup_database(source, staged_snapshot, full_verify=True)
    if not verify_backup(staged_snapshot, full=True)["ok"]:
        raise RuntimeError("isolated source snapshot copy failed full verification")

    candidate_db = runtime / "candidate" / "bridge.db"
    candidate_restore = restore_database(staged_snapshot, candidate_db)
    candidate_before_write = build_database_manifest(candidate_db)
    if not durable_manifests_equivalent(source_manifest, candidate_before_write):
        raise RuntimeError("candidate durable contents differ from the source snapshot before rehearsal write")
    if candidate_before_write["database_epoch"] == source_manifest["database_epoch"]:
        raise RuntimeError("candidate restore did not rotate the database epoch")

    candidate_log_dir = runtime / "candidate" / "logs"
    source_clone_log_dir = runtime / "source-clone" / "logs"
    with _isolated_store_environment(
        runtime,
        "candidate",
        db_path=candidate_db,
        log_dir=candidate_log_dir,
    ):
        source_index_before_startup = _index_health(staged_snapshot)
        candidate_index_before_startup = _index_health(candidate_db)
        if source_index_before_startup != candidate_index_before_startup:
            raise RuntimeError("candidate restore changed index health from the source clone")
        source_clone_store = MemoryStore(staged_snapshot, log_dir=source_clone_log_dir)
        source_index_after_startup = _index_health(staged_snapshot)
        candidate_store = MemoryStore(candidate_db, log_dir=candidate_log_dir)
        candidate_after_startup = build_database_manifest(candidate_db)
        candidate_index_after_startup = _index_health(candidate_db)
        if not durable_manifests_equivalent(source_manifest, candidate_after_startup):
            raise RuntimeError("candidate startup changed durable contents before the rehearsal write")
        if source_index_before_startup != source_index_after_startup:
            raise RuntimeError("source clone startup changed index health")
        if source_index_before_startup != candidate_index_after_startup:
            raise RuntimeError("candidate startup changed index health from the source clone")
        representative = _representative_memory(source_manifest, source_clone_store, candidate_store)
        marker_token = uuid4().hex
        marker = f"remote-migration-rehearsal-{marker_token}"
        stored = candidate_store.store(
            namespace="project:remote-migration-rehearsal",
            kind="memory",
            title="Remote migration rehearsal marker",
            content=marker,
            tags=["migration:rehearsal"],
        )
        marker_recall = candidate_store.recall(
            namespace="project:remote-migration-rehearsal",
            query=marker_token,
            kind="memory",
            limit=20,
        )
        candidate_index_after_write = _index_health(candidate_db)
    if stored.get("stored") is not True:
        raise RuntimeError("candidate rehearsal marker was not stored")
    stored_id = str(stored.get("id") or "")
    if not stored_id or not any(
        str(item.get("id")) == stored_id and str(item.get("content")) == marker for item in marker_recall["items"]
    ):
        raise RuntimeError("candidate rehearsal marker could not be recalled exactly")

    candidate_after_write = build_database_manifest(candidate_db)
    if candidate_after_write["durable_tables"]["memories"]["row_count"] != (
        candidate_after_startup["durable_tables"]["memories"]["row_count"] + 1
    ):
        raise RuntimeError("candidate rehearsal write did not add exactly one durable memory row")
    if candidate_index_after_write["fts"]["index_count"] != source_index_before_startup["fts"]["index_count"] + 1:
        raise RuntimeError("candidate rehearsal write did not add exactly one FTS row")
    if candidate_index_after_write["embeddings"]["missing_embedding_count"] != (
        source_index_before_startup["embeddings"]["missing_embedding_count"] + 1
    ):
        raise RuntimeError("candidate rehearsal write changed the expected embedding deficit")
    source_after_write = build_database_manifest(source)
    source_bytes_after_candidate = _file_sha256(source)
    if source_after_write["durable_fingerprint"] != source_manifest["durable_fingerprint"]:
        raise RuntimeError("source snapshot changed during isolated rehearsal")
    if source_bytes_after_candidate != source_bytes_before:
        raise RuntimeError("source snapshot bytes changed during isolated rehearsal")

    rollback_db = runtime / "rollback" / "bridge.db"
    rollback_restore = restore_database(staged_snapshot, rollback_db)
    rollback_manifest = build_database_manifest(rollback_db)
    if not durable_manifests_equivalent(source_manifest, rollback_manifest):
        raise RuntimeError("rollback durable contents differ from the source snapshot")
    if rollback_manifest["database_epoch"] in {
        source_manifest["database_epoch"],
        candidate_before_write["database_epoch"],
    }:
        raise RuntimeError("rollback restore did not receive a distinct database epoch")
    rollback_log_dir = runtime / "rollback" / "logs"
    with _isolated_store_environment(
        runtime,
        "rollback",
        db_path=rollback_db,
        log_dir=rollback_log_dir,
    ):
        rollback_index_before_startup = _index_health(rollback_db)
        if source_index_before_startup != rollback_index_before_startup:
            raise RuntimeError("rollback restore changed index health from the source clone")
        source_clone_store = MemoryStore(staged_snapshot, log_dir=source_clone_log_dir)
        rollback_store = MemoryStore(rollback_db, log_dir=rollback_log_dir)
        rollback_representative = _representative_memory(source_manifest, source_clone_store, rollback_store)
        rollback_index_after_startup = _index_health(rollback_db)
    if source_index_before_startup != rollback_index_after_startup:
        raise RuntimeError("rollback startup changed index health from the source clone")
    source_bytes_after_rollback = _file_sha256(source)
    if source_bytes_after_rollback != source_bytes_before:
        raise RuntimeError("source snapshot bytes changed during rollback rehearsal")

    _write_json(candidate_manifest_path, candidate_after_write)
    _write_json(rollback_manifest_path, rollback_manifest)
    checks = {
        "source_full_verification": bool(source_verification["ok"]),
        "source_schema_v12": source_manifest["schema_version"] == CURRENT_SCHEMA_VERSION,
        "candidate_durable_equivalence_before_write": durable_manifests_equivalent(
            source_manifest, candidate_before_write
        ),
        "candidate_durable_equivalence_after_startup": durable_manifests_equivalent(
            source_manifest, candidate_after_startup
        ),
        "candidate_epoch_rotated": candidate_before_write["database_epoch"] != source_manifest["database_epoch"],
        "candidate_representative_lexical_parity": representative["source_result_versions"]
        == representative["candidate_result_versions"],
        "candidate_governed_exclusion_preserved": representative["expected_visible"]
        == (
            representative["expected_version_sha256"]
            in {item["version_sha256"] for item in representative["candidate_result_versions"]}
        ),
        "candidate_unique_write": candidate_after_write["durable_tables"]["memories"]["row_count"]
        == candidate_after_startup["durable_tables"]["memories"]["row_count"] + 1,
        "candidate_marker_exact_recall": any(
            str(item.get("id")) == stored_id and str(item.get("content")) == marker for item in marker_recall["items"]
        ),
        "source_clone_index_health_after_startup": source_index_before_startup == source_index_after_startup,
        "candidate_index_health_before_startup": source_index_before_startup == candidate_index_before_startup,
        "candidate_index_health_after_startup": source_index_before_startup == candidate_index_after_startup,
        "candidate_embedding_deficit_accounted": candidate_index_after_write["embeddings"]["missing_embedding_count"]
        == source_index_before_startup["embeddings"]["missing_embedding_count"] + 1,
        "source_unchanged_after_candidate_write": source_after_write["durable_fingerprint"]
        == source_manifest["durable_fingerprint"],
        "source_bytes_unchanged": source_bytes_after_rollback == source_bytes_before,
        "rollback_durable_equivalence": durable_manifests_equivalent(source_manifest, rollback_manifest),
        "rollback_epoch_rotated": rollback_manifest["database_epoch"]
        not in {source_manifest["database_epoch"], candidate_before_write["database_epoch"]},
        "rollback_representative_lexical_parity": rollback_representative["source_result_versions"]
        == rollback_representative["candidate_result_versions"],
        "rollback_index_health_before_startup": source_index_before_startup == rollback_index_before_startup,
        "rollback_index_health_after_startup": source_index_before_startup == rollback_index_after_startup,
    }
    report = {
        "ok": all(checks.values()),
        "runtime_dir": str(runtime),
        "source_snapshot": str(source),
        "source_verification": source_verification,
        "staged_backup": staged_backup,
        "candidate_restore": candidate_restore,
        "rollback_restore": rollback_restore,
        "source_snapshot_sha256": {
            "before": source_bytes_before,
            "after_candidate": source_bytes_after_candidate,
            "after_rollback": source_bytes_after_rollback,
        },
        "checks": checks,
        "representative": representative,
        "rollback_representative": rollback_representative,
        "index_health": {
            "source_clone_before_startup": source_index_before_startup,
            "source_clone_after_startup": source_index_after_startup,
            "candidate_before_startup": candidate_index_before_startup,
            "candidate_after_startup": candidate_index_after_startup,
            "candidate_after_marker": candidate_index_after_write,
            "rollback_before_startup": rollback_index_before_startup,
            "rollback_after_startup": rollback_index_after_startup,
            "original_embedding_deficit": {
                "missing_before": source_index_before_startup["embeddings"]["missing_embedding_count"],
                "missing_after_candidate_startup": candidate_index_after_startup["embeddings"][
                    "missing_embedding_count"
                ],
                "missing_after_marker": candidate_index_after_write["embeddings"]["missing_embedding_count"],
            },
        },
        "manifests": {
            "pre": str(source_manifest_path),
            "candidate_before_write": candidate_before_write,
            "post": str(candidate_manifest_path),
            "rollback": str(rollback_manifest_path),
        },
        "evaluation": {
            "id": "E9_MIGRATION_REHEARSAL",
            "status": "INCONCLUSIVE",
            "reason": "This tool validates an isolated supplied snapshot only; source provenance and a rehearsal on the real source database remain owner evidence.",
        },
    }
    _write_json(runtime / "rehearsal-report.json", report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Rehearse AMB migration and rollback using an isolated backup snapshot."
    )
    parser.add_argument(
        "--source-snapshot",
        required=True,
        type=Path,
        help="Consistent standalone snapshot created with AMB backup tooling; never pass a live database/WAL file set.",
    )
    parser.add_argument(
        "--runtime-dir",
        type=Path,
        help="Absent or empty directory for private rehearsal copies and manifests. Defaults to a new temporary directory.",
    )
    args = parser.parse_args(argv)
    try:
        report = rehearse_remote_migration(args.source_snapshot, args.runtime_dir)
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        report = {
            "ok": False,
            "evaluation": {
                "id": "E9_MIGRATION_REHEARSAL",
                "status": "INCONCLUSIVE",
                "reason": "Rehearsal refused or incomplete. Check source full verification and private runtime evidence.",
            },
            "error_type": type(exc).__name__,
        }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
