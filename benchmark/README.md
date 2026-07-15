# Benchmark Plan

This directory holds the proof and benchmark track for Agent Memory Bridge.

The goal is not a big leaderboard suite. It is a small, repeatable set of checks
that tell us whether the bridge stays useful as a coding-agent memory layer while
the engine gets more expressive.

## Runnable Entry Points

- `python ./scripts/run_benchmark.py`
- `python ./scripts/run_benchmark.py --include-hybrid`
- `python ./scripts/run_deterministic_proof.py`
- `python ./scripts/run_classifier_calibration.py`
- `python ./scripts/run_classifier_calibration.py --fixture-gateway`
- `python ./scripts/run_activation_stress_pack.py`
- `python ./scripts/run_task_memory_benchmark.py`
- `python ./scripts/run_procedure_governance_benchmark.py`
- `python ./scripts/run_signal_contention_benchmark.py`
- `python ./scripts/run_adversarial_benchmark.py`
- `python ./scripts/run_memory_evolution_benchmark.py`
- `python ./scripts/run_review_queue_benchmark.py`
- `python ./scripts/run_review_workflow_benchmark.py`
- `python ./scripts/run_task_brief_benchmark.py`
- `python ./scripts/run_v019_adoption_proof.py`
- `python ./scripts/run_v020_clean_room_proof.py`

## Planning Manifests

- `benchmark/v0.19-fixture-manifest.json` is the planned `0.19` proof-breadth
  denominator. It names the 12 reviewed fixture cases before implementation so
  `0.19` cannot silently grow from proof work into new product surface.
- `benchmark/latest-v0.19-adoption-proof-report.json` is the executable
  snapshot for that denominator. It is a synthetic fixture proof for retrieval,
  Task Brief, and first-run guidance; it is not a claim of clean-room external
  adoption.
- `benchmark/latest-v0.20-clean-room-proof-report.json` is the executable
  snapshot for the local clean-room adoption path. It launches the real stdio
  entrypoint against a temp store, performs one demo `store -> recall`, renders
  first-run and Task Brief CLI reports, and keeps client config writes at zero.

## What The Reports Cover

The checked-in proof and benchmark flow covers:

- deterministic signal lifecycle checks for `claim`, `extend`, `ack`, expiry, reclaim, and fairness
- duplicate suppression
- recall timing
- relation metadata surfaced through recall, export, stats, and proof
- retrieval comparison against a simple file-scan baseline
- optional lexical-vs-semantic-vs-hybrid shadow retrieval comparison
- `precision@1`, `precision@3`, `recall@1`, `recall@3`, `MRR`, and `expected_top1_accuracy`
- reviewed classifier-vs-fallback calibration
- isolated learning-ladder activation stress cases
- reviewed task-memory packet comparison between flat/current assembly and relation-aware assembly
- reviewed procedure-governance packet comparison between flat/current assembly and governed procedure assembly
- reviewed signal contention lifecycle cases for unique claims, stale-owner avoidance, reclaim, and done/expired leakage
- adversarial memory-governance fixtures for stale/current scope conflicts, contradictions, task intent, noisy summaries, provenance collisions, and validity windows
- reviewed memory-evolution fixtures for supersession lineage, tombstone audit, quarantine, principal-scope warnings, bitemporal validity, and hidden review lanes
- reviewed memory-operations queue fixtures for staged candidates, review receipts, tombstones, quarantined claims, stale records, and proposal-only writeback plans
- human review workflow fixtures for decision prompts, manual steps, allowed outcomes, blocked-until gates, zero auto-writeback, and stable MCP surface boundaries
- Task Brief fixtures for used, ignored, and needs-review sections over task-memory assembly, review queue items, and active signals
- v0.19 adoption-proof fixtures for fixed-denominator retrieval, Task Brief,
  and first-run guidance while preserving the `10`-tool MCP surface
- v0.20 clean-room proof cases for local import sanity, real stdio MCP
  `store -> recall`, first-run CLI rendering, Task Brief CLI rendering, and
  temp-store write-scope isolation

The current canonical retrieval fixture has `11` questions, including overlap-heavy
review queue, release cutover, and context-compaction cases.

## Current Public Snapshot

The release-facing snapshot currently reports:

- `memory_expected_top1_accuracy = 1.0`
- `memory_mrr = 1.0`
- `file_scan_expected_top1_accuracy = 0.636`
- `file_scan_mrr = 0.909`
- `relation_metadata_passed = true`
- `duplicate_suppression_rate = 1.0`

These numbers are meant to keep regressions visible. They are not meant to imply
that the benchmark is broad enough to compare against unrelated systems.

## Hybrid Retrieval Shadow Report

`python ./scripts/run_benchmark.py --include-hybrid` writes
`benchmark/latest-hybrid-retrieval-report.json`.

This report compares three local retrieval paths on the same reviewed fixture:

