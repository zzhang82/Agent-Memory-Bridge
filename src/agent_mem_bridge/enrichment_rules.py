from __future__ import annotations


DOMAIN_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("domain:orchestration", ("orchestration", "subagent", "worker", "validation", "contract drift", "handoff")),
    ("domain:memory-bridge", ("memory bridge", "shared memory", "recall", "store", "context")),
    ("domain:sqlite", ("sqlite", "wal", "fts", "database")),
    ("domain:retrieval", ("recall", "search", "fts", "semantic")),
    ("domain:reliability", ("mistake", "drift", "canonical", "wrong db", "trust recall", "fix")),
    (
        "domain:agent-memory",
        (
            "agent recall",
            "machine-readable",
            "human readable",
            "structured",
            "token",
            "summary",
            "learn",
            "gotcha",
            "domain note",
        ),
    ),
)


TOPIC_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("topic:session-sync", ("session sync", "closeout", "watcher", "rollout", "thread")),
    ("topic:dedup", ("dedup", "duplicate")),
    ("topic:runtime-path", ("wrong db", "runtime path", "canonical", "database")),
    ("topic:subagents", ("subagent", "worker", "parent thread")),
    ("topic:fts", ("fts", "values.yaml", "search")),
    ("topic:memory-shaping", ("machine-readable", "human readable", "structured", "token", "summary", "gotcha")),
    ("topic:model-routing", ("high reasoning", "coding model", "validation", "bounded implementation")),
    ("topic:cross-project-reuse", ("project b", "cross projects", "cross-project", "reuse", "gotcha")),
)


def normalize_text(text: str) -> str:
    return " ".join(text.lower().split())


def infer_keyword_tags(text: str) -> list[str]:
    normalized = normalize_text(text)
    tags: list[str] = []
    for tag, keywords in DOMAIN_HINTS:
        if any(keyword in normalized for keyword in keywords):
            tags.append(tag)
    for tag, keywords in TOPIC_HINTS:
        if any(keyword in normalized for keyword in keywords):
            tags.append(tag)
    if not tags:
        tags.append("domain:general")
    return tags
