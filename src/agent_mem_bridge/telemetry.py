from __future__ import annotations

import hashlib
import json
from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any, Mapping
from uuid import uuid4

from .paths import resolve_telemetry_log_dir, resolve_telemetry_mode, resolve_telemetry_service_name

UNSAFE_ATTRIBUTE_EXACT_KEYS = (
    "body",
    "content",
    "prompt",
    "query",
    "response",
    "result",
    "text",
    "title",
)
MAX_STRING_LENGTH = 160
MAX_COLLECTION_ITEMS = 12

_current_trace_id: ContextVar[str | None] = ContextVar("agent_mem_bridge_trace_id", default=None)
_current_span_id: ContextVar[str | None] = ContextVar("agent_mem_bridge_span_id", default=None)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def hash_label(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    return hashlib.sha256(cleaned.encode("utf-8")).hexdigest()[:12]


def _is_safe_attribute_key(key: str) -> bool:
    lowered = key.strip().lower().replace("-", "_")
    if not lowered:
        return False
    if lowered in UNSAFE_ATTRIBUTE_EXACT_KEYS:
        return False
    return not lowered.endswith(("_body", "_content", "_prompt", "_query", "_response", "_text", "_title"))


def _sanitize_attribute_value(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, str):
        return value[:MAX_STRING_LENGTH]
    if isinstance(value, Mapping):
        cleaned: dict[str, Any] = {}
        for key, nested in value.items():
            key_str = str(key)
            if not _is_safe_attribute_key(key_str):
                continue
            cleaned_value = _sanitize_attribute_value(nested)
            if cleaned_value is not None:
                cleaned[key_str] = cleaned_value
        return cleaned
    if isinstance(value, list | tuple | set):
        cleaned_items = []
        for item in list(value)[:MAX_COLLECTION_ITEMS]:
            cleaned_item = _sanitize_attribute_value(item)
            if cleaned_item is not None:
                cleaned_items.append(cleaned_item)
        return cleaned_items
    return str(value)[:MAX_STRING_LENGTH]


def sanitize_attributes(attributes: Mapping[str, Any] | None) -> dict[str, Any]:
    if not attributes:
        return {}
    cleaned: dict[str, Any] = {}
    for key, value in attributes.items():
        key_str = str(key)
        if not _is_safe_attribute_key(key_str):
            continue
        cleaned_value = _sanitize_attribute_value(value)
        if cleaned_value is not None:
            cleaned[key_str] = cleaned_value
    return cleaned


@dataclass(slots=True)
class TelemetryConfig:
    mode: str = "off"
    log_dir: Path | None = None
    service_name: str = "agent-memory-bridge"


class TelemetrySpan:
    def __init__(self, telemetry: "Telemetry", name: str, attributes: Mapping[str, Any] | None = None) -> None:
        self._telemetry = telemetry
        self.name = name
        self.attributes = sanitize_attributes(attributes)
        self._trace_token: Token[str | None] | None = None
        self._span_token: Token[str | None] | None = None
        self.trace_id: str | None = None
        self.span_id: str | None = None
        self.parent_span_id: str | None = None
        self._started_at = 0.0

    def __enter__(self) -> "TelemetrySpan":
        parent_trace_id = _current_trace_id.get()
        parent_span_id = _current_span_id.get()
        self.trace_id = parent_trace_id or uuid4().hex
        self.parent_span_id = parent_span_id
        self.span_id = uuid4().hex[:16]
        self._trace_token = _current_trace_id.set(self.trace_id)
        self._span_token = _current_span_id.set(self.span_id)
        self._started_at = perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        duration_ms = round((perf_counter() - self._started_at) * 1000, 3)
        self.attributes.setdefault("status", "ok" if exc_type is None else "error")
        self.attributes["duration_ms"] = duration_ms
        if exc_type is not None:
            self.attributes["error_type"] = exc_type.__name__
        self._telemetry._emit(
            name=self.name,
            trace_id=self.trace_id,
            span_id=self.span_id,
            parent_span_id=self.parent_span_id,
            attributes=self.attributes,
        )
        if self._span_token is not None:
            _current_span_id.reset(self._span_token)
        if self._trace_token is not None:
            _current_trace_id.reset(self._trace_token)

    def set_attribute(self, key: str, value: Any) -> None:
        cleaned = sanitize_attributes({key: value})
        if cleaned:
            self.attributes.update(cleaned)

    def set_attributes(self, attributes: Mapping[str, Any]) -> None:
        self.attributes.update(sanitize_attributes(attributes))


class Telemetry:
    def __init__(self, config: TelemetryConfig | None = None) -> None:
        self.config = config or TelemetryConfig()
        self.mode = self.config.mode.strip().lower() or "off"
        self.log_dir = Path(self.config.log_dir) if self.config.log_dir is not None else None
        self.service_name = self.config.service_name.strip() or "agent-memory-bridge"
        if self.mode == "jsonl" and self.log_dir is not None:
            self.log_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_env(cls) -> "Telemetry":
        mode = resolve_telemetry_mode()
        log_dir = resolve_telemetry_log_dir() if mode == "jsonl" else None
        service_name = resolve_telemetry_service_name()
        return cls(TelemetryConfig(mode=mode, log_dir=log_dir, service_name=service_name))

    @property
    def enabled(self) -> bool:
        return self.mode != "off"

    def span(self, name: str, attributes: Mapping[str, Any] | None = None) -> TelemetrySpan:
        return TelemetrySpan(self, name=name, attributes=attributes)

    def _emit(
        self,
        *,
        name: str,
        trace_id: str | None,
        span_id: str | None,
        parent_span_id: str | None,
        attributes: Mapping[str, Any],
    ) -> None:
        if self.mode != "jsonl" or self.log_dir is None:
            return
        entry = {
            "ts": _utc_now(),
            "service": self.service_name,
            "name": name,
            "trace_id": trace_id,
            "span_id": span_id,
            "parent_span_id": parent_span_id,
            "attributes": dict(attributes),
        }
        log_path = self.log_dir / "spans.jsonl"
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=True) + "\n")
