from __future__ import annotations

"""Profile source migration helpers.

These helpers import and compare markdown-based profile source trees. They keep
compatibility with the original Cole source layout, but the public module shape
is neutral so new users do not need to start from Cole-specific naming.
"""

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from typing import Any

from .archive_snapshot import (
    build_default_live_manifest_path,
    find_latest_snapshot_manifest,
    load_manifest,
    load_manifest_relative_paths,
)
from .storage import MemoryStore


ROOT_MARKDOWN_FILES = {
    "architecture.md",
    "HOW-TO-USE-COLE.md",
}
SKIP_RELATIVE_PARTS = {
    "__pycache__",
    "executors",
    "scripts",
}
IMPORT_TAG = "source:cole-files"
IMPORT_ACTOR = "cole-importer"
IMPORT_SOURCE_APP = "agent-memory-bridge-importer"


@dataclass(frozen=True, slots=True)
class ProfileDocument:
    path: Path
    relative_path: Path
    namespace: str
    title: str
    tags: list[str]
    content: str


def import_profile_memory(
    store: MemoryStore,
    source_root: Path,
) -> dict[str, Any]:
    documents = build_profile_documents(source_root)
    results: list[dict[str, Any]] = []
    for document in documents:
        result = store.store(
            namespace=document.namespace,
            content=document.content,
            kind="memory",
            tags=document.tags,
            actor=IMPORT_ACTOR,
            title=document.title,
            source_app=IMPORT_SOURCE_APP,
        )
        results.append(
            {
                "relative_path": document.relative_path.as_posix(),
                "namespace": document.namespace,
                "title": document.title,
                **result,
            }
        )

    comparison = compare_profile_migration_with_mode(store, source_root, mode="full")
    return {
        "source_root": str(Path(source_root).resolve()),
        "document_count": len(documents),
        "stored_count": sum(1 for item in results if item["stored"]),
        "duplicate_count": sum(1 for item in results if not item["stored"]),
        "results": results,
        "comparison": comparison,
    }


def compare_profile_migration(store: MemoryStore, source_root: Path) -> dict[str, Any]:
    return compare_profile_migration_with_mode(store, source_root, mode="full")


def prune_stale_profile_imports(
    store: MemoryStore,
    source_root: Path,
    *,
    mode: Literal["full", "live", "snapshot-audit"] = "full",
    live_manifest_path: Path | None = None,
    snapshot_manifest_path: Path | None = None,
) -> dict[str, Any]:
    comparison = compare_profile_migration_with_mode(
        store,
        source_root,
        mode=mode,
        live_manifest_path=live_manifest_path,
        snapshot_manifest_path=snapshot_manifest_path,
    )
    stale_paths = list(comparison["extra_paths"])
    deleted_rows = _delete_imported_rows_by_source_path(store, stale_paths)
    return {
        "source_root": str(Path(source_root).resolve()),
        "mode": mode,
        "stale_path_count": len(stale_paths),
        "stale_paths": stale_paths,
        "deleted_row_count": deleted_rows,
        "comparison_before": comparison,
    }