- `memory`: the stable lexical FTS5 path
- `semantic`: the optional local sidecar-vector path
- `hybrid`: lexical-anchored recall that may include sidecar-only additions
  when packet budget allows

The semantic sidecar currently uses the deterministic `local-token-hash-v1`
provider. Treat the report as a migration and regression guard, not a claim that
AMB ships broad embedding-model quality. The canonical public retrieval numbers
still come from the lexical `memory` track unless a release explicitly states
otherwise.

The report also includes `hybrid_comparison_summary`, which separates:

- preserved lexical top-1 results
- improved relevant-rank cases
- degraded relevant-rank cases
- semantic-only records that became visible in the hybrid packet

This keeps the hybrid story honest: matching lexical quality is a safety signal,
not proof that the sidecar improved retrieval.

## Classifier Calibration

Classifier calibration is intentionally narrow. It answers:

- where the classifier already beats keyword fallback
- where fallback still wins
- where low-confidence classifier output should stay out of assist mode
- whether widening assist usage would be justified

The current reviewed calibration slices are:

- coordination
- retrieval
- runtime
- memory-shaping
- model-routing
- storage

If no classifier command is configured, `run_classifier_calibration.py` reports
fallback-only calibration. Use `--fixture-gateway` for the deterministic bundled
calibration path, or pass a real classifier command with `--command`.

## Task-Memory Packet Benchmark

The task-memory benchmark is separate from the canonical retrieval benchmark. It
answers a narrower 0.10 question:

> Given the same records and task query, does relation-aware assembly produce a
> cleaner packet than the current flat/current assembler?

The reviewed cases live in `benchmark/task-memory-cases.json`. They exercise
support-chain completion, superseded-record suppression, contradiction leakage,
validity-window filtering, and project-vs-global precedence under stale project
overrides.

The report is written to `benchmark/latest-task-memory-report.json` and tracks:

- `flat_case_pass_rate`
- `relation_aware_case_pass_rate`
- required primary/support hit rates
- blocked-item leak rates
- average packet size

These metrics are packet-quality checks. They are not retrieval benchmarks, graph
reasoning claims, or productivity claims.

## Procedure-Governance Benchmark

The procedure-governance benchmark is separate from the broader retrieval and
task-memory packet benchmarks. It answers a narrower 0.11 question:

> Given the same procedure records and task query, does governed procedure
> assembly keep validated/current procedures visible while suppressing stale,
> replaced, or unsafe procedures?

The reviewed cases live in `benchmark/procedure-governance-cases.json`. They
exercise validated-vs-draft selection, stale/replaced/unsafe procedure
suppression, complete procedure field surfacing, backward-compatible legacy
procedures without explicit status, and transcript-like draft warnings.

The report is written to `benchmark/latest-procedure-governance-report.json` and
tracks:

- `flat_case_pass_rate`
- `governed_case_pass_rate`
- top procedure match rates
- required procedure hit rates
- blocked procedure leak rates
- governance status match rates
- required field and warning hit rates
- average visible procedure counts

These metrics are procedure packet-quality checks. They are not productivity
claims, automatic procedure-learning claims, or procedure execution claims.

## Signal Contention Benchmark

The signal contention benchmark is separate from the deterministic lifecycle
proof. It answers a narrower 0.13 question:

> Given several workers polling the same namespace, do signal claims stay unique,
> avoid stale same-owner bias, reclaim expired leases, and keep acked/expired
> signals out of generic claim selection?

The reviewed cases are implemented in the signal contention runner and exercise
multi-consumer unique claims, active claim-vs-renewal separation, stale lease
ack blocking, expired lease reclaim, pending work under active-claim pressure,
and initial hard-expiry lease caps.

The report is written to `benchmark/latest-signal-contention-report.json` and
tracks:

- `case_pass_rate`
- `unique_active_claim_rate`
- `duplicate_active_claim_count`
- `active_reclaim_block_rate`
- `stale_ack_blocked_rate`
- `stale_reclaim_success_rate`
- `pending_under_pressure_claim_rate`
- `initial_hard_expiry_cap_rate`

These metrics are local lifecycle checks. They are not a distributed queue,
scheduling, or throughput benchmark.

## Adversarial Memory-Governance Benchmark

The adversarial memory-governance benchmark is a small deterministic slice for
realism traps that are easy to miss in clean retrieval fixtures. It answers:

> Given synthetic memory records with governance hazards, can a report harness
> make stale, contradictory, noisy, provenance-colliding, intent-sensitive, and
> expired cases visible without querying live bridge state?

The reviewed cases live in `benchmark/adversarial-memory-cases.json`. They
exercise stale project overrides vs current global guidance, contradictory
gotchas, the same query under different task intents, noisy session summaries,
multi-client provenance collisions, and expired validity windows.

The report is written to `benchmark/latest-adversarial-memory-report.json` and
tracks:

- `raw_task_pass_rate`
- `governed_task_pass_rate`
- required visible record hit rate
- blocked record leak rates
- required warning hit rate
- preferred record match rate
- scenario pass rates

