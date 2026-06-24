# Production Status

Last updated: 2026-06-24 (America/New_York)

This maintainer note describes the `0.17.0` human review workflow release-candidate shape plus the validation snapshot used to support it.

## 0.17.0 Runtime Shape

`agent-memory-bridge` now has these cooperating layers:

1. stdio MCP server for `store`, `recall`, `browse`, `stats`, `forget`, `promote`, `claim_signal`, `extend_signal_lease`, `ack_signal`, and `export`
2. shared SQLite/WAL durable storage with FTS5 lexical and optional embedding sidecar indexes
3. optional checkpoint/closeout capture helpers around the core bridge, disabled by default in the always-on service
4. optional reflex promotion into machine-first durable artifacts, disabled by default in the always-on service
5. optional consolidation with compression-aware `domain-note`, `belief-candidate`, `belief`, and `concept-note` generation, disabled by default in the always-on service
6. relation-lite metadata parsing and surfacing
7. profile/control-layer startup assembly
8. local metadata-only telemetry
9. task-time assembly over procedures, concepts, beliefs, and linked supporting records
10. onboarding and integration hardening through platform-neutral docs, rendered client configs, and local `doctor` / `verify` checks
11. contention-tested signal ownership, reclaim, and stale-ack boundaries
12. policy-gated learning candidates that can stage runtime learning without entering ordinary recall, browse, export, or stats until explicitly reviewed
13. internal governance triggers that scan hidden learning candidates and open review signals without promoting or rewriting memory
14. optional embedding sidecar scheduling for derived-cache maintenance without changing durable memory rows
15. reviewed memory revision receipts and deterministic evolution fixtures for supersession, tombstone audit, quarantine, scope warnings, bitemporal validity, and hidden review lanes
16. a proposal-only review queue CLI/report over hidden candidates, learning reviews, tombstones, stale/expired records, and quarantined claims, with no automatic durable writeback
17. a human review workflow CLI/report that turns review-queue items into explicit decision prompts, manual steps, allowed outcomes, and blocked-until gates without adding an MCP tool

## Verified On 2026-06-24

- `pytest` passes: `306 passed`
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
- reviewed memory-evolution snapshot reports:
  - `memory_evolution_case_count = 6`
  - `memory_evolution_task_count = 7`
  - `memory_evolution_governed_task_pass_rate = 1.0`
  - `memory_evolution_governed_blocked_record_leak_rate = 0.0`
  - `memory_evolution_governed_disposition_reason_hit_rate = 1.0`
- reviewed memory-operations queue snapshot reports:
  - `review_queue_item_count = 6`
  - `review_queue_actionable_count = 6`
  - `review_queue_hidden_lane_count = 2`
  - `review_queue_writeback_plan_count = 6`
  - `review_queue_no_auto_mutation = true`
  - `review_queue_public_mcp_surface_change = false`
  - `review_queue_item_type_count = 6`
- human review workflow snapshot reports:
  - `review_workflow_source_queue_item_count = 6`
  - `review_workflow_item_count = 6`
  - `review_workflow_manual_step_count = 27`
  - `review_workflow_requires_human_count = 6`
  - `review_workflow_auto_write_count = 0`
  - `review_workflow_no_auto_writeback = true`
  - `review_workflow_public_mcp_surface_change = false`
  - `review_workflow_item_type_count = 6`
- learning candidates are stored with review tags such as `kind:learning-candidate` and `candidate_status:*`
- learning reviews now include deterministic review-receipt hashes, `writeback_boundary:review_receipt_only`, and `durable_mutation_performed_by_review: false`
- normal recall, browse, export, and stats suppress learning candidates unless explicit review tags are requested
- the storage boundary recomputes learning policy so callers cannot forge an allow decision
- governance triggers scan AMB's candidate lane rather than Codex logs, so non-Codex runtimes can use the same review path when they write candidates
- always-on service gates default watcher/reflex/consolidation off, so multi-runtime installs can keep governance and embedding maintenance active without automatic Codex-log promotion
- `service --once` reports watcher, reflex, and consolidation disabled when configured off; governance stays idle without pending candidates
- `index-health` reports FTS and embedding sidecars synchronized with zero missing, stale, or orphan rows in the local validation snapshot
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

