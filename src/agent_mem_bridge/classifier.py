from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Any, Literal


ClassifierMode = Literal["off", "shadow", "assist"]
ClassifierProvider = Literal["command"]


@dataclass(frozen=True, slots=True)
class ClassifierConfig:
    mode: ClassifierMode = "off"
    provider: ClassifierProvider = "command"
    command: str = ""
    timeout_seconds: float = 10.0
    batch_size: int = 16
    minimum_confidence: float = 0.6


@dataclass(frozen=True, slots=True)
class EnrichmentCandidate:
    key: str
    text: str
    fallback_tags: tuple[str, ...] = ()
    title: str = ""
    source_id: str = ""


@dataclass(frozen=True, slots=True)
class Classification:
    key: str
    tags: tuple[str, ...]
    domains: tuple[str, ...]
    topics: tuple[str, ...]
    confidence: float | None = None


@dataclass(frozen=True, slots=True)
class ClassificationBatchOutcome:
    predictions: dict[str, Classification]
    requested_count: int
    error: str | None = None


class EnrichmentClassifier:
    def __init__(self, config: ClassifierConfig) -> None:
        self.config = config

    @property
    def active(self) -> bool:
        return self.config.mode != "off" and bool(self.config.command.strip())

    def classify(self, candidates: list[EnrichmentCandidate]) -> ClassificationBatchOutcome:
        if not candidates:
            return ClassificationBatchOutcome(predictions={}, requested_count=0)
        if not self.active:
            return ClassificationBatchOutcome(predictions={}, requested_count=len(candidates))

        predictions: dict[str, Classification] = {}
        for batch in self._batched(candidates):
            outcome = self._classify_batch(batch)
            predictions.update(outcome.predictions)
            if outcome.error:
                return ClassificationBatchOutcome(
                    predictions=predictions,
                    requested_count=len(candidates),
                    error=outcome.error,
                )
        return ClassificationBatchOutcome(predictions=predictions, requested_count=len(candidates))

    def accepted_tags(self, classification: Classification | None) -> list[str]:
        if classification is None:
            return []
        if classification.confidence is not None and classification.confidence < self.config.minimum_confidence:
            return []
        return list(classification.tags)

    def _classify_batch(self, batch: list[EnrichmentCandidate]) -> ClassificationBatchOutcome:
        payload = {
            "items": [
                {
                    "key": candidate.key,
                    "text": candidate.text,
                    "title": candidate.title,
                    "source_id": candidate.source_id,
                    "fallback_tags": list(candidate.fallback_tags),
                }
                for candidate in batch
            ]
        }
        try:
            completed = subprocess.run(
                self.config.command,
                shell=True,
                check=False,
                capture_output=True,
                text=True,
                input=json.dumps(payload),
                timeout=self.config.timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return ClassificationBatchOutcome(
                predictions={},
                requested_count=len(batch),
                error=f"classifier command failed: {exc}",
            )

        if completed.returncode != 0:
            message = completed.stderr.strip() or completed.stdout.strip() or f"exit {completed.returncode}"
            return ClassificationBatchOutcome(
                predictions={},
                requested_count=len(batch),
                error=f"classifier command failed: {message}",
            )

        try:
            raw = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            return ClassificationBatchOutcome(
                predictions={},
                requested_count=len(batch),
                error=f"classifier returned invalid JSON: {exc}",
            )

        items = raw.get("items", raw) if isinstance(raw, dict) else raw
        if not isinstance(items, list):
            return ClassificationBatchOutcome(
                predictions={},
                requested_count=len(batch),
                error="classifier output must be a list or an object with an items list",
            )

        predictions: dict[str, Classification] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            key = str(item.get("key", "")).strip()
            if not key:
                continue
            tags = self._normalize_tags(item)
            predictions[key] = Classification(
                key=key,
                tags=tuple(tags),
                domains=tuple(tag for tag in tags if tag.startswith("domain:")),
                topics=tuple(tag for tag in tags if tag.startswith("topic:")),
                confidence=self._normalize_confidence(item.get("confidence")),
            )

        return ClassificationBatchOutcome(predictions=predictions, requested_count=len(batch))

    def _normalize_tags(self, item: dict[str, Any]) -> list[str]:
        tags: list[str] = []
        for value in item.get("tags", []):
            if isinstance(value, str):
                normalized = value.strip()
                if normalized:
                    tags.append(normalized)
        for field, prefix in (("domains", "domain:"), ("topics", "topic:")):
            for value in item.get(field, []):
                if not isinstance(value, str):
                    continue
                normalized = value.strip()
                if not normalized:
                    continue
                tags.append(normalized if normalized.startswith(prefix) else f"{prefix}{normalized}")
        seen: set[str] = set()
        result: list[str] = []
        for tag in tags:
            if tag in seen:
                continue
            seen.add(tag)
            result.append(tag)
        return result

    @staticmethod
    def _normalize_confidence(value: Any) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _batched(self, candidates: list[EnrichmentCandidate]) -> list[list[EnrichmentCandidate]]:
        batch_size = max(1, int(self.config.batch_size))
        return [candidates[index : index + batch_size] for index in range(0, len(candidates), batch_size)]
