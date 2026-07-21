# Production Status

Last updated: 2026-07-21 (America/New_York)

This maintainer note describes the current `0.23.0` reliability-hardening release, the inherited v0.22.3 correctness guarantees, the inherited v0.22 activation receipt behavior, the inherited v0.21 governed-change proof, and the validation snapshot used to support the release.

## 0.23.0 Release Status

- Package version: `0.23.0`
- Release thesis: shorten embedding write transactions, contain background-lane failures, and give schema upgrades an explicit version contract
- MCP runtime behavior: retrieval reliability, service isolation, state persistence, and schema migration behavior are tightened; the tool count is unchanged
- Baseline install: immutable `v0.23.0` archive in `.amb-venv`, using the derived venv interpreter
- Embedding transaction contract: semantic recall, scheduled maintenance, and rebuild read candidates first, call the provider in a batch outside the SQLite write transaction, then revalidate content hashes before writing vectors in a short transaction
- Hybrid degradation contract: only typed embedding-provider failures fall back to lexical results with explicit degraded metadata; SQLite and programming failures still propagate, and explicit semantic mode fails clearly
- Vector contract: provider vectors must contain finite numeric values; invalid generated or persisted vectors are rejected rather than stored or scored
- Chinese/Han boundary: Chinese/Han characters and bigrams participate in the local hash-semantic path; ordinary Chinese lexical recall still uses the existing LIKE fallback when FTS has no direct match
- Service contract: during cycle execution, watcher, reflex, consolidation, governance, and embedding lanes isolate ordinary exceptions, report total and consecutive failure counts, and use capped exponential backoff without preventing later lanes from running
- State contract: service-lane JSON state uses tolerant loading and unique temporary files followed by atomic replacement; failed replacement preserves the previous valid state
- Schema contract: ordered migrations are recorded in SQLite `PRAGMA user_version`, run under `BEGIN IMMEDIATE`, roll back DDL and version changes together, reject future versions, and serialize concurrent legacy upgrades
- Polling contract: `since` is valid only for empty-query `kind="signal"` recall; rows are returned by ascending insertion order and invalid same-namespace anchors fail explicitly
- Cursor boundary: `since` tracks later insertions, not later lifecycle transitions on older Signals; text and memory recall return `next_since: null`
- Ack contract: pending unclaimed Signals remain ownerless-ack compatible; active claims require the current owner and use a conditional update
- Promotion contract: relation, validity, content-carried lineage, database lineage state, and lineage issues survive promotion
- Operational-output contract: business results remain authoritative when operational or telemetry JSONL append fails
- Registration boundary: `doctor` and `verify` are local checks; client MCP status/tool visibility proves config loading
- Config boundary: placeholder-safe examples remain separate from real config rendered with approved local paths; all config writes remain manual
- Receipt command: `agent-memory-bridge activation-receipt --namespace ... --correlation-id ... --format markdown`
- Receipt proof: one reviewed writer memory plus one acked reader signal under one namespace and correlation id, with two distinct declared `source_client` labels
- Receipt boundary: declared provenance only; not identity proof, certification, distribution proof, or use proof
- Public MCP surface: unchanged at exactly `10` tools
- Automatic writes: no auto durable writeback and no client config writes
- Fixed governed-change proof report target: `0.21.0`
- Governed-change manifest releases: `current_release = 0.20.0`, `target_release = 0.21.0`
- Windows proof hashing: manifest bytes are normalized to LF before the fixed SHA256 check
- Current README hero asset: `examples/diagrams/v0.22-shared-memory-hero.png` (conceptual visual only)
- Current overview asset: `examples/diagrams/amb-overview.svg` in the README "How It Works" section
- Current receipt visuals: `examples/diagrams/v0.22-cross-client-activation.svg` and `examples/diagrams/v0.22-receipt-anatomy.svg`
- Machine visual inventory: `examples/diagrams/visual-claims.json`; the release contract treats it as hygiene, not semantic proof
- Visual render gate: native-size and README-width raster renders must show no clipping, overlap, or crossed labels before visuals support a release story

