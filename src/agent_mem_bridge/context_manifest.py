"""Transient deterministic context manifests over governed task-memory inputs.

The Context Compiler is a derived-view boundary. It receives an already
relation-aware task-memory report, exact Dynamic State read snapshots, explicit
session-local items, and bounded repository facts supplied as a distinct derived
input. It never queries storage, ranks records, or persists a manifest. Render
text exists only in-process; serialized manifests contain source references,
digests, governance metadata, and selection facts.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .repository import content_hash_for_content
from .schema import exact_content_hash

COMPILER_VERSION = "context-compiler-v1"
SELECTION_POLICY_VERSION = "session-1-governed-token-budget-v1"
_TASK_MEMORY_SECTIONS = (
    ("procedure_hits", "procedure"),
    ("decision_hits", "decision"),
    ("constraint_hits", "constraint"),
    ("concept_hits", "concept"),
    ("belief_hits", "belief"),
    ("domain_hits", "domain"),
    ("supporting_hits", "support"),
    ("corrective_items", "corrective"),
)
_REQUIRED_REPORT_KEYS = tuple(key for key, _ in _TASK_MEMORY_SECTIONS) + ("suppressed_items",)
_REQUIRED_REPORT_TEXT_FIELDS = ("query", "project_namespace", "global_namespace", "summary")
_SENSITIVE_FIELD_PATTERN = re.compile(
    r"(?:api[ _-]?key|authorization|cookie|credential|password|secret|session[ _-]?token|token)",
    re.IGNORECASE,
)
_TOKEN_PATTERN = re.compile(r"[\u3400-\u9fff]|[A-Za-z0-9_]+|[^\s]")
_PROVENANCE_KEYS = (
    "actor",
    "source_app",
    "source_client",
    "source_model",
    "client_session_id",
    "client_workspace",
    "client_transport",
    "session_id",
    "correlation_id",
)


@dataclass(frozen=True, slots=True)
class ContextItemRef:
    """One selected transient item with render text kept out of serialization."""

    source: str
    section: str
    item_id: str | None
    title_sha256: str
    render_text: str
    token_cost: int
    content_hash: str | None = None
    exact_content_hash: str | None = None
    selected_as: str | None = None
    selection_score: float | None = None
    selection_reasons: tuple[str, ...] = ()
    provenance: tuple[tuple[str, str], ...] = ()
    workspace_key: str | None = None
    state_key: str | None = None
    version: int | None = None
    value_hash: str | None = None
    database_epoch: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return auditable metadata only; never include title or render text."""
        payload: dict[str, Any] = {
            "source": self.source,
            "section": self.section,
            "id": self.item_id,
            "title_sha256": self.title_sha256,
            "token_cost": self.token_cost,
            "content_hash": self.content_hash,
            "exact_content_hash": self.exact_content_hash,
            "selection": {
                "selected_as": self.selected_as,
                "score": self.selection_score,
                "reasons": list(self.selection_reasons),
            },
            "provenance": dict(self.provenance),
        }
        if self.state_key is not None:
            payload["state_ref"] = {
                "workspace_key": self.workspace_key,
                "state_key": self.state_key,
                "version": self.version,
                "value_hash": self.value_hash,
                "database_epoch": self.database_epoch,
            }
        payload["fingerprint"] = self.fingerprint
        return payload

    @property
    def fingerprint(self) -> str:
        return _sha256(_canonical_json(self._fingerprint_payload()))

    def _fingerprint_payload(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "section": self.section,
            "id": self.item_id,
            "title_sha256": self.title_sha256,
            "token_cost": self.token_cost,
            "content_hash": self.content_hash,
            "exact_content_hash": self.exact_content_hash,
            "selection": {
                "selected_as": self.selected_as,
                "score": self.selection_score,
                "reasons": list(self.selection_reasons),
            },
            "provenance": dict(self.provenance),
            "state_ref": {
                "workspace_key": self.workspace_key,
                "state_key": self.state_key,
                "version": self.version,
                "value_hash": self.value_hash,
                "database_epoch": self.database_epoch,
            }
            if self.state_key is not None
            else None,
            "rendered_text_sha256": _sha256(self.render_text),
        }


