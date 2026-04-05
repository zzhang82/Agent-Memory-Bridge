---
namespace: project:demo-app
kind: signal
title: Handoff for cache invalidation follow-up
tags:
  - source:codex
  - project:demo-app
  - handoff
  - next-step
  - topic:cache
actor: orchestrator
session_id: demo-session-001
correlation_id: cache-follow-up
source_app: codex
---

Follow-up needed:

- add cache invalidation tests for profile updates
- verify stale reads do not survive across worker restarts
