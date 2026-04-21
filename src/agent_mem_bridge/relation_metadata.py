from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


RELATION_KEYS = ("supports", "contradicts", "supersedes", "depends_on")
VALIDITY_STATUSES = ("unbounded", "current", "future", "expired", "invalid")


def parse_relation_metadata(content: str, *, now: datetime | None = None) -> dict[str, Any]:
    fields = parse_content_fields(content)
    relations = {
        relation: _split_relation_values(fields.get(relation, ""))
        for relation in RELATION_KEYS
    }
    valid_from = fields.get("valid_from")
    valid_until = fields.get("valid_until")
    validity_status = resolve_validity_status(valid_from=valid_from, valid_until=valid_until, now=now)
    return {
        "relations": relations,
        "valid_from": valid_from,
        "valid_until": valid_until,
        "validity_status": validity_status,
        "has_relation_metadata": any(relations.values()),
        "has_validity_window": bool(valid_from or valid_until),
    }


def extract_relation_tags(content: str) -> list[str]:
    metadata = parse_relation_metadata(content)
    tags = [
        f"relation:{relation}"
        for relation, targets in metadata["relations"].items()
        if targets
    ]
    if metadata["has_validity_window"]:
        tags.append("validity:bounded")
    return tags


def parse_content_fields(content: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for raw_line in content.splitlines():
        label, separator, remainder = raw_line.partition(":")
        if not separator:
            continue
        key = label.strip().lower().replace("-", "_")
        value = " ".join(remainder.split()).strip()
        if not key or not value:
            continue
        fields.setdefault(key, value)
    return fields


def resolve_validity_status(
    *,
    valid_from: str | None,
    valid_until: str | None,
    now: datetime | None = None,
) -> str:
    if not valid_from and not valid_until:
        return "unbounded"
    current_time = (now or datetime.now(UTC)).astimezone(UTC)
    parsed_valid_from = _parse_timestamp(valid_from)
    parsed_valid_until = _parse_timestamp(valid_until)
    if (valid_from and parsed_valid_from is None) or (valid_until and parsed_valid_until is None):
        return "invalid"
    if parsed_valid_from is not None and current_time < parsed_valid_from:
        return "future"
    if parsed_valid_until is not None and current_time > parsed_valid_until:
        return "expired"
    return "current"


def _parse_timestamp(value: str | None) -> datetime | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _split_relation_values(value: str) -> list[str]:
    if not value:
        return []
    seen: set[str] = set()
    items: list[str] = []
    for raw_part in value.split("|"):
        normalized = " ".join(raw_part.split()).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        items.append(normalized)
    return items
