from __future__ import annotations

import json
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from .proof import parse_markdown_entry, run_deterministic_proof
from .storage import MemoryStore


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CORPUS_DIR = ROOT / "benchmark" / "corpus"
DEFAULT_QUESTIONS_PATH = ROOT / "benchmark" / "questions.json"


def run_benchmark(
    *,
    corpus_dir: Path | None = None,
    questions_path: Path | None = None,
) -> dict[str, Any]:
    corpus_root = corpus_dir or DEFAULT_CORPUS_DIR
    questions_file = questions_path or DEFAULT_QUESTIONS_PATH
    entries = [parse_markdown_entry(path) for path in sorted(corpus_root.glob("*.md"))]
    questions = json.loads(questions_file.read_text(encoding="utf-8"))

    runtime_dir = Path(tempfile.mkdtemp(prefix="agent-memory-bridge-bench-"))
    try:
        store = MemoryStore(runtime_dir / "benchmark.db", log_dir=runtime_dir / "logs")
        for entry in entries:
            store.store(**entry)

        retrieval_results = []
        for question in questions:
            retrieval_results.append(
                {
                    "id": question["id"],
                    "query": question["query"],
                    "expected_title": question["expected_title"],
                    "relevant_titles": normalize_relevant_titles(question),
                    "memory": run_memory_benchmark(store, question),
                    "file_scan": run_file_benchmark(entries, question),
                }
            )

        proof_report = run_deterministic_proof(corpus_dir=corpus_root, questions_path=questions_file)
        retrieval_summary = build_retrieval_summary(retrieval_results)
        return {
            "summary": {
                "question_count": retrieval_summary["question_count"],
                "memory_precision_at_1": retrieval_summary["memory_precision_at_1"],
                "memory_precision_at_3": retrieval_summary["memory_precision_at_3"],
                "memory_expected_top1_accuracy": retrieval_summary["memory_expected_top1_accuracy"],
                "memory_avg_latency_ms": retrieval_summary["memory_avg_latency_ms"],
                "file_scan_precision_at_1": retrieval_summary["file_scan_precision_at_1"],
                "file_scan_precision_at_3": retrieval_summary["file_scan_precision_at_3"],
                "file_scan_expected_top1_accuracy": retrieval_summary["file_scan_expected_top1_accuracy"],
                "file_scan_avg_latency_ms": retrieval_summary["file_scan_avg_latency_ms"],
                "signal_correctness_passed": proof_report["summary"]["signal_correctness_passed"],
                "duplicate_suppression_rate": proof_report["summary"]["duplicate_suppression_rate"],
            },
            "retrieval_summary": retrieval_summary,
            "deterministic_proof_summary": proof_report["summary"],
            "results": retrieval_results,
            "deterministic_proof": proof_report,
        }
    finally:
        shutil.rmtree(runtime_dir, ignore_errors=True)


def run_memory_benchmark(store: MemoryStore, question: dict[str, Any]) -> dict[str, Any]:
    relevant_titles = normalize_relevant_titles(question)
    started = time.perf_counter_ns()
    response = store.recall(
        namespace="bench",
        query=question["query"],
        kind=question.get("kind"),
        limit=max(3, len(relevant_titles)),
    )
    elapsed_ms = round((time.perf_counter_ns() - started) / 1_000_000, 3)
    top_titles = [item["title"] for item in response["items"] if item.get("title")]
    return {
        "hit": bool(top_titles) and top_titles[0] in relevant_titles,
        "expected_top1": bool(top_titles) and top_titles[0] == question["expected_title"],
        "latency_ms": elapsed_ms,
        "count": response["count"],
        "top_title": top_titles[0] if top_titles else None,
        "top_titles": top_titles,
        "precision_at_1": precision_at_k(top_titles, relevant_titles, 1),
        "precision_at_3": precision_at_k(top_titles, relevant_titles, 3),
        "first_relevant_rank": first_relevant_rank(top_titles, relevant_titles),
    }


