from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent_mem_bridge.archive_snapshot import build_default_snapshot_root, create_cole_archive_snapshot
from agent_mem_bridge.cole_migration import compare_cole_migration
from agent_mem_bridge.paths import resolve_bridge_db_path, resolve_bridge_log_dir, resolve_cole_source_root
from agent_mem_bridge.storage import MemoryStore


def main() -> None:
    args = _parse_args()
    source_root = args.source_root.resolve() if args.source_root else _default_cole_root()
    snapshot_root = args.snapshot_root.resolve() if args.snapshot_root else build_default_snapshot_root(source_root)

    store = MemoryStore(
        db_path=resolve_bridge_db_path(),
        log_dir=resolve_bridge_log_dir(),
    )
    compare_report = compare_cole_migration(store, source_root)
    manifest = create_cole_archive_snapshot(
        source_root=source_root,
        snapshot_root=snapshot_root,
        compare_report=compare_report,
    )
    print(json.dumps(manifest, indent=2))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a non-destructive Cole markdown archive snapshot.")
    parser.add_argument(
        "source_root",
        nargs="?",
        type=Path,
        help="Path to the Cole source root. Defaults to the sibling Cole directory.",
    )
    parser.add_argument(
        "--snapshot-root",
        type=Path,
        help="Optional snapshot destination. Defaults to <Cole>/archive/<date>-source-snapshot.",
    )
    return parser.parse_args()


def _default_cole_root() -> Path:
    return resolve_cole_source_root()


if __name__ == "__main__":
    main()

