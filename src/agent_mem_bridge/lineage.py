from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Iterator

from .structured_record import (
    LINEAGE_LIST_FIELDS,
    LINEAGE_SINGLETON_FIELDS,
    parse_structured_content,
)


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


_SINGLETON_FIELDS = frozenset(LINEAGE_SINGLETON_FIELDS)
_PLAIN_LIST_FIELDS = frozenset(LINEAGE_LIST_FIELDS)


def parse_lineage(content: str) -> Lineage:
    """Parse exact record IDs from supported structured content fields only."""
    record = parse_structured_content(content)
    singletons = {key: record.first(key) for key in _SINGLETON_FIELDS}
    collections = {key: record.values(key) for key in _PLAIN_LIST_FIELDS}

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
    record = parse_structured_content(content)
    supported = _SINGLETON_FIELDS | _PLAIN_LIST_FIELDS
    return {key: record.values(key) for key in supported if record.values(key)}


def _deduplicate(values: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return tuple(result)
