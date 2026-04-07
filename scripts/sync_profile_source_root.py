from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent_mem_bridge.paths import resolve_profile_source_root
from agent_mem_bridge.source_sync import (
    build_default_sync_snapshot_root,
    plan_source_sync,
    sync_source_root,
)


def main() -> None:
    args = _parse_args()
    source_root = args.source_root.resolve() if args.source_root else _default_local_profile_root()
    target_root = args.target_root.resolve() if args.target_root else resolve_profile_source_root()

    if args.plan_only:
        result = plan_source_sync(source_root, target_root, include_skills=args.include_skills)
    else:
        snapshot_root = args.snapshot_root.resolve() if args.snapshot_root else build_default_sync_snapshot_root(target_root)
        result = sync_source_root(
            source_root,
            target_root,
            snapshot_root=snapshot_root,
            include_skills=args.include_skills,
            dry_run=args.dry_run,
        )
    print(json.dumps(result, indent=2))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sync selected markdown files from a local profile source root into the configured shared profile vault."
    )
    parser.add_argument(
        "--source-root",
        type=Path,
        help="Local profile source root to sync from. Defaults to a repo-local sibling profile-source directory if present.",
    )
    parser.add_argument(
        "--target-root",
        type=Path,
        help="Shared target source root to sync into. Defaults to configured [profile].source_root.",
    )
    parser.add_argument(
        "--snapshot-root",
        type=Path,
        help="Optional snapshot root for remote-file backups. Defaults to <target>/archive/<date>-source-sync.",
    )
    parser.add_argument(
        "--include-skills",
        action="store_true",
        help="Also sync top-level skills markdown. By default skills are skipped for separate migration handling.",
    )
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="Only print the sync plan without making changes.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview the sync result and backup plan without copying files.",
    )
    return parser.parse_args()


def _default_local_profile_root() -> Path:
    return Path(__file__).resolve().parents[2] / "profile-source"


if __name__ == "__main__":
    main()
