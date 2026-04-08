from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .classifier import ClassifierConfig, EnrichmentCandidate, EnrichmentClassifier
from .enrichment_rules import infer_keyword_tags, normalize_text
from .paths import (
    resolve_classifier_batch_size,
    resolve_classifier_command,
    resolve_classifier_minimum_confidence,
    resolve_classifier_mode,
    resolve_classifier_provider,
    resolve_classifier_timeout_seconds,
    resolve_domain_title_prefix,
    resolve_learn_title_prefix,
    resolve_profile_namespace,
    resolve_reflex_actor,
)
from .storage import MemoryStore


NOISE_PREFIXES = (
    "user asked:",
    "assistant outcome:",
)

HIGH_SIGNAL_MARKERS = (
    "decision",
    "lesson",
    "mistake",
    "validate",
    "validation",
    "prefer",
    "always",
    "should",
    "must",
    "use ",
    "avoid",
    "fix",
    "drift",
    "orchestration",
    "subagent",
    "worker",
    "reasoning",
    "canonical",
    "recall",
    "memory bridge",
    "wrong db",
    "database",
    "handoff",
    "human readable",
    "machine-readable",
    "machine read",
    "token",
    "gotcha",
    "summary",
)

SYSTEM_PREFERENCE_MARKERS = (
    "prefer",
    "always",
    "must",
    "never",
)

SYSTEM_CONTEXT_MARKERS = (
    "model",
    "reasoning",
    "coding",
    "validate",
    "validation",
    "recall",
    "memory",
    "search",
    "subagent",
    "orchestration",
)

LEADING_FILLER_PREFIXES = (
    "keep in mind ",
    "also ",
    "one thing i do want to point out is ",
    "the main thing is ",
    "the final goal from the iteration is to ",
    "later in a project ",
    "for example ",
    "for example. ",
)

CLAIM_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("would prefer you use", "coding model", "validate"),
        "Use high reasoning for planning and validation; use coding models for bounded implementation.",
    ),
    (
        ("foundation is in place", "real product spec"),
        "Replace build playbooks with a real product spec before expanding implementation.",
    ),
    (
        ("foundation now runs end to end", "real database"),
        "Validate milestones end to end with tests plus a real-database smoke path.",
    ),
    (
        ("real drift in the codebase",),
        "Contract drift occurs when implementation and tests diverge on the current interface.",
    ),
    (
        ("storage.py", "current contract"),
        "Keep storage and server aligned to the current store/recall contract with compatibility wrappers.",
    ),
    (
        ("biggest design gap", "write-side trigger"),
        "The main design gap is the missing pre-compaction write-side trigger.",
    ),
    (
        ("watcher and mcp server share the same database",),
        "Watcher and MCP server must share the same database.",
    ),
    (
        ("trust in recall", "wrong db"),
        "Wrong-DB splits break trust in recall even when sync appears healthy.",
    ),
    (
        ("not need to put in each project",),
        "Keep shared operating memory in one global namespace instead of copying it into each project.",
    ),
    (
        ("domain based", "topic based"),
        "Organize long-term memory with both domain and topic tags linked through Obsidian references.",
    ),
    (
        ("other agent to read rather than human",),
        "Store MCP memory in machine-readable low-token records because agents are the primary readers.",
    ),
    (
        ("machine read effective", "token effective"),
        "Store MCP memory in machine-readable low-token records because agents are the primary readers.",
    ),
    (
        ("agent first", "human readability is optional"),
        "Shared memory should optimize for agents first, not human-readable prose.",
    ),
    (
        ("structure beats prose",),
        "Structured low-token records beat polished prose for agent memory.",
    ),
    (
        ("summary -> learn -> gotcha",),
        "Promote memory from summary to learn, gotcha, and domain-note instead of keeping summaries as the final artifact.",
    ),
    (
        ("summaries remain the final artifact",),
        "Promote memory from summary to learn, gotcha, and domain-note instead of keeping summaries as the final artifact.",
    ),
    (
        ("before web search", "bridge memory"),
        "Check local bridge memory before external search for issue-like prompts.",
    ),
    (
        ("search local memory first",),
        "Check local bridge memory before external search for issue-like prompts.",
    ),
    (
        ("project b", "gocha"),
        "Capture cross-project gotchas so later projects can reuse prior fixes before external search.",
    ),
    (
        ("you are helping define a new project called",),
        "Define a real product and MVP plan before implementation.",
    ),
)


