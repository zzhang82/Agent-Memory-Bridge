from pathlib import Path

from agent_mem_bridge.benchmarking import (
    build_retrieval_summary,
    DEFAULT_CORPUS_DIR,
    DEFAULT_QUESTIONS_PATH,
    first_relevant_rank,
    precision_at_k,
    run_benchmark,
)


def test_precision_at_k_and_rank_helpers() -> None:
    top_titles = ["A", "B", "C"]
    relevant = ["B", "C"]

    assert precision_at_k(top_titles, relevant, 1) == 0.0
    assert precision_at_k(top_titles, relevant, 3) == 0.667
    assert precision_at_k(["B"], relevant, 3) == 0.333
    assert first_relevant_rank(top_titles, relevant) == 2


def test_build_retrieval_summary_aggregates_precision_and_latency() -> None:
    summary = build_retrieval_summary(
        [
            {
                "memory": {
                    "hit": True,
                    "expected_top1": True,
                    "precision_at_1": 1.0,
                    "precision_at_3": 0.667,
                    "latency_ms": 1.0,
                },
                "file_scan": {
                    "hit": False,
                    "expected_top1": False,
                    "precision_at_1": 0.0,
                    "precision_at_3": 0.333,
                    "latency_ms": 3.0,
                },
            },
            {
                "memory": {
                    "hit": False,
                    "expected_top1": False,
                    "precision_at_1": 0.0,
                    "precision_at_3": 0.333,
                    "latency_ms": 2.0,
                },
                "file_scan": {
                    "hit": True,
                    "expected_top1": True,
                    "precision_at_1": 1.0,
                    "precision_at_3": 0.667,
                    "latency_ms": 5.0,
                },
            },
        ]
    )

    assert summary["question_count"] == 2
    assert summary["memory_hit_count"] == 1
    assert summary["memory_expected_top1_count"] == 1
    assert summary["memory_expected_top1_accuracy"] == 0.5
    assert summary["memory_precision_at_1"] == 0.5
    assert summary["memory_precision_at_3"] == 0.5
    assert summary["memory_avg_latency_ms"] == 1.5
    assert summary["file_scan_hit_count"] == 1
    assert summary["file_scan_expected_top1_count"] == 1
    assert summary["file_scan_expected_top1_accuracy"] == 0.5
    assert summary["file_scan_precision_at_1"] == 0.5
    assert summary["file_scan_precision_at_3"] == 0.5
    assert summary["file_scan_avg_latency_ms"] == 4.0


def test_run_benchmark_returns_report_with_precision_and_proof(tmp_path: Path) -> None:
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()
    (corpus_dir / "01-storage.md").write_text(
        """---
namespace: bench
kind: memory
title: Storage Decision
tags:
  - topic:storage
actor: benchmark
session_id: bench-1
correlation_id: task-storage
source_app: benchmark
---

Use SQLite WAL mode for the bridge storage layer.
""",
        encoding="utf-8",
    )
    (corpus_dir / "02-signal.md").write_text(
        """---
namespace: bench
kind: signal
title: Review Handoff
tags:
  - handoff
  - review
actor: benchmark
session_id: bench-2
correlation_id: handoff-review
source_app: benchmark
---

Reviewer needed for the API handoff.
""",
        encoding="utf-8",
    )
    questions_path = tmp_path / "questions.json"
    questions_path.write_text(
        """[
  {
    "id": "q1",
    "query": "SQLite WAL",
    "expected_title": "Storage Decision",
    "relevant_titles": ["Storage Decision"],
    "kind": "memory"
  },
  {
    "id": "q2",
    "query": "API handoff",
    "expected_title": "Review Handoff",
    "relevant_titles": ["Review Handoff"],
    "kind": "signal"
  }
]""",
        encoding="utf-8",
    )

    report = run_benchmark(corpus_dir=corpus_dir, questions_path=questions_path)

    assert report["summary"]["question_count"] == 2
    assert "memory_precision_at_1" in report["summary"]
    assert "memory_expected_top1_accuracy" in report["summary"]
    assert "file_scan_precision_at_3" in report["summary"]
    assert "signal_correctness_passed" in report["summary"]
    assert "deterministic_proof_summary" in report
    assert len(report["results"]) == 2
    assert report["results"][0]["memory"]["top_titles"][0] == "Storage Decision"


def test_repo_benchmark_fixtures_capture_top1_ranking_improvements() -> None:
    report = run_benchmark(corpus_dir=DEFAULT_CORPUS_DIR, questions_path=DEFAULT_QUESTIONS_PATH)
    by_id = {item["id"]: item for item in report["results"]}

    assert by_id["q1"]["memory"]["top_title"] == "Storage Decision"
    assert by_id["q3"]["memory"]["top_title"] == "Codex Context Bridge"
    assert by_id["q4"]["memory"]["top_title"] == "Review Handoff"
    assert by_id["q7"]["memory"]["top_title"] == "Review Handoff"
    assert by_id["q8"]["memory"]["top_title"] == "Codex Context Bridge"
    assert report["retrieval_summary"]["memory_expected_top1_count"] == 8
    assert report["retrieval_summary"]["memory_expected_top1_accuracy"] == 1.0
