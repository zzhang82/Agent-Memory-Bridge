# Roadmap

Last updated: 2026-04-05

Agent Memory Bridge already covers the foundation:

- MCP-native `store` and `recall`
- session-aware capture
- summary -> learn / gotcha / domain-note promotion
- first-pass domain consolidation

The next work is about making the memory more useful, not making the system heavier.

## Near Term

### Better learning quality

1. Add a durable-event scorer for checkpoint and closeout lines.
2. Improve promotion quality on reviewed real samples.
3. Expand `gotcha` extraction beyond the current known patterns.

### Better synthesis

1. Strengthen domain/topic synthesis across many sessions.
2. Improve topic clustering quality.
3. Reduce generic or weak domain notes.

### Better coordination

1. Add an active signal consumer loop on top of `since` polling.
2. Make signal-driven handoff and review flows easier to build on top of the bridge.

## Later

### Data-gated tuning

These depend on more real usage:

- calibrated durable-memory selection
- split audit tracing from deduplicated reusable memory
- low-frequency fallback checkpoints for long-running sessions with weak explicit signal

### Research track

- pre-compaction capture before model-side context loss

### Separate future track: worker execution

These are intentionally outside the bridge core:

- task-query records
- worker-facing task consumers
- local open-source worker MCPs
- model-routing policy for local execution
- task-result writeback into bridge memory

## Guardrails

- keep the bridge focused on memory and signaling
- keep worker execution as a separate layer
- prefer compact machine-first records over transcript growth
- do not expand the MCP surface unless real workflows require it
