from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .consolidation import ConsolidationConfig
from .paths import resolve_consolidation_actor, resolve_profile_namespace
from .storage import MemoryStore


@dataclass(frozen=True, slots=True)
class BeliefObservationConfig:
    namespace: str = "global"
    actor: str = "bridge-consolidation"
    belief_min_support: int = 4
    belief_min_distinct_sessions: int = 3
    belief_max_contradictions: int = 0
    belief_required_stable_candidates: int = 2
    belief_freshness_days: int = 14
    top_n: int = 10


@dataclass(frozen=True, slots=True)
class LadderRecord:
    id: str
    title: str | None
    created_at: datetime
    tags: tuple[str, ...]
    domain: str
    claim: str
    boundary: str
    claim_hash: str
    boundary_hash: str
    support_count: int
    distinct_session_count: int
    contradiction_count: int
    contradiction_reason_counts: tuple[tuple[str, int], ...]
    confidence: str
    evidence_refs: tuple[str, ...]
    supersedes: str | None
    status: str
    record_type: str

    @property
    def key(self) -> tuple[str, str, str]:
        if self.claim_hash or self.boundary_hash:
            return (self.domain, self.claim_hash, self.boundary_hash)
        return (self.domain, self.claim, self.boundary)


def build_default_belief_observation_config() -> BeliefObservationConfig:
    defaults = ConsolidationConfig(state_path=Path("."))
    return BeliefObservationConfig(
        namespace=resolve_profile_namespace(),
        actor=resolve_consolidation_actor(),
        belief_min_support=defaults.belief_min_support,
        belief_min_distinct_sessions=defaults.belief_min_distinct_sessions,
        belief_max_contradictions=defaults.belief_max_contradictions,
        belief_required_stable_candidates=defaults.belief_required_stable_candidates,
        belief_freshness_days=defaults.belief_freshness_days,
    )


def observe_belief_ladder(store: MemoryStore, config: BeliefObservationConfig) -> dict[str, Any]:
    candidate_rows = _load_rows(store, namespace=config.namespace, actor=config.actor, tag="kind:belief-candidate")
    belief_rows = _load_rows(store, namespace=config.namespace, actor=config.actor, tag="kind:belief")
    domain_note_rows = _load_rows(store, namespace=config.namespace, actor=config.actor, tag="kind:domain-note")

    latest_candidates = _latest_by_key(candidate_rows)
    active_beliefs = {
        key: record
        for key, record in _latest_by_key(belief_rows).items()
        if record.status == "active"
    }
    stable_candidate_counts = Counter(record.key for record in _fresh_rows(candidate_rows, config.belief_freshness_days))

    candidate_status_rows: list[dict[str, Any]] = []
    by_domain: dict[str, dict[str, int]] = defaultdict(
        lambda: {
            "domain_note_count": 0,
            "candidate_count": 0,
            "belief_count": 0,
            "blocked_by_contradiction": 0,
            "blocked_by_staleness": 0,
            "blocked_by_low_support": 0,
            "blocked_by_stability": 0,
        }
    )

    stale_drop_count = 0
    contradiction_block_count = 0
    low_support_block_count = 0
    stability_block_count = 0
    contradiction_reason_counts: Counter[str] = Counter()

    for key, record in latest_candidates.items():
        has_active_belief = key in active_beliefs
        status = _candidate_status(
            record=record,
            has_active_belief=has_active_belief,
            stable_candidate_count=stable_candidate_counts.get(key, 0),
            config=config,
        )
        candidate_status_rows.append(_render_row(record, status=status))
        contradiction_reason_counts.update(dict(record.contradiction_reason_counts))
        by_domain[record.domain]["candidate_count"] += 1
        if status == "blocked-contradiction":
            contradiction_block_count += 1
            by_domain[record.domain]["blocked_by_contradiction"] += 1
        elif status == "stale":
            stale_drop_count += 1
            by_domain[record.domain]["blocked_by_staleness"] += 1
        elif status == "blocked-low-support":
            low_support_block_count += 1
            by_domain[record.domain]["blocked_by_low_support"] += 1
        elif status == "blocked-stability":
            stability_block_count += 1
            by_domain[record.domain]["blocked_by_stability"] += 1

    belief_rows_rendered: list[dict[str, Any]] = []
    for record in active_beliefs.values():
        belief_rows_rendered.append(_render_row(record, status="belief"))
        by_domain[record.domain]["belief_count"] += 1

    latest_domain_notes = _latest_by_key(domain_note_rows)
    for record in latest_domain_notes.values():
        by_domain[record.domain]["domain_note_count"] += 1

    candidate_count = len(latest_candidates)
    belief_count = len(active_beliefs)
    domain_note_count = len(latest_domain_notes)
    superseded_candidate_count = sum(1 for record in latest_candidates.values() if record.supersedes)
    supersede_rate = _safe_rate(superseded_candidate_count, candidate_count)

    summary = {
        "belief_candidate_count": candidate_count,
        "belief_count": belief_count,
        "domain_note_count": domain_note_count,
        "candidate_to_belief_rate": _safe_rate(belief_count, candidate_count),
        "belief_to_domain_note_ratio": _safe_rate(belief_count, domain_note_count),
        "blocked_by_contradiction": contradiction_block_count,
        "blocked_by_staleness": stale_drop_count,
        "blocked_by_low_support": low_support_block_count,
        "blocked_by_stability": stability_block_count,
        "contradiction_reason_counts": dict(sorted(contradiction_reason_counts.items())),
        "supersede_rate": supersede_rate,
        "startup_belief_default_loaded": False,
        "startup_belief_hit_rate": 0.0,
    }

    return {
        "summary": summary,
        "thresholds": {
            "belief_min_support": config.belief_min_support,
            "belief_min_distinct_sessions": config.belief_min_distinct_sessions,
            "belief_max_contradictions": config.belief_max_contradictions,
            "belief_required_stable_candidates": config.belief_required_stable_candidates,
            "belief_freshness_days": config.belief_freshness_days,
        },
        "leaderboards": {
            "beliefs": _sorted_rows(belief_rows_rendered)[: config.top_n],
            "candidates": _sorted_rows(candidate_status_rows)[: config.top_n],
        },
        "distributions": {
            "candidate_distinct_session_count": _histogram(
                record.distinct_session_count for record in latest_candidates.values()
            ),
            "belief_distinct_session_count": _histogram(
                record.distinct_session_count for record in active_beliefs.values()
            ),
            "candidate_support_count": _histogram(record.support_count for record in latest_candidates.values()),
            "belief_support_count": _histogram(record.support_count for record in active_beliefs.values()),
        },
        "cohorts": {
            "by_domain": [
                {"domain": domain, **counts}
                for domain, counts in sorted(by_domain.items(), key=lambda item: item[0])
            ]
        },
    }


