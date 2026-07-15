from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Iterator


class LineageRelation(StrEnum):
    DERIVED_FROM_CANDIDATE = "derived_from_candidate_id"
    DERIVED_FROM_BELIEF = "derived_from_belief_id"
    EVIDENCE = "evidence_refs"
    SOURCE_CANDIDATE = "source_candidate_id"
    CANDIDATE = "candidate_id"
    TARGET_RECORD = "target_record_id"
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    SUPERSEDES = "supersedes"
    DEPENDS_ON = "depends_on"


DEGRADING_LINEAGE_RELATIONS = frozenset(
    {
        LineageRelation.DERIVED_FROM_CANDIDATE,
        LineageRelation.DERIVED_FROM_BELIEF,
        LineageRelation.EVIDENCE,
        LineageRelation.SOURCE_CANDIDATE,
        LineageRelation.CANDIDATE,
        LineageRelation.TARGET_RECORD,
        LineageRelation.SUPPORTS,
        LineageRelation.DEPENDS_ON,
    }
)
HISTORICAL_LINEAGE_RELATIONS = frozenset(
    {
        LineageRelation.CONTRADICTS,
        LineageRelation.SUPERSEDES,
    }
)


@dataclass(frozen=True, slots=True)
class LineageReference:
    relation: LineageRelation
    target_id: str


@dataclass(frozen=True, slots=True)
class Lineage:
    derived_from_candidate_id: str | None = None
    derived_from_belief_id: str | None = None
    evidence_refs: tuple[str, ...] = ()
    source_candidate_id: str | None = None
    candidate_id: str | None = None
    target_record_id: str | None = None
    supports: tuple[str, ...] = ()
    contradicts: tuple[str, ...] = ()
    supersedes: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()

    def iter_references(self) -> Iterator[LineageReference]:
        singletons = (
            (LineageRelation.DERIVED_FROM_CANDIDATE, self.derived_from_candidate_id),
            (LineageRelation.DERIVED_FROM_BELIEF, self.derived_from_belief_id),
            (LineageRelation.SOURCE_CANDIDATE, self.source_candidate_id),
            (LineageRelation.CANDIDATE, self.candidate_id),
            (LineageRelation.TARGET_RECORD, self.target_record_id),
        )
        for relation, target_id in singletons:
            if target_id is not None:
                yield LineageReference(relation=relation, target_id=target_id)

        collections = (
            (LineageRelation.EVIDENCE, self.evidence_refs),
            (LineageRelation.SUPPORTS, self.supports),
            (LineageRelation.CONTRADICTS, self.contradicts),
            (LineageRelation.SUPERSEDES, self.supersedes),
            (LineageRelation.DEPENDS_ON, self.depends_on),
        )
        for relation, target_ids in collections:
            for target_id in target_ids:
                yield LineageReference(relation=relation, target_id=target_id)

    @property
    def references(self) -> tuple[LineageReference, ...]:
        return tuple(self.iter_references())

    @property
    def degrading_references(self) -> tuple[LineageReference, ...]:
        return tuple(
            reference
            for reference in self.iter_references()
            if reference.relation in DEGRADING_LINEAGE_RELATIONS
        )

    @property
    def historical_references(self) -> tuple[LineageReference, ...]:
        return tuple(
            reference
            for reference in self.iter_references()
            if reference.relation in HISTORICAL_LINEAGE_RELATIONS
        )

    @property
    def target_ids(self) -> tuple[str, ...]:
        return _deduplicate(reference.target_id for reference in self.iter_references())

    def references_to(self, target_id: str) -> tuple[LineageReference, ...]:
        return tuple(reference for reference in self.iter_references() if reference.target_id == target_id)

    def as_dict(self) -> dict[str, Any]:
        return {
            "derived_from_candidate_id": self.derived_from_candidate_id,
            "derived_from_belief_id": self.derived_from_belief_id,
            "evidence_refs": list(self.evidence_refs),
            "source_candidate_id": self.source_candidate_id,
            "candidate_id": self.candidate_id,
            "target_record_id": self.target_record_id,
            "supports": list(self.supports),
            "contradicts": list(self.contradicts),
            "supersedes": list(self.supersedes),
            "depends_on": list(self.depends_on),
        }


_SINGLETON_FIELDS = {
    "derived_from_candidate_id",
    "derived_from_belief_id",
    "source_candidate_id",
    "candidate_id",
    "target_record_id",
}
_PLAIN_LIST_FIELDS = {"evidence_refs", "supports", "contradicts", "supersedes", "depends_on"}
_JSON_LIST_FIELDS = {
    "evidence_refs_json": "evidence_refs",
    "supports_record_ids_json": "supports",
    "contradicts_record_ids_json": "contradicts",
    "supersedes_record_ids_json": "supersedes",
    "depends_on_record_ids_json": "depends_on",
}


def parse_lineage(content: str) -> Lineage:
    """Parse exact record IDs from supported structured content fields only."""
    fields = _parse_structured_fields(content)
    singletons = {
        key: _first_nonempty(fields.get(key, ()))
        for key in _SINGLETON_FIELDS
    }
    collections: dict[str, tuple[str, ...]] = {}
    for key in _PLAIN_LIST_FIELDS:
        plain_values: list[str] = []
        for raw_value in fields.get(key, ()):
            plain_values.extend(_parse_pipe_list(raw_value))
        json_values: list[str] = []
        json_key = next((candidate for candidate, target in _JSON_LIST_FIELDS.items() if target == key), None)
        if json_key is not None:
            for raw_value in fields.get(json_key, ()):
                json_values.extend(_parse_json_string_list(raw_value))
        collections[key] = _deduplicate([*plain_values, *json_values])

    return Lineage(
        derived_from_candidate_id=singletons["derived_from_candidate_id"],
        derived_from_belief_id=singletons["derived_from_belief_id"],
        evidence_refs=collections["evidence_refs"],
        source_candidate_id=singletons["source_candidate_id"],
        candidate_id=singletons["candidate_id"],
        target_record_id=singletons["target_record_id"],
        supports=collections["supports"],
        contradicts=collections["contradicts"],
        supersedes=collections["supersedes"],
        depends_on=collections["depends_on"],
    )


def parse_lineage_metadata(content: str) -> Lineage:
    return parse_lineage(content)


def extract_lineage_references(content: str) -> tuple[LineageReference, ...]:
    return parse_lineage(content).references


def _parse_structured_fields(content: str) -> dict[str, tuple[str, ...]]:
    values: dict[str, list[str]] = {}
    for raw_line in str(content).splitlines():
        label, separator, remainder = raw_line.partition(":")
        if not separator:
            continue
        key = label.strip().lower()
        value = remainder.strip()
        if key not in _SINGLETON_FIELDS and key not in _PLAIN_LIST_FIELDS and key not in _JSON_LIST_FIELDS:
            continue
        if not value:
            continue
        values.setdefault(key, []).append(value)
    return {key: tuple(items) for key, items in values.items()}


def _first_nonempty(values: tuple[str, ...]) -> str | None:
    for value in values:
        cleaned = value.strip()
        if cleaned:
            return cleaned
    return None


def _parse_pipe_list(value: str) -> list[str]:
    return [part.strip() for part in value.split("|") if part.strip()]


def _parse_json_string_list(value: str) -> list[str]:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [item.strip() for item in parsed if isinstance(item, str) and item.strip()]


def _deduplicate(values: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return tuple(result)
