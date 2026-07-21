from __future__ import annotations

import json
from dataclasses import dataclass


RELATION_FIELDS = ("supports", "contradicts", "supersedes", "depends_on")
LINEAGE_SINGLETON_FIELDS = (
    "derived_from_candidate_id",
    "derived_from_belief_id",
    "source_candidate_id",
    "candidate_id",
    "target_record_id",
)
LINEAGE_LIST_FIELDS = ("evidence_refs", *RELATION_FIELDS)
LINEAGE_JSON_LIST_ALIASES = {
    "evidence_refs_json": "evidence_refs",
    "supports_record_ids_json": "supports",
    "contradicts_record_ids_json": "contradicts",
    "supersedes_record_ids_json": "supersedes",
    "depends_on_record_ids_json": "depends_on",
}
ACCUMULATING_FIELDS = frozenset(LINEAGE_LIST_FIELDS)


@dataclass(frozen=True, slots=True)
class StructuredRecord:
    fields: dict[str, tuple[str, ...]]

    def first(self, key: str) -> str | None:
        values = self.fields.get(normalize_field_name(key), ())
        return values[0] if values else None

    def values(self, key: str) -> tuple[str, ...]:
        return self.fields.get(normalize_field_name(key), ())

    def as_compat_dict(self) -> dict[str, str]:
        return {
            key: " | ".join(values) if key in ACCUMULATING_FIELDS else values[0]
            for key, values in self.fields.items()
            if values
        }


def parse_structured_content(content: str) -> StructuredRecord:
    parsed: dict[str, list[str]] = {}
    for raw_line in str(content).splitlines():
        label, separator, remainder = raw_line.partition(":")
        if not separator:
            continue
        key = normalize_field_name(label)
        raw_value = remainder.strip()
        if not key or not raw_value:
            continue

        alias_target = LINEAGE_JSON_LIST_ALIASES.get(key)
        if alias_target is not None:
            _extend_unique(parsed.setdefault(alias_target, []), _parse_json_string_list(raw_value))
            continue

        if key in ACCUMULATING_FIELDS:
            _extend_unique(parsed.setdefault(key, []), _parse_pipe_list(raw_value))
            continue

        if key not in parsed:
            value = _compact_value(raw_value)
            if value:
                parsed[key] = [value]

    return StructuredRecord({key: tuple(values) for key, values in parsed.items() if values})


def build_structured_content(fields: dict[str, str]) -> str:
    lines: list[str] = []
    for key, value in fields.items():
        normalized_key = normalize_field_name(key)
        normalized_value = _compact_value(str(value))
        if not normalized_key or not normalized_value:
            continue
        lines.append(f"{normalized_key}: {normalized_value}")
    return "\n".join(lines)


def normalize_field_name(value: str) -> str:
    return value.strip().lower().replace("-", "_")


def _parse_pipe_list(value: str) -> list[str]:
    return [cleaned for part in value.split("|") if (cleaned := _compact_value(part))]


def _parse_json_string_list(value: str) -> list[str]:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [cleaned for item in parsed if isinstance(item, str) and (cleaned := _compact_value(item))]


def _compact_value(value: str) -> str:
    return " ".join(value.split()).strip()


def _extend_unique(target: list[str], values: list[str]) -> None:
    seen = set(target)
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        target.append(value)
