from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any, Literal

from .command_provider import CommandLimits, CommandProviderError, run_json_command

ClassifierMode = Literal["off", "shadow", "assist"]
ClassifierProvider = Literal["command"]
ALLOWED_CLASSIFIER_TAG_PREFIXES = ("domain:", "topic:")


@dataclass(frozen=True, slots=True)
class ClassifierConfig:
    mode: ClassifierMode = "off"
    provider: ClassifierProvider = "command"
    command: str | tuple[str, ...] = ""
    timeout_seconds: float = 10.0
    batch_size: int = 16
    minimum_confidence: float = 0.6
    trusted_shell: bool = False
    max_input_bytes: int = 1_000_000
    max_output_bytes: int = 2_000_000
    max_stderr_bytes: int = 65_536
    env_allowlist: tuple[str, ...] = ()


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
    classifier_suggested_tags: tuple[str, ...] = ()
    confidence: float | None = None


@dataclass(frozen=True, slots=True)
class ClassificationBatchOutcome:
    predictions: dict[str, Classification]
    requested_count: int
    error: str | None = None


class EnrichmentClassifier:
    def __init__(self, config: ClassifierConfig) -> None:
        if not math.isfinite(config.minimum_confidence) or not 0.0 <= config.minimum_confidence <= 1.0:
            raise ValueError("minimum_confidence must be a finite value between 0 and 1")
        self.config = config

    @property
    def active(self) -> bool:
        command_configured = (
            bool(self.config.command.strip()) if isinstance(self.config.command, str) else bool(self.config.command)
        )
        return self.config.mode != "off" and command_configured

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
        confidence = classification.confidence
        if confidence is None or not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            return []
        if confidence < self.config.minimum_confidence:
            return []
        return self._policy_tags(list(classification.tags))

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
            completed = run_json_command(
                self.config.command,
                payload,
                timeout_seconds=self.config.timeout_seconds,
                trusted_shell=self.config.trusted_shell,
                limits=CommandLimits(
                    max_input_bytes=self.config.max_input_bytes,
                    max_stdout_bytes=self.config.max_output_bytes,
                    max_stderr_bytes=self.config.max_stderr_bytes,
                ),
                env_allowlist=self.config.env_allowlist,
            )
        except CommandProviderError as exc:
            return ClassificationBatchOutcome(
                predictions={},
                requested_count=len(batch),
                error=f"classifier command failed: {exc}",
            )

        if completed.returncode != 0:
            return ClassificationBatchOutcome(
                predictions={},
                requested_count=len(batch),
                error=(
                    f"classifier command failed with exit code {completed.returncode} "
                    f"(fingerprint={completed.fingerprint})"
                ),
            )

        try:
            raw = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            return ClassificationBatchOutcome(
                predictions={},
                requested_count=len(batch),
                error=f"classifier returned invalid JSON ({exc.__class__.__name__})",
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
            classifier_suggested_tags = self._normalize_tags(item)
            tags = self._policy_tags(classifier_suggested_tags)
            predictions[key] = Classification(
                key=key,
                tags=tuple(tags),
                domains=tuple(tag for tag in tags if tag.startswith("domain:")),
                topics=tuple(tag for tag in tags if tag.startswith("topic:")),
                classifier_suggested_tags=tuple(classifier_suggested_tags),
                confidence=self._normalize_confidence(item.get("confidence")),
            )

        return ClassificationBatchOutcome(predictions=predictions, requested_count=len(batch))

    def _normalize_tags(self, item: dict[str, Any]) -> list[str]:
        tags: list[str] = []
        raw_tags = item.get("tags", [])
        tag_values = raw_tags if isinstance(raw_tags, (list, tuple)) else ()
        for value in tag_values:
            if isinstance(value, str):
                normalized = value.strip()
                if normalized:
                    tags.append(normalized)
        for field, prefix in (("domains", "domain:"), ("topics", "topic:")):
            raw_values = item.get(field, [])
            values = raw_values if isinstance(raw_values, (list, tuple)) else ()
            for value in values:
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
    def _policy_tags(tags: list[str]) -> list[str]:
        return [
            tag
            for tag in tags
            if any(tag.startswith(prefix) and len(tag) > len(prefix) for prefix in ALLOWED_CLASSIFIER_TAG_PREFIXES)
        ]

    @staticmethod
    def _normalize_confidence(value: Any) -> float | None:
        if value is None:
            return None
        try:
            normalized = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(normalized) or not 0.0 <= normalized <= 1.0:
            return None
        return normalized

    def _batched(self, candidates: list[EnrichmentCandidate]) -> list[list[EnrichmentCandidate]]:
        batch_size = max(1, int(self.config.batch_size))
        return [candidates[index : index + batch_size] for index in range(0, len(candidates), batch_size)]