@dataclass(frozen=True, slots=True)
class ContextManifest:
    """A non-persistent, reproducible context plan with metadata-only serialization."""

    task_identifier_sha256: str
    input_fingerprint: str
    budget_tokens: int
    used_tokens: int
    items: tuple[ContextItemRef, ...]
    omissions: tuple[dict[str, Any], ...]
    compiler_version: str = COMPILER_VERSION
    selection_policy_version: str = SELECTION_POLICY_VERSION

    def to_dict(self) -> dict[str, Any]:
        """Return auditable selection metadata without a prompt/context archive."""
        payload = self._metadata_payload()
        payload["rendered_context_sha256"] = self.rendered_context_sha256
        payload["fingerprint"] = self.fingerprint
        return payload

    def serialize(self) -> str:
        """Return canonical metadata JSON; render text stays transient in-process."""
        return _canonical_json(self.to_dict())

    @property
    def remaining_tokens(self) -> int:
        return self.budget_tokens - self.used_tokens

    @property
    def rendered_context_sha256(self) -> str:
        return _sha256(render_context(self))

    @property
    def fingerprint(self) -> str:
        return _sha256(_canonical_json(self._metadata_payload()))

    def _metadata_payload(self) -> dict[str, Any]:
        return {
            "compiler_version": self.compiler_version,
            "selection_policy_version": self.selection_policy_version,
            "task_identifier_sha256": self.task_identifier_sha256,
            "input_fingerprint": self.input_fingerprint,
            "budget_tokens": self.budget_tokens,
            "used_tokens": self.used_tokens,
            "remaining_tokens": self.remaining_tokens,
            "items": [item.to_dict() for item in self.items],
            "omissions": [dict(omission) for omission in self.omissions],
        }


def compile_context(
    *,
    task_memory: Mapping[str, Any],
    state_snapshots: Sequence[Mapping[str, Any]] = (),
    session_items: Sequence[Mapping[str, Any] | str] = (),
    repository_items: Sequence[Mapping[str, Any]] = (),
    budget_tokens: int = 2_048,
) -> ContextManifest:
    """Compile one transient context from relation-aware governed inputs.

    The task-memory report must be produced by the default relation-aware
    assembly. State snapshots are rendered as sanitized authoritative values;
    their serialized references retain only exact state identity/version/digest
    metadata. Valid Dynamic State items are required inputs: compilation fails
    closed if they cannot fit within ``budget_tokens``.
    """
    _validate_task_memory(task_memory)
    if isinstance(budget_tokens, bool) or not isinstance(budget_tokens, int) or budget_tokens < 0:
        raise ValueError("budget_tokens must be a non-negative integer")

    validated_state_snapshots = _validate_state_snapshots(state_snapshots)
    omissions: list[dict[str, Any]] = []
    state_items: list[ContextItemRef] = []
    for snapshot in sorted(
        validated_state_snapshots,
        key=lambda item: (str(item.get("workspace_key") or ""), str(item.get("state_key") or "")),
    ):
        state_item, omission = _state_candidate(snapshot)
        if state_item is not None:
            state_items.append(state_item)
        elif omission is not None:
            omissions.append(omission)

    required_state_tokens = sum(item.token_cost for item in state_items)
    if required_state_tokens > budget_tokens:
        raise ValueError("budget_tokens cannot fit required Dynamic State context")

    candidates: list[ContextItemRef] = []
    for position, repository_raw_item in enumerate(repository_items):
        repository_item, omission = _repository_candidate(repository_raw_item, position)
        if repository_item is not None:
            candidates.append(repository_item)
        elif omission is not None:
            omissions.append(omission)
    for position, session_raw_item in enumerate(session_items):
        session_item, omission = _session_candidate(session_raw_item, position)
        if session_item is not None:
            candidates.append(session_item)
        elif omission is not None:
            omissions.append(omission)

    seen_memory_ids: set[str] = set()
    for report_key, section in _TASK_MEMORY_SECTIONS:
        for position, raw_item in enumerate(task_memory[report_key]):
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
                        title_sha256=item.title_sha256,
                        reason="duplicate_item",
                    )
                )
                continue
            if item.item_id:
                seen_memory_ids.add(item.item_id)
            candidates.append(item)

    for raw_item in task_memory["suppressed_items"]:
        if isinstance(raw_item, Mapping):
            reason = _clean_label(raw_item.get("reason")) or "governed_suppressed"
            omissions.append(
                _omission(
                    source="task_memory",
                    section=_clean_label(raw_item.get("section")) or "suppressed",
                    item_id=_clean_label(raw_item.get("id")) or None,
                    title_sha256=_sha256(_sanitize_text(str(raw_item.get("title") or ""))),
                    reason=f"governed_suppressed:{reason}",
                )
            )

    selected = list(state_items)
    used_tokens = required_state_tokens
    for candidate in candidates:
        if used_tokens + candidate.token_cost > budget_tokens:
            omissions.append(
                _omission(
                    source=candidate.source,
                    section=candidate.section,
                    item_id=candidate.item_id,
                    title_sha256=candidate.title_sha256,
                    reason="budget_exceeded",
                    required_tokens=candidate.token_cost,
                )
            )
            continue
        selected.append(candidate)
        used_tokens += candidate.token_cost

    task_identifier_sha256 = _sha256(str(task_memory["query"]))
    input_fingerprint = _input_fingerprint(task_memory, validated_state_snapshots, session_items, repository_items)
    return ContextManifest(
        task_identifier_sha256=task_identifier_sha256,
        input_fingerprint=input_fingerprint,
        budget_tokens=budget_tokens,
        used_tokens=used_tokens,
        items=tuple(selected),
        omissions=tuple(omissions),
    )