These metrics are fixture governance checks. They are not ranking changes, live
bridge measurements, broad retrieval claims, or productivity claims.

## Reviewed Memory-Evolution Benchmark

The reviewed memory-evolution benchmark is a small deterministic slice for the
0.15 governance question:

> Given synthetic records that represent memory revision hazards, can a report
> harness prove that reviewed governance keeps current records visible while
> blocking obsolete, deleted, quarantined, scope-mismatched, or review-lane-only
> records?

The reviewed cases live in `benchmark/memory-evolution-cases.json`. They
exercise reviewed supersession lineage, deletion/tombstone audit without
retaining deleted content, untrusted provenance quarantine, principal-scope
filter warnings, point-in-time validity, and hidden learning-candidate/review
lanes.

The report is written to `benchmark/latest-memory-evolution-report.json` and
tracks:

- `raw_task_pass_rate`
- `governed_task_pass_rate`
- required visible record hit rate
- blocked record leak rates
- required warning hit rate
- disposition reason hit rate
- blocked reason and warning counts
- scenario pass rates

These metrics are local governance checks. They are not ACL enforcement, privacy
compliance, poisoning certification, autonomous mutation, or graph-memory
claims.

## Reviewed Memory-Operations Queue Benchmark

The reviewed memory-operations queue benchmark is a small deterministic slice
for the 0.16 operator workflow question:

> Given staged learning candidates, review receipts, tombstones, quarantined
> claims, and stale records, can AMB produce a proposal-only operator queue without
> turning review material into durable authority?

The report is written to `benchmark/latest-review-queue-report.json` and tracks:

- `review_queue_item_count`
- `review_queue_actionable_count`
- `review_queue_hidden_lane_count`
- `review_queue_writeback_plan_count`
- `review_queue_no_auto_mutation`
- `review_queue_public_mcp_surface_change`
- `review_queue_item_type_count`

These metrics are local operator-workflow checks. They are not a review UI,
autonomous promotion, deletion automation, policy approval, or a new MCP
surface.

## Human Review Workflow Benchmark

The human review workflow benchmark is a small deterministic slice for the 0.17
operator workflow question:

> Given the reviewed memory-operations queue, can AMB produce explicit human
> decision prompts and manual steps without executing durable writeback?

The report is written to `benchmark/latest-review-workflow-report.json` and
tracks:

- `review_workflow_source_queue_item_count`
- `review_workflow_item_count`
- `review_workflow_manual_step_count`
- `review_workflow_requires_human_count`
- `review_workflow_auto_write_count`
- `review_workflow_no_auto_writeback`
- `review_workflow_public_mcp_surface_change`
- `review_workflow_item_type_count`

These metrics are local human-review checks. They are not a review UI,
autonomous reviewer, writeback executor, policy approval system, or a new MCP
surface.

## Task Brief Benchmark

The Task Brief benchmark is a small deterministic slice for the context-assembly
question:

> Given existing AMB task memory, review queue items, and active signals, can AMB
> render an operator-facing brief that separates used, ignored, and needs-review
> context without executing durable writeback?

The report is written to `benchmark/latest-task-brief-report.json` and tracks:

- `task_brief_used_count`
- `task_brief_ignored_count`
- `task_brief_needs_review_count`
- `task_brief_review_queue_item_count`
- `task_brief_active_signal_count`
- `task_brief_no_auto_writeback`
- `task_brief_public_mcp_surface_change`
- `task_brief_needs_review_source_type_count`

These metrics are local context-assembly checks. They are not a runtime adapter,
watcher, scheduler, automatic reviewer, writeback executor, or a new MCP
surface.

## Activation Stress

The activation stress pack is intentionally conservative. It reuses reviewed belief
cases plus isolated replay scenarios so we can shake the learning ladder without
replaying a live bridge back into itself.

## v0.21 Governed Change Proof

The pre-v0.21 governed-change proof executes the exact reviewed manifest in
`benchmark/v0.21-governed-change-manifest.json`. Each of its 20 cases gets one
fresh temp `MemoryStore` and two checkpoints. The proof compares an equal-budget
flat path with governed task memory, builds a Task Brief at every checkpoint,
and exercises real forget, tombstone, lineage, recall, browse, export, FTS, and
hash-embedding behavior where the case requires it.

The deterministic report is written to
`benchmark/latest-v0.21-governed-change-report.json` and gates on:

- the exact manifest SHA256 and fixed 20-case / 40-checkpoint denominators
- `flat_baseline_hazards: 17/20`
- `governed_failures: 0/20`
- `governed_checkpoint_passes: 40/40`
- useful current guidance or corrective evidence at every checkpoint
- the unchanged 10-tool public MCP surface
- temp-only runtime writes, zero config writes, and zero durable live writeback

This is a local deterministic pre-release proof, not a version bump, runtime
adapter, new MCP surface, auto-writeback system, or use of private operator data.
