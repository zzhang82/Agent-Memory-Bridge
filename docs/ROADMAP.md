# Roadmap

Last updated: 2026-04-06

Agent Memory Bridge now covers the v0.4 foundation:

- two-channel state: `memory` and `signal`
- inspectable MCP tools: `browse`, `stats`, `forget`, `promote`, `export`
- signal lifecycle primitives: `claim_signal`, `ack_signal`, lease, and TTL
- session-aware capture from Codex rollout files
- `summary -> learn / gotcha / domain-note` promotion
- first-pass domain consolidation
- neutral core defaults with profile-shaped config on top

The next stage is about proof, selection, and composition.

## Near Term

### Publish proof

1. Add benchmark docs for recall quality, latency, duplicate suppression, and token efficiency.
2. Show simple before/after cases where reusable memory reduces repeated work.
3. Add coordination benchmarks for signal polling and claim latency.

### Improve memory governance

1. Add stronger durable-event scoring for checkpoint and closeout lines.
2. Add clearer retention signals such as confidence, freshness, reuse, and support count.
3. Add stale-handling and demotion rules so weak memory does not accumulate forever.

### Finish storage reorganization

1. Continue splitting storage around the new semantics.
2. Pull signal-specific persistence and query logic into clearer modules.
3. Reduce cross-cutting responsibilities inside `storage.py`.

### Improve recall assembly

1. Define an explicit recall assembly policy for startup and task-time retrieval.
2. Improve how project, global, gotcha, and domain layers are combined into compact working context.
3. Generate smaller machine-first briefs instead of dumping more raw memory into context.

## Later

### Richer coordination

These build on top of the current signal lifecycle:

- retry and dead-letter handling
- stronger consumer ownership semantics
- signal-driven review and handoff flows
- active signal consumer loops

### Data-gated tuning

These depend on more real usage:

- calibrated durable-memory selection
- split audit tracing from deduplicated reusable memory
- low-frequency fallback checkpoints for long-running sessions with weak explicit signal

### Research track

- pre-compaction capture before model-side context loss
- event segmentation that better distinguishes routine chatter from durable decisions
- longer-term lifecycle ideas inspired by memory-system research without turning the bridge into a heavyweight cognitive platform

## Guardrails

- keep the bridge focused on memory lifecycle and signaling
- keep worker execution as a separate layer
- prefer compact machine-first records over transcript growth
- do not expand the MCP surface unless real workflows require it
- do not turn the bridge into a universal cognitive platform before the narrow coding-agent use case is clearly strong
