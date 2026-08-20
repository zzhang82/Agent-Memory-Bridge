from __future__ import annotations
# ruff: noqa: E402, I001

import sys
from pathlib import Path


def ensure_source_root() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    for path in (repository_root, repository_root / "src"):
        path_text = str(path)
        if path_text not in sys.path:
            sys.path.insert(0, path_text)
