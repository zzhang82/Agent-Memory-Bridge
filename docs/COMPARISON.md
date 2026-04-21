# Comparison

This note is about positioning, not benchmarks.

Agent Memory Bridge is intentionally narrower than broader memory platforms.

## Short Version

- **Agent Memory Bridge**: small, MCP-native memory for coding-agent workflows
- **OpenMemory**: broader long-term memory platform with SDKs, server, dashboard, and a larger cognitive model
- **Mem0**: broad memory layer for agent and app personalization across multiple frameworks and deployment modes

## What This Project Optimizes For

Agent Memory Bridge is optimized for:

- coding-agent workflows
- local-first operation
- two-channel memory plus coordination signals
- a very small MCP surface
- inspectable storage
- turning session traces into reusable engineering memory
- progressively more structured and applicable task-time memory without becoming a larger runtime platform

The center of gravity is:

`session -> summary -> learn / gotcha -> domain-note -> belief -> concept-note`

Procedures sit beside that ladder as curated durable artifacts that can be assembled
at task time.

## What It Does Not Try To Be

This project is not trying to be:

- a universal cognitive memory engine
- a dashboard-heavy memory platform
- a connector-first data ingestion product
- a full hosted backend from day one

## Comparison Table

| Project | Primary shape | Best fit | Runtime bias |
| --- | --- | --- | --- |
| Agent Memory Bridge | MCP-native memory substrate for coding agents | Codex and coding-agent workflows that want reusable decisions, gotchas, task-time guidance, and coordination signals | local-first, intentionally small |
| OpenMemory | broader long-term memory engine and platform | teams that want SDKs, server modes, dashboards, connectors, and a richer memory model | local-first plus platform and server surfaces |
| Mem0 | broad memory layer for AI apps and agents | teams that want a larger ecosystem, SDKs, hosted options, and generalized memory APIs | platform-oriented |

## Why The Surface Is Small

Agent Memory Bridge keeps the public MCP contract small on purpose:

- `store`
- `recall`
- `browse`
- `stats`
- `forget`
- `promote`
- `claim_signal`
- `extend_signal_lease`
- `ack_signal`
- `export`

The complexity sits behind the bridge:

- watcher
- checkpoint and closeout sync
- reflex promotion
- consolidation
- signal lifecycle with lease renewal, hard expiry, and acknowledgement
- task-time assembly over procedures, concepts, beliefs, and linked support

That keeps the integration contract simple while still allowing the memory engine
to improve over time.

## The Wedge

The wedge is not "memory for everything."

The wedge is:

**persistent, reusable engineering memory for coding agents**

That is the shape this project is trying to win first.