def render_context(manifest: ContextManifest) -> str:
    """Render transient model context from selected in-memory item bodies."""
    if not isinstance(manifest, ContextManifest):
        raise TypeError("manifest must be a ContextManifest")
    rendered = "\n\n".join(item.render_text for item in manifest.items)
    if sum(item.token_cost for item in manifest.items) != manifest.used_tokens:
        raise ValueError("manifest token accounting does not match selected context")
    if manifest.used_tokens > manifest.budget_tokens:
        raise ValueError("manifest exceeds its token budget")
    return rendered


def _validate_task_memory(task_memory: Mapping[str, Any]) -> None:
    if not isinstance(task_memory, Mapping):
        raise TypeError("task_memory must be a relation-aware task-memory report")
    if task_memory.get("assembly_mode") != "relation-aware":
        raise ValueError("task_memory must use the relation-aware governed assembly")
    for key in _REQUIRED_REPORT_TEXT_FIELDS:
        if not isinstance(task_memory.get(key), str):
            raise ValueError(f"task_memory report field {key!r} must be a string")
    for key in _REQUIRED_REPORT_KEYS:
        value = task_memory.get(key)
        if not isinstance(value, list):
            raise ValueError(f"task_memory report field {key!r} must be a list")


def _validate_state_snapshots(state_snapshots: Sequence[Mapping[str, Any]]) -> tuple[Mapping[str, Any], ...]:
    validated: list[Mapping[str, Any]] = []
    identities: set[tuple[str, str]] = set()
    database_epochs: set[str] = set()
    for snapshot in state_snapshots:
        if not isinstance(snapshot, Mapping):
            raise TypeError("state_snapshots must contain Dynamic State read mappings")
        workspace_key = _clean_label(snapshot.get("workspace_key"))
        state_key = _clean_label(snapshot.get("state_key"))
        database_epoch = _clean_label(snapshot.get("database_epoch"))
        exists = snapshot.get("exists")
        if not workspace_key or not state_key:
            raise ValueError("Dynamic State snapshot requires workspace_key and state_key")
        if not isinstance(exists, bool):
            raise ValueError("Dynamic State snapshot requires boolean exists")
        if not database_epoch:
            raise ValueError("Dynamic State snapshot requires database_epoch")
        identity = (workspace_key, state_key)
        if identity in identities:
            raise ValueError("duplicate Dynamic State snapshot identity")
        identities.add(identity)
        database_epochs.add(database_epoch)
        if exists:
            version = snapshot.get("version")
            value = snapshot.get("value")
            value_hash = _clean_label(snapshot.get("value_hash"))
            if isinstance(version, bool) or not isinstance(version, int) or version < 1:
                raise ValueError("existing Dynamic State snapshot requires positive integer version")
            if value is None or not value_hash:
                raise ValueError("existing Dynamic State snapshot requires value and value_hash")
            if value_hash != _state_value_hash(value):
                raise ValueError("Dynamic State snapshot value_hash mismatch")
        validated.append(snapshot)
    if len(database_epochs) > 1:
        raise ValueError("Dynamic State snapshots must share one database_epoch")
    return tuple(validated)