## What 0.17.0 Actually Means

- the public MCP surface is still the same small bridge
- runtime learning can be proposed as a policy-gated candidate instead of becoming ordinary durable memory immediately
- candidate records are review material, not source-of-truth memory
- review receipts are audit material, not a hidden promotion/delete mechanism
- `review-queue` is an operator-facing CLI/report, not an MCP tool and not an auto-reviewer
- `review-workflow` is an operator-facing CLI/report, not an MCP tool and not a workflow executor
- review-queue and review-workflow plans are proposal-only; they explain next steps but do not mutate durable memory
- the store boundary owns policy verification; callers do not get to provide authoritative allow decisions
- governance triggers may open review signals for staged candidates, but they do not approve, promote, rewrite, or delete memory
- learning candidates are hidden from normal user-facing memory operations unless explicitly queried through review tags
- deterministic evolution fixtures now check supersession, tombstone audit, quarantine, principal-scope warnings, bitemporal validity, and hidden review lanes
- deterministic review-queue fixtures now check candidate/review/tombstone/quarantine/validity slices and assert no public MCP surface expansion
- deterministic review-workflow fixtures now check source-queue coverage, human-required decisions, manual steps, zero auto-writeback, and no public MCP surface expansion
- watcher/reflex/consolidation automation is opt-in for the always-on service
- derived FTS and embedding indexes are cache/proof surfaces, not memory authority
- relation-lite structure, task assembly, onboarding, and signal contention semantics from 0.13 remain intact

## Honest Boundaries

The release still does **not** mean:

- a graph database
- full relation-aware traversal or ranking across the whole store
- automatic durable writeback from raw transcripts
- a complete candidate review UI or autonomous reviewer
- automatic execution of review-queue writeback plans
- automatic execution of review-workflow manual steps
- autonomous memory revision, deletion, or policy promotion
- ACL enforcement, GDPR/privacy compliance, or certified poisoning defense
- a full agent runtime, scheduler, queue platform, or distributed lock
- pre-compaction capture before model-side context loss
- active pubsub or consumer execution on top of stored signals
- exactly-once distributed coordination
- that every MCP client is fully verified just because the generic stdio contract is stable

## Pressure Points After 0.17.0

The most important remaining gaps are:

1. broader reviewed retrieval and task-success fixtures so credibility does not overfit the current corpus
2. stronger write-side calibration for promotion quality and merge/reject decisions
3. safe, explicit tombstone/audit ergonomics for real deletion workflows
4. cross-domain concept synthesis beyond the current domain-local concept-note step
5. more deliberate procedure curation or promotion instead of only manual procedure records
6. pre-compaction capture before model-side loss
7. deeper real multi-client contention dogfood beyond serialized benchmark cases
8. a human-facing review UI or external harness that consumes review-workflow output without moving execution into AMB core

## Maintainer Read

`0.17.0` keeps the public MCP surface small while adding human review workflow ergonomics on top of the governed learning-candidate lane. The project now reads as a general MCP memory product with local proof for memory, task assembly, procedure governance, onboarding, signal ownership, governed learning writeback, conservative service operation, audit-preserving revision/forgetting gates, an operator queue, and explicit manual decision plans for review work.

It now behaves like:

- a shared MCP memory backend
- a governed learning layer with candidate staging
- a structured relation-lite memory layer
- a first pass at applicable/compositional task memory
- a platform-neutral stdio bridge with real install confidence
- a lightweight coordination layer with measured claim/reclaim boundaries
- an operator-facing review queue that keeps hidden/stale/quarantined memory work visible without making it authority
- an operator-facing human workflow plan that makes each review decision explicit without becoming an auto-writer

The next work should protect those gains and improve review/promote ergonomics without widening the public surface too fast or letting proposal-only review plans become automatic durable writes.
