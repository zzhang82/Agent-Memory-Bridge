from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent_mem_bridge.cole_migration import compare_cole_migration_with_mode, import_cole_memory, prune_stale_cole_imports
from agent_mem_bridge.paths import resolve_bridge_db_path, resolve_bridge_log_dir, resolve_cole_source_root
from agent_mem_bridge.storage import MemoryStore


def main() -> None:
    args = _parse_args()
    source_root = args.source_root.resolve() if args.source_root else _default_cole_root()

    store = MemoryStore(
        db_path=resolve_bridge_db_path(),
        log_dir=resolve_bridge_log_dir(),
    )

    if args.prune_stale:
        result = prune_stale_cole_imports(
            store,
            source_root,
            mode=args.compare_mode,
            live_manifest_path=args.live_manifest_path,
            snapshot_manifest_path=args.snapshot_manifest_path,
        )
    elif args.compare_only:
        result = compare_cole_migration_with_mode(
            store,
            source_root,
            mode=args.compare_mode,
            live_manifest_path=args.live_manifest_path,
            snapshot_manifest_path=args.snapshot_manifest_path,
        )
    else:
        result = import_cole_memory(store, source_root)
    rendered = json.dumps(result, indent=2)
    if args.report_path is not None:
        args.report_path.parent.mkdir(parents=True, exist_ok=True)
        args.report_path.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


def _parse_args() -> argparse.Namespace:
parser = argparse.ArgumentParser(description="Import or compare Cole markdown memory in Agent Memory Bridge.")
    parser.add_argument(
        "source_root",
        nargs="?",
        type=Path,
        help="Path to the Cole source root. Defaults to the sibling Cole directory.",
    )
    parser.add_argument(
        "--compare-only",
        action="store_true",
        help="Skip storing and only compare source markdown against imported bridge records.",
    )
    parser.add_argument(
        "--prune-stale",
        action="store_true",
        help="Delete stale imported Cole rows whose source paths no longer exist in the selected compare basis.",
    )
    parser.add_argument(
        "--compare-mode",
        choices=["full", "live", "snapshot-audit"],
        default="full",
        help="Compare the full source tree, only the live manifest, or the latest archived snapshot.",
    )
    parser.add_argument(
        "--live-manifest-path",
        type=Path,
        help="Optional path to the live source manifest. Defaults to <Cole>/live-source-manifest.json.",
    )
    parser.add_argument(
        "--snapshot-manifest-path",
        type=Path,
        help="Optional path to a specific snapshot manifest for snapshot-audit mode.",
    )
    parser.add_argument(
        "--report-path",
        type=Path,
        help="Optional path to write the JSON report to disk.",
    )
    return parser.parse_args()


def _default_cole_root() -> Path:
    return resolve_cole_source_root()


if __name__ == "__main__":
    main()