def compare_profile_migration_with_mode(
    store: MemoryStore,
    source_root: Path,
    *,
    mode: Literal["full", "live", "snapshot-audit"] = "full",
    live_manifest_path: Path | None = None,
    snapshot_manifest_path: Path | None = None,
) -> dict[str, Any]:
    expected_documents, basis = _resolve_expected_documents(
        source_root=source_root,
        mode=mode,
        live_manifest_path=live_manifest_path,
        snapshot_manifest_path=snapshot_manifest_path,
    )
    expected_documents_by_path = {
        document.relative_path.as_posix(): document for document in expected_documents
    }
    expected_paths = set(expected_documents_by_path)
    expected_by_namespace = Counter(document.namespace for document in expected_documents)

    imported_rows = _load_imported_rows(store)
    imported_rows_by_path: dict[str, list[dict[str, Any]]] = {}
    for row in imported_rows:
        tags = json.loads(row["tags_json"] or "[]")
        source_path = _extract_tag_value(tags, "source-path:")
        if source_path:
            imported_rows_by_path.setdefault(source_path, []).append(
                {
                    "namespace": str(row["namespace"]),
                    "content": str(row["content"]),
                }
            )

    imported_paths = set(imported_rows_by_path)
    missing_paths = sorted(expected_paths - imported_paths)
    raw_extra_paths = sorted(imported_paths - expected_paths)
    content_mismatch_paths: list[str] = []
    namespace_mismatch_paths: list[str] = []
    content_match_count = 0
    imported_by_namespace: Counter[str] = Counter()

    for path, document in expected_documents_by_path.items():
        imported_versions = imported_rows_by_path.get(path, [])
        if not imported_versions:
            continue

        has_namespace_match = any(
            row["namespace"] == document.namespace for row in imported_versions
        )
        has_exact_match = any(
            row["namespace"] == document.namespace
            and row["content"] == document.content.strip()
            for row in imported_versions
        )

        if not has_namespace_match:
            namespace_mismatch_paths.append(path)
            continue
        if not has_exact_match:
            content_mismatch_paths.append(path)
            continue
        imported_by_namespace[document.namespace] += 1
        content_match_count += 1

    extra_paths = raw_extra_paths if mode == "full" else []
    out_of_scope_paths = raw_extra_paths if mode != "full" else []
    return {
        "mode": mode,
        "basis": basis,
        "expected_count": len(expected_paths),
        "imported_count": len(imported_paths),
        "missing_count": len(missing_paths),
        "extra_count": len(extra_paths),
        "out_of_scope_count": len(out_of_scope_paths),
        "out_of_scope_paths": out_of_scope_paths,
        "content_match_count": content_match_count,
        "content_mismatch_count": len(content_mismatch_paths),
        "content_mismatch_paths": content_mismatch_paths,
        "namespace_mismatch_count": len(namespace_mismatch_paths),
        "namespace_mismatch_paths": namespace_mismatch_paths,
        "missing_paths": missing_paths,
        "extra_paths": extra_paths,
        "expected_by_namespace": dict(sorted(expected_by_namespace.items())),
        "imported_by_namespace": dict(sorted(imported_by_namespace.items())),
    }


def build_profile_documents(
    source_root: Path,
    *,
    relative_paths: list[Path] | None = None,
) -> list[ProfileDocument]:
    root = Path(source_root).resolve()
    documents: list[ProfileDocument] = []
    if relative_paths is None:
        paths = iter_profile_markdown_files(root)
    else:
        paths = [root / relative_path for relative_path in relative_paths if (root / relative_path).is_file()]
    for path in paths:
        relative_path = path.relative_to(root)
        namespace = classify_profile_namespace(relative_path)
        title = extract_document_title(path, relative_path)
        tags = build_document_tags(relative_path, namespace)
        content = render_document_content(path, relative_path, namespace)
        documents.append(
            ProfileDocument(
                path=path,
                relative_path=relative_path,
                namespace=namespace,
                title=title,
                tags=tags,
                content=content,
            )
        )
    return documents


def iter_profile_markdown_files(source_root: Path) -> list[Path]:
    root = Path(source_root).resolve()
    files: list[Path] = []

    for name in sorted(ROOT_MARKDOWN_FILES):
        candidate = root / name
        if candidate.is_file():
            files.append(candidate)

    for subdir_name in ("memory", "skills"):
        subdir = root / subdir_name
        if not subdir.is_dir():
            continue
        for path in sorted(subdir.rglob("*.md")):
            relative = path.relative_to(root)
            if any(part in SKIP_RELATIVE_PARTS for part in relative.parts):
                continue
            files.append(path)

    deduped: list[Path] = []
    seen: set[Path] = set()
    for path in files:
        if path in seen:
            continue
        seen.add(path)
        deduped.append(path)
    return deduped


def classify_profile_namespace(relative_path: Path) -> str:
    parts = relative_path.parts
    as_posix = relative_path.as_posix()

    if as_posix in ROOT_MARKDOWN_FILES:
        return "cole-core"
    if parts[:2] == ("memory", "core"):
        return "cole-core"
    if parts[:2] == ("memory", "team"):
        return "cole-team"
    if parts[:2] == ("memory", "workflows"):
        return "cole-workflows"
    if parts[:2] == ("memory", "skills") or parts[:1] == ("skills",):
        return "cole-skills"
    if parts[:2] == ("memory", "workspace"):
        return "cole-workspace"
    return "cole-core"


def extract_document_title(path: Path, relative_path: Path) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            heading = stripped.lstrip("#").strip()
            if heading:
                return heading
    return relative_path.stem.replace("-", " ").replace("_", " ").strip() or relative_path.name


