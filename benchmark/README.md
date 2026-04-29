# Benchmark Plan

This directory holds the proof and benchmark track for Agent Memory Bridge.

The goal is not a big leaderboard suite. It is a small, repeatable set of checks
that tell us whether the bridge stays useful as a coding-agent memory layer while
the engine gets more expressive.

## Runnable Entry Points

- `python ./scripts/run_benchmark.py`
- `python ./scripts/run_deterministic_proof.py`
- `python ./scripts/run_classifier_calibration.py`
- `python ./scripts/run_classifier_calibration.py --fixture-gateway`
- `python ./scripts/run_activation_stress_pack.py`
- `python ./scripts/run_task_memory_benchmark.py`
- `python ./scripts/run_procedure_governance_benchmark.py`
- `python ./scripts/run_signal_contention_benchmark.py`

## What The Reports Cover

The checked-in proof and benchmark flow covers:

- deterministic signal lifecycle checks for `claim`, `extend`, `ack`, expiry, reclaim, and fairness
- duplicate suppression
- recall timing
- relation metadata surfaced through recall, export, stats, and proof
- retrieval comparison against a simple file-scan baseline
- `precision@1`, `precision@3`, `recall@1`, `recall@3`, `MRR`, and `expected_top1_accuracy`
- reviewed classifier-vs-fallback calibration
- isolated learning-ladder activation stress cases
- reviewed task-memory packet comparison between flat/current assembly and relation-aware assembly
- reviewed procedure-governance packet comparison between flat/current assembly and governed procedure assembly
- reviewed signal contention lifecycle cases for unique claims, stale-owner avoidance, reclaim, and done/expired leakage

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

## Activation Stress

The activation stress pack is intentionally conservative. It reuses reviewed belief
cases plus isolated replay scenarios so we can shake the learning ladder without
replaying a live bridge back into itself.
