---
namespace: bench
kind: memory
title: FTS Sanitizer
tags:
  - project:agent-memory-bridge
  - topic:fts
actor: benchmark
session_id: bench-1
correlation_id: task-search
source_app: benchmark
---

The recall layer must handle punctuation-heavy inputs such as values.yaml without crashing FTS5.

If tokenized search fails to match directly, the system should fall back to a safe LIKE-based query path.

