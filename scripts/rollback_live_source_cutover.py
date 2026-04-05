from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agent_mem_bridge.rollback_cutover import (
    build_rollback_preflight,
    find_latest_live_cutover_manifest,
    rollback_live_source_cutover,
)
from agent_mem_bridge.paths import resolve_cole_source_root


def main() -> None:
    args = _parse_args()
    source_root = args.source_root.resolve() if args.source_root else _default_cole_root()
    manifest_path = args.cutover_manifest_path.resolve() if args.cutover_manifest_path else find_latest_live_cutover_manifest(source_root)
    if manifest_path is None:
        raise SystemExit("No live cutover manifest found.")

    preflight = build_rollback_preflight(manifest_path)
    if preflight["newer_live_conflict_count"] > 0:
        print(
            f"Warning: rollback would overwrite {preflight['newer_live_conflict_count']} newer live file(s).",
            file=sys.stderr,
        )
    result = rollback_live_source_cutover(manifest_path, force=args.force, dry_run=args.dry_run)
    result["preflight"] = preflight
    print(json.dumps(result, indent=2))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Restore archive-first Cole markdown back into the live source tree.")
    parser.add_argument(
        "source_root",
        nargs="?",
        type=Path,
        help="Path to the Cole source root. Defaults to the sibling Cole directory.",
    )
    parser.add_argument(
        "--cutover-manifest-path",
        type=Path,
        help="Optional path to a specific live cutover manifest. Defaults to the latest <date>-live-cutover manifest.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing live files by replacing them from the retired copy.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview which files would be restored or overwritten without changing the filesystem.",
    )
    return parser.parse_args()


def _default_cole_root() -> Path:
    return resolve_cole_source_root()


if __name__ == "__main__":
    main()