def run_file_benchmark(entries: list[dict[str, Any]], question: dict[str, Any]) -> dict[str, Any]:
    relevant_titles = normalize_relevant_titles(question)
    started = time.perf_counter_ns()
    tokens = tokenize(question["query"])
    scored: list[tuple[int, str | None]] = []
    for entry in entries:
        haystack = " ".join(
            [
                entry.get("title") or "",
                entry["content"],
                " ".join(entry.get("tags") or []),
            ]
        ).lower()
        score = sum(haystack.count(token) for token in tokens)
        if question.get("kind") and entry.get("kind") != question["kind"]:
            continue
        if score > 0:
            scored.append((score, entry.get("title")))
    scored.sort(key=lambda item: item[0], reverse=True)
    elapsed_ms = round((time.perf_counter_ns() - started) / 1_000_000, 3)
    top_titles = [title for _, title in scored if title]
    return {
        "hit": bool(top_titles) and top_titles[0] in relevant_titles,
        "expected_top1": bool(top_titles) and top_titles[0] == question["expected_title"],
        "latency_ms": elapsed_ms,
        "count": len(scored),
        "top_title": top_titles[0] if top_titles else None,
        "top_titles": top_titles,
        "precision_at_1": precision_at_k(top_titles, relevant_titles, 1),
        "precision_at_3": precision_at_k(top_titles, relevant_titles, 3),
        "first_relevant_rank": first_relevant_rank(top_titles, relevant_titles),
    }


def build_retrieval_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    memory_p1 = [result["memory"]["precision_at_1"] for result in results]
    memory_p3 = [result["memory"]["precision_at_3"] for result in results]
    memory_latency = [result["memory"]["latency_ms"] for result in results]
    file_p1 = [result["file_scan"]["precision_at_1"] for result in results]
    file_p3 = [result["file_scan"]["precision_at_3"] for result in results]
    file_latency = [result["file_scan"]["latency_ms"] for result in results]
    return {
        "question_count": len(results),
        "memory_hit_count": sum(1 for result in results if result["memory"]["hit"]),
        "memory_expected_top1_count": sum(1 for result in results if result["memory"]["expected_top1"]),
        "memory_expected_top1_accuracy": average(
            [1.0 if result["memory"]["expected_top1"] else 0.0 for result in results]
        ),
        "memory_precision_at_1": average(memory_p1),
        "memory_precision_at_3": average(memory_p3),
        "memory_avg_latency_ms": average(memory_latency),
        "file_scan_hit_count": sum(1 for result in results if result["file_scan"]["hit"]),
        "file_scan_expected_top1_count": sum(1 for result in results if result["file_scan"]["expected_top1"]),
        "file_scan_expected_top1_accuracy": average(
            [1.0 if result["file_scan"]["expected_top1"] else 0.0 for result in results]
        ),
        "file_scan_precision_at_1": average(file_p1),
        "file_scan_precision_at_3": average(file_p3),
        "file_scan_avg_latency_ms": average(file_latency),
    }


def normalize_relevant_titles(question: dict[str, Any]) -> list[str]:
    raw_titles = question.get("relevant_titles") or [question["expected_title"]]
    return [str(title).strip() for title in raw_titles if str(title).strip()]


def precision_at_k(top_titles: list[str], relevant_titles: list[str], k: int) -> float:
    if k <= 0:
        raise ValueError("k must be greater than 0")
    window = top_titles[:k]
    if not top_titles:
        return 0.0
    relevant = set(relevant_titles)
    return round(sum(1 for title in window if title in relevant) / k, 3)


def first_relevant_rank(top_titles: list[str], relevant_titles: list[str]) -> int | None:
    relevant = set(relevant_titles)
    for index, title in enumerate(top_titles, start=1):
        if title in relevant:
            return index
    return None


def tokenize(text: str) -> list[str]:
    return [token.lower() for token in text.replace(".", " ").split() if token.strip()]


def average(values: list[float]) -> float:
    if not values:
        return 0.0
    return round(sum(values) / len(values), 3)