def render_belief_observation_text(report: dict[str, Any]) -> str:
    summary = report["summary"]
    thresholds = report["thresholds"]
    lines = [
        "Belief Ladder Observation",
        "",
        "Overview",
        f"candidates: {summary['belief_candidate_count']}",
        f"beliefs: {summary['belief_count']}",
        f"domain_notes: {summary['domain_note_count']}",
        f"candidate_to_belief_rate: {_format_percent(summary['candidate_to_belief_rate'])}",
        f"belief_to_domain_note_ratio: {_format_percent(summary['belief_to_domain_note_ratio'])}",
        f"blocked_by_contradiction: {summary['blocked_by_contradiction']}",
        f"blocked_by_staleness: {summary['blocked_by_staleness']}",
        f"blocked_by_low_support: {summary['blocked_by_low_support']}",
        f"blocked_by_stability: {summary['blocked_by_stability']}",
        f"contradiction_reason_counts: {json.dumps(summary['contradiction_reason_counts'], sort_keys=True)}",
        f"supersede_rate: {_format_percent(summary['supersede_rate'])}",
        f"startup_belief_default_loaded: {summary['startup_belief_default_loaded']}",
        f"startup_belief_hit_rate: {_format_percent(summary['startup_belief_hit_rate'])}",
        "",
        "Thresholds",
        f"belief_min_support: {thresholds['belief_min_support']}",
        f"belief_min_distinct_sessions: {thresholds['belief_min_distinct_sessions']}",
        f"belief_max_contradictions: {thresholds['belief_max_contradictions']}",
        f"belief_required_stable_candidates: {thresholds['belief_required_stable_candidates']}",
        f"belief_freshness_days: {thresholds['belief_freshness_days']}",
        "",
        "Belief Leaderboard",
    ]
    lines.extend(_render_table(report["leaderboards"]["beliefs"]))
    lines.extend(["", "Candidate Leaderboard"])
    lines.extend(_render_table(report["leaderboards"]["candidates"]))
    lines.extend(["", "Distinct Session Distribution"])
    lines.extend(
        [
            f"candidates: {json.dumps(report['distributions']['candidate_distinct_session_count'], sort_keys=True)}",
            f"beliefs: {json.dumps(report['distributions']['belief_distinct_session_count'], sort_keys=True)}",
            "",
            "Domain Cohorts",
        ]
    )
    for row in report["cohorts"]["by_domain"]:
        lines.append(
            f"{row['domain']}: domain_notes={row['domain_note_count']} "
            f"candidates={row['candidate_count']} beliefs={row['belief_count']} "
            f"blocked_by_contradiction={row['blocked_by_contradiction']} "
            f"blocked_by_staleness={row['blocked_by_staleness']} "
            f"blocked_by_low_support={row['blocked_by_low_support']} "
            f"blocked_by_stability={row['blocked_by_stability']}"
        )
    return "\n".join(lines)


def _load_rows(store: MemoryStore, *, namespace: str, actor: str, tag: str) -> list[LadderRecord]:
    with store._connect() as conn:
        rows = conn.execute(
            """
            SELECT
                id,
                title,
                content,
                tags_json,
                created_at
            FROM memories
            WHERE namespace = ?
              AND actor = ?
              AND tags_json LIKE ?
            ORDER BY created_at DESC
            """,
            (namespace, actor, f'%"{tag}"%'),
        ).fetchall()
    return [_to_ladder_record(row) for row in rows]


