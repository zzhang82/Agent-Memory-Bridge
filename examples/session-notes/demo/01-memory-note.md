---
namespace: project:demo-app
kind: memory
title: [[API Client]] retry policy
tags:
  - source:codex
  - project:demo-app
  - phase:checkpoint
  - topic:http
  - topic:retries
actor: orchestrator
session_id: demo-session-001
correlation_id: retry-policy
source_app: codex
---

The client now retries only idempotent requests.

Key points:
- retries apply to `GET`, `HEAD`, and `PUT`
- backoff is exponential with jitter
- `POST` remains opt-in to avoid duplicate side effects
