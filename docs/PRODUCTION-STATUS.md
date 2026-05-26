# Production Status

Last updated: 2026-05-26 (America/Toronto)

This maintainer note describes the released `0.14.1` governed learning-candidate hardening shape plus the validation snapshot used to support it.

## Released 0.14.1 Runtime Shape

`agent-memory-bridge` now has these cooperating layers:

1. stdio MCP server for `store`, `recall`, `browse`, `stats`, `forget`, `promote`, `claim_signal`, `extend_signal_lease`, `ack_signal`, and `export`
2. shared SQLite/WAL + FTS5 bridge storage
3. optional checkpoint/closeout capture helpers around the core bridge
4. reflex promotion into machine-first durable artifacts
5. consolidation with compression-aware `domain-note`, `belief-candidate`, `belief`, and `concept-note` generation
6. relation-lite metadata parsing and surfacing
7. profile/control-layer startup assembly
8. local metadata-only telemetry
9. task-time assembly over procedures, concepts, beliefs, and linked supporting records
10. onboarding and integration hardening through platform-neutral docs, rendered client configs, and local `doctor` / `verify` checks
11. contention-tested signal ownership, reclaim, and stale-ack boundaries
12. policy-gated learning candidates that can stage runtime learning without entering ordinary recall, browse, export, or stats until explicitly reviewed

## Verified On 2026-05-26

- `pytest` passes: `243 passed`
- targeted learning-candidate tests cover policy decisions, hidden review records, forged-decision rejection, and public-surface stability
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
- signal contention snapshot reports:
  - `signal_contention_case_count = 5`
  - `signal_contention_case_pass_rate = 1.0`
  - `unique_active_claim_rate = 1.0`
  - `duplicate_active_claim_count = 0`
  - `active_reclaim_block_rate = 1.0`
  - `stale_ack_blocked_rate = 1.0`
  - `stale_reclaim_success_rate = 1.0`
  - `pending_under_pressure_claim_rate = 1.0`
  - `initial_hard_expiry_cap_rate = 1.0`
- adversarial memory-governance snapshot reports:
  - `adversarial_case_count = 6`
  - `adversarial_task_count = 7`
  - `adversarial_governed_task_pass_rate = 1.0`
  - `adversarial_governed_blocked_record_leak_rate = 0.0`
- learning candidates are stored with review tags such as `kind:learning-candidate` and `candidate_status:*`
- normal recall, browse, export, and stats suppress learning candidates unless explicit review tags are requested
- the storage boundary recomputes learning policy so callers cannot forge an allow decision
- healthcheck includes a relation-metadata smoke path
- onboarding contract passes for required docs, README linkage, generated config parsing, and placeholder-safe public examples
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
- the CLI can now render config snippets for generic stdio MCP, Codex, Cursor, Cline, Claude Code, Claude Desktop, and Antigravity
- `doctor` and `verify` provide local install confidence without touching live bridge state

## What 0.14.1 Actually Means

- the public MCP surface is still the same small bridge
- runtime learning can be proposed as a policy-gated candidate instead of becoming ordinary durable memory immediately
- candidate records are review material, not source-of-truth memory
- the store boundary owns policy verification; callers do not get to provide authoritative allow decisions
- learning candidates are hidden from normal user-facing memory operations unless explicitly queried through review tags
- relation-lite structure, task assembly, onboarding, and signal contention semantics from 0.13 remain intact

## Honest Boundaries

The release still does **not** mean:

- a graph database
- full relation-aware traversal or ranking across the whole store
- automatic durable writeback from raw transcripts
- a complete candidate review UI or autonomous reviewer
- a full agent runtime, scheduler, queue platform, or distributed lock
- pre-compaction capture before model-side context loss
- active pubsub or consumer execution on top of stored signals
- exactly-once distributed coordination
- that every MCP client is fully verified just because the generic stdio contract is stable

## Pressure Points After 0.14.1

The most important remaining gaps are:

1. a fuller candidate review/promote workflow for accepted learning candidates
2. broader reviewed retrieval fixtures so credibility does not overfit the current corpus
3. stronger write-side calibration for promotion quality
4. cross-domain concept synthesis beyond the current domain-local concept-note step
5. more deliberate procedure curation or promotion instead of only manual procedure records
6. pre-compaction capture before model-side loss
7. deeper real multi-client contention dogfood beyond serialized benchmark cases

## Maintainer Read

`0.14.1` keeps the public MCP surface small while hardening the 0.14 governed-learning boundary between runtime learning and durable AMB memory. The project now reads as a general MCP memory product with local proof for memory, task assembly, procedure governance, onboarding, signal ownership, and governed learning writeback.

It now behaves like:

- a shared MCP memory backend
- a governed learning layer with candidate staging
- a structured relation-lite memory layer
- a first pass at applicable/compositional task memory
- a platform-neutral stdio bridge with real install confidence
- a lightweight coordination layer with measured claim/reclaim boundaries

The next work should protect those gains and improve review/promote ergonomics without widening the public surface too fast.
