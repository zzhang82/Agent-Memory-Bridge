from __future__ import annotations

import json
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from agent_mem_bridge.storage import MemoryStore


ROOT = Path(__file__).resolve().parents[1]
CORPUS_DIR = ROOT / "benchmark" / "corpus"
QUESTIONS_PATH = ROOT / "benchmark" / "questions.json"
REPORT_PATH = ROOT / "benchmark" / "latest-report.json"


def main() -> None:
    entries = [parse_markdown_entry(path) for path in sorted(CORPUS_DIR.glob("*.md"))]
    questions = json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))

    runtime_dir = Path(tempfile.mkdtemp(prefix="agent-memory-bridge-bench-"))
    try:
        store = MemoryStore(runtime_dir / "benchmark.db", log_dir=runtime_dir / "logs")
        for entry in entries:
            store.store(**entry)

        results = []
        for question in questions:
            memory_result = run_memory_benchmark(store, question)
            file_result = run_file_benchmark(entries, question)
            results.append(
                {
                    "id": question["id"],
                    "query": question["query"],
                    "expected_title": question["expected_title"],
                    "memory": memory_result,
                    "file_scan": file_result,
                }
            )

        summary = build_summary(results)
        report = {
            "summary": summary,
            "results": results,
        }
        REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))
    finally:
        shutil.rmtree(runtime_dir, ignore_errors=True)


def parse_markdown_entry(path: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8")
    frontmatter, content = split_frontmatter(raw)
    return {
        "namespace": frontmatter["namespace"],
        "kind": frontmatter["kind"],
        "title": frontmatter.get("title"),
        "tags": frontmatter.get("tags", []),
        "actor": frontmatter.get("actor"),
        "session_id": frontmatter.get("session_id"),
        "correlation_id": frontmatter.get("correlation_id"),
        "source_app": frontmatter.get("source_app"),
        "content": content.strip(),
    }


def split_frontmatter(raw: str) -> tuple[dict[str, Any], str]:
    lines = raw.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("benchmark corpus file must start with YAML frontmatter")

    frontmatter_lines: list[str] = []
    body_start = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            body_start = index + 1
            break
        frontmatter_lines.append(lines[index])

    if body_start is None:
        raise ValueError("frontmatter block is not closed")

    frontmatter = parse_simple_yaml(frontmatter_lines)
    content = "\n".join(lines[body_start:])
    return frontmatter, content


def parse_simple_yaml(lines: list[str]) -> dict[str, Any]:
    data: dict[str, Any] = {}
    current_list_key: str | None = None
    for raw_line in lines:
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            continue

        if stripped.startswith("- "):
            if current_list_key is None:
                raise ValueError(f"list item without key: {raw_line}")
            data.setdefault(current_list_key, []).append(stripped[2:].strip())
            continue

        current_list_key = None
        if ":" not in stripped:
            raise ValueError(f"unsupported frontmatter line: {raw_line}")
        key, value = stripped.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not value:
            data[key] = []
            current_list_key = key
        else:
            data[key] = value
    return data


def run_memory_benchmark(store: MemoryStore, question: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter_ns()
    response = store.recall(
        namespace="bench",
        query=question["query"],
        kind=question.get("kind"),
        limit=3,
    )
    elapsed_ms = round((time.perf_counter_ns() - started) / 1_000_000, 3)
    top_title = response["items"][0]["title"] if response["items"] else None
    return {
        "hit": top_title == question["expected_title"],
        "latency_ms": elapsed_ms,
        "count": response["count"],
        "top_title": top_title,
    }


def run_file_benchmark(entries: list[dict[str, Any]], question: dict[str, Any]) -> dict[str, Any]:
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
    top_title = scored[0][1] if scored else None
    return {
        "hit": top_title == question["expected_title"],
        "latency_ms": elapsed_ms,
        "count": len(scored),
        "top_title": top_title,
    }


def tokenize(text: str) -> list[str]:
    return [token.lower() for token in text.replace(".", " ").split() if token.strip()]


def build_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    memory_hits = sum(1 for result in results if result["memory"]["hit"])
    file_hits = sum(1 for result in results if result["file_scan"]["hit"])
    memory_avg_ms = round(sum(result["memory"]["latency_ms"] for result in results) / len(results), 3)
    file_avg_ms = round(sum(result["file_scan"]["latency_ms"] for result in results) / len(results), 3)
    return {
        "question_count": len(results),
        "memory_hits": memory_hits,
        "file_hits": file_hits,
        "memory_avg_latency_ms": memory_avg_ms,
        "file_avg_latency_ms": file_avg_ms,
    }


if __name__ == "__main__":
    main()

