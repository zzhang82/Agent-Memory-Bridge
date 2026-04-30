# Context Assembly

Agent Memory Bridge can be described as a small context compiler for coding
agents: it turns stored engineering memory into the context an agent needs at
startup or during a task.

This is a story over the existing MCP surface. It does not add `task_packet`,
`startup_packet`, or any other new MCP tool.

## Existing Surface

The public MCP contract stays:

- `store`, `recall`, `browse`, `stats`
- `forget`, `promote`, `export`
- `claim_signal`, `extend_signal_lease`, `ack_signal`

Assembly happens behind that surface by selecting, filtering, and rendering
records that were already stored through ordinary memory operations.

## Startup Context

Startup context is the small set of durable guidance an agent should recover
before it starts touching files:

- global operating or team norms
- relevant specialization notes
- active project memory
- project/global gotchas when the work is issue-like
- domain notes when they directly match the task

The important constraint is size. Startup should load enough context to avoid
known mistakes, but not so much that old notes become a second prompt history.

## Task-Time Context

Task-time context is assembled after the agent knows the concrete issue. It can
combine:

- procedures that explain how to do a task safely
- concept notes that summarize stable patterns
- beliefs or decisions that should guide judgment
- gotchas that prevent repeated mistakes
- linked supporting records, when relation metadata makes the inclusion useful

The result can look like a task packet, but it is not a separate public tool. It
is a compact rendering of existing records for the current job.

## Why Not Add Packet Tools

Keeping packets behind the bridge avoids expanding the public API before the
assembly policy settles.

- Clients only need to learn the stable memory and signal tools.
- The ranking, suppression, and rendering logic can improve without a migration.
- Operators can preview or benchmark assembly with local scripts.
- MCP clients that only support basic tool calls still work.

The contract is therefore simple: store useful memory, recall relevant memory,
and let the engine get better at compiling that memory into the right context.