def _to_ladder_record(row: sqlite3.Row) -> LadderRecord:
    fields = _parse_fields(str(row["content"]))
    tags = tuple(json.loads(row["tags_json"] or "[]"))
    return LadderRecord(
        id=str(row["id"]),
        title=row["title"],
        created_at=datetime.fromisoformat(str(row["created_at"])).astimezone(UTC),
        tags=tags,
        domain=fields.get("domain", "domain:unknown"),
        claim=fields.get("claim", ""),
        boundary=fields.get("boundary", ""),
        claim_hash=fields.get("claim_hash", ""),
        boundary_hash=fields.get("boundary_hash", ""),
        support_count=_parse_int(fields.get("support_count")),
        distinct_session_count=_parse_int(fields.get("distinct_session_count")),
        contradiction_count=_parse_int(fields.get("contradiction_count")),
        contradiction_reason_counts=tuple(_parse_reason_counts(fields.get("contradiction_reasons")).items()),
        confidence=fields.get("confidence", ""),
        evidence_refs=_split_pipe_list(fields.get("evidence_refs")),
        supersedes=fields.get("supersedes"),
        status=fields.get("status", ""),
        record_type=fields.get("record_type", ""),
    )


def _latest_by_key(records: list[LadderRecord]) -> dict[tuple[str, str, str], LadderRecord]:
    latest: dict[tuple[str, str, str], LadderRecord] = {}
    for record in records:
        latest.setdefault(record.key, record)
    return latest


def _fresh_rows(records: list[LadderRecord], freshness_days: int) -> list[LadderRecord]:
    cutoff = datetime.now(UTC) - timedelta(days=freshness_days)
    return [record for record in records if record.created_at >= cutoff]


def _candidate_status(
    *,
    record: LadderRecord,
    has_active_belief: bool,
    stable_candidate_count: int,
    config: BeliefObservationConfig,
) -> str:
    if has_active_belief:
        return "promoted"
    if record.created_at < datetime.now(UTC) - timedelta(days=config.belief_freshness_days):
        return "stale"
    if record.contradiction_count > config.belief_max_contradictions:
        return "blocked-contradiction"
    if (
        record.support_count < config.belief_min_support
        or record.distinct_session_count < config.belief_min_distinct_sessions
    ):
        return "blocked-low-support"
    if stable_candidate_count < config.belief_required_stable_candidates:
        return "blocked-stability"
    return "candidate"


def _render_row(record: LadderRecord, *, status: str) -> dict[str, Any]:
    age_days = max(0, int((datetime.now(UTC) - record.created_at).total_seconds() // 86400))
    return {
        "record_type": record.record_type,
        "domain": record.domain,
        "claim": record.claim,
        "support_count": record.support_count,
        "distinct_session_count": record.distinct_session_count,
        "contradiction_count": record.contradiction_count,
        "contradiction_reason_counts": dict(record.contradiction_reason_counts),
        "confidence": record.confidence,
        "status": status,
        "age_days": age_days,
        "id": record.id,
    }


def _sorted_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (
            0 if row["status"] == "belief" else 1,
            -int(row["support_count"]),
            -int(row["distinct_session_count"]),
            int(row["contradiction_count"]),
            row["claim"],
        ),
    )


def _histogram(values: Any) -> dict[str, int]:
    counter = Counter(int(value) for value in values)
    return {str(key): counter[key] for key in sorted(counter)}


def _render_table(rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return ["(none)"]
    header = "claim | support | sessions | contradictions | confidence | status"
    divider = "-" * len(header)
    rendered = [header, divider]
    for row in rows:
        rendered.append(
            f"{_truncate(row['claim'], 52)} | {row['support_count']} | {row['distinct_session_count']} | "
            f"{row['contradiction_count']} | {row['confidence']} | {row['status']}"
        )
    return rendered


def _truncate(text: str, width: int) -> str:
    compact = " ".join(text.split())
    if len(compact) <= width:
        return compact
    return compact[: width - 3] + "..."


def _parse_fields(content: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in content.splitlines():
        label, separator, remainder = line.partition(":")
        if not separator:
            continue
        key = label.strip().lower()
        value = " ".join(remainder.split()).strip()
        if not key or not value:
            continue
        fields.setdefault(key, value)
    return fields


def _parse_int(value: str | None) -> int:
    if not value:
        return 0
    return int(value)


def _split_pipe_list(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(part.strip() for part in value.split("|") if part.strip())


def _parse_reason_counts(value: str | None) -> dict[str, int]:
    counts: dict[str, int] = {}
    if not value:
        return counts
    for part in _split_pipe_list(value):
        reason, separator, raw_count = part.rpartition(":")
        if not separator or not reason or not raw_count.isdigit():
            counts[part] = counts.get(part, 0) + 1
            continue
        counts[reason] = counts.get(reason, 0) + int(raw_count)
    return dict(sorted(counts.items()))


def _safe_rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return numerator / denominator


def _format_percent(value: float) -> str:
    return f"{value * 100:.1f}%"
