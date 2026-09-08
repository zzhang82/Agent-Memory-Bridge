# Canonical Semantic Memory Use Policy

Version: `canonical-policy-v1`  
Policy SHA-256: `4e2b343a03717300f65cd3c123bf641488c5279d7dad8290da7705b237c4ad3c`

This is the portable policy for deciding when a coding agent should consult
configured Agent Memory Bridge (AMB) memory. The structured source of truth is
[`src/agent_mem_bridge/semantic_policy.py`](../src/agent_mem_bridge/semantic_policy.py);
host renderings are derived from it and must not redefine its meaning.

## Decision boundary

Recall before a material decision when project history—an earlier decision,
constraint, unresolved item, failure, gotcha, handoff, or project-specific
rationale—could change the next action. Fresh sessions, compaction, and
cross-client continuation are included. An indirect reference to how the
repository is kept safe can trigger the same check; literal words such as
“memory” or “recall” are not required.

Recall may help with a large design choice or an ambiguity where project
preference could matter. Do not recall merely because a tool or skill is being
used, another reasoning step began, a trivial deterministic edit is underway,
or a sufficient recent recall already answers the question.

When recall is used, make one bounded call in the relevant
`project:<workspace>` namespace. Distinguish a successful empty result from an
unavailable/error route. Check relevance, lifecycle, authority, and current
repository evidence; current live evidence wins over stale or conflicting
history. An agent statement such as “I remember” is not invocation evidence.

Durable memory remains explicit and governed. This policy never performs
automatic learning, durable writeback, prompt mutation, or hidden-reasoning
capture. Invocation evidence is bounded and derived; it may reuse existing
`record_run_event` types (`memory_recalled`, `memory_applied`,
`memory_rejected`, and `observation`) when a run is already being recorded.

## Host rendering

Render a reviewed block for the host instead of copying this policy into each
project:

```bash
<venv-python> -m agent_mem_bridge memory-policy --host codex
<venv-python> -m agent_mem_bridge memory-policy --host opencode
<venv-python> -m agent_mem_bridge memory-policy --host generic
```

The Codex and OpenCode adapters target the project-root `AGENTS.md` surface;
Codex should be checked for a deliberate `AGENTS.override.md`. MCP server
registration remains in each host’s native configuration. The renderer is
manual/export-first, refuses to overwrite an existing output file, and does
not certify that a host loaded or followed the block.

See [Integrations](INTEGRATIONS.md) for the host-specific placement notes and
[Context Assembly](CONTEXT-ASSEMBLY.md) for how invocation policy relates to
the existing derived context path.
