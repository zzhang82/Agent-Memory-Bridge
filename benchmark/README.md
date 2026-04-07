# Benchmark Plan

This directory is the proof track for Agent Memory Bridge.

The first goal is not a big benchmark suite. It is a small, repeatable set of checks that prove the bridge is useful as a coding-agent memory layer.

Initial metrics:

1. recall latency
2. recall relevance / precision@k
3. duplicate suppression rate
4. signal coordination latency / correctness

The point of this benchmark set is to show more than "the tools work." It should show whether the bridge improves retrieval quality, keeps noise down, and makes coordination signals fast enough to use in real workflows.
