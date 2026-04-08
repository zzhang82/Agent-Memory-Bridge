# Roadmap

Last updated: 2026-04-07 (America/New_York)

Agent Memory Bridge now covers the v0.6 foundation:

- two-channel state: `memory` and `signal`
- inspectable MCP tools: `browse`, `stats`, `forget`, `promote`, `export`
- signal lifecycle primitives: `claim_signal`, `ack_signal`, lease, and TTL
- session-aware capture from Codex rollout files
- `summary -> learn / gotcha / domain-note` promotion
- first-pass domain consolidation
- neutral core defaults with profile-shaped config on top
- benchmarked retrieval with `expected_top1_accuracy`
- classifier-assisted reflex enrichment with `shadow` and `assist` rollout modes plus deterministic rule fallback
- reviewed classifier calibration on labeled samples
- broader canonical benchmark fixtures with more overlap-heavy retrieval cases

The next stage is about calibration, fairness, and broader learning quality.

## Near Term

### Calibrate learning quality

1. Compare classifier output against a larger reviewed sample set before expanding `assist` mode by default.
2. Add clearer durable-event scoring for checkpoint and closeout lines.
3. Add richer domain/topic synthesis over many sessions without dropping the deterministic fallback path.

### Improve coordination fairness

1. Add claim-selection fairness so one polling consumer does not win by accident.
2. Add retry boundaries before considering dead-letter handling.
3. Add stronger coordination benchmarks for claim, extend, ack, and reclaim behavior under contention.

### Keep benchmark coverage honest

1. Keep tracking `precision@k` and `expected_top1_accuracy` together.
2. Add more real-world ambiguity cases so ranking does not overfit the current canonical corpus.
3. Add a small reviewed retrieval set that captures where ranking helps and where it still drifts.

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
