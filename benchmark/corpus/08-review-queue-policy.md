---
namespace: bench
kind: memory
title: Review Queue Policy
tags:
  - project:agent-memory-bridge
  - topic:review
  - topic:queue
actor: benchmark
session_id: bench-8
correlation_id: task-review-queue
source_app: benchmark
---

Review handoff should preserve queue ownership and keep the approval queue explicit.

The next reviewer should be able to see who owns the queue, what is blocked, and what can be claimed next.
