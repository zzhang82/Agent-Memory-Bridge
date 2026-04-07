---
namespace: bench
kind: memory
title: Storage Decision
tags:
  - project:agent-memory-bridge
  - topic:storage
actor: benchmark
session_id: bench-1
correlation_id: task-storage
source_app: benchmark
---

We decided to use SQLite in WAL mode for agent-memory-bridge so multiple readers can query the store while writes continue safely.

This is the foundation for a shared memory journal across Codex and other agents.

