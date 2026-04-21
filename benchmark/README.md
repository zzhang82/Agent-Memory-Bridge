# Benchmark Plan

This directory holds the proof and benchmark track for Agent Memory Bridge.

The goal is not a big leaderboard suite. It is a small, repeatable set of checks
that tell us whether the bridge stays useful as a coding-agent memory layer while
the engine gets more expressive.

## Runnable Entry Points

- `python .\\scripts\\run_benchmark.py`
- `python .\\scripts\\run_deterministic_proof.py`
- `python .\\scripts\\run_classifier_calibration.py`
- `python .\\scripts\\run_classifier_calibration.py --fixture-gateway`
- `python .\\scripts\\run_activation_stress_pack.py`

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

## Activation Stress

The activation stress pack is intentionally conservative. It reuses reviewed belief
cases plus isolated replay scenarios so we can shake the learning ladder without
replaying a live bridge back into itself.
