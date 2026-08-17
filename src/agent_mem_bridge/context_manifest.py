"""Transient deterministic context manifests over governed task-memory inputs.

The Context Compiler is deliberately a derived-view layer. It receives an already
assembled task-memory report, exact Dynamic State read snapshots, and explicit
session-local items; it never queries storage, ranks records, or persists a
manifest. This preserves task-memory as the single governed retrieval path.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

_MANIFEST_VERSION = "context-manifest-v1"
_TASK_MEMORY_SECTIONS = (
    ("procedure_hits", "procedure"),
    ("concept_hits", "concept"),
    ("belief_hits", "belief"),
    ("domain_hits", "domain"),
    ("supporting_hits", "support"),
    ("corrective_items", "corrective"),
)
_SENSITIVE_LINE_PATTERN = re.compile(
    r"^\s*(?:api[ _-]?key|authorization|cookie|credential|password|secret|session[ _-]?token|token)\s*[:=]",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class ContextItemRef:
    """One sanitized, transient context item with an optional authority reference."""

    source: str
    section: str
    item_id: str | None
    title: str
    text: str
    char_count: int
    workspace_key: str | None = None
    state_key: str | None = None
    version: int | None = None
    value_hash: str | None = None
    database_epoch: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "source": self.source,
            "section": self.section,
            "id": self.item_id,
            "title": self.title,
            "text": self.text,
            "char_count": self.char_count,
            "fingerprint": self.fingerprint,
        }
        if self.state_key is not None:
            payload["state_ref"] = {
                "workspace_key": self.workspace_key,
                "state_key": self.state_key,
                "version": self.version,
                "value_hash": self.value_hash,
                "database_epoch": self.database_epoch,
            }
        return payload

    @property
    def fingerprint(self) -> str:
        return _fingerprint(
            {
                "source": self.source,
                "section": self.section,
                "id": self.item_id,
                "title": self.title,
                "text": self.text,
                "state_ref": {
                    "workspace_key": self.workspace_key,
                    "state_key": self.state_key,
                    "version": self.version,
                    "value_hash": self.value_hash,
                    "database_epoch": self.database_epoch,
                }
                if self.state_key is not None
                else None,
            }
        )


@dataclass(frozen=True, slots=True)
class ContextManifest:
    """A non-persistent, reproducible context plan and its budget accounting."""

    query: str
    budget_chars: int
    used_chars: int
    items: tuple[ContextItemRef, ...]
    omissions: tuple[dict[str, Any], ...]
    manifest_version: str = _MANIFEST_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifest_version": self.manifest_version,
            "query": self.query,
            "budget_chars": self.budget_chars,
            "used_chars": self.used_chars,
            "remaining_chars": self.remaining_chars,
            "items": [item.to_dict() for item in self.items],
            "omissions": [dict(omission) for omission in self.omissions],
            "fingerprint": self.fingerprint,
        }

    def serialize(self) -> str:
        """Return canonical JSON suitable for deterministic comparison or logging."""
        return _canonical_json(self.to_dict())

    @property
    def remaining_chars(self) -> int:
        return self.budget_chars - self.used_chars

    @property
    def fingerprint(self) -> str:
        return _fingerprint(
            {
                "manifest_version": self.manifest_version,
                "query": self.query,
                "budget_chars": self.budget_chars,
                "used_chars": self.used_chars,
                "items": [item.to_dict() for item in self.items],
                "omissions": [dict(omission) for omission in self.omissions],
            }
        )


def compile_context(
    *,
    task_memory: Mapping[str, Any],
    state_snapshots: Sequence[Mapping[str, Any]] = (),
    session_items: Sequence[Mapping[str, Any] | str] = (),
    budget_chars: int = 8_000,
) -> ContextManifest:
    """Compile one transient manifest from already-governed inputs.

    ``task_memory`` must be the output of :func:`assemble_task_memory`; this
    function intentionally does not accept a ``MemoryStore`` or retrieval query.
    Dynamic State values are not copied into context. Only their exact authority
    references are rendered: workspace/state keys, version, value hash, and
    database epoch.
    """
    if isinstance(budget_chars, bool) or not isinstance(budget_chars, int) or budget_chars < 0:
        raise ValueError("budget_chars must be a non-negative integer")

    query = _sanitize_text(str(task_memory.get("query") or ""))
    candidates: list[ContextItemRef] = []
    omissions: list[dict[str, Any]] = []

    for snapshot in sorted(
        state_snapshots,
        key=lambda item: (str(item.get("workspace_key") or ""), str(item.get("state_key") or "")),
    ):
        state_item, omission = _state_candidate(snapshot)
        if state_item is not None:
            candidates.append(state_item)
        elif omission is not None:
            omissions.append(omission)

    for position, raw_item in enumerate(session_items):
        session_item, omission = _session_candidate(raw_item, position)
        if session_item is not None:
            candidates.append(session_item)
        elif omission is not None:
            omissions.append(omission)

    seen_memory_ids: set[str] = set()
    for report_key, section in _TASK_MEMORY_SECTIONS:
        raw_items = task_memory.get(report_key) or []
        if not isinstance(raw_items, Sequence) or isinstance(raw_items, (str, bytes, bytearray)):
            omissions.append(
                _omission(
                    source="task_memory",
                    section=section,
                    item_id=None,
                    title=report_key,
                    reason="invalid_task_memory_section",
                )
            )
            continue
        for position, raw_item in enumerate(raw_items):
            item, omission = _task_memory_candidate(raw_item, section, position)
            if item is None:
                if omission is not None:
                    omissions.append(omission)
                continue
            if item.item_id and item.item_id in seen_memory_ids:
                omissions.append(
                    _omission(
                        source="task_memory",
                        section=section,
                        item_id=item.item_id,
                        title=item.title,
                        reason="duplicate_item",
                    )
                )
                continue
            if item.item_id:
                seen_memory_ids.add(item.item_id)
            candidates.append(item)

    suppressed_items = task_memory.get("suppressed_items") or []
    if isinstance(suppressed_items, Sequence) and not isinstance(suppressed_items, (str, bytes, bytearray)):
        for raw_item in suppressed_items:
            if isinstance(raw_item, Mapping):
                reason = _clean_label(raw_item.get("reason")) or "governed_suppressed"
                omissions.append(
                    _omission(
                        source="task_memory",
                        section=_clean_label(raw_item.get("section")) or "suppressed",
                        item_id=_clean_label(raw_item.get("id")) or None,
                        title=_sanitize_text(str(raw_item.get("title") or "")),
                        reason=f"governed_suppressed:{reason}",
                    )
                )

    selected: list[ContextItemRef] = []
    used_chars = 0
    for candidate in candidates:
        separator_chars = 2 if selected else 0
        required_chars = separator_chars + candidate.char_count
        if used_chars + required_chars > budget_chars:
            omissions.append(
                _omission(
                    source=candidate.source,
                    section=candidate.section,
                    item_id=candidate.item_id,
                    title=candidate.title,
                    reason="budget_exceeded",
                    required_chars=required_chars,
                )
            )
            continue
        selected.append(candidate)
        used_chars += required_chars

    return ContextManifest(
        query=query,
        budget_chars=budget_chars,
        used_chars=used_chars,
        items=tuple(selected),
        omissions=tuple(omissions),
    )


def render_context(manifest: ContextManifest) -> str:
    """Render a manifest without adding content beyond its accounted item text."""
    if not isinstance(manifest, ContextManifest):
        raise TypeError("manifest must be a ContextManifest")
    rendered = "\n\n".join(item.text for item in manifest.items)
    if len(rendered) != manifest.used_chars:
        raise ValueError("manifest budget accounting does not match rendered context")
    return rendered


def _state_candidate(snapshot: Mapping[str, Any]) -> tuple[ContextItemRef | None, dict[str, Any] | None]:
    workspace_key = _clean_label(snapshot.get("workspace_key"))
    state_key = _clean_label(snapshot.get("state_key"))
    title = _sanitize_text(f"{workspace_key}/{state_key}".strip("/"))
    if not snapshot.get("exists"):
        return None, _omission(
            source="dynamic_state",
            section="state",
            item_id=None,
            title=title,
            reason="state_absent",
        )
    version = snapshot.get("version")
    value_hash = _clean_label(snapshot.get("value_hash"))
    database_epoch = _clean_label(snapshot.get("database_epoch"))
    if not workspace_key or not state_key or isinstance(version, bool) or not isinstance(version, int) or version < 1:
        return None, _omission(
            source="dynamic_state",
            section="state",
            item_id=None,
            title=title,
            reason="invalid_state_reference",
        )
    if not value_hash or not database_epoch:
        return None, _omission(
            source="dynamic_state",
            section="state",
            item_id=None,
            title=title,
            reason="incomplete_state_reference",
        )
    text = (
        f"[Dynamic State] {workspace_key}/{state_key}\n"
        f"version: {version}\n"
        f"value_hash: {value_hash}\n"
        f"database_epoch: {database_epoch}"
    )
    return (
        ContextItemRef(
            source="dynamic_state",
            section="state",
            item_id=None,
            title=title,
            text=text,
            char_count=len(text),
            workspace_key=workspace_key,
            state_key=state_key,
            version=version,
            value_hash=value_hash,
            database_epoch=database_epoch,
        ),
        None,
    )


def _session_candidate(
    raw_item: Mapping[str, Any] | str,
    position: int,
) -> tuple[ContextItemRef | None, dict[str, Any] | None]:
    if isinstance(raw_item, str):
        item_id = None
        title = f"session-{position + 1}"
        content = raw_item
    elif isinstance(raw_item, Mapping):
        item_id = _clean_label(raw_item.get("id")) or None
        title = _sanitize_text(str(raw_item.get("title") or raw_item.get("label") or f"session-{position + 1}"))
        content = str(raw_item.get("content") or raw_item.get("text") or "")
    else:
        return None, _omission(
            source="session",
            section="session",
            item_id=None,
            title=f"session-{position + 1}",
            reason="invalid_session_item",
        )
    sanitized = _sanitize_text(content)
    if not sanitized:
        return None, _omission(
            source="session",
            section="session",
            item_id=item_id,
            title=title,
            reason="empty_after_sanitization",
        )
    text = f"[Session] {title}\n{sanitized}"
    return (
        ContextItemRef(
            source="session",
            section="session",
            item_id=item_id,
            title=title,
            text=text,
            char_count=len(text),
        ),
        None,
    )


def _task_memory_candidate(
    raw_item: object,
    section: str,
    position: int,
) -> tuple[ContextItemRef | None, dict[str, Any] | None]:
    if not isinstance(raw_item, Mapping):
        return None, _omission(
            source="task_memory",
            section=section,
            item_id=None,
            title=f"{section}-{position + 1}",
            reason="invalid_task_memory_item",
        )
    item_id = _clean_label(raw_item.get("id")) or None
    title = _sanitize_text(str(raw_item.get("title") or item_id or f"{section}-{position + 1}"))
    content = _task_memory_text(raw_item, section)
    if not content:
        return None, _omission(
            source="task_memory",
            section=section,
            item_id=item_id,
            title=title,
            reason="empty_after_sanitization",
        )
    text = f"[{_section_label(section)}] {title}\n{content}"
    return (
        ContextItemRef(
            source="task_memory",
            section=section,
            item_id=item_id,
            title=title,
            text=text,
            char_count=len(text),
        ),
        None,
    )


def _task_memory_text(item: Mapping[str, Any], section: str) -> str:
    if section == "procedure" and isinstance(item.get("procedure"), Mapping):
        procedure = item["procedure"]
        lines: list[str] = []
        for label, key in (
            ("goal", "goal"),
            ("when_to_use", "when_to_use"),
            ("steps", "steps"),
            ("failure_mode", "failure_mode"),
            ("rollback_path", "rollback_path"),
        ):
            value = procedure.get(key)
            if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
                value = " | ".join(str(entry) for entry in value if str(entry).strip())
            cleaned = _sanitize_text(str(value or ""))
            if cleaned:
                lines.append(f"{label}: {cleaned}")
        if lines:
            return "\n".join(lines)
    return _sanitize_text(str(item.get("content") or ""))


def _sanitize_text(value: str) -> str:
    normalized = str(value).replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    lines = []
    for raw_line in normalized.split("\n"):
        cleaned = " ".join(raw_line.split())
        if not cleaned:
            continue
        if _SENSITIVE_LINE_PATTERN.search(cleaned):
            lines.append("[redacted sensitive line]")
        else:
            lines.append(cleaned)
    return "\n".join(lines).strip()


def _clean_label(value: object) -> str:
    return " ".join(str(value or "").replace("\x00", "").split())


def _section_label(section: str) -> str:
    return {
        "procedure": "Procedure",
        "concept": "Concept",
        "belief": "Belief",
        "domain": "Domain",
        "support": "Supporting Record",
        "corrective": "Corrective Evidence",
    }.get(section, section.replace("_", " ").title())


def _omission(
    *,
    source: str,
    section: str,
    item_id: str | None,
    title: str,
    reason: str,
    required_chars: int | None = None,
) -> dict[str, Any]:
    omission: dict[str, Any] = {
        "source": source,
        "section": section,
        "id": item_id,
        "title": title,
        "reason": reason,
    }
    if required_chars is not None:
        omission["required_chars"] = required_chars
    return omission


def _canonical_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _fingerprint(payload: object) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


__all__ = ["ContextItemRef", "ContextManifest", "compile_context", "render_context"]
