---
namespace: bench
kind: signal
title: Release Cutover Ready
tags:
  - handoff
  - release
  - cutover
actor: benchmark
session_id: bench-9
correlation_id: handoff-release-cutover
source_app: benchmark
---

Release cutover is ready after the final checkpoint.

The next worker should claim this signal, verify the cutover checklist, and acknowledge it when the release is complete.