@dataclass(frozen=True, slots=True)
class GotchaRule:
    name: str
    title: str
    claim: str
    trigger: str
    symptom: str
    fix: str
    keywords: tuple[str, ...]
    tags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DomainRule:
    name: str
    title: str
    summary: str
    keywords: tuple[str, ...]
    tags: tuple[str, ...]
    min_matches: int = 2


@dataclass(slots=True)
class ReflexConfig:
    state_path: Path
    target_namespace: str = "global"
    actor: str = "bridge-reflex"
    learn_title_prefix: str = "[[Learn]]"
    domain_title_prefix: str = "[[Domain Note]]"
    scan_limit: int = 200
    classifier_mode: str = "off"
    classifier_provider: str = "command"
    classifier_command: str = ""
    classifier_timeout_seconds: float = 10.0
    classifier_batch_size: int = 16
    classifier_minimum_confidence: float = 0.6


GOTCHA_RULES: tuple[GotchaRule, ...] = (
    GotchaRule(
        name="wrong-db",
        title="[[Gotcha]] automation and MCP used different databases",
        claim="automation and interactive MCP must share one canonical bridge database",
        trigger="automation path writes to a different database than interactive recall reads",
        symptom="sync appears healthy while recall misses new memories",
        fix="use one canonical runtime path and one shared bridge.db",
        keywords=(
            "wrong db",
            "different database",
            "canonical runtime path",
            "trust recall",
            "same database",
            "share the same database",
        ),
        tags=(
            "kind:gotcha",
            "problem:split-store",
            "symptom:wrong-db",
            "fix:canonical-runtime-path",
            "domain:memory-bridge",
            "topic:runtime-path",
            "confidence:validated",
        ),
    ),
    GotchaRule(
        name="contract-drift",
        title="[[Gotcha]] overlapping workers can cause contract drift",
        claim="overlapping workers on a moving contract can cause contract drift",
        trigger="multiple agents edit the same contract-bearing files at once",
        symptom="overwritten file, stale interface, or mismatched behavior",
        fix="assign single ownership for execution slices and reserve shared review for validation",
        keywords=(
            "contract drift",
            "overlapping agent",
            "single ownership",
            "moving contract",
            "drift in the codebase",
            "was overwritten",
            "overwritten after",
        ),
        tags=(
            "kind:gotcha",
            "problem:contract-drift",
            "symptom:conflicting-edits",
            "fix:single-ownership",
            "domain:orchestration",
            "topic:subagents",
            "confidence:validated",
        ),
    ),
    GotchaRule(
        name="fts-punctuation",
        title="[[Gotcha]] punctuation-heavy queries can break naive FTS recall",
        claim="punctuation-heavy queries can break naive FTS recall paths",
        trigger="query text contains punctuation or special characters that the FTS matcher cannot parse safely",
        symptom="recall fails, crashes, or misses obvious results for dotted or punctuated terms",
        fix="sanitize FTS queries and fall back to safer substring matching when needed",
        keywords=(
            "values.yaml",
            "special character",
            "punctuation-heavy",
            "fts query sanitizer",
            "handle values.yaml",
        ),
        tags=(
            "kind:gotcha",
            "problem:fts-query-shape",
            "symptom:punctuation-query-failure",
            "fix:safe-fts-fallback",
            "domain:retrieval",
            "topic:fts",
            "confidence:validated",
        ),
    ),
    GotchaRule(
        name="summary-noise",
        title="[[Gotcha]] raw session summaries create noisy long-term memory",
        claim="raw session summaries create noisy long-term memory when they are treated as final memory",
        trigger="session summaries are stored as the final artifact without promotion",
        symptom="recall returns chat-shaped history instead of reusable claims",
        fix="treat summaries as source material and promote durable items into learn, gotcha, and domain-note records",
        keywords=(
            "summaries remain the final artifact",
            "memory turns noisy",
            "summary -> learn -> gotcha",
            "save more chat",
        ),
        tags=(
            "kind:gotcha",
            "problem:summary-noise",
            "symptom:chat-shaped-recall",
            "fix:layered-promotion",
            "domain:agent-memory",
            "topic:memory-shaping",
            "confidence:validated",
        ),
    ),
    GotchaRule(
        name="narrative-memory",
        title="[[Gotcha]] narrative memory wastes tokens and weakens agent recall",
        claim="human-readable narrative memory wastes tokens and weakens agent recall",
        trigger="memory records are optimized for prose instead of compact machine-readable structure",
        symptom="agent recall costs more tokens and returns less precise context",
        fix="store compact field-structured records with stable tags",
        keywords=(
            "human readable",
            "machine read effective",
            "token effective",
            "structure beats prose",
            "claims beat summaries",
            "agent first",
        ),
        tags=(
            "kind:gotcha",
            "problem:narrative-memory",
            "symptom:token-heavy-recall",
            "fix:structured-records",
            "domain:agent-memory",
            "topic:memory-shaping",
            "confidence:validated",
        ),
    ),
    GotchaRule(
        name="copied-core-memory",
        title="[[Gotcha]] copying shared operating memory into each project causes drift",
        claim="copying shared operating memory into each project causes cross-project drift",
        trigger="shared operating memory is duplicated into repo-local project stores",
        symptom="core guidance diverges across projects and becomes harder to trust",
        fix="keep shared operating memory in one global namespace and reference it from project memory",
        keywords=(
            "not need to put in each project",
            "cross projects",
            "core memory structure should be inside here",
            "shared global bridge",
        ),
        tags=(
            "kind:gotcha",
            "problem:memory-drift",
            "symptom:cross-project-divergence",
            "fix:shared-global-bridge",
            "domain:memory-bridge",
            "topic:cross-project-reuse",
            "confidence:validated",
        ),
    ),
)


