# Production Status

Last updated: 2026-04-19 (America/New_York)

This maintainer note describes the released `0.9.0` shape plus the validation
snapshot used to support it.

## Released 0.9.0 Runtime Shape

`agent-memory-bridge` now has these cooperating layers:

1. stdio MCP server for `store`, `recall`, `browse`, `stats`, `forget`, `promote`, `claim_signal`, `extend_signal_lease`, `ack_signal`, and `export`
2. shared SQLite/WAL + FTS5 bridge storage
3. session watcher and checkpoint/closeout capture
4. reflex promotion into machine-first durable artifacts
5. consolidation with compression-aware `domain-note`, `belief-candidate`, and `belief` generation
6. relation-lite metadata parsing and surfacing
7. profile/control-layer startup assembly
8. local metadata-only telemetry
9. `concept-note` emission from stable beliefs
10. task-time assembly over procedures, concepts, beliefs, and linked supporting records

## Verified On 2026-04-19

- `pytest` passes: `152 passed`
- deterministic proof reports `4/4` checks passed
- deterministic proof and benchmark both report `relation_metadata_passed = true`
- benchmark summary reports:
  - `question_count = 11`
  - `memory_expected_top1_accuracy = 1.0`
  - `memory_mrr = 1.0`
  - `file_scan_expected_top1_accuracy = 0.636`
  - `file_scan_mrr = 0.909`
  - `duplicate_suppression_rate = 1.0`
- reviewed classifier calibration snapshot reports:
  - `sample_count = 16`
  - `classifier_exact_match_rate = 0.875`
  - `fallback_exact_match_rate = 0.062`
  - `classifier_better_count = 13`
  - `fallback_better_count = 2`
  - `classifier_filtered_low_confidence_count = 2`
- healthcheck includes a relation-metadata smoke path
- relation-lite metadata is available on recall, export, and stats for:
  - `supports`
  - `contradicts`
  - `supersedes`
  - `depends_on`
  - `valid_from`
  - `valid_until`
- promotion re-derives relation and validity tags after content rewrite, preventing stale derived-tag drift
- consolidation emits a first-class `concept-note` once a stable belief is promoted
- task-time memory assembly composes:
  - `kind:procedure`
  - `kind:concept-note`
  - `kind:belief`
  - linked supporting records via relation metadata
- `recall_first(...)` can surface procedure, concept, belief, and supporting layers alongside project/global gotcha and domain retrieval

## What 0.9.0 Actually Means

- the public MCP surface is still the same small bridge
- relation-lite structure is now real and auditable
- retrieval claims are benchmarked instead of guessed
- the engine can assemble task-time memory over procedures, concepts, beliefs, and linked support without exposing a larger MCP API

## Honest Boundaries

The release still does **not** mean:

- a graph database
- full relation-aware traversal or ranking across the whole store
- automatic procedure learning from raw transcripts
- a full agent runtime or scheduler
- pre-compaction capture before model-side context loss
- active pubsub or consumer execution on top of stored signals

## Pressure Points After 0.9.0

The most important remaining gaps are:

1. pre-compaction capture before model-side loss
2. broader reviewed retrieval fixtures so credibility does not overfit the current corpus
3. stronger write-side calibration for promotion quality
4. cross-domain concept synthesis beyond the current domain-local concept-note step
5. more deliberate procedure curation or promotion instead of only manual procedure records
6. richer coordination semantics only after the memory/governance lane is fully stable

## Maintainer Read

`0.9.0` is the point where the project reads as more than foundation work.

It now behaves like:

- a shared MCP memory backend
- a governed learning layer
- a structured relation-lite memory layer
- a first pass at applicable/compositional task memory

The next work should protect those gains instead of widening the public surface too fast.