def _state_candidate(snapshot: Mapping[str, Any]) -> tuple[ContextItemRef | None, dict[str, Any] | None]:
    workspace_key = _clean_label(snapshot.get("workspace_key"))
    state_key = _clean_label(snapshot.get("state_key"))
    title_sha256 = _sha256(f"{workspace_key}/{state_key}".strip("/"))
    if not snapshot["exists"]:
        return None, _omission(
            source="dynamic_state",
            section="state",
            item_id=None,
            title_sha256=title_sha256,
            reason="state_absent",
        )
    version = int(snapshot["version"])
    value_hash = _clean_label(snapshot["value_hash"])
    database_epoch = _clean_label(snapshot["database_epoch"])
    sanitized_value = _sanitize_state_value(snapshot["value"])
    text = (
        f"[Authoritative Dynamic State] {workspace_key}/{state_key}\n"
        f"version: {version}\n"
        f"value: {_canonical_json(sanitized_value)}\n"
        f"value_hash: {value_hash}\n"
        f"database_epoch: {database_epoch}"
    )
    return (
        ContextItemRef(
            source="dynamic_state",
            section="state",
            item_id=None,
            title_sha256=title_sha256,
            render_text=text,
            token_cost=_estimate_tokens(text),
            workspace_key=workspace_key,
            state_key=state_key,
            version=version,
            value_hash=value_hash,
            database_epoch=database_epoch,
        ),
        None,
    )


