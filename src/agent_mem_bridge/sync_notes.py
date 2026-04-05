from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .storage import MemoryStore


def sync_markdown_path(store: MemoryStore, path: Path) -> list[dict[str, Any]]:
    target = Path(path)
    if target.is_file():
        return [sync_markdown_file(store, target)]

    results: list[dict[str, Any]] = []
    for note_path in sorted(target.rglob("*.md")):
        results.append(sync_markdown_file(store, note_path))
    return results


def sync_markdown_file(store: MemoryStore, path: Path) -> dict[str, Any]:
    frontmatter, content = parse_obsidian_note(path)
    tags = frontmatter.get("tags", [])
    if not isinstance(tags, list):
        raise ValueError(f"tags must be a list in {path}")

    return store.store(
        namespace=_expect_string(frontmatter, "namespace", path),
        content=content.strip(),
        kind=frontmatter.get("kind", "memory"),
        tags=tags,
        session_id=_optional_string(frontmatter, "session_id"),
        actor=_optional_string(frontmatter, "actor"),
        title=_optional_string(frontmatter, "title"),
        correlation_id=_optional_string(frontmatter, "correlation_id"),
        source_app=_optional_string(frontmatter, "source_app"),
    )


def parse_obsidian_note(path: Path) -> tuple[dict[str, Any], str]:
    raw = Path(path).read_text(encoding="utf-8")
    lines = raw.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError(f"missing YAML frontmatter in {path}")

    frontmatter_lines: list[str] = []
    body_start = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            body_start = index + 1
            break
        frontmatter_lines.append(lines[index])

    if body_start is None:
        raise ValueError(f"frontmatter not closed in {path}")

    return parse_simple_yaml(frontmatter_lines), "\n".join(lines[body_start:])


def parse_simple_yaml(lines: list[str]) -> dict[str, Any]:
    data: dict[str, Any] = {}
    current_list_key: str | None = None
    for raw_line in lines:
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            continue

        if stripped.startswith("- "):
            if current_list_key is None:
                raise ValueError(f"list item without key: {raw_line}")
            data.setdefault(current_list_key, []).append(stripped[2:].strip())
            continue

        current_list_key = None
        if ":" not in stripped:
            raise ValueError(f"unsupported frontmatter line: {raw_line}")

        key, value = stripped.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not value:
            data[key] = []
            current_list_key = key
        else:
            data[key] = value
    return data


def _expect_string(data: dict[str, Any], key: str, path: Path) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} is required in {path}")
    return value


def _optional_string(data: dict[str, Any], key: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    stripped = value.strip()
    return stripped or None


def summarize_sync_results(results: list[dict[str, Any]]) -> str:
    return json.dumps(
        {
            "count": len(results),
            "stored": sum(1 for item in results if item.get("stored")),
            "duplicates": sum(1 for item in results if not item.get("stored")),
            "items": results,
        },
        indent=2,
    )
