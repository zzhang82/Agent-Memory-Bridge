from __future__ import annotations

import json
import shutil
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .storage import MemoryStore


ROOT = Path(__file__).resolve().parents[2]
CORPUS_DIR = ROOT / "benchmark" / "corpus"
QUESTIONS_PATH = ROOT / "benchmark" / "questions.json"


def run_deterministic_proof(
    *,
    corpus_dir: Path | None = None,
    questions_path: Path | None = None,
) -> dict[str, Any]:
    corpus_root = corpus_dir or CORPUS_DIR
    questions_file = questions_path or QUESTIONS_PATH
    entries = [parse_markdown_entry(path) for path in sorted(corpus_root.glob("*.md"))]
    questions = json.loads(questions_file.read_text(encoding="utf-8"))

    runtime_dir = Path(tempfile.mkdtemp(prefix="agent-memory-bridge-proof-"))
    try:
        recall_store = MemoryStore(runtime_dir / "proof-recall.db", log_dir=runtime_dir / "logs-recall")
        for entry in entries:
            recall_store.store(**entry)

        signal_report = run_signal_correctness_check(runtime_dir / "signal-check.db")
        latency_report = run_recall_latency_check(recall_store, questions)
        duplicate_report = run_duplicate_suppression_check(runtime_dir / "duplicate-check.db")
        relation_report = run_relation_metadata_check(runtime_dir / "relation-check.db")

        checks = {
            "signal_correctness": signal_report["passed"],
            "recall_latency": latency_report["question_count"] > 0,
            "duplicate_suppression": duplicate_report["suppression_rate"] == 1.0,
            "relation_metadata": relation_report["passed"],
        }
        return {
            "summary": {
                "check_count": len(checks),
                "checks_passed": sum(1 for passed in checks.values() if passed),
                "signal_correctness_passed": signal_report["passed"],
                "relation_metadata_passed": relation_report["passed"],
                "recall_avg_latency_ms": latency_report["avg_latency_ms"],
                "duplicate_suppression_rate": duplicate_report["suppression_rate"],
            },
            "signal_correctness": signal_report,
            "recall_latency": latency_report,
            "duplicate_suppression": duplicate_report,
            "relation_metadata": relation_report,
        }
    finally:
        shutil.rmtree(runtime_dir, ignore_errors=True)


