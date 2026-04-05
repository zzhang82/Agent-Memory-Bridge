from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent_mem_bridge.archive_snapshot import build_default_live_manifest_path, write_live_source_manifest
from agent_mem_bridge.paths import resolve_cole_source_root


def main() -> None:
    args = _parse_args()
    source_root = args.source_root.resolve() if args.source_root else _default_cole_root()
    manifest_path = args.manifest_path.resolve() if args.manifest_path else build_default_live_manifest_path(source_root)
    result = write_live_source_manifest(source_root, manifest_path)
    print(json.dumps(result, indent=2))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Write the minimal live source manifest for Cole fallback files.")
    parser.add_argument(
        "source_root",
        nargs="?",
        type=Path,
        help="Path to the Cole source root. Defaults to the sibling Cole directory.",
    )
    parser.add_argument(
        "--manifest-path",
        type=Path,
        help="Optional path for the live source manifest. Defaults to <Cole>/live-source-manifest.json.",
    )
    return parser.parse_args()


def _default_cole_root() -> Path:
    return resolve_cole_source_root()


if __name__ == "__main__":
    main()