## Current Runtime Shape

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
11. serialized lifecycle contention checks plus an eight-process exact-ID claim test with one winner
12. policy-gated learning candidates that can stage runtime learning without entering ordinary recall, browse, export, or stats until explicitly reviewed
13. internal governance triggers that scan hidden learning candidates and open review signals without promoting or rewriting memory
14. optional embedding sidecar scheduling for derived-cache maintenance without changing durable memory rows
15. reviewed memory revision receipts and deterministic evolution fixtures for supersession, tombstone audit, quarantine, scope warnings, bitemporal validity, and hidden review lanes
16. a proposal-only review queue CLI/report over hidden candidates, learning reviews, tombstones, stale/expired records, and quarantined claims, with no automatic durable writeback
17. a human review workflow CLI/report that turns review-queue items into explicit decision prompts, manual steps, allowed outcomes, and blocked-until gates without adding an MCP tool
18. a Task Brief CLI/report that composes existing task-memory assembly, review queue items, and active signals into `Used`, `Ignored`, and `Needs Review` sections without adding an MCP tool
19. a first-run CLI/report that renders install steps, client config snippets, verification steps, and a Task Brief without writing client config, durable memory records, or requiring AMH
20. a clean-room proof runner that launches the real stdio MCP entrypoint against an isolated temp store, performs one tokened demo `store -> recall`, renders first-run and Task Brief CLI reports, and proves zero client config writes
21. governed change handling for transactional redacted tombstones, conservative exact-lineage cascades, degraded audit retention, bounded transitive supersession, current-premise evidence, and declared task-domain applicability
22. a Cross-Client Activation Receipt CLI/report that reads existing writer memory and reader signal rows for one namespace and correlation id, hashes sensitive identifiers, and performs no durable or config writes
23. embedding maintenance that batches provider work outside SQLite write transactions and revalidates content hashes before derived-vector writes
24. one shared service-lane boundary with exception isolation, failure counters, capped backoff, and tolerant atomic state replacement
25. an ordered transactional schema migration spine recorded as SQLite schema version `1`

## Verified On 2026-07-21

- `pytest` passes: `445 passed`
- the integrated embedding, service, state, schema, and storage regression gate covers 114 targeted tests
- semantic recall, scheduled maintenance, and rebuild tests verify batched provider execution outside write transactions plus content-hash revalidation before vector writes
- hybrid recall tests verify lexical degradation only for typed provider failures; explicit semantic failures and non-provider errors remain visible
- service tests verify one failing lane does not stop later lanes, failure counts and capped backoff are reported, and `KeyboardInterrupt` / `SystemExit` are not swallowed
- shared state-I/O tests cover malformed JSON, atomic replacement, unique temporary files, and preservation of the previous valid state when replacement fails
- schema tests cover ordered version `1` migration, DDL/version rollback, rejection of too-new databases, missing-step fail-closed behavior, representative legacy layouts, and four-process convergence on one upgrade
- Chinese/Han hash-semantic tests cover character and bigram tokenization; no Chinese FTS support is claimed
- 10,000-Signal polling acceptance with `limit=100`: exact insertion order, 10,000 unique ids, zero missing, zero unexpected, 100 pages
- eight independent `spawn` processes claiming one exact Signal: one stored winner and no lock error in the local Linux run; the same test is part of the normal cross-platform CI matrix
- cross-client activation receipt tests cover pass/review-required outcomes, distinct declared `source_client` labels, acked reader signals, observed writer-id matching, deterministic redaction, CLI exit codes, no memory mutation, and public MCP surface stability
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
- serialized signal lifecycle snapshot reports:
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
  - `task_brief_used_count = 2`
  - `task_brief_ignored_count = 1`
  - `task_brief_needs_review_count = 4`
  - `task_brief_review_queue_item_count = 2`
  - `task_brief_active_signal_count = 1`
  - `task_brief_no_auto_writeback = true`
  - `task_brief_public_mcp_surface_change = false`
  - `task_brief_needs_review_source_type_count = 3`
