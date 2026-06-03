from __future__ import annotations

import json
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from .proof import parse_markdown_entry, run_deterministic_proof
from .query import recall_candidates
from .storage import MemoryStore


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CORPUS_DIR = ROOT / "benchmark" / "corpus"
DEFAULT_QUESTIONS_PATH = ROOT / "benchmark" / "questions.json"


def run_benchmark(
    *,
    corpus_dir: Path | None = None,
    questions_path: Path | None = None,
    include_hybrid: bool = False,
) -> dict[str, Any]:
    corpus_root = corpus_dir or DEFAULT_CORPUS_DIR
    questions_file = questions_path or DEFAULT_QUESTIONS_PATH
    entries = [parse_markdown_entry(path) for path in sorted(corpus_root.glob("*.md"))]
    questions = json.loads(questions_file.read_text(encoding="utf-8"))

    runtime_dir = Path(tempfile.mkdtemp(prefix="agent-memory-bridge-bench-"))
    try:
        store = build_benchmark_store(runtime_dir / "benchmark.db", entries, log_dir=runtime_dir / "logs")
        semantic_store = (
            build_benchmark_store(runtime_dir / "benchmark-semantic.db", entries, log_dir=runtime_dir / "semantic-logs")
            if include_hybrid
            else None
        )
        hybrid_store = (
            build_benchmark_store(runtime_dir / "benchmark-hybrid.db", entries, log_dir=runtime_dir / "hybrid-logs")
            if include_hybrid
            else None
        )

        retrieval_results = []
        for question in questions:
            result = {
                "id": question["id"],
                "query": question["query"],
                "expected_title": question["expected_title"],
                "relevant_titles": normalize_relevant_titles(question),
                "memory": run_memory_benchmark(store, question, retrieval_mode="lexical"),
                "file_scan": run_file_benchmark(entries, question),
            }
            if include_hybrid:
                assert semantic_store is not None
                assert hybrid_store is not None
                result["semantic"] = run_memory_benchmark(semantic_store, question, retrieval_mode="semantic")
                result["hybrid"] = run_memory_benchmark(hybrid_store, question, retrieval_mode="hybrid")
            retrieval_results.append(result)

        proof_report = run_deterministic_proof(corpus_dir=corpus_root, questions_path=questions_file)
        retrieval_summary = build_retrieval_summary(retrieval_results)
        semantic_summary = build_mode_summary(retrieval_results, "semantic") if include_hybrid else None
        hybrid_summary = build_mode_summary(retrieval_results, "hybrid") if include_hybrid else None
        hybrid_comparison = build_hybrid_comparison_summary(retrieval_results) if include_hybrid else None
        extra_summary = {}
        if semantic_summary is not None:
            extra_summary.update({f"semantic_{key}": value for key, value in semantic_summary.items()})
        if hybrid_summary is not None:
            extra_summary.update({f"hybrid_{key}": value for key, value in hybrid_summary.items()})
        if hybrid_comparison is not None:
            extra_summary.update({f"hybrid_{key}": value for key, value in hybrid_comparison.items()})
        return {
            "summary": {
                "question_count": retrieval_summary["question_count"],
                "memory_precision_at_1": retrieval_summary["memory_precision_at_1"],
                "memory_precision_at_3": retrieval_summary["memory_precision_at_3"],
                "memory_recall_at_1": retrieval_summary["memory_recall_at_1"],
                "memory_recall_at_3": retrieval_summary["memory_recall_at_3"],
                "memory_mrr": retrieval_summary["memory_mrr"],
                "memory_expected_top1_accuracy": retrieval_summary["memory_expected_top1_accuracy"],
                "memory_avg_latency_ms": retrieval_summary["memory_avg_latency_ms"],
                "file_scan_precision_at_1": retrieval_summary["file_scan_precision_at_1"],
                "file_scan_precision_at_3": retrieval_summary["file_scan_precision_at_3"],
                "file_scan_recall_at_1": retrieval_summary["file_scan_recall_at_1"],
                "file_scan_recall_at_3": retrieval_summary["file_scan_recall_at_3"],
                "file_scan_mrr": retrieval_summary["file_scan_mrr"],
                "file_scan_expected_top1_accuracy": retrieval_summary["file_scan_expected_top1_accuracy"],
                "file_scan_avg_latency_ms": retrieval_summary["file_scan_avg_latency_ms"],
                "signal_correctness_passed": proof_report["summary"]["signal_correctness_passed"],
                "relation_metadata_passed": proof_report["summary"]["relation_metadata_passed"],
                "duplicate_suppression_rate": proof_report["summary"]["duplicate_suppression_rate"],
                **extra_summary,
            },
            "retrieval_summary": retrieval_summary,
            "semantic_summary": semantic_summary,
            "hybrid_summary": hybrid_summary,
            "hybrid_comparison_summary": hybrid_comparison,
            "deterministic_proof_summary": proof_report["summary"],
            "results": retrieval_results,
            "deterministic_proof": proof_report,
        }
    finally:
        shutil.rmtree(runtime_dir, ignore_errors=True)


def build_benchmark_store(db_path: Path, entries: list[dict[str, Any]], *, log_dir: Path) -> MemoryStore:
    store = MemoryStore(db_path, log_dir=log_dir)
    for entry in entries:
        store.store(**entry)
    return store


