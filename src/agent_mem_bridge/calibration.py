from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .classifier import ClassifierConfig, EnrichmentCandidate, EnrichmentClassifier
from .enrichment_rules import infer_keyword_tags


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REVIEWED_SAMPLES_PATH = ROOT / "benchmark" / "classifier-reviewed-samples.json"


def run_classifier_calibration(
    *,
    reviewed_samples_path: Path | None = None,
    command: str = "",
    batch_size: int = 16,
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    samples_path = reviewed_samples_path or DEFAULT_REVIEWED_SAMPLES_PATH
    samples = json.loads(samples_path.read_text(encoding="utf-8"))

    classifier = EnrichmentClassifier(
        ClassifierConfig(
            mode="shadow",
            command=command,
            batch_size=batch_size,
            timeout_seconds=timeout_seconds,
        )
    )
    predictions = classifier.classify(
        [
            EnrichmentCandidate(
                key=str(sample["id"]),
                text=str(sample["text"]),
            )
            for sample in samples
        ]
    )

    results: list[dict[str, Any]] = []
    classifier_better = 0
    fallback_better = 0
    tied = 0
    for sample in samples:
        expected = normalize_tags(sample.get("expected_tags", []))
        fallback = normalize_tags(infer_keyword_tags(str(sample["text"])))
        predicted = normalize_tags(list(predictions.predictions.get(str(sample["id"])).tags) if predictions.predictions.get(str(sample["id"])) else [])

        fallback_score = tag_match_score(expected, fallback)
        classifier_score = tag_match_score(expected, predicted)
        if classifier_score > fallback_score:
            winner = "classifier"
            classifier_better += 1
        elif fallback_score > classifier_score:
            winner = "fallback"
            fallback_better += 1
        else:
            winner = "tie"
            tied += 1

        results.append(
            {
                "id": sample["id"],
                "text": sample["text"],
                "expected_tags": expected,
                "fallback_tags": fallback,
                "classifier_tags": predicted,
                "fallback_score": fallback_score,
                "classifier_score": classifier_score,
                "winner": winner,
                "classifier_missing": [tag for tag in expected if tag not in predicted],
                "classifier_extra": [tag for tag in predicted if tag not in expected],
                "fallback_missing": [tag for tag in expected if tag not in fallback],
                "fallback_extra": [tag for tag in fallback if tag not in expected],
            }
        )

    sample_count = len(results)
    classifier_exact = sum(1 for result in results if result["classifier_tags"] == result["expected_tags"])
    fallback_exact = sum(1 for result in results if result["fallback_tags"] == result["expected_tags"])
    return {
        "summary": {
            "sample_count": sample_count,
            "classifier_prediction_count": len(predictions.predictions),
            "classifier_error": predictions.error,
            "classifier_exact_match_count": classifier_exact,
            "classifier_exact_match_rate": rate(classifier_exact, sample_count),
            "fallback_exact_match_count": fallback_exact,
            "fallback_exact_match_rate": rate(fallback_exact, sample_count),
            "classifier_better_count": classifier_better,
            "fallback_better_count": fallback_better,
            "tie_count": tied,
        },
        "results": results,
    }


def write_classifier_calibration_report(
    *,
    report_path: Path,
    reviewed_samples_path: Path | None = None,
    command: str = "",
    batch_size: int = 16,
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    report = run_classifier_calibration(
        reviewed_samples_path=reviewed_samples_path,
        command=command,
        batch_size=batch_size,
        timeout_seconds=timeout_seconds,
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def normalize_tags(tags: list[str]) -> list[str]:
    seen: set[str] = set()
    normalized: list[str] = []
    for tag in tags:
        compact = str(tag).strip()
        if not compact or compact in seen:
            continue
        seen.add(compact)
        normalized.append(compact)
    return normalized


def tag_match_score(expected: list[str], actual: list[str]) -> float:
    if not expected and not actual:
        return 1.0
    expected_set = set(expected)
    actual_set = set(actual)
    true_positive = len(expected_set & actual_set)
    false_positive = len(actual_set - expected_set)
    false_negative = len(expected_set - actual_set)
    denominator = (2 * true_positive) + false_positive + false_negative
    if denominator == 0:
        return 0.0
    return round((2 * true_positive) / denominator, 3)


def rate(count: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round(count / total, 3)