- v0.19 adoption-proof snapshot reports:
  - `v019_case_count = 12`
  - `v019_pass_count = 12`
  - `v019_pass_rate = 1.0`
  - `v019_retrieval_case_count = 4`
  - `v019_retrieval_pass_rate = 1.0`
  - `v019_task_brief_case_count = 4`
  - `v019_task_brief_pass_rate = 1.0`
  - `v019_first_run_adoption_case_count = 4`
  - `v019_first_run_adoption_pass_rate = 1.0`
  - `v019_public_mcp_tool_count = 10`
  - `v019_public_mcp_surface_change = false`
  - `v019_client_config_write_count = 0`
  - `v019_durable_writeback_count = 0`
  - `v019_amh_required = false`
  - `v019_native_memory_comparison_required = true`
- v0.20 clean-room proof snapshot reports:
  - `v020_case_count = 6`
  - `v020_pass_count = 6`
  - `v020_pass_rate = 1.0`
  - `v020_import_sanity_pass = true`
  - `v020_stdio_round_trip_pass = true`
  - `v020_first_run_pass = true`
  - `v020_task_brief_pass = true`
  - `v020_public_mcp_tool_count = 10`
  - `v020_public_mcp_surface_change = false`
  - `v020_client_config_write_count = 0`
  - `v020_explicit_demo_memory_write_count = 1`
  - `v020_explicit_demo_signal_write_count = 0`
  - `v020_non_demo_durable_writeback_count = 0`
  - `v020_amh_required = false`
  - `v020_external_vendor_adoption_claim = false`
- v0.21 governed-change snapshot reports:
  - `v021_case_count = 20`
  - `v021_category_count = 4`
  - `v021_flat_baseline_hazards = 17`
  - `v021_flat_baseline_hazards_expected = 17/20`
  - `v021_governed_case_pass_count = 20`
  - `v021_governed_failures = 0`
  - `v021_governed_failures_target = 0/20`
  - `v021_governed_checkpoint_passes = 40`
  - `v021_governed_checkpoint_passes_target = 40/40`
  - `v021_governed_checkpoint_result_count = 40`
  - `v021_useful_current_retention_pass = true`
  - `v021_public_mcp_tool_count = 10`
  - `v021_public_mcp_surface_change = false`
  - `v021_auto_writeback_count = 0`
  - `v021_config_write_count = 0`
  - `v021_durable_live_writeback_count = 0`
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
- the CLI can now render config snippets for generic stdio MCP, Codex, Cursor, Cline, Claude Code, Claude Desktop, Antigravity, OpenCode, and Hermes
- `first-run` combines install, config snippet, verification steps, and Task Brief into one copy/paste report while keeping config writes manual
- `doctor` and `verify` provide local install confidence without touching live bridge state

## What 0.23.0 Actually Means

- external embedding-provider execution does not run inside the bridge's SQLite write transaction in semantic recall, scheduled maintenance, or rebuild paths
- content hashes are rechecked before vectors are written, so content changed during provider execution does not receive a stale vector
- hybrid recall degrades only on the configured provider's typed failure; explicit semantic mode and unrelated database or programming errors do not silently change modes
- service lanes share one cycle-execution isolation contract with failure counts and capped backoff, while process-level interrupts remain process-level interrupts
- service lanes remain sequential; exception isolation does not provide lane-wide timeouts or prevent a slow call from delaying later lanes
- service startup and lane construction remain process-level operations outside the per-cycle lane boundary
- service state uses tolerant reads and atomic replacement; this is state-file hardening, not a multi-instance service lock
- schema version `1` establishes an ordered migration spine with rollback and concurrent-upgrade serialization; it does not add every desirable database CHECK constraint
- Chinese/Han text participates in hash-semantic retrieval; this is not broad CJK lexical or FTS support
- Signal writes remain append-like, and this release does not add exactly-once creation or an idempotency-key API
- the public MCP surface remains exactly 10 tools

## What 0.22.1 Actually Means

- this is release-facing visual polish over the v0.22 activation receipt
- runtime behavior is unchanged from `0.22.0`
- the README first screen uses `examples/diagrams/v0.22-shared-memory-hero.png` as a conceptual visual only
- `examples/diagrams/amb-overview.svg` now belongs in a concise "How It Works" section
- `docs/v0.22.1-announcement.md` embeds `examples/diagrams/v0.22-cross-client-activation.svg` and `examples/diagrams/v0.22-receipt-anatomy.svg`
- `docs/v0.22.0-announcement.md` remains historical and text-only
- `examples/diagrams/visual-claims.json` is the machine visual inventory
- the visual inventory is release hygiene, not semantic proof
- native-size and README-width raster renders are a release gate for clipping,
  overlap, and crossed labels
