from __future__ import annotations

import json
import sqlite3
from collections import Counter
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .contradiction import assess_contradiction_claim
from .paths import resolve_consolidation_actor, resolve_domain_title_prefix, resolve_profile_namespace
from .storage import MemoryStore


@dataclass(slots=True)
class ConsolidationConfig:
    state_path: Path
    target_namespace: str = "global"
    actor: str = "bridge-consolidation"
    domain_title_prefix: str = "[[Domain Note]]"
    scan_limit: int = 200
    min_support: int = 2
    belief_candidate_min_support: int = 3
    belief_min_support: int = 4
    belief_min_distinct_sessions: int = 3
    belief_max_contradictions: int = 0
    belief_required_stable_candidates: int = 2
    belief_freshness_days: int = 14


@dataclass(frozen=True, slots=True)
class SynthesisCandidate:
    domain_tag: str
    rows: list[sqlite3.Row]


class ConsolidationEngine:
    def __init__(self, store: MemoryStore, config: ConsolidationConfig) -> None:
        self.store = store
        self.config = config
        self.config.state_path.parent.mkdir(parents=True, exist_ok=True)

    def run_once(self) -> dict[str, Any]:
        state = self._load_state()
        new_rows = self._load_new_source_rows(state.get("since_id"), self.config.scan_limit)
        if not new_rows:
            return {"processed_count": 0, "stored": []}

        recent_rows = self._load_recent_source_rows(self.config.scan_limit)
        stored: list[dict[str, Any]] = []
        touched_domains = self._collect_touched_domains(new_rows)
        for candidate in self._build_domain_candidates(recent_rows, touched_domains):
            result = self._store_domain_note(candidate)
            if result is not None:
                stored.append(result)
            belief_result = self._store_belief_candidate(candidate)
            if belief_result is not None:
                stored.append(belief_result)
                belief_record = self._store_belief(candidate, belief_result)
                if belief_record is not None:
                    stored.append(belief_record)
                    concept_note = self._store_concept_note(candidate, belief_record)
                    if concept_note is not None:
                        stored.append(concept_note)

        state["since_id"] = new_rows[-1]["id"]
        self._save_state(state)
        return {"processed_count": len(stored), "stored": stored, "since_id": state["since_id"]}

    def _load_new_source_rows(self, since_id: str | None, limit: int) -> list[sqlite3.Row]:
        params: list[Any] = [
            self.config.target_namespace,
            '%"kind:learn"%',
            '%"kind:gotcha"%',
        ]
        sql = """
            SELECT
                id,
                namespace,
                kind,
                title,
                content,
                tags_json,
                session_id,
                actor,
                correlation_id,
                source_app,
                source_client,
                source_model,
                client_session_id,
                client_workspace,
                client_transport,
                created_at
            FROM memories
            WHERE namespace = ?
              AND (tags_json LIKE ? OR tags_json LIKE ?)
        """
        if since_id:
            since_created_at = self._lookup_created_at(since_id)
            if since_created_at:
                sql += " AND created_at > ?"
                params.append(since_created_at)
        sql += " ORDER BY created_at ASC LIMIT ?"
        params.append(limit)
        with self.store._connect() as conn:
            return conn.execute(sql, params).fetchall()

    def _load_recent_source_rows(self, limit: int) -> list[sqlite3.Row]:
        with self.store._connect() as conn:
            return conn.execute(
                """
                SELECT
                    id,
                    namespace,
                    kind,
                    title,
                    content,
                    tags_json,
                    session_id,
                    actor,
                    correlation_id,
                    source_app,
                    source_client,
                    source_model,
                    client_session_id,
                    client_workspace,
                    client_transport,
                    created_at
                FROM memories
                WHERE namespace = ?
                  AND (tags_json LIKE ? OR tags_json LIKE ?)
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (
                    self.config.target_namespace,
                    '%"kind:learn"%',
                    '%"kind:gotcha"%',
                    limit,
                ),
            ).fetchall()

    def _collect_touched_domains(self, rows: list[sqlite3.Row]) -> set[str]:
        touched: set[str] = set()
        for row in rows:
            for tag in self._tags_for_row(row):
                if tag.startswith("domain:"):
                    touched.add(tag)
        return touched

    def _build_domain_candidates(
        self,
        rows: list[sqlite3.Row],
        touched_domains: set[str],
    ) -> list[SynthesisCandidate]:
        grouped: dict[str, list[sqlite3.Row]] = defaultdict(list)
        for row in rows:
            row_domains = [tag for tag in self._tags_for_row(row) if tag.startswith("domain:")]
            for domain_tag in row_domains:
                if domain_tag in touched_domains:
                    grouped[domain_tag].append(row)

        candidates: list[SynthesisCandidate] = []
        for domain_tag, domain_rows in grouped.items():
            if domain_tag == "domain:general":
                continue
            if len(domain_rows) < self.config.min_support:
                continue
            candidates.append(SynthesisCandidate(domain_tag=domain_tag, rows=domain_rows))
        candidates.sort(key=lambda item: item.domain_tag)
        return candidates

    def _store_domain_note(self, candidate: SynthesisCandidate) -> dict[str, Any] | None:
        support_rows = candidate.rows[: self.config.scan_limit]
        claim_points = self._collect_claim_points(support_rows)
        if not claim_points:
            return None

        topic_tags = self._collect_tags_with_prefix(support_rows, "topic:")
        project_tags = self._collect_tags_with_prefix(support_rows, "project:")
        support_count = len(support_rows)
        domain_name = candidate.domain_tag.split(":", 1)[1]
        concept_fields = self._build_concept_compression(candidate.domain_tag, support_rows, claim_points)
        content = self._build_structured_record(
            {
                "record_type": "domain-note",
                "domain": candidate.domain_tag,
                "claim": f"Recent patterns concentrate on: {' | '.join(claim_points[:3])}",
                **concept_fields,
                "support_count": str(support_count),
                "recent_claims": " | ".join(claim_points[:4]),
                "related_topics": " | ".join(topic_tags[:4]),
                "source_projects": " | ".join(project_tags[:4]),
                "scope": "global",
            }
        )
        tags = [
            "kind:domain-note",
            candidate.domain_tag,
            "scope:global",
            "source:consolidation",
            f"support-count:{support_count}",
        ]
        tags.extend(topic_tags[:4])
        title = f"{self.config.domain_title_prefix} {domain_name} synthesis"
        newest_row = support_rows[0]
        origin_kwargs = self._origin_kwargs_from_rows(support_rows)
        result = self.store.store(
            namespace=self.config.target_namespace,
            content=content,
            kind="memory",
            tags=self._unique_tags(tags),
            session_id=newest_row["session_id"],
            actor=self.config.actor,
            title=title,
            correlation_id=newest_row["correlation_id"],
            source_app="agent-memory-bridge-consolidation",
            **origin_kwargs,
        )
        return {
            "type": "domain-note",
            "domain": candidate.domain_tag,
            "support_count": support_count,
            "result": result,
        }

    def _store_belief_candidate(self, candidate: SynthesisCandidate) -> dict[str, Any] | None:
        support_rows = candidate.rows[: self.config.scan_limit]
        support_count = len(support_rows)
        if support_count < self.config.belief_candidate_min_support:
            return None

        claim_points = self._collect_claim_points(support_rows)
        if not claim_points:
            return None

        concept_fields = self._build_concept_compression(candidate.domain_tag, support_rows, claim_points)
        claim = concept_fields.get("rule") or concept_fields.get("anchor") or claim_points[0]
        if not claim:
            return None

        boundary = concept_fields.get("boundary", "")
        distinct_session_count = self._count_distinct_sessions(support_rows)
        contradiction_reason_counts = self._collect_contradiction_reason_counts(support_rows)
        contradiction_count = sum(
            count
            for reason, count in contradiction_reason_counts.items()
            if not reason.startswith("boundary-exempt:")
            and reason != "no-marker"
        )
        confidence = self._belief_confidence(
            support_count=support_count,
            distinct_session_count=distinct_session_count,
            contradiction_count=contradiction_count,
        )
        evidence_refs = [str(row["id"]) for row in support_rows[:6]]
        previous_id = self._latest_belief_candidate_id(candidate.domain_tag)
        domain_name = candidate.domain_tag.split(":", 1)[1]
        content_fields = {
            "record_type": "belief-candidate",
            "domain": candidate.domain_tag,
            "claim": claim,
            "boundary": boundary,
            "support_count": str(support_count),
            "distinct_session_count": str(distinct_session_count),
            "contradiction_count": str(contradiction_count),
            "contradiction_reasons": self._format_reason_counts(contradiction_reason_counts),
            "confidence": confidence,
            "evidence_refs": " | ".join(evidence_refs),
            "claim_hash": self._stable_hash(claim),
            "boundary_hash": self._stable_hash(boundary),
            "status": "candidate",
            "staleness_policy": "reconfirm with fresh supporting evidence before treating as durable policy",
            "scope": "global",
        }
        if previous_id:
            content_fields["supersedes"] = previous_id

        tags = [
            "kind:belief-candidate",
            "control:belief",
            candidate.domain_tag,
            "scope:global",
            "source:consolidation",
            f"support-count:{support_count}",
            f"distinct-sessions:{distinct_session_count}",
            f"contradiction-count:{contradiction_count}",
            f"confidence:{confidence}",
        ]
        title = f"[[Belief Candidate]] {domain_name} pattern"
        newest_row = support_rows[0]
        result = self.store.store(
            namespace=self.config.target_namespace,
            content=self._build_structured_record(content_fields),
            kind="memory",
            tags=self._unique_tags(tags),
            session_id=newest_row["session_id"],
            actor=self.config.actor,
            title=title,
            correlation_id=newest_row["correlation_id"],
            source_app="agent-memory-bridge-consolidation",
            **self._origin_kwargs_from_rows(support_rows),
        )
        return {
            "type": "belief-candidate",
            "domain": candidate.domain_tag,
            "support_count": support_count,
            "contradiction_count": contradiction_count,
            "result": result,
        }

    def _store_belief(self, candidate: SynthesisCandidate, belief_candidate_result: dict[str, Any]) -> dict[str, Any] | None:
        candidate_id = str(belief_candidate_result["result"]["id"])
        candidate_row = self._fetch_memory_row(candidate_id)
        if candidate_row is None:
            return None

        candidate_fields = self._parse_fields(str(candidate_row["content"]))
        support_count = self._parse_int_field(candidate_fields.get("support_count"))
        distinct_session_count = self._parse_int_field(candidate_fields.get("distinct_session_count"))
        contradiction_count = self._parse_int_field(candidate_fields.get("contradiction_count"))
        claim_hash = candidate_fields.get("claim_hash", "")
        boundary_hash = candidate_fields.get("boundary_hash", "")

        if support_count < self.config.belief_min_support:
            return None
        if distinct_session_count < self.config.belief_min_distinct_sessions:
            return None
        if contradiction_count > self.config.belief_max_contradictions:
            return None

        recent_candidates = self._load_recent_belief_candidate_rows(candidate.domain_tag)
        stable_candidates = [
            row
            for row in recent_candidates
            if self._parse_fields(str(row["content"])).get("claim_hash", "") == claim_hash
            and self._parse_fields(str(row["content"])).get("boundary_hash", "") == boundary_hash
        ]
        if len(stable_candidates) < self.config.belief_required_stable_candidates:
            return None

        if self._active_belief_exists(candidate.domain_tag, claim_hash, boundary_hash):
            return None

        support_rows = candidate.rows[: self.config.scan_limit]
        concept_fields = self._build_concept_compression(
            candidate.domain_tag,
            support_rows,
            self._collect_claim_points(support_rows),
        )
        claim = candidate_fields.get("claim", "")
        boundary = candidate_fields.get("boundary", "")
        confidence = self._belief_score(
            support_count=support_count,
            distinct_session_count=distinct_session_count,
            contradiction_count=contradiction_count,
        )
        domain_name = candidate.domain_tag.split(":", 1)[1]
        content_fields = {
            "record_type": "belief",
            "domain": candidate.domain_tag,
            "claim": claim,
            "boundary": boundary,
            "support_count": str(support_count),
            "distinct_session_count": str(distinct_session_count),
            "contradiction_count": str(contradiction_count),
            "confidence": confidence,
            "evidence_refs": candidate_fields.get("evidence_refs", ""),
            "derived_from_candidate_id": candidate_id,
            "staleness_policy": "decay_if_unseen",
            "status": "active",
            "claim_hash": claim_hash,
            "boundary_hash": boundary_hash,
            "scope": "global",
        }
        if concept_fields.get("rule"):
            content_fields["rule"] = concept_fields["rule"]
        if concept_fields.get("failure_mode"):
            content_fields["failure_mode"] = concept_fields["failure_mode"]

        tags = [
            "kind:belief",
            "control:belief",
            candidate.domain_tag,
            "scope:global",
            "source:consolidation",
            "status:active",
            f"support-count:{support_count}",
            f"distinct-sessions:{distinct_session_count}",
            f"contradiction-count:{contradiction_count}",
        ]
        title = f"[[Belief]] {domain_name} pattern"
        newest_row = support_rows[0]
        result = self.store.store(
            namespace=self.config.target_namespace,
            content=self._build_structured_record(content_fields),
            kind="memory",
            tags=self._unique_tags(tags),
            session_id=newest_row["session_id"],
            actor=self.config.actor,
            title=title,
            correlation_id=newest_row["correlation_id"],
            source_app="agent-memory-bridge-consolidation",
            **self._origin_kwargs_from_rows(support_rows),
        )
        return {
            "type": "belief",
            "domain": candidate.domain_tag,
            "support_count": support_count,
            "distinct_session_count": distinct_session_count,
            "result": result,
        }

    def _store_concept_note(
        self,
        candidate: SynthesisCandidate,
        belief_result: dict[str, Any],
    ) -> dict[str, Any] | None:
        belief_id = str(belief_result["result"]["id"])
        belief_row = self._fetch_memory_row(belief_id)
        if belief_row is None:
            return None

        belief_fields = self._parse_fields(str(belief_row["content"]))
        support_rows = candidate.rows[: self.config.scan_limit]
        claim_points = self._collect_claim_points(support_rows)
        concept_fields = self._build_concept_compression(candidate.domain_tag, support_rows, claim_points)
        domain_name = candidate.domain_tag.split(":", 1)[1]
        concept_label = concept_fields.get("rule") or concept_fields.get("anchor") or belief_fields.get("claim", "")
        if not concept_label:
            return None

        content_fields = {
            "record_type": "concept-note",
            "domain": candidate.domain_tag,
            "concept": concept_label,
            "claim": belief_fields.get("claim", concept_label),
            "support_count": belief_fields.get("support_count", ""),
            "distinct_session_count": belief_fields.get("distinct_session_count", ""),
            "confidence": belief_fields.get("confidence", ""),
            "derived_from_belief_id": belief_id,
            "depends_on": belief_id,
            "scope": "global",
        }
        for key in ("anchor", "boundary", "rule", "failure_mode", "epiphany"):
            value = concept_fields.get(key)
            if value:
                content_fields[key] = value

        topic_tags = self._collect_tags_with_prefix(support_rows, "topic:")
        title = f"[[Concept Note]] {domain_name} pattern"
        newest_row = support_rows[0]
        result = self.store.store(
            namespace=self.config.target_namespace,
            content=self._build_structured_record(content_fields),
            kind="memory",
            tags=self._unique_tags(
                [
                    "kind:concept-note",
                    candidate.domain_tag,
                    "scope:global",
                    "source:consolidation",
                    *topic_tags[:4],
                ]
            ),
            session_id=newest_row["session_id"],
            actor=self.config.actor,
            title=title,
            correlation_id=newest_row["correlation_id"],
            source_app="agent-memory-bridge-consolidation",
            **self._origin_kwargs_from_rows(support_rows),
        )
        return {
            "type": "concept-note",
            "domain": candidate.domain_tag,
            "result": result,
        }

    def _build_concept_compression(
        self,
        domain_tag: str,
        rows: list[sqlite3.Row],
        claim_points: list[str],
    ) -> dict[str, str]:
        domain_name = domain_tag.split(":", 1)[1]
        anchor = self._select_anchor(rows, claim_points)
        rule = self._select_rule(rows, claim_points)
        failure_mode = self._select_failure_mode(rows, claim_points)
        boundary = self._select_boundary(anchor, rule, failure_mode)
        epiphany = self._build_epiphany(domain_name, anchor, rule, failure_mode)
        compressed: dict[str, str] = {}
        if anchor:
            compressed["anchor"] = anchor
        if boundary:
            compressed["boundary"] = boundary
        if rule:
            compressed["rule"] = rule
        if failure_mode:
            compressed["failure_mode"] = failure_mode
        if epiphany:
            compressed["epiphany"] = epiphany
        return compressed

    def _select_anchor(self, rows: list[sqlite3.Row], claim_points: list[str]) -> str:
        for row in rows:
            if not self._row_has_tag(row, "kind:learn"):
                continue
            claim = self._parse_fields(str(row["content"])).get("claim", "").strip()
            if claim:
                return claim
        for row in rows:
            claim = self._parse_fields(str(row["content"])).get("claim", "").strip()
            if claim:
                return claim
        return claim_points[0] if claim_points else ""

    def _collect_claim_points(self, rows: list[sqlite3.Row]) -> list[str]:
        points: list[str] = []
        seen: set[str] = set()
        for row in rows:
            fields = self._parse_fields(str(row["content"]))
            for key in ("claim", "fix", "symptom"):
                value = fields.get(key, "").strip()
                if not value:
                    continue
                normalized = " ".join(value.lower().split())
                if normalized in seen:
                    continue
                seen.add(normalized)
                points.append(value)
        return points

    def _select_rule(self, rows: list[sqlite3.Row], claim_points: list[str]) -> str:
        for row in rows:
            fields = self._parse_fields(str(row["content"]))
            fix = fields.get("fix", "").strip()
            if fix:
                return fix
        for claim in claim_points:
            normalized = claim.lower()
            if any(marker in normalized for marker in ("should", "must", "prefer", "keep", "use", "check", "treat", "assign")):
                return claim
        return ""

    def _select_failure_mode(self, rows: list[sqlite3.Row], claim_points: list[str]) -> str:
        for row in rows:
            fields = self._parse_fields(str(row["content"]))
            for key in ("symptom", "trigger"):
                value = fields.get(key, "").strip()
                if value:
                    return value
        if len(claim_points) >= 2:
            return claim_points[1]
        return ""

    @staticmethod
    def _select_boundary(anchor: str, rule: str, failure_mode: str) -> str:
        if rule and failure_mode:
            return f"This is not just {failure_mode.lower()}; it is the repeatable rule that prevents it."
        if rule and anchor and rule != anchor:
            return f"This is not just {anchor.lower()}; it is the operating rule for handling it."
        return ""

    @staticmethod
    def _build_epiphany(domain_name: str, anchor: str, rule: str, failure_mode: str) -> str:
        clean_anchor = ConsolidationEngine._strip_terminal_punctuation(anchor)
        clean_rule = ConsolidationEngine._strip_terminal_punctuation(rule)
        clean_failure_mode = ConsolidationEngine._strip_terminal_punctuation(failure_mode).lower()
        if rule and failure_mode:
            return f"In {domain_name}, {clean_rule} because otherwise {clean_failure_mode}."
        if rule and anchor:
            return f"In {domain_name}, {clean_anchor.lower()} becomes durable only when it turns into a reusable rule."
        if anchor:
            return f"In {domain_name}, the durable pattern is: {clean_anchor.lower()}."
        return ""

    @staticmethod
    def _strip_terminal_punctuation(text: str) -> str:
        return text.rstrip(" .!?;:")

    @staticmethod
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

    @staticmethod
    def _build_structured_record(fields: dict[str, str]) -> str:
        lines: list[str] = []
        for key, value in fields.items():
            compact = " ".join(str(value).split()).strip()
            if compact:
                lines.append(f"{key}: {compact}")
        return "\n".join(lines)

    @staticmethod
    def _tags_for_row(row: sqlite3.Row) -> list[str]:
        return json.loads(row["tags_json"] or "[]")

    def _row_has_tag(self, row: sqlite3.Row, tag: str) -> bool:
        return tag in self._tags_for_row(row)

    def _count_contradictions(self, rows: list[sqlite3.Row]) -> int:
        contradiction_count = 0
        for row in rows:
            claim = self._parse_fields(str(row["content"])).get("claim", "")
            if assess_contradiction_claim(claim).counts_as_contradiction:
                contradiction_count += 1
        return contradiction_count

    def _collect_contradiction_reason_counts(self, rows: list[sqlite3.Row]) -> dict[str, int]:
        counts: Counter[str] = Counter()
        for row in rows:
            claim = self._parse_fields(str(row["content"])).get("claim", "")
            assessment = assess_contradiction_claim(claim)
            counts[assessment.reason_code] += 1
        return dict(sorted(counts.items(), key=lambda item: item[0]))

    @staticmethod
    def _format_reason_counts(reason_counts: dict[str, int]) -> str:
        parts = [f"{reason}:{count}" for reason, count in reason_counts.items() if count > 0]
        return " | ".join(parts)

    @staticmethod
    def _belief_confidence(*, support_count: int, distinct_session_count: int, contradiction_count: int) -> str:
        if contradiction_count > 0:
            return "tentative"
        if support_count >= 4 and distinct_session_count >= 3:
            return "strong-candidate"
        return "candidate"

    @staticmethod
    def _belief_score(*, support_count: int, distinct_session_count: int, contradiction_count: int) -> str:
        score = 0.35
        score += min(support_count, 6) * 0.07
        score += min(distinct_session_count, 4) * 0.08
        score -= contradiction_count * 0.18
        clamped = max(0.0, min(score, 0.95))
        return f"{clamped:.2f}"

    def _latest_belief_candidate_id(self, domain_tag: str) -> str | None:
        with self.store._connect() as conn:
            row = conn.execute(
                """
                SELECT id
                FROM memories
                WHERE namespace = ?
                  AND tags_json LIKE ?
                  AND tags_json LIKE ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (
                    self.config.target_namespace,
                    '%"kind:belief-candidate"%',
                    f'%"{domain_tag}"%',
                ),
            ).fetchone()
        if row is None:
            return None
        return str(row["id"])

    def _load_recent_belief_candidate_rows(self, domain_tag: str) -> list[sqlite3.Row]:
        cutoff = (datetime.now(UTC) - timedelta(days=self.config.belief_freshness_days)).isoformat()
        with self.store._connect() as conn:
            return conn.execute(
                """
                SELECT
                    id,
                    namespace,
                    kind,
                    title,
                    content,
                    tags_json,
                    session_id,
                    actor,
                    correlation_id,
                    source_app,
                    source_client,
                    source_model,
                    client_session_id,
                    client_workspace,
                    client_transport,
                    created_at
                FROM memories
                WHERE namespace = ?
                  AND tags_json LIKE ?
                  AND tags_json LIKE ?
                  AND created_at >= ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (
                    self.config.target_namespace,
                    '%"kind:belief-candidate"%',
                    f'%"{domain_tag}"%',
                    cutoff,
                    self.config.scan_limit,
                ),
            ).fetchall()

    def _active_belief_exists(self, domain_tag: str, claim_hash: str, boundary_hash: str) -> bool:
        cutoff = (datetime.now(UTC) - timedelta(days=self.config.belief_freshness_days)).isoformat()
        with self.store._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    id,
                    content,
                    created_at
                FROM memories
                WHERE namespace = ?
                  AND tags_json LIKE ?
                  AND tags_json LIKE ?
                  AND created_at >= ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (
                    self.config.target_namespace,
                    '%"kind:belief"%',
                    f'%"{domain_tag}"%',
                    cutoff,
                    self.config.scan_limit,
                ),
            ).fetchall()
        for row in rows:
            fields = self._parse_fields(str(row["content"]))
            if (
                fields.get("status") == "active"
                and fields.get("claim_hash", "") == claim_hash
                and fields.get("boundary_hash", "") == boundary_hash
            ):
                return True
        return False

    def _fetch_memory_row(self, memory_id: str) -> sqlite3.Row | None:
        with self.store._connect() as conn:
            return conn.execute(
                """
                SELECT
                    id,
                    namespace,
                    kind,
                    title,
                    content,
                    tags_json,
                    session_id,
                    actor,
                    correlation_id,
                    source_app,
                    source_client,
                    source_model,
                    client_session_id,
                    client_workspace,
                    client_transport,
                    created_at
                FROM memories
                WHERE id = ?
                LIMIT 1
                """,
                (memory_id,),
            ).fetchone()

    @staticmethod
    def _count_distinct_sessions(rows: list[sqlite3.Row]) -> int:
        return len({str(row["session_id"]).strip() for row in rows if row["session_id"]})

    @staticmethod
    def _stable_hash(text: str) -> str:
        normalized = " ".join(text.lower().split()).strip()
        if not normalized:
            return ""
        import hashlib

        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    @staticmethod
    def _parse_int_field(value: str | None) -> int:
        if not value:
            return 0
        return int(value)

    def _collect_tags_with_prefix(self, rows: list[sqlite3.Row], prefix: str) -> list[str]:
        ordered: list[str] = []
        seen: set[str] = set()
        for row in rows:
            for tag in self._tags_for_row(row):
                if not tag.startswith(prefix) or tag in seen:
                    continue
                seen.add(tag)
                ordered.append(tag)
        return ordered

    def _lookup_created_at(self, memory_id: str) -> str | None:
        with self.store._connect() as conn:
            row = conn.execute("SELECT created_at FROM memories WHERE id = ?", (memory_id,)).fetchone()
        if row is None:
            return None
        return str(row["created_at"])

    @staticmethod
    def _uniform_origin_value(rows: list[sqlite3.Row], key: str) -> str | None:
        values = {str(row[key]).strip() for row in rows if row[key]}
        if len(values) == 1:
            return next(iter(values))
        return None

    def _origin_kwargs_from_rows(self, rows: list[sqlite3.Row]) -> dict[str, str | None]:
        return {
            "source_client": self._uniform_origin_value(rows, "source_client"),
            "source_model": self._uniform_origin_value(rows, "source_model"),
            "client_session_id": self._uniform_origin_value(rows, "client_session_id"),
            "client_workspace": self._uniform_origin_value(rows, "client_workspace"),
            "client_transport": self._uniform_origin_value(rows, "client_transport"),
        }

    @staticmethod
    def _unique_tags(tags: list[str]) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        for tag in tags:
            normalized = tag.strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            ordered.append(normalized)
        return ordered

    def _load_state(self) -> dict[str, str]:
        if not self.config.state_path.exists():
            return {}
        return json.loads(self.config.state_path.read_text(encoding="utf-8"))

    def _save_state(self, state: dict[str, str]) -> None:
        self.config.state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def build_default_consolidation_config(state_path: Path, scan_limit: int, min_support: int = 2) -> ConsolidationConfig:
    return ConsolidationConfig(
        state_path=state_path,
        target_namespace=resolve_profile_namespace(),
        actor=resolve_consolidation_actor(),
        domain_title_prefix=resolve_domain_title_prefix(),
        scan_limit=scan_limit,
        min_support=min_support,
    )