def build_document_tags(relative_path: Path, namespace: str) -> list[str]:
    section = relative_path.parts[1] if len(relative_path.parts) > 1 else "root"
    tags = [
        IMPORT_TAG,
        "record:cole-doc",
        "link:Cole",
        f"cole-namespace:{namespace}",
        f"cole-section:{section}",
        f"source-path:{relative_path.as_posix()}",
        f"source-file:{relative_path.name}",
    ]
    if relative_path.parts[:1] == ("skills",) or relative_path.parts[:2] == ("memory", "skills"):
        tags.append("cole-doc-type:skill")
    elif relative_path.parts[:2] == ("memory", "team"):
        tags.append("cole-doc-type:team")
    elif relative_path.parts[:2] == ("memory", "workflows"):
        tags.append("cole-doc-type:workflow")
    else:
        tags.append("cole-doc-type:memory")
    return tags


def render_document_content(path: Path, relative_path: Path, namespace: str) -> str:
    raw = path.read_text(encoding="utf-8").strip()
    section = relative_path.parts[1] if len(relative_path.parts) > 1 else "root"
    return (
        f"record_type: cole-doc\n"
        f"source_path: {relative_path.as_posix()}\n"
        f"namespace: {namespace}\n"
        f"section: {section}\n"
        f"format: markdown\n\n"
        f"{raw}\n"
    )


def _load_imported_rows(store: MemoryStore) -> list[Any]:
    with store._connect() as conn:
        return conn.execute(
            """
            SELECT namespace, tags_json, content
            FROM memories
            WHERE actor = ? AND source_app = ?
            """,
            (IMPORT_ACTOR, IMPORT_SOURCE_APP),
        ).fetchall()


def _delete_imported_rows_by_source_path(store: MemoryStore, relative_paths: list[str]) -> int:
    if not relative_paths:
        return 0

    deleted_total = 0
    with store._connect() as conn:
        for relative_path in relative_paths:
            tag_json = json.dumps(f"source-path:{relative_path}")
            row_ids = [
                row["id"]
                for row in conn.execute(
                    """
                    SELECT id
                    FROM memories
                    WHERE actor = ? AND source_app = ? AND tags_json LIKE ? ESCAPE '\\'
                    """,
                    (IMPORT_ACTOR, IMPORT_SOURCE_APP, f"%{tag_json}%"),
                ).fetchall()
            ]
            if not row_ids:
                continue
            placeholders = ", ".join("?" for _ in row_ids)
            conn.execute(
                f"DELETE FROM memories_fts WHERE memory_id IN ({placeholders})",
                row_ids,
            )
            conn.execute(
                f"DELETE FROM memories WHERE id IN ({placeholders})",
                row_ids,
            )
            deleted_total += len(row_ids)
        conn.commit()
    return deleted_total


def _extract_tag_value(tags: list[str], prefix: str) -> str | None:
    for tag in tags:
        if tag.startswith(prefix):
            return tag[len(prefix):]
    return None


def _resolve_expected_documents(
    *,
    source_root: Path,
    mode: Literal["full", "live", "snapshot-audit"],
    live_manifest_path: Path | None,
    snapshot_manifest_path: Path | None,
) -> tuple[list[ProfileDocument], dict[str, Any]]:
    resolved_source_root = Path(source_root).resolve()

    if mode == "full":
        return build_profile_documents(resolved_source_root), {
            "source_root": str(resolved_source_root),
        }

    if mode == "live":
        manifest_path = (live_manifest_path or build_default_live_manifest_path(resolved_source_root)).resolve()
        relative_paths = load_manifest_relative_paths(manifest_path)
        return build_profile_documents(resolved_source_root, relative_paths=relative_paths), {
            "source_root": str(resolved_source_root),
            "live_manifest_path": str(manifest_path),
        }

    manifest_path = snapshot_manifest_path or find_latest_snapshot_manifest(resolved_source_root)
    if manifest_path is None:
        raise FileNotFoundError("No snapshot manifest found for snapshot-audit mode")
    manifest = load_manifest(manifest_path)
    snapshot_root = Path(manifest["snapshot_root"]).resolve()
    relative_paths = [Path(item) for item in manifest.get("files", [])]
    return build_profile_documents(snapshot_root, relative_paths=relative_paths), {
        "source_root": str(resolved_source_root),
        "snapshot_manifest_path": str(Path(manifest_path).resolve()),
        "snapshot_root": str(snapshot_root),
    }


# Legacy compatibility aliases for older internal callers.
ColeDocument = ProfileDocument
import_cole_memory = import_profile_memory
compare_cole_migration = compare_profile_migration
prune_stale_cole_imports = prune_stale_profile_imports
compare_cole_migration_with_mode = compare_profile_migration_with_mode
build_cole_documents = build_profile_documents
iter_cole_markdown_files = iter_profile_markdown_files
classify_cole_namespace = classify_profile_namespace