def run_signal_correctness_check(db_path: Path) -> dict[str, Any]:
    store = MemoryStore(db_path, log_dir=db_path.parent / "logs-signal")

    created = store.store(
        namespace="proof:signal",
        content="Reviewer needed for release note review.",
        kind="signal",
        tags=["handoff:review"],
        ttl_seconds=600,
    )
    claim_started = time.perf_counter_ns()
    claimed = store.claim_signal(
        namespace="proof:signal",
        consumer="reviewer-a",
        lease_seconds=120,
        signal_id=created["id"],
    )
    claim_latency_ms = round((time.perf_counter_ns() - claim_started) / 1_000_000, 3)
    extend_started = time.perf_counter_ns()
    extended = store.extend_signal_lease(created["id"], consumer="reviewer-a", lease_seconds=180)
    extend_latency_ms = round((time.perf_counter_ns() - extend_started) / 1_000_000, 3)
    wrong_extend = store.extend_signal_lease(created["id"], consumer="reviewer-b", lease_seconds=180)

    wrong_ack = store.ack_signal(created["id"], consumer="reviewer-b")
    ack_started = time.perf_counter_ns()
    acked = store.ack_signal(created["id"], consumer="reviewer-a")
    ack_latency_ms = round((time.perf_counter_ns() - ack_started) / 1_000_000, 3)
    extend_after_ack = store.extend_signal_lease(created["id"], consumer="reviewer-a", lease_seconds=60)

    expired = store.store(
        namespace="proof:signal",
        content="This handoff expired before any worker picked it up.",
        kind="signal",
        tags=["handoff:expired"],
        expires_at="2000-01-01T00:00:00Z",
    )
    expired_hits = store.recall(namespace="proof:signal", kind="signal", signal_status="expired", limit=10)
    expired_ack = store.ack_signal(expired["id"])

    lease = store.store(
        namespace="proof:signal",
        content="Lease should be reclaimable after expiry.",
        kind="signal",
        tags=["handoff:lease"],
        ttl_seconds=600,
    )
    store.claim_signal(namespace="proof:signal", consumer="worker-a", lease_seconds=120, signal_id=lease["id"])
    force_expire_lease(store, lease["id"])
    expired_extend = store.extend_signal_lease(lease["id"], consumer="worker-a", lease_seconds=120)
    reclaim_started = time.perf_counter_ns()
    reclaimed = store.claim_signal(namespace="proof:signal", consumer="worker-b", lease_seconds=120, signal_id=lease["id"])
    reclaim_latency_ms = round((time.perf_counter_ns() - reclaim_started) / 1_000_000, 3)

    fairness_stale = store.store(
        namespace="proof:signal",
        content="Stale worker-owned review signal.",
        kind="signal",
        tags=["handoff:review", "proof:fairness"],
        ttl_seconds=600,
    )
    fairness_fresh = store.store(
        namespace="proof:signal",
        content="Fresh pending review signal.",
        kind="signal",
        tags=["handoff:review", "proof:fairness"],
        ttl_seconds=600,
    )
    store.claim_signal(
        namespace="proof:signal",
        consumer="worker-fair",
        lease_seconds=60,
        signal_id=fairness_stale["id"],
    )
    force_expire_lease(store, fairness_stale["id"])
    fair_claim = store.claim_signal(
        namespace="proof:signal",
        consumer="worker-fair",
        lease_seconds=60,
        tags_any=["proof:fairness"],
    )

    capped_expiry = datetime.now(UTC) + timedelta(seconds=45)
    capped = store.store(
        namespace="proof:signal",
        content="Lease should stop at the hard signal expiry.",
        kind="signal",
        tags=["handoff:capped"],
        expires_at=capped_expiry.isoformat(),
    )
    store.claim_signal(namespace="proof:signal", consumer="worker-c", lease_seconds=10, signal_id=capped["id"])
    capped_extend = store.extend_signal_lease(capped["id"], consumer="worker-c", lease_seconds=600)

    checks = {
        "claim_sets_claimed_state": claimed["claimed"] and claimed["item"]["signal_status"] == "claimed",
        "owner_can_extend_lease": extended["extended"] is True and extended["item"]["claimed_by"] == "reviewer-a",
        "extend_rejects_wrong_consumer": wrong_extend["extended"] is False and wrong_extend["reason"] == "claimed-by-other",
        "ack_rejects_wrong_consumer": wrong_ack["acked"] is False and wrong_ack["reason"] == "claimed-by-other",
        "ack_marks_completion": acked["acked"] is True and acked["item"]["signal_status"] == "acked",
        "acked_signal_cannot_extend": extend_after_ack["extended"] is False and extend_after_ack["reason"] == "already-acked",
        "expired_signal_filters_as_expired": any(item["id"] == expired["id"] for item in expired_hits["items"]),
        "expired_signal_cannot_be_acked": expired_ack["acked"] is False and expired_ack["reason"] == "expired",
        "expired_lease_cannot_be_extended": expired_extend["extended"] is False and expired_extend["reason"] == "lease-expired",
        "stale_lease_can_be_reclaimed": reclaimed["claimed"] is True and reclaimed["item"]["claimed_by"] == "worker-b",
        "fair_claim_avoids_same_consumer_reclaim_bias": fair_claim["claimed"] is True and fair_claim["signal_id"] == fairness_fresh["id"],
        "hard_expiry_caps_extended_lease": capped_extend["extended"] is True and capped_extend["lease_expires_at"] == capped["expires_at"],
    }

    return {
        "passed": all(checks.values()),
        "checks": checks,
        "latency_ms": {
            "claim": claim_latency_ms,
            "extend": extend_latency_ms,
            "ack": ack_latency_ms,
            "reclaim": reclaim_latency_ms,
        },
    }


