from __future__ import annotations

import json
import sys


def infer_tags(text: str) -> tuple[list[str], list[str]]:
    normalized = " ".join(text.lower().split())
    domains: list[str] = []
    topics: list[str] = []
    if "review handoff" in normalized or "review queue" in normalized or "approval queue" in normalized:
        domains.append("domain:orchestration")
        topics.append("topic:review-flow")
    if "sqlite" in normalized or "wal" in normalized:
        domains.append("domain:sqlite")
        topics.append("topic:storage")
    if "context compaction" in normalized:
        domains.append("domain:retrieval")
        topics.append("topic:context-assembly")
    return domains, topics


def main() -> None:
    payload = json.load(sys.stdin)
    items = []
    for item in payload.get("items", []):
        domains, topics = infer_tags(str(item.get("text", "")))
        items.append(
            {
                "key": item.get("key"),
                "domains": domains,
                "topics": topics,
                "confidence": 0.8,
            }
        )
    json.dump({"items": items}, sys.stdout)


if __name__ == "__main__":
    main()
