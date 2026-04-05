# Conversation Ingest Direction

The end goal is to make `agent-memory-bridge` useful from Codex, not just technically valid in isolation.

## Target Outcome

Each Codex conversation should be able to persist useful project memory into the bridge so later sessions can recover context without re-reading the whole thread.

## What Should Be Stored

Only durable, high-signal items should be stored:

- decisions
- constraints
- validated findings
- handoff checkpoints
- explicit user preferences

Do not store every message verbatim by default.

## Obsidian-Aware Store Shape

The store path should preserve Obsidian-friendly structure:

- caller-provided tags stay intact
- `#tags` in content become stored labels like `tag:memory/bridge`
- `[[wikilinks]]` become stored labels like `link:Codex`

This gives us a bridge between:

- text search today
- tag-filtered retrieval now
- richer semantic routing later

## Near-Term Codex Integration Plan

Phase 1:
- keep manual store calls and benchmark recall quality

Phase 2:
- define a small "conversation distill" step for Codex sessions
- emit only curated `memory` and `signal` entries

Phase 3:
- add a thin Codex-side helper or workflow so project conversations can sync into the bridge consistently

## Guardrails

- durable memories only
- append-only audit trail
- no automatic semantic search until the benchmark proves the keyword baseline
- no full transcript dumping unless explicitly requested

