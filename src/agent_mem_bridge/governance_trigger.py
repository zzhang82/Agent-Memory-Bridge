from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .promotion_governance import candidate_from_learning_candidate_item, review_learning_candidate
from .repository import LEARNING_CANDIDATE_TAG, MEMORY_ROW_SELECT, MemoryRow
from .state_io import load_json_state, write_json_state_atomic
from .storage import MemoryStore

REVIEWABLE_CANDIDATE_STATUSES = {"pending", "needs_review"}


@dataclass(slots=True)
class GovernanceTriggerConfig:
    state_path: Path
    scan_limit: int = 100
    actor: str = "bridge-governance-trigger"


class GovernanceTriggerEngine:
    """Surface hidden learning candidates as review signals.

    This engine is intentionally non-promoting. It may write a coordination
    signal so an operator can review a candidate, but it never writes approved
    durable memory and never changes the hidden candidate row.
    """

    def __init__(self, store: MemoryStore, config: GovernanceTriggerConfig) -> None:
        self.store = store
        self.config = config
        self.config.state_path.parent.mkdir(parents=True, exist_ok=True)

    def run_once(self) -> dict[str, Any]:
        state = self._load_state()
        signaled_ids = set(state.get("signaled_candidate_ids") or [])
        rows = self._load_reviewable_candidate_rows(limit=self.config.scan_limit)
        created: list[dict[str, Any]] = []
        reviewed_count = 0

        for row in rows:
            item = MemoryRow.from_sqlite(row).as_dict()
            candidate_id = str(item["id"])
            if candidate_id in signaled_ids or self._existing_trigger_signal(candidate_id, namespace=str(item["namespace"])):
                continue
            candidate = candidate_from_learning_candidate_item(item)
            review = review_learning_candidate(self.store, candidate)
            reviewed_count += 1
            signal = self._store_review_signal(candidate_id=candidate_id, item=item, review=review)
            created.append(signal)
            signaled_ids.add(candidate_id)

        state["signaled_candidate_ids"] = sorted(signaled_ids)
        self._save_state(state)
        return {
            "processed_count": len(created),
            "reviewed_count": reviewed_count,
            "created": created,
        }

    def _load_reviewable_candidate_rows(self, *, limit: int) -> list[sqlite3.Row]:
        candidate_limit = max(1, min(limit, 500))
        status_filters = [f'%"{tag}"%' for tag in sorted(f"candidate_status:{status}" for status in REVIEWABLE_CANDIDATE_STATUSES)]
        with self.store._connect() as conn:
            return conn.execute(
                f"""
                SELECT
                    {MEMORY_ROW_SELECT}
                FROM memories
                WHERE is_learning_candidate = 1
                  AND tags_json LIKE ?
                  AND tags_json NOT LIKE ?
                  AND ({' OR '.join('tags_json LIKE ?' for _ in status_filters)})
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (
                    f'%"{LEARNING_CANDIDATE_TAG}"%',
                    '%"kind:learning-review"%',
                    *status_filters,
                    candidate_limit,
                ),
            ).fetchall()

    def _existing_trigger_signal(self, candidate_id: str, *, namespace: str) -> bool:
        correlation_id = self._trigger_correlation_id(candidate_id)
        existing = self.store.recall(
            namespace=namespace,
            kind="signal",
            correlation_id=correlation_id,
            limit=1,
        )
        return bool(existing.get("items"))

    def _store_review_signal(self, *, candidate_id: str, item: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
        action = str(review.get("recommended_action") or "keep_staged")
        reason_codes = [str(reason) for reason in (review.get("reason_codes") or [])]
        checks = review.get("checks") or {}
        content = "\n".join(
            line
            for line in [
                "record_type: governance-trigger",
                "trigger_type: learning-candidate-review",
                f"candidate_id: {candidate_id}",
                f"candidate_ref: {review.get('candidate_ref') or ''}",
                f"recommended_action: {action}",
                f"target_record_type: {review.get('target_record_type') or ''}",
                f"reason_codes_json: {json.dumps(reason_codes, ensure_ascii=True, sort_keys=True)}",
                f"review_checks_json: {json.dumps(checks, ensure_ascii=True, sort_keys=True)}",
                "mutation_boundary: signal_only_no_promotion",
            ]
            if line.strip()
        )
        result = self.store.store(
            namespace=str(item["namespace"]),
            kind="signal",
            title=f"[[Governance Trigger]] review learning candidate {candidate_id}",
            content=content,
            tags=[
                "kind:governance-trigger",
                "trigger:learning-candidate-review",
                f"governance_action:{action}",
                f"target_record_type:{review.get('target_record_type') or 'unknown'}",
            ],
            session_id=item.get("session_id"),
            actor=self.config.actor,
            correlation_id=self._trigger_correlation_id(candidate_id),
            source_app="agent-memory-bridge-governance-trigger",
            source_client=item.get("source_client"),
            source_model=item.get("source_model"),
            client_session_id=item.get("client_session_id"),
            client_workspace=item.get("client_workspace"),
            client_transport=item.get("client_transport"),
        )
        return {
            "candidate_id": candidate_id,
            "signal_id": result["id"],
            "recommended_action": action,
            "reason_codes": reason_codes,
        }

    @staticmethod
    def _trigger_correlation_id(candidate_id: str) -> str:
        return f"governance-trigger:{candidate_id}"

    def _load_state(self) -> dict[str, Any]:
        return load_json_state(self.config.state_path)

    def _save_state(self, state: dict[str, Any]) -> None:
        write_json_state_atomic(self.config.state_path, state)
