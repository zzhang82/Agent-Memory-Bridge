from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
        content = self._build_structured_record(
            {
                "record_type": "domain-note",
                "domain": candidate.domain_tag,
                "claim": f"Recent patterns concentrate on: {' | '.join(claim_points[:3])}",
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
        )
        return {
            "type": "domain-note",
            "domain": candidate.domain_tag,
            "support_count": support_count,
            "result": result,
        }

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
