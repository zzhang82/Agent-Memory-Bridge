from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal


ControlLevel = Literal["signal", "reflection", "belief", "policy"]
ProfileRecordType = Literal["persona", "soul", "core-policy", "belief", "reflection", "reference-doc"]


VALID_CONTROL_LEVELS: tuple[ControlLevel, ...] = ("signal", "reflection", "belief", "policy")
VALID_RECORD_TYPES: tuple[ProfileRecordType, ...] = (
    "persona",
    "soul",
    "core-policy",
    "belief",
    "reflection",
    "reference-doc",
)


@dataclass(frozen=True, slots=True)
class ProfileBundleRecord:
    title: str
    record_type: ProfileRecordType
    control_level: ControlLevel
    content: str
    tags: tuple[str, ...]
    domains: tuple[str, ...]
    startup_load: bool
    source_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProfileBundle:
    name: str
    namespace: str
    records: tuple[ProfileBundleRecord, ...]


def load_profile_bundle(path: Path) -> ProfileBundle:
    with Path(path).expanduser().open("rb") as handle:
        data = tomllib.load(handle)

    name = _require_non_empty_str(data, "bundle", "name")
    namespace = _require_non_empty_str(data, "bundle", "namespace")
    raw_records = data.get("record", [])
    if not isinstance(raw_records, list):
        raise ValueError("Bundle TOML must contain [[record]] entries.")

    records = tuple(_parse_record(entry) for entry in raw_records)
    return ProfileBundle(name=name, namespace=namespace, records=records)


def startup_records(bundle: ProfileBundle) -> tuple[ProfileBundleRecord, ...]:
    wanted_order = {
        "core-policy": 0,
        "persona": 1,
        "soul": 2,
        "belief": 3,
        "reflection": 4,
        "reference-doc": 5,
    }
    selected = [record for record in bundle.records if record.startup_load]
    selected.sort(key=lambda item: (wanted_order.get(item.record_type, 999), item.title.lower()))
    return tuple(selected)


def render_profile_bundle(bundle: ProfileBundle) -> str:
    lines = [
        f"name: {bundle.name}",
        f"namespace: {bundle.namespace}",
        f"record_count: {len(bundle.records)}",
        "",
        "startup_records:",
    ]
    for record in startup_records(bundle):
        lines.append(f"- {record.title}")
        lines.append(f"  type: {record.record_type}")
        lines.append(f"  control: {record.control_level}")
        if record.domains:
            lines.append(f"  domains: {', '.join(record.domains)}")
        if record.source_refs:
            lines.append(f"  source_refs: {', '.join(record.source_refs)}")
    return "\n".join(lines)


def _parse_record(entry: Any) -> ProfileBundleRecord:
    if not isinstance(entry, dict):
        raise ValueError("Each [[record]] entry must be a table.")

    title = _require_non_empty_str(entry, "title")
    record_type = _require_literal(entry, "record_type", VALID_RECORD_TYPES)
    control_level = _require_literal(entry, "control_level", VALID_CONTROL_LEVELS)
    content = _require_non_empty_str(entry, "content")
    tags = _tuple_of_strings(entry.get("tags", []))
    domains = _tuple_of_strings(entry.get("domains", []))
    source_refs = _tuple_of_strings(entry.get("source_refs", []))
    startup_load = bool(entry.get("startup_load", False))

    return ProfileBundleRecord(
        title=title,
        record_type=record_type,
        control_level=control_level,
        content=content,
        tags=tags,
        domains=domains,
        startup_load=startup_load,
        source_refs=source_refs,
    )


def _tuple_of_strings(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError("Expected a list of strings.")
    items: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError("Expected a list of strings.")
        cleaned = item.strip()
        if cleaned:
            items.append(cleaned)
    return tuple(items)


def _require_non_empty_str(mapping: dict[str, Any], *keys: str) -> str:
    value: Any = mapping
    for key in keys:
        if not isinstance(value, dict):
            raise ValueError(f"Missing required string value for {'.'.join(keys)}")
        value = value.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Missing required string value for {'.'.join(keys)}")
    return value.strip()


def _require_literal(mapping: dict[str, Any], key: str, allowed: tuple[str, ...]) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or value not in allowed:
        raise ValueError(f"{key} must be one of: {', '.join(allowed)}")
    return value