- the current validation snapshot is `445 passed`

## What 0.22.0 Actually Means

- the public MCP surface is still the same 10-tool bridge
- `activation-receipt` is a local CLI/report, not an MCP tool
- client A can store one reviewed writer memory and client B can record an acked reader signal under the same correlation
- the pass condition requires two distinct declared `source_client` labels, not authenticated client identity
- the receipt emits hashes for namespace, correlation, record ids, and source-client labels
- the receipt does not include raw memory content, private paths, session ids, client workspace values, or model ids
- the receipt reads existing rows and performs no durable writeback or client config writes
- this is evidence of a bounded local shared-memory loop, not vendor certification or proof of external adoption

## What 0.21.0 Actually Means

- the public MCP surface is still the same small bridge
- explicit forgetting records content-redacted tombstones transactionally with memory and derived-index deletion
- tombstones audit deleted record IDs; they do not prevent later explicit re-storage of the same content under a new ID
- only exact structured machine-owned descendants cascade; uncertain lineage is retained with degraded audit metadata
- task assembly suppresses bounded transitive predecessors, retains current corrective evidence when premises change, and rejects explicitly domain-mismatched procedures
- the clean-room proof is local reproducible evidence through the real stdio entrypoint, not a vendor certification claim
- runtime learning can be proposed as a policy-gated candidate instead of becoming ordinary durable memory immediately
- candidate records are review material, not source-of-truth memory
- review receipts are audit material, not a hidden promotion/delete mechanism
- `review-queue` is an operator-facing CLI/report, not an MCP tool and not an auto-reviewer
- `review-workflow` is an operator-facing CLI/report, not an MCP tool and not a workflow executor
- `first-run` is an operator-facing CLI/report, not an MCP tool, not a config writer, and not a plugin runtime
- `v0.20` clean-room proof writes exactly one explicit demo memory in a temp store and performs no client config writes, signals, non-demo writeback, or AMH-required work
- review-queue and review-workflow plans are proposal-only; they explain next steps but do not mutate durable memory
- the store boundary owns policy verification; callers do not get to provide authoritative allow decisions
- governance triggers may open review signals for staged candidates, but they do not approve, promote, rewrite, or delete memory
- learning candidates are hidden from normal user-facing memory operations unless explicitly queried through review tags
- deterministic evolution fixtures now check supersession, tombstone audit, quarantine, principal-scope warnings, bitemporal validity, and hidden review lanes
- the fixed v0.21 proof checks 20 governed-change cases at 40 checkpoints; it is executable evidence rather than an automatic policy engine
- deterministic review-queue fixtures now check candidate/review/tombstone/quarantine/validity slices and assert no public MCP surface expansion
- deterministic review-workflow fixtures now check source-queue coverage, human-required decisions, manual steps, zero auto-writeback, and no public MCP surface expansion
- watcher/reflex/consolidation automation is opt-in for the always-on service
- derived FTS and embedding indexes are cache/proof surfaces, not memory authority
- relation-lite structure, task assembly, onboarding, and signal contention semantics from prior releases remain intact

## Honest Boundaries

The release still does **not** mean:

- a graph database
- full relation-aware traversal or ranking across the whole store
- automatic durable writeback from raw transcripts
- a complete candidate review UI or autonomous reviewer
- automatic execution of review-queue writeback plans
- automatic execution of review-workflow manual steps
- autonomous memory revision, deletion, or policy promotion
- general machine unlearning, unbounded graph traversal, or automatic policy enforcement
- ACL enforcement, GDPR/privacy compliance, vendor certification, authenticated client identity, external adoption proof, or certified poisoning defense
- a full agent runtime, scheduler, queue platform, or distributed lock
- pre-compaction capture before model-side context loss
- active pubsub or consumer execution on top of stored signals
- exactly-once distributed coordination
- broad CJK lexical or FTS support
- service singleton locking, a service heartbeat, or lane-wide execution timeouts
- complete database CHECK constraints or a Signal idempotency-key contract
- an ANN/vector database or full-store semantic index
- that every MCP client is fully verified just because the generic stdio contract is stable
- that distinct declared `source_client` labels are cryptographic or vendor-authenticated identity