def run_memory_benchmark(
    store: MemoryStore,
    question: dict[str, Any],
    *,
    retrieval_mode: str | None = None,
) -> dict[str, Any]:
    relevant_titles = normalize_relevant_titles(question)
    started = time.perf_counter_ns()
    items = recall_candidates(
        store,
        namespace="bench",
        query=question["query"],
        limit=max(3, len(relevant_titles)),
        kind=question.get("kind"),
        signal_status=None,
        tags_any=None,
        session_id=None,
        actor=None,
        correlation_id=None,
        since=None,
        retrieval_mode=retrieval_mode,
    )
    elapsed_ms = round((time.perf_counter_ns() - started) / 1_000_000, 3)
    top_titles = [item["title"] for item in items if item.get("title")]
    return {
        "hit": bool(top_titles) and top_titles[0] in relevant_titles,
        "expected_top1": bool(top_titles) and top_titles[0] == question["expected_title"],
        "latency_ms": elapsed_ms,
        "count": len(items),
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
        "memory_recall_at_1": average(
            [recall_at_k(result["memory"]["first_relevant_rank"], 1) for result in results]
        ),
        "memory_recall_at_3": average(
            [recall_at_k(result["memory"]["first_relevant_rank"], 3) for result in results]
        ),
        "memory_mrr": average(
            [reciprocal_rank(result["memory"]["first_relevant_rank"]) for result in results]
        ),
        "memory_avg_latency_ms": average(memory_latency),
        "file_scan_hit_count": sum(1 for result in results if result["file_scan"]["hit"]),
        "file_scan_expected_top1_count": sum(1 for result in results if result["file_scan"]["expected_top1"]),
        "file_scan_expected_top1_accuracy": average(
            [1.0 if result["file_scan"]["expected_top1"] else 0.0 for result in results]
        ),
        "file_scan_precision_at_1": average(file_p1),
        "file_scan_precision_at_3": average(file_p3),
        "file_scan_recall_at_1": average(
            [recall_at_k(result["file_scan"]["first_relevant_rank"], 1) for result in results]
        ),
        "file_scan_recall_at_3": average(
            [recall_at_k(result["file_scan"]["first_relevant_rank"], 3) for result in results]
        ),
        "file_scan_mrr": average(
            [reciprocal_rank(result["file_scan"]["first_relevant_rank"]) for result in results]
        ),
        "file_scan_avg_latency_ms": average(file_latency),
    }


def build_mode_summary(results: list[dict[str, Any]], mode: str) -> dict[str, Any]:
    mode_p1 = [result[mode]["precision_at_1"] for result in results if mode in result]
    mode_p3 = [result[mode]["precision_at_3"] for result in results if mode in result]
    mode_latency = [result[mode]["latency_ms"] for result in results if mode in result]
    return {
        "hit_count": sum(1 for result in results if mode in result and result[mode]["hit"]),
        "expected_top1_count": sum(1 for result in results if mode in result and result[mode]["expected_top1"]),
        "expected_top1_accuracy": average(
            [1.0 if result[mode]["expected_top1"] else 0.0 for result in results if mode in result]
        ),
        "precision_at_1": average(mode_p1),
        "precision_at_3": average(mode_p3),
        "recall_at_1": average(
            [recall_at_k(result[mode]["first_relevant_rank"], 1) for result in results if mode in result]
        ),
        "recall_at_3": average(
            [recall_at_k(result[mode]["first_relevant_rank"], 3) for result in results if mode in result]
        ),
        "mrr": average(
            [reciprocal_rank(result[mode]["first_relevant_rank"]) for result in results if mode in result]
        ),
        "avg_latency_ms": average(mode_latency),
    }


def build_hybrid_comparison_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    comparable = [result for result in results if "hybrid" in result and "semantic" in result]
    improved = 0
    degraded = 0
    preserved_top1 = 0
    semantic_only_visible = 0
    semantic_only_top1 = 0
    for result in comparable:
        memory_rank = first_relevant_rank(result["memory"]["top_titles"], result["relevant_titles"])
        hybrid_rank = first_relevant_rank(result["hybrid"]["top_titles"], result["relevant_titles"])
        if result["memory"]["top_title"] == result["hybrid"]["top_title"]:
            preserved_top1 += 1
        if rank_is_better(hybrid_rank, memory_rank):
            improved += 1
        elif rank_is_better(memory_rank, hybrid_rank):
            degraded += 1

        memory_titles = set(result["memory"]["top_titles"])
        semantic_only_titles = [title for title in result["hybrid"]["top_titles"] if title not in memory_titles]
        if semantic_only_titles:
            semantic_only_visible += 1
        if result["hybrid"]["top_title"] in semantic_only_titles:
            semantic_only_top1 += 1

    count = len(comparable)
    return {
        "comparison_question_count": count,
        "preserved_lexical_top1_count": preserved_top1,
        "improved_relevant_rank_count": improved,
        "degraded_relevant_rank_count": degraded,
        "semantic_only_visible_count": semantic_only_visible,
        "semantic_only_top1_count": semantic_only_top1,
        "semantic_only_visible_rate": round(semantic_only_visible / count, 3) if count else 0.0,
    }


def rank_is_better(left: int | None, right: int | None) -> bool:
    if left is None:
        return False
    if right is None:
        return True
    return left < right


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


def recall_at_k(rank: int | None, k: int) -> float:
    if k <= 0:
        raise ValueError("k must be greater than 0")
    if rank is None:
        return 0.0
    return 1.0 if rank <= k else 0.0


def reciprocal_rank(rank: int | None) -> float:
    if rank is None or rank <= 0:
        return 0.0
    return round(1.0 / rank, 3)


def tokenize(text: str) -> list[str]:
    return [token.lower() for token in text.replace(".", " ").split() if token.strip()]


def average(values: list[float]) -> float:
    if not values:
        return 0.0
    return round(sum(values) / len(values), 3)