def _repository_candidate(
    raw_item: Mapping[str, Any],
    position: int,
) -> tuple[ContextItemRef | None, dict[str, Any] | None]:
    title = _sanitize_text(str(raw_item.get("key") or raw_item.get("fact_kind") or f"repository-fact-{position + 1}"))
    source = _clean_label(raw_item.get("source")) or "repository"
    commit = _clean_label(raw_item.get("commit")) or None
    value = raw_item.get("value")
    rendered_value = _canonical_json(value)
    text = f"[Repository WHAT] {title}\nsource: {source}\ncommit: {commit or 'unavailable'}\nvalue: {rendered_value}"
    sanitized = _sanitize_text(text)
    if not sanitized:
        return None, _omission(
            source="derived_repository",
            section="repository",
            item_id=None,
            title_sha256=_sha256(title),
            reason="empty_after_sanitization",
        )
    return (
        ContextItemRef(
            source="derived_repository",
            section="repository",
            item_id=_clean_label(raw_item.get("id")) or None,
            title_sha256=_sha256(title),
            render_text=sanitized,
            token_cost=_resolve_token_cost(raw_item.get("token_cost"), sanitized),
            content_hash=_sha256(_canonical_json(value)),
            exact_content_hash=_sha256(_canonical_json(value)),
            provenance=(
                ("authority", "derived_repository"),
                ("source", source),
                *((("commit", commit),) if commit else ()),
            ),
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
        raw_cost: object | None = None
        provenance: tuple[tuple[str, str], ...] = ()
    elif isinstance(raw_item, Mapping):
        item_id = _clean_label(raw_item.get("id")) or None
        title = _sanitize_text(str(raw_item.get("title") or raw_item.get("label") or f"session-{position + 1}"))
        content = str(raw_item.get("content") or raw_item.get("text") or "")
        raw_cost = raw_item.get("token_cost")
        provenance = _provenance(raw_item)
    else:
        return None, _omission(
            source="session",
            section="session",
            item_id=None,
            title_sha256=_sha256(f"session-{position + 1}"),
            reason="invalid_session_item",
        )
    sanitized = _sanitize_text(content)
    title_sha256 = _sha256(title)
    if not sanitized:
        return None, _omission(
            source="session",
            section="session",
            item_id=item_id,
            title_sha256=title_sha256,
            reason="empty_after_sanitization",
        )
    text = f"[Session] {title}\n{sanitized}"
    return (
        ContextItemRef(
            source="session",
            section="session",
            item_id=item_id,
            title_sha256=title_sha256,
            render_text=text,
            token_cost=_resolve_token_cost(raw_cost, text),
            content_hash=_content_digests(content)[0],
            exact_content_hash=_content_digests(content)[1],
            provenance=provenance,
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
            title_sha256=_sha256(f"{section}-{position + 1}"),
            reason="invalid_task_memory_item",
        )
    item_id = _clean_label(raw_item.get("id")) or None
    title = _sanitize_text(str(raw_item.get("title") or item_id or f"{section}-{position + 1}"))
    title_sha256 = _sha256(title)
    content = _task_memory_text(raw_item, section)
    if not content:
        return None, _omission(
            source="task_memory",
            section=section,
            item_id=item_id,
            title_sha256=title_sha256,
            reason="empty_after_sanitization",
        )
    text = f"[{_section_label(section)}] {title}\n{content}"
    task_metadata = raw_item.get("task_memory")
    selection = task_metadata if isinstance(task_metadata, Mapping) else {}
    raw_content = str(raw_item.get("content") or "")
    content_hash, exact_hash = _content_digests(raw_content)
    return (
        ContextItemRef(
            source="task_memory",
            section=section,
            item_id=item_id,
            title_sha256=title_sha256,
            render_text=text,
            token_cost=_resolve_token_cost(raw_item.get("token_cost"), text),
            content_hash=_clean_label(raw_item.get("content_hash")) or content_hash,
            exact_content_hash=_clean_label(raw_item.get("exact_content_hash")) or exact_hash,
            selected_as=_clean_label(selection.get("selected_as")) or None,
            selection_score=_finite_score(selection.get("score")),
            selection_reasons=tuple(
                _clean_label(reason) for reason in selection.get("reasons", []) if _clean_label(reason)
            )
            if isinstance(selection.get("reasons"), list)
            else (),
            provenance=_provenance(raw_item),
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
            ("when_not_to_use", "when_not_to_use"),
            ("prerequisites", "prerequisites"),
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


def _sanitize_state_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            _clean_label(key): "[redacted sensitive value]"
            if _SENSITIVE_FIELD_PATTERN.search(_clean_label(key))
            else _sanitize_state_value(raw_value)
            for key, raw_value in sorted(value.items(), key=lambda item: _clean_label(item[0]))
            if _clean_label(key)
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_sanitize_state_value(item) for item in value]
    if isinstance(value, str):
        return _sanitize_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _sanitize_text(str(value))


def _sanitize_text(value: str) -> str:
    normalized = _normalize_content(value).replace("\x00", "")
    lines = []
    for raw_line in normalized.split("\n"):
        cleaned = " ".join(raw_line.split())
        if not cleaned:
            continue
        field_name = cleaned.split(":", 1)[0].split("=", 1)[0]
        if _SENSITIVE_FIELD_PATTERN.fullmatch(field_name.strip()):
            lines.append("[redacted sensitive line]")
        else:
            lines.append(cleaned)
    return "\n".join(lines).strip()


def _provenance(item: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    return tuple((key, _clean_label(item.get(key))) for key in _PROVENANCE_KEYS if _clean_label(item.get(key)))


def _resolve_token_cost(raw_cost: object | None, text: str) -> int:
    if raw_cost is None:
        return _estimate_tokens(text)
    if isinstance(raw_cost, bool) or not isinstance(raw_cost, int) or raw_cost < 0:
        raise ValueError("token_cost must be a non-negative integer when supplied")
    return raw_cost


def _estimate_tokens(text: str) -> int:
    """Use the frozen deterministic approximation when callers provide no cost."""
    return len(_TOKEN_PATTERN.findall(text))


def _finite_score(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value != value or value in (float("inf"), float("-inf")):
        return None
    return float(value)


def _input_fingerprint(
    task_memory: Mapping[str, Any],
    state_snapshots: Sequence[Mapping[str, Any]],
    session_items: Sequence[Mapping[str, Any] | str],
    repository_items: Sequence[Mapping[str, Any]],
) -> str:
    payload = {
        "assembly_mode": task_memory["assembly_mode"],
        "task_identifier_sha256": _sha256(str(task_memory["query"])),
        "task_items": [
            _task_memory_fingerprint_payload(item, section)
            for report_key, section in _TASK_MEMORY_SECTIONS
            for item in task_memory[report_key]
        ],
        "suppressed": [
            {
                "id": _clean_label(item.get("id")),
                "reason": _clean_label(item.get("reason")),
                "by_id": _clean_label(item.get("by_id")),
            }
            for item in task_memory["suppressed_items"]
            if isinstance(item, Mapping)
        ],
        "state_refs": [_state_fingerprint_payload(item) for item in state_snapshots],
        "session_refs": [_session_fingerprint_payload(item, index) for index, item in enumerate(session_items)],
        "repository_refs": [
            _repository_fingerprint_payload(item, index) for index, item in enumerate(repository_items)
        ],
    }
    return _sha256(_canonical_json(payload))


def _repository_fingerprint_payload(item: Mapping[str, Any], index: int) -> dict[str, Any]:
    value = item.get("value")
    return {
        "position": index,
        "id": _clean_label(item.get("id")),
        "fact_kind": _clean_label(item.get("fact_kind")) or _clean_label(item.get("key")),
        "source": _clean_label(item.get("source")),
        "commit": _clean_label(item.get("commit")),
        "value_sha256": _sha256(_canonical_json(value)),
        "token_cost": item.get("token_cost"),
    }


def _task_memory_fingerprint_payload(item: object, section: str) -> dict[str, Any]:
    if not isinstance(item, Mapping):
        return {"section": section, "invalid": True}
    raw_content = str(item.get("content") or "")
    content_hash, exact_hash = _content_digests(raw_content)
    task_metadata = item.get("task_memory")
    return {
        "section": section,
        "id": _clean_label(item.get("id")),
        "title_sha256": _sha256(_sanitize_text(str(item.get("title") or item.get("id") or ""))),
        "content_hash": _clean_label(item.get("content_hash")) or content_hash,
        "exact_content_hash": _clean_label(item.get("exact_content_hash")) or exact_hash,
        "selection": task_metadata if isinstance(task_metadata, Mapping) else {},
        "provenance": dict(_provenance(item)),
        "token_cost": item.get("token_cost"),
    }


def _state_fingerprint_payload(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "workspace_key": _clean_label(item.get("workspace_key")),
        "state_key": _clean_label(item.get("state_key")),
        "version": item.get("version"),
        "value_hash": _clean_label(item.get("value_hash")),
        "database_epoch": _clean_label(item.get("database_epoch")),
        "exists": bool(item.get("exists")),
    }


def _session_fingerprint_payload(item: Mapping[str, Any] | str, index: int) -> dict[str, Any]:
    if isinstance(item, str):
        content_hash, exact_hash = _content_digests(item)
        return {
            "position": index,
            "title_sha256": _sha256(f"session-{index + 1}"),
            "content_hash": content_hash,
            "exact_content_hash": exact_hash,
        }
    if isinstance(item, Mapping):
        content = str(item.get("content") or item.get("text") or "")
        content_hash, exact_hash = _content_digests(content)
        title = _sanitize_text(str(item.get("title") or item.get("label") or f"session-{index + 1}"))
        return {
            "position": index,
            "id": _clean_label(item.get("id")),
            "title_sha256": _sha256(title),
            "content_hash": content_hash,
            "exact_content_hash": exact_hash,
            "token_cost": item.get("token_cost"),
            "provenance": dict(_provenance(item)),
        }
    return {"position": index, "invalid": True}


def _omission(
    *,
    source: str,
    section: str,
    item_id: str | None,
    title_sha256: str,
    reason: str,
    required_tokens: int | None = None,
) -> dict[str, Any]:
    omission: dict[str, Any] = {
        "source": source,
        "section": section,
        "id": item_id,
        "title_sha256": title_sha256,
        "reason": reason,
    }
    if required_tokens is not None:
        omission["required_tokens"] = required_tokens
    return omission


def _content_digests(value: str) -> tuple[str, str]:
    content = str(value)
    return content_hash_for_content(content), exact_content_hash(content)


def _state_value_hash(value: object) -> str:
    return _sha256(_canonical_json(value))


def _normalize_content(value: str) -> str:
    return str(value).replace("\r\n", "\n").replace("\r", "\n")


def _clean_label(value: object) -> str:
    return " ".join(str(value or "").replace("\x00", "").split())


def _section_label(section: str) -> str:
    return {
        "procedure": "Procedure",
        "decision": "Project Decision",
        "constraint": "Project Constraint",
        "concept": "Concept",
        "belief": "Belief",
        "domain": "Domain",
        "support": "Supporting Record",
        "corrective": "Corrective Evidence",
    }.get(section, section.replace("_", " ").title())


def _canonical_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True, allow_nan=False)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


__all__ = ["ContextItemRef", "ContextManifest", "compile_context", "render_context"]
