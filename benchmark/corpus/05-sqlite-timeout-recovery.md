---
namespace: bench
kind: memory
title: SQLite Timeout Recovery
tags:
  - project:agent-memory-bridge
  - topic:storage
  - topic:sync
actor: benchmark
session_id: bench-4
correlation_id: task-timeout
source_app: benchmark
---

SQLite timeout during sync usually means a write scope stayed open too long.

Use WAL, keep write scopes short, and retry after reducing lock duration.