DOMAIN_RULES: tuple[DomainRule, ...] = (
    DomainRule(
        name="orchestration",
        title="[[Domain Note]] orchestration patterns",
        summary="recurring orchestration guidance promoted from recent session summaries",
        keywords=("orchestration", "subagent", "worker", "validation", "high reasoning"),
        tags=("kind:domain-note", "domain:orchestration", "scope:global"),
    ),
    DomainRule(
        name="memory-bridge",
        title="[[Domain Note]] memory bridge patterns",
        summary="recurring memory-bridge practices promoted from recent session summaries",
        keywords=("memory bridge", "shared memory", "recall", "store", "context"),
        tags=("kind:domain-note", "domain:memory-bridge", "scope:global"),
    ),
    DomainRule(
        name="agent-memory",
        title="[[Domain Note]] agent memory patterns",
        summary="recurring agent-memory practices promoted from recent session summaries",
        keywords=("machine-readable", "structured", "token", "summary", "gotcha", "agent recall"),
        tags=("kind:domain-note", "domain:agent-memory", "scope:global"),
    ),
)


class ReflexEngine:
    def __init__(self, store: MemoryStore, config: ReflexConfig) -> None:
        self.store = store
        self.config = config
        self.config.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.classifier = EnrichmentClassifier(
            ClassifierConfig(
                mode=config.classifier_mode,
                provider=config.classifier_provider,
                command=config.classifier_command,
                timeout_seconds=config.classifier_timeout_seconds,
                batch_size=config.classifier_batch_size,
                minimum_confidence=config.classifier_minimum_confidence,
            )
        )
        self._classifier_stats = self._empty_classifier_stats()

    def run_once(self) -> dict[str, Any]:
        self._classifier_stats = self._empty_classifier_stats()
        state = self._load_state()
        rows = self._load_recent_summary_rows(
            since_id=state.get("since_id"),
            limit=self.config.scan_limit,
        )
        if not rows:
            return {"processed_count": 0, "stored": [], "classifier": dict(self._classifier_stats)}

        cycle_id = rows[-1]["id"]
        stored: list[dict[str, Any]] = []
        row_predictions = self._classify_summary_rows(rows)

        for row in rows:
            stored.extend(self._promote_learns(row))
            stored.extend(self._promote_gotchas(row))

        stored.extend(self._promote_domain_notes(rows, cycle_id, row_predictions))

        state["since_id"] = cycle_id
        self._save_state(state)
        return {
            "processed_count": len(stored),
            "stored": stored,
            "since_id": cycle_id,
            "classifier": dict(self._classifier_stats),
        }

    def _promote_learns(self, row: sqlite3.Row) -> list[dict[str, Any]]:
        evidence = self._extract_evidence_fragments(row)
        stored: list[dict[str, Any]] = []
        seen_claims: set[str] = set()
        prepared: list[dict[str, Any]] = []
        for index, line in enumerate(evidence, start=1):
            if not self._should_promote_learn(line):
                continue
            clean_line = self._canonicalize_claim(line)
            if not clean_line:
                continue
            normalized_claim = self._normalize_text(clean_line)
            if normalized_claim in seen_claims:
                continue
            seen_claims.add(normalized_claim)
            fallback_tags = self._infer_domain_tags(clean_line)
            prepared.append(
                {
                    "key": f"{row['id']}:{index}",
                    "index": index,
                    "claim": clean_line,
                    "fallback_tags": fallback_tags,
                }
            )

        predictions = self._classify_candidates(
            [
                EnrichmentCandidate(
                    key=str(item["key"]),
                    text=str(item["claim"]),
                    fallback_tags=tuple(item["fallback_tags"]),
                    title=str(row["title"] or ""),
                    source_id=str(row["id"]),
                )
                for item in prepared
            ]
        )

        for item in prepared:
            prediction = predictions.get(str(item["key"]))
            classifier_tags = self.classifier.accepted_tags(prediction)
            inferred_tags = self._merge_inferred_tags(list(item["fallback_tags"]), classifier_tags)
            title = f"{self.config.learn_title_prefix} {self._truncate_title(str(item['claim']))}"
            tags = self._base_tags_for_row(row)
            tags.extend(("kind:learn", "confidence:observed", f"source-summary:{row['id']}"))
            tags.extend(inferred_tags)
            content = self._build_structured_record(
                {
                    "record_type": "learn",
                    "claim": str(item["claim"]),
                    "scope": "global",
                    "confidence": "observed",
                    "domains": self._joined_tags(inferred_tags, "domain:"),
                    "topics": self._joined_tags(inferred_tags, "topic:"),
                }
            )
            result = self.store.store(
                namespace=self.config.target_namespace,
                content=content,
                kind="memory",
                tags=self._unique_tags(tags),
                session_id=row["session_id"],
                actor=self.config.actor,
                title=title,
                correlation_id=row["correlation_id"],
                source_app="agent-memory-bridge-reflex",
            )
            stored.append({"type": "learn", "index": item["index"], "result": result, "source_id": row["id"]})
        return stored

    def _promote_gotchas(self, row: sqlite3.Row) -> list[dict[str, Any]]:
        text = self._normalize_text(self._row_text(row))
        stored: list[dict[str, Any]] = []
        structured_gotcha = self._structured_gotcha_from_row(row)
        if structured_gotcha is not None:
            result = self.store.store(
                namespace=self.config.target_namespace,
                content=self._build_structured_record(structured_gotcha["fields"]),
                kind="memory",
                tags=self._unique_tags(structured_gotcha["tags"]),
                session_id=row["session_id"],
                actor=self.config.actor,
                title=structured_gotcha["title"],
                correlation_id=row["correlation_id"],
                source_app="agent-memory-bridge-reflex",
            )
            stored.append({"type": "gotcha", "rule": "checkpoint-structured", "result": result, "source_id": row["id"]})
        for rule in GOTCHA_RULES:
            if not any(keyword in text for keyword in rule.keywords):
                continue
            tags = list(rule.tags)
            tags.extend(self._base_tags_for_row(row))
            tags.append(f"source-summary:{row['id']}")
            content = self._build_structured_record(
                {
                    "record_type": "gotcha",
                    "claim": rule.claim,
                    "trigger": rule.trigger,
                    "symptom": rule.symptom,
                    "fix": rule.fix,
                    "scope": "global",
                    "confidence": "validated",
                }
            )
            result = self.store.store(
                namespace=self.config.target_namespace,
                content=content,
                kind="memory",
                tags=self._unique_tags(tags),
                session_id=row["session_id"],
                actor=self.config.actor,
                title=rule.title,
                correlation_id=row["correlation_id"],
                source_app="agent-memory-bridge-reflex",
            )
            stored.append({"type": "gotcha", "rule": rule.name, "result": result, "source_id": row["id"]})
        return stored

    def _promote_domain_notes(
        self,
        rows: list[sqlite3.Row],
        cycle_id: str,
        row_predictions: dict[str, tuple[str, ...]],
    ) -> list[dict[str, Any]]:
        stored: list[dict[str, Any]] = []
        for rule in DOMAIN_RULES:
            matched = [
                row
                for row in rows
                if self._rule_matches_row(rule.keywords, row)
                or self._domain_rule_matches_prediction(rule, row_predictions.get(str(row["id"]), ()))
            ]
            if len(matched) < rule.min_matches:
                continue
            tags = list(rule.tags)
            tags.append(f"cycle:{cycle_id}")
            for row in matched[:4]:
                tags.append(self._project_tag_for_row(row))
            content = self._build_structured_record(
                {
                    "record_type": "domain-note",
                    "domain": next((tag for tag in rule.tags if tag.startswith("domain:")), "domain:general"),
                    "claim": rule.summary,
                    "scope": "global",
                    "signals": " | ".join(rule.keywords),
                }
            )
            result = self.store.store(
                namespace=self.config.target_namespace,
                content=content,
                kind="memory",
                tags=self._unique_tags(tags),
                session_id=cycle_id,
                actor=self.config.actor,
                title=self._domain_title_for_rule(rule),
                correlation_id=cycle_id,
                source_app="agent-memory-bridge-reflex",
            )
            stored.append({"type": "domain-note", "rule": rule.name, "result": result, "match_count": len(matched)})
        return stored

    def _classify_summary_rows(self, rows: list[sqlite3.Row]) -> dict[str, tuple[str, ...]]:
        predictions = self._classify_candidates(
            [
                EnrichmentCandidate(
                    key=str(row["id"]),
                    text=self._row_text(row),
                    fallback_tags=tuple(self._infer_domain_tags(self._row_text(row))),
                    title=str(row["title"] or ""),
                    source_id=str(row["id"]),
                )
                for row in rows
            ]
        )
        return {key: tuple(self.classifier.accepted_tags(prediction)) for key, prediction in predictions.items()}

    def _classify_candidates(
        self,
        candidates: list[EnrichmentCandidate],
    ) -> dict[str, Any]:
        if not candidates:
            return {}
        outcome = self.classifier.classify(candidates)
        self._classifier_stats["request_count"] += outcome.requested_count
        self._classifier_stats["prediction_count"] += len(outcome.predictions)
        accepted_prediction_count = 0
        filtered_low_confidence_count = 0
        for prediction in outcome.predictions.values():
            if self.classifier.accepted_tags(prediction):
                accepted_prediction_count += 1
            else:
                filtered_low_confidence_count += 1
        self._classifier_stats["accepted_prediction_count"] += accepted_prediction_count
        self._classifier_stats["filtered_low_confidence_count"] += filtered_low_confidence_count
        if outcome.error:
            self._classifier_stats["error_count"] += 1
            self._classifier_stats["last_error"] = outcome.error
        return outcome.predictions

    def _merge_inferred_tags(self, fallback_tags: list[str], classifier_tags: list[str]) -> list[str]:
        fallback_unique = self._unique_tags(list(fallback_tags))
        classifier_unique = self._unique_tags(list(classifier_tags))
        if classifier_unique and any(tag.startswith("domain:") and tag != "domain:general" for tag in classifier_unique):
            fallback_unique = [tag for tag in fallback_unique if tag != "domain:general"]
        if classifier_unique and set(classifier_unique) != set(fallback_unique):
            self._classifier_stats["divergence_count"] += 1
        if self.config.classifier_mode == "assist" and classifier_unique:
            return self._unique_tags(fallback_unique + classifier_unique)
        return fallback_unique

    @staticmethod
    def _domain_rule_matches_prediction(rule: DomainRule, predicted_tags: tuple[str, ...]) -> bool:
        domain_tag = next((tag for tag in rule.tags if tag.startswith("domain:")), "")
        return bool(domain_tag and domain_tag in predicted_tags)

    def _load_recent_summary_rows(self, since_id: str | None, limit: int) -> list[sqlite3.Row]:
        params: list[Any] = [self.config.target_namespace, '%"kind:summary"%']
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
            WHERE namespace != ?
              AND tags_json LIKE ?
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

    def _extract_evidence_fragments(self, row: sqlite3.Row) -> list[str]:
        collected: list[str] = []
        for line in str(row["content"]).splitlines():
            compact = " ".join(line.split()).strip()
            if not compact.startswith("- "):
                continue
            bullet = compact[2:]
            fragments = self._split_fragments(bullet)
            if fragments:
                collected.extend(fragments)
            else:
                    collected.append(bullet)
        return collected

    def _structured_gotcha_from_row(self, row: sqlite3.Row) -> dict[str, Any] | None:
        fields: dict[str, str] = {}
        for key, value in self._extract_structured_bullet_fields(row).items():
            if key in {"claim", "problem", "symptom", "fix", "trigger"} and value:
                fields[key] = value.rstrip(".")

        if not fields.get("problem"):
            return None
        if not fields.get("fix"):
            return None
        claim = fields.get("problem") or fields.get("claim")
        if not claim:
            return None

        tags = self._base_tags_for_row(row)
        tags.extend(
            [
                "kind:gotcha",
                "confidence:validated" if row["source_app"] == "codex-session-checkpointer" else "confidence:observed",
                f"source-summary:{row['id']}",
            ]
        )
        fallback_tags = self._infer_domain_tags(" ".join(fields.values()))
        predictions = self._classify_candidates(
            [
                EnrichmentCandidate(
                    key=str(row["id"]),
                    text=" ".join(fields.values()),
                    fallback_tags=tuple(fallback_tags),
                    title=str(row["title"] or ""),
                    source_id=str(row["id"]),
                )
            ]
        )
        prediction = predictions.get(str(row["id"]))
        inferred_tags = self._merge_inferred_tags(fallback_tags, self.classifier.accepted_tags(prediction))
        tags.extend(inferred_tags)
        fields_payload = {
            "record_type": "gotcha",
            "claim": self._finalize_claim(claim),
            "trigger": self._finalize_claim(fields.get("trigger", "")),
            "symptom": self._finalize_claim(fields.get("symptom", fields.get("problem", ""))),
            "fix": self._finalize_claim(fields["fix"]),
            "scope": "global",
            "confidence": "validated" if row["source_app"] == "codex-session-checkpointer" else "observed",
        }
        return {
            "title": f"[[Gotcha]] {self._truncate_title(fields_payload['claim'])}",
            "tags": tags,
            "fields": fields_payload,
        }

    def _extract_structured_bullet_fields(self, row: sqlite3.Row) -> dict[str, str]:
        fields: dict[str, str] = {}
        for line in str(row["content"]).splitlines():
            compact = " ".join(line.split()).strip()
            if not compact.startswith("- "):
                continue
            bullet = compact[2:]
            label, separator, remainder = bullet.partition(":")
            if not separator:
                continue
            key = label.strip().lower()
            if key not in {"claim", "decision", "problem", "symptom", "fix", "trigger"}:
                continue
            value = self._clean_fragment(remainder)
            if not value:
                continue
            if key == "decision":
                key = "claim"
            fields.setdefault(key, value)
        return fields

    def _base_tags_for_row(self, row: sqlite3.Row) -> list[str]:
        source_tags = json.loads(row["tags_json"] or "[]")
        tags = [self._project_tag_for_row(row), "scope:global", "source:reflex"]
        if row["actor"]:
            tags.append(f"actor:{str(row['actor']).lower()}")
        for tag in source_tags:
            if tag.startswith("agent:") or tag.startswith("agent-role:") or tag.startswith("link:"):
                tags.append(tag)
        return tags

    def _project_tag_for_row(self, row: sqlite3.Row) -> str:
        namespace = str(row["namespace"])
        if namespace.startswith("project:"):
            return namespace
        return f"project:{namespace}"

    def _infer_domain_tags(self, text: str) -> list[str]:
        return infer_keyword_tags(text)

    def _rule_matches_row(self, keywords: tuple[str, ...], row: sqlite3.Row) -> bool:
        text = self._normalize_text(self._row_text(row))
        return any(keyword in text for keyword in keywords)

    def _should_promote_learn(self, text: str) -> bool:
        normalized = self._normalize_text(text)
        if normalized.startswith("user asked:"):
            has_preference = any(marker in normalized for marker in SYSTEM_PREFERENCE_MARKERS)
            has_system_context = any(marker in normalized for marker in SYSTEM_CONTEXT_MARKERS)
            return has_preference and has_system_context
        return any(marker in normalized for marker in HIGH_SIGNAL_MARKERS)

    def _canonicalize_claim(self, text: str) -> str:
        clean = self._clean_fragment(text)
        normalized = self._normalize_text(clean)

        for required_markers, claim in CLAIM_RULES:
            if all(marker in normalized for marker in required_markers):
                return claim

        if self._is_noise_claim(normalized):
            return ""
        return self._finalize_claim(clean)

    def _lookup_created_at(self, memory_id: str) -> str | None:
        with self.store._connect() as conn:
            row = conn.execute("SELECT created_at FROM memories WHERE id = ?", (memory_id,)).fetchone()
        if row is None:
            return None
        return str(row["created_at"])

    @staticmethod
    def _row_text(row: sqlite3.Row) -> str:
        parts = [row["title"] or "", row["content"] or "", row["namespace"] or ""]
        return "\n".join(parts)

    @staticmethod
    def _strip_noise_prefix(text: str) -> str:
        normalized = text.strip()
        lowered = normalized.lower()
        for prefix in NOISE_PREFIXES:
            if lowered.startswith(prefix):
                return normalized[len(prefix):].strip()
        return normalized

    @classmethod
    def _clean_fragment(cls, text: str) -> str:
        normalized = cls._strip_noise_prefix(text)
        normalized = normalized.replace("â€™", "'")
        normalized = re.sub(r"[`*_]+", "", normalized)
        normalized = " ".join(normalized.split()).strip(" -:;,.")
        lowered = normalized.lower()
        for prefix in LEADING_FILLER_PREFIXES:
            if lowered.startswith(prefix):
                normalized = normalized[len(prefix):].strip(" -:;,.")
                lowered = normalized.lower()
        return normalized

    @classmethod
    def _split_fragments(cls, text: str) -> list[str]:
        clean = cls._clean_fragment(text)
        if not clean:
            return []
        fragments = re.split(r"(?<=[.!?;])\s+", clean)
        results: list[str] = []
        for fragment in fragments:
            compact = cls._clean_fragment(fragment)
            if not compact:
                continue
            if len(compact.split()) < 4 and cls._normalize_text(compact) not in {"wrong db", "contract drift"}:
                continue
            results.append(compact)
        return results

    @staticmethod
    def _is_noise_claim(normalized: str) -> bool:
        return any(
            marker in normalized
            for marker in (
                "this project is what we should do for linkin job submission",
                "make sense. update the skill accordingly",
                "review this file as a high-rigor product/architecture reviewer",
                "sounds good",
                "exactly",
                "make sense",
            )
        )

    @staticmethod
    def _finalize_claim(text: str) -> str:
        compact = " ".join(text.split()).strip(" -:;,.")
        if not compact:
            return ""
        if compact[-1] not in ".!?":
            compact += "."
        return compact[0].upper() + compact[1:]

    @staticmethod
    def _truncate_title(text: str, limit: int = 72) -> str:
        compact = " ".join(text.split())
        if len(compact) <= limit:
            return compact
        return compact[: limit - 3].rstrip() + "..."

    @staticmethod
    def _build_structured_record(fields: dict[str, str]) -> str:
        lines: list[str] = []
        for key, value in fields.items():
            normalized = " ".join(str(value).split()).strip()
            if not normalized:
                continue
            lines.append(f"{key}: {normalized}")
        return "\n".join(lines)

    @staticmethod
    def _joined_tags(tags: list[str], prefix: str) -> str:
        values = [tag for tag in tags if tag.startswith(prefix)]
        return " | ".join(values)

    @staticmethod
    def _normalize_text(text: str) -> str:
        return normalize_text(text)

    @staticmethod
    def _unique_tags(tags: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for tag in tags:
            normalized = tag.strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            result.append(normalized)
        return result

    def _load_state(self) -> dict[str, str]:
        if not self.config.state_path.exists():
            return {}
        return json.loads(self.config.state_path.read_text(encoding="utf-8"))

    def _save_state(self, state: dict[str, str]) -> None:
        self.config.state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    def _domain_title_for_rule(self, rule: DomainRule) -> str:
        suffix = rule.title.split("]]", 1)[-1].strip() if "]]" in rule.title else rule.title
        return f"{self.config.domain_title_prefix} {suffix}".strip()

    def _empty_classifier_stats(self) -> dict[str, Any]:
        return {
            "mode": self.config.classifier_mode,
            "minimum_confidence": self.config.classifier_minimum_confidence,
            "request_count": 0,
            "prediction_count": 0,
            "accepted_prediction_count": 0,
            "filtered_low_confidence_count": 0,
            "divergence_count": 0,
            "error_count": 0,
            "last_error": None,
        }


def build_default_reflex_config(state_path: Path, scan_limit: int) -> ReflexConfig:
    return ReflexConfig(
        state_path=state_path,
        target_namespace=resolve_profile_namespace(),
        actor=resolve_reflex_actor(),
        learn_title_prefix=resolve_learn_title_prefix(),
        domain_title_prefix=resolve_domain_title_prefix(),
        scan_limit=scan_limit,
        classifier_mode=resolve_classifier_mode(),
        classifier_provider=resolve_classifier_provider(),
        classifier_command=resolve_classifier_command(),
        classifier_timeout_seconds=resolve_classifier_timeout_seconds(),
        classifier_batch_size=resolve_classifier_batch_size(),
        classifier_minimum_confidence=resolve_classifier_minimum_confidence(),
    )
