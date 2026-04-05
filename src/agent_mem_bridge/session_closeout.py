from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .storage import MemoryStore
from .sync_notes import sync_markdown_file


SLUG_RE = re.compile(r"[^a-z0-9]+")


def closeout_session_from_json(
    store: MemoryStore,
    payload_path: Path,
    notes_root: Path,
) -> dict[str, Any]:
    payload = json.loads(Path(payload_path).read_text(encoding="utf-8"))
    return persist_session_payload(store, payload, notes_root)


def persist_session_payload(
    store: MemoryStore,
    payload: dict[str, Any],
    notes_root: Path,
) -> dict[str, Any]:
    note_path = write_session_note(payload, notes_root)
    sync_result = sync_markdown_file(store, note_path)
    return {"note_path": str(note_path), "sync_result": sync_result}


def write_session_note(payload: dict[str, Any], notes_root: Path) -> Path:
    normalized = _normalize_payload(payload)
    note_dir = Path(notes_root) / normalized["session_folder"]
    note_dir.mkdir(parents=True, exist_ok=True)
    note_path = note_dir / f"{normalized['slug']}.md"
    note_path.write_text(render_session_note(normalized), encoding="utf-8")
    return note_path


def render_session_note(payload: dict[str, Any]) -> str:
    tags = payload["tags"]
    bullet_lines = "\n".join(f"- {item}" for item in payload["bullets"])
    next_step = payload.get("next_step")
    next_step_block = f"\n## Next Step\n\n{next_step}\n" if next_step else ""
    return (
        "---\n"
        f"namespace: {payload['namespace']}\n"
        f"kind: {payload['kind']}\n"
        f"title: {payload['title']}\n"
        "tags:\n"
        + "".join(f"  - {tag}\n" for tag in tags)
        + f"actor: {payload['actor']}\n"
        + f"session_id: {payload['session_id']}\n"
        + f"correlation_id: {payload['correlation_id']}\n"
        + f"source_app: {payload['source_app']}\n"
        + "---\n\n"
        + f"{payload['summary']}\n\n"
        + "## Durable Points\n\n"
        + bullet_lines
        + next_step_block
    )


def _normalize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(UTC)
    session_folder = str(payload.get("session_folder") or now.strftime("%Y-%m-%d-session"))
    title = _expect_non_empty(payload, "title")
    summary = _expect_non_empty(payload, "summary")
    bullets = payload.get("bullets")
    if not isinstance(bullets, list) or not bullets:
        raise ValueError("bullets must be a non-empty list")
    clean_bullets = [_ensure_string(item, "bullets item") for item in bullets]

    tags = payload.get("tags", [])
    if not isinstance(tags, list):
        raise ValueError("tags must be a list")

    return {
        "namespace": _expect_non_empty(payload, "namespace"),
        "kind": payload.get("kind", "memory"),
        "title": title,
        "tags": [_ensure_string(tag, "tag") for tag in tags],
        "actor": _expect_non_empty(payload, "actor"),
        "session_id": _expect_non_empty(payload, "session_id"),
        "correlation_id": _expect_non_empty(payload, "correlation_id"),
        "source_app": _expect_non_empty(payload, "source_app"),
        "summary": summary,
        "bullets": clean_bullets,
        "next_step": payload.get("next_step"),
        "session_folder": session_folder,
        "slug": _slugify(str(payload.get("slug") or title)),
    }


def _expect_non_empty(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value.strip()


def _ensure_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _slugify(text: str) -> str:
    slug = SLUG_RE.sub("-", text.lower()).strip("-")
    return slug or "session-note"