## Pressure Points After 0.23.0

The most important remaining gaps are:

1. broader reviewed retrieval and task-success fixtures so credibility does not overfit the current corpus
2. stronger write-side calibration for promotion quality and merge/reject decisions
3. operator ergonomics for reviewing degraded lineage and tombstone evidence at larger store sizes
4. cross-domain concept synthesis beyond the current domain-local concept-note step
5. more deliberate procedure curation or promotion instead of only manual procedure records
6. pre-compaction capture before model-side loss
7. broader multi-process contention and crash-recovery dogfood beyond the exact-ID claim test, concurrent schema upgrade test, and serialized lifecycle benchmark
8. a human-facing review UI or external harness that consumes review-workflow output without moving execution into AMB core
9. optional receipt ergonomics for operators without moving receipt generation into the MCP tool surface
10. measured need before adding service singleton/heartbeat/timeout machinery, database CHECK constraints, or a Signal idempotency API

## Maintainer Read

`0.23.0` shortens SQLite write transactions around embedding maintenance, degrades hybrid retrieval only for typed provider failures, isolates background service lanes, makes their state replacement atomic, and establishes ordered transactional schema versioning. Chinese/Han text participates in the local hash-semantic path, while Chinese lexical retrieval retains the existing LIKE fallback. The release does not claim broad CJK FTS support, exactly-once Signals, authenticated actors, namespace isolation, service singleton locking, lane-wide timeouts, or distributed coordination.

`0.22.3` makes the existing local coordination contract deterministic where it previously was not. Signal polling is insertion-ordered and rejects invalid anchors; active claims require owner-matched ack; promotion retains relation, validity, and lineage information; and JSONL output failures do not overturn committed operations. The release still does not claim exactly-once delivery, a distributed queue, authenticated actors, or namespace isolation.

`0.22.2` aligned the immutable install archive, installed `first-run` report, client-config guidance, and verification sequence while keeping client registration explicit and manual. Independent-user completion time, failure rate, and support burden remain Phase 1 pilot questions rather than release claims.

`0.22.1` keeps the v0.22 receipt runtime unchanged and gives the public docs a clearer visual entry point. The README hero is conceptual only; the detailed overview sits under "How It Works"; the v0.22.1 announcement carries the two receipt-specific SVGs; and the visual inventory plus native-size/README-width render gate record asset hygiene without proving visual semantics.

`0.22.0` keeps the 10-tool public MCP surface and adds a local receipt for one cross-client memory loop. The receipt is intentionally narrow: it shows two distinct declared `source_client` labels participated under one correlation, with one reviewed writer memory and one acked reader signal, while omitting private paths, content, session ids, and model ids.

`0.21.0` remains the governed-change proof base. Forgetting is transactional and content-redacted; cascade deletion requires exact machine-owned lineage; uncertain relationships remain visible as degraded audit evidence; and task assembly accounts for bounded supersession, changed premises, and declared domains. The fixed executable proof found hazards in 17 of 20 equal-budget flat cases and recorded zero governed failures across all 40 checkpoints.

It now behaves like:

- a shared MCP memory backend
- a governed learning layer with candidate staging
- a structured relation-lite memory layer
- a first pass at applicable/compositional task memory
- a bounded governed-change layer for deletion, lineage, current premises, and declared task domains
- a platform-neutral stdio bridge with real install confidence
- a lightweight coordination layer with measured claim/reclaim boundaries
- an operator-facing review queue that keeps hidden/stale/quarantined memory work visible without making it authority
- an operator-facing human workflow plan that makes each review decision explicit without becoming an auto-writer
- an operator-facing activation receipt that keeps cross-client provenance inspectable without becoming an identity system
- a local maintenance service whose ordinary lane failures are isolated and whose schema upgrades follow an explicit transactional version path

The next work should protect those gains and improve review ergonomics without turning bounded relation-lite handling into a graph-memory claim, letting proposal-only plans become automatic durable writes, or turning declared client labels into identity claims.
