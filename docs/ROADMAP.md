# Roadmap

Last updated: 2026-04-05

Agent Memory Bridge already covers the foundation:

- MCP-native `store` and `recall`
- session-aware capture
- `summary -> learn / gotcha / domain-note` promotion
- first-pass domain consolidation

The next stage is about memory governance:

- what to keep
- what to down-rank
- what to assemble into context
- what to synthesize into reusable engineering knowledge

The goal is not to make the bridge bigger. The goal is to make the memory more selective, more structured, and more useful in real coding workflows.

## Near Term

### Better lifecycle and retention

1. Add a durable-event scorer for checkpoint and closeout lines.
2. Add clearer retention signals such as confidence, freshness, reuse, and support count.
3. Add stale-handling and demotion rules so weak memory does not accumulate forever.

### Better recall assembly

1. Define an explicit recall assembly policy for startup and task-time retrieval.
2. Improve how project, global, gotcha, and domain layers are combined into compact working context.
3. Generate smaller machine-first briefs instead of dumping more raw memory into context.

### Better synthesis and relations

1. Strengthen domain and topic synthesis across many sessions.
2. Add lightweight relationship fields such as `derived_from`, `fix_for`, `related_problem`, and `supersedes`.
3. Reduce generic or weak domain notes.

### Better evaluation

1. Add practical retrieval checks for repeated engineering issues and known fixes.
2. Measure whether cross-project gotcha reuse actually reduces repeated work.
3. Add consolidation quality checks so synthesis improves instead of drifting.

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
- event segmentation that better distinguishes routine chatter from durable decisions
- longer-term lifecycle ideas inspired by memory-system research without turning the bridge into a heavyweight cognitive platform

### Separate future track: worker execution

These are intentionally outside the bridge core:

- task-query records
- worker-facing task consumers
- local open-source worker MCPs
- model-routing policy for local execution
- task-result writeback into bridge memory

## Guardrails

- keep the bridge focused on memory lifecycle and signaling
- keep worker execution as a separate layer
- prefer compact machine-first records over transcript growth
- do not expand the MCP surface unless real workflows require it
- do not turn the bridge into a universal cognitive platform before the narrow coding-agent use case is clearly strong
