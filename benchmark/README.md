# Benchmark Plan

This directory is the proof track for Agent Memory Bridge.

The first goal is not a big benchmark suite. It is a small, repeatable set of checks that prove the bridge is useful as a coding-agent memory layer.

Initial proof order:

1. signal coordination latency / correctness
2. recall latency
3. duplicate suppression rate
4. recall relevance / precision@k

The point of this benchmark set is to show more than "the tools work." It should show whether the bridge improves retrieval quality, keeps noise down, and makes coordination signals fast enough to use in real workflows.

Current runnable entrypoints:

- `python .\\scripts\\run_benchmark.py`
- `python .\\scripts\\run_deterministic_proof.py`
- `python .\\scripts\\run_classifier_calibration.py`

The benchmark report combines two layers:

- deterministic proof for signal correctness, duplicate suppression, and recall timing
- retrieval comparison for `precision@1`, `precision@3`, `expected_top1_accuracy`, and latency against a simple file-scan baseline
- classifier-vs-fallback regression coverage in tests so learning-quality changes can roll out in shadow mode first
- reviewed-sample calibration that compares expected tags, keyword fallback tags, raw classifier tags, retained classifier tags, and low-confidence filtering side by side

The goal is not to win a leaderboard. It is to make regressions visible and keep the bridge honest as retrieval and signal semantics evolve.

Classifier calibration is intentionally narrow. It is there to answer:

- where the classifier already beats keyword fallback
- where fallback still wins
- where low-confidence classifier output should stay out of assist-mode enrichment
- whether widening `assist` mode would be justified

The current fixture set now includes:

- exact-match retrieval checks
- punctuation-heavy fallback checks
- multi-relevant memory queries
- multi-relevant signal queries
- overlap-heavy review queue and release-cutover cases
- context-compaction checklist vs bridge-note ambiguity
