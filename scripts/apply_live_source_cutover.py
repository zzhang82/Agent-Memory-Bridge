from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent_mem_bridge.profile_migration import compare_profile_migration_with_mode
from agent_mem_bridge.live_cutover import apply_live_source_cutover, build_default_cutover_root
from agent_mem_bridge.paths import resolve_bridge_db_path, resolve_bridge_log_dir, resolve_profile_source_root
from agent_mem_bridge.storage import MemoryStore


def main() -> None:
    args = _parse_args()
    source_root = args.source_root.resolve() if args.source_root else _default_profile_root()
    cutover_root = args.cutover_root.resolve() if args.cutover_root else build_default_cutover_root(source_root)

    store = MemoryStore(
        db_path=resolve_bridge_db_path(),
        log_dir=resolve_bridge_log_dir(),
    )
    preflight = compare_profile_migration_with_mode(store, source_root, mode="live")
    if not args.force:
        if (
            preflight["missing_count"] != 0
            or preflight["content_mismatch_count"] != 0
            or preflight["namespace_mismatch_count"] != 0
        ):
            raise SystemExit("Live compare preflight failed. Re-run with --force only if you understand the mismatch.")

    result = apply_live_source_cutover(
        source_root=source_root,
        cutover_root=cutover_root,
        preflight_report=preflight,
    )
    print(json.dumps(result, indent=2))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Move archive-first profile markdown out of the live source tree.")
    parser.add_argument(
        "source_root",
        nargs="?",
        type=Path,
        help="Path to the profile source root. Defaults to configured [profile].source_root.",
    )
    parser.add_argument(
        "--cutover-root",
        type=Path,
        help="Optional destination for retired live-source files. Defaults to <profile-root>/archive/<date>-live-cutover.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Allow cutover even if the live compare preflight reports mismatches.",
    )
    return parser.parse_args()


def _default_profile_root() -> Path:
    return resolve_profile_source_root()


if __name__ == "__main__":
    main()

