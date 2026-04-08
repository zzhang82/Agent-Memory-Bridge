from __future__ import annotations

import json
import sys


def infer_tags(text: str) -> tuple[list[str], list[str]]:
    normalized = " ".join(text.lower().split())
    domains: list[str] = []
    topics: list[str] = []
    confidence = 0.8
    if (
        "review handoff" in normalized
        or "review queue" in normalized
        or "approval queue" in normalized
        or "next reviewer" in normalized
    ):
        domains.append("domain:orchestration")
        topics.append("topic:review-flow")
        confidence = 0.92
    if "sqlite" in normalized or "wal" in normalized:
        domains.append("domain:sqlite")
        topics.append("topic:storage")
        confidence = 0.9
    if "context compaction" in normalized or "bridge note" in normalized:
        domains.append("domain:retrieval")
        topics.append("topic:context-assembly")
        confidence = 0.86
    if (
        "machine-readable" in normalized
        or "token-efficient" in normalized
        or "narrative memory" in normalized
        or "domain notes" in normalized
        or "noisy summary" in normalized
    ):
        domains.append("domain:agent-memory")
        topics.append("topic:memory-shaping")
        confidence = 0.79
    if (
        "wrong db" in normalized
        or "canonical runtime path" in normalized
        or "canonical bridge database path" in normalized
        or "same database and logs" in normalized
    ):
        domains.append("domain:memory-bridge")
        topics.append("topic:runtime-path")
        confidence = 0.84
    if "high reasoning" in normalized or "bounded code edits" in normalized or "architecture review" in normalized:
        topics.append("topic:model-routing")
        confidence = 0.88
    if "cross-project" in normalized or "projects reuse prior fixes" in normalized:
        topics.append("topic:cross-project-reuse")
        confidence = 0.74
    if "single ownership" in normalized or "subagent execution" in normalized:
        domains.append("domain:orchestration")
        topics.append("topic:subagents")
        confidence = 0.71
    if (
        "values.yaml" in normalized
        or "safe fts fallback" in normalized
        or "punctuation-heavy" in normalized
        or "fts tokenization" in normalized
        or "substring recall" in normalized
    ):
        domains.append("domain:retrieval")
        topics.append("topic:fts")
        confidence = 0.55
    if "bridge memory" in normalized and "external search" in normalized:
        domains.append("domain:memory-bridge")
        domains.append("domain:retrieval")
        confidence = 0.68
    if (
        "release cutover handoff" in normalized
        or ("claimed by one worker" in normalized and "acknowledged" in normalized)
    ):
        domains.append("domain:orchestration")
        confidence = 0.77
    return domains, topics, confidence


def main() -> None:
    payload = json.load(sys.stdin)
    items = []
    for item in payload.get("items", []):
        domains, topics, confidence = infer_tags(str(item.get("text", "")))
        items.append(
            {
                "key": item.get("key"),
                "domains": domains,
                "topics": topics,
                "confidence": confidence,
            }
        )
    json.dump({"items": items}, sys.stdout)


if __name__ == "__main__":
    main()