def run_recall_latency_check(store: MemoryStore, questions: list[dict[str, Any]]) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for question in questions:
        started = time.perf_counter_ns()
        response = store.recall(
            namespace="bench",
            query=question["query"],
            kind=question.get("kind"),
            limit=3,
        )
        elapsed_ms = round((time.perf_counter_ns() - started) / 1_000_000, 3)
        top_title = response["items"][0]["title"] if response["items"] else None
        results.append(
            {
                "id": question["id"],
                "query": question["query"],
                "expected_title": question["expected_title"],
                "hit": top_title == question["expected_title"],
                "latency_ms": elapsed_ms,
                "count": response["count"],
                "top_title": top_title,
            }
        )

    latencies = [result["latency_ms"] for result in results]
    return {
        "question_count": len(results),
        "hit_count": sum(1 for result in results if result["hit"]),
        "avg_latency_ms": round(sum(latencies) / len(latencies), 3) if latencies else 0.0,
        "max_latency_ms": max(latencies) if latencies else 0.0,
        "results": results,
    }


def run_duplicate_suppression_check(db_path: Path) -> dict[str, Any]:
    store = MemoryStore(db_path, log_dir=db_path.parent / "logs-duplicate")
    attempts = []
    for _ in range(4):
        attempts.append(
            store.store(
                namespace="proof:duplicate",
                content="Use one canonical bridge DB path.",
                kind="memory",
                tags=["topic:runtime-path"],
                title="Canonical bridge path",
            )
        )

    stored_count = sum(1 for result in attempts if result["stored"])
    duplicate_count = sum(1 for result in attempts if result["duplicate"])
    duplicate_attempts = max(len(attempts) - 1, 1)
    suppression_rate = round(duplicate_count / duplicate_attempts, 3)
    return {
        "attempt_count": len(attempts),
        "stored_count": stored_count,
        "duplicate_count": duplicate_count,
        "suppression_rate": suppression_rate,
    }


def run_relation_metadata_check(db_path: Path) -> dict[str, Any]:
    store = MemoryStore(db_path, log_dir=db_path.parent / "logs-relation")
    valid_from = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    valid_until = (datetime.now(UTC) + timedelta(days=7)).isoformat()

    created = store.store(
        namespace="proof:relation",
        kind="memory",
        title="Relation proof memory",
        tags=["domain:retrieval"],
        content=(
            "record_type: belief\n"
            "claim: Compose task memory from procedure and support layers.\n"
            "supports: mem-a | mem-b\n"
            "contradicts: mem-c\n"
            "depends_on: proc-checklist\n"
            f"valid_from: {valid_from}\n"
            f"valid_until: {valid_until}\n"
        ),
    )
    recall = store.recall(namespace="proof:relation", tags_any=["relation:supports"], limit=5)
    stats = store.stats(namespace="proof:relation")
    exported = store.export(namespace="proof:relation", format="text")

    item = recall["items"][0] if recall["items"] else None
    checks = {
        "store_succeeds": created["stored"] is True,
        "recall_surfaces_relations": bool(item)
        and item["relations"]["supports"] == ["mem-a", "mem-b"]
        and item["relations"]["contradicts"] == ["mem-c"]
        and item["relations"]["depends_on"] == ["proc-checklist"],
        "recall_surfaces_validity_status": bool(item)
        and item["valid_from"] == valid_from
        and item["valid_until"] == valid_until
        and item["validity_status"] == "current",
        "stats_counts_relations_and_validity": stats["relation_counts"]["supports"] == 2
        and stats["relation_counts"]["contradicts"] == 1
        and stats["relation_counts"]["depends_on"] == 1
        and stats["validity_counts"]["current"] == 1,
        "export_mentions_relation_metadata": "relations: supports=mem-a, mem-b; contradicts=mem-c; depends_on=proc-checklist"
        in exported["content"]
        and "validity_status: current" in exported["content"],
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
    }


def force_expire_lease(store: MemoryStore, signal_id: str) -> None:
    with store._connect() as conn:
        conn.execute(
            "UPDATE memories SET lease_expires_at = ? WHERE id = ?",
            ("2000-01-01T00:00:00+00:00", signal_id),
        )
        conn.commit()


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
    normalized = raw.lstrip("\ufeff")
    lines = normalized.splitlines()
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
