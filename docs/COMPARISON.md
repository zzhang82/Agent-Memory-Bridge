# Comparison

This note is about positioning, not benchmarks. It is intentionally narrow and
source-linked so Agent Memory Bridge can describe its wedge without pretending to
be a universal memory platform.

Last reviewed: 2026-04-30.

## Short Version

- **Agent Memory Bridge**: a local-first MCP bridge and context compiler for
  coding agents. It turns durable engineering memory and lightweight coordination
  state into recallable, task-time context while keeping the public tool surface
  small.
- **Mem0**: a broader memory layer for AI apps and agents, with managed
  platform, open-source/self-hosted options, SDKs, integrations, graph memory,
  rerankers, and hosted MCP support.
- **OpenMemory**: Mem0's coding-agent-oriented MCP memory product. It focuses on
  auto-capture, memory organization, project-scoped context, and delivery to MCP
  clients with a richer app/plugin experience.
- **Zep / Graphiti**: a context-engineering and temporal knowledge graph stack
  for agent memory, graph search, changing facts, and business/user context.
- **MemOS**: a memory operating-system project for LLM and agent systems, with
  MemCube abstractions, multiple memory types, scheduling, cloud/self-hosted
  paths, and plugin surfaces.

## What This Project Optimizes For

Agent Memory Bridge optimizes for a smaller job:

- coding-agent workflows
- local stdio MCP operation
- persistent engineering memory: decisions, gotchas, procedures, concepts,
  beliefs, and supporting records
- two-channel storage: durable memory and short-lived coordination signals
- a small, stable MCP surface
- SQLite + FTS5 storage that can be inspected directly
- task-time and startup context assembly behind the same public tools
- proof discipline: release contract checks, public-surface checks, onboarding
  checks, and benchmark snapshots

The center of gravity is:

`session -> summary -> learn / gotcha -> domain-note -> belief -> concept-note`

Procedures sit beside that ladder as curated durable artifacts that can be
assembled at task time. Signals sit separately so handoffs and reviews do not
pollute durable knowledge.

## What It Does Not Try To Be

This project is not trying to be:

- a universal cognitive memory engine
- a hosted memory platform
- a dashboard-first product
- a graph database or temporal knowledge graph
- a memory operating system
- a connector marketplace
- an agent runtime, scheduler, queue, worker system, or distributed lock

The deliberately plain positioning is:

**persistent, reusable engineering memory for coding agents**

The sharper implementation story is:

**a small MCP context compiler over local, inspectable memory**

## Comparison Table

| Project | Primary shape | Best fit | Runtime bias |
| --- | --- | --- | --- |
| Agent Memory Bridge | MCP-native memory bridge and context compiler for coding agents | teams that want local, inspectable engineering memory, startup/task context assembly, and lightweight coordination without a larger platform | local-first stdio MCP, SQLite + FTS5, intentionally small |
| Mem0 | universal memory layer for AI applications and agents | teams building personalized AI products that want SDKs, managed infrastructure, self-hosting options, graph memory, rerankers, and framework integrations | managed platform or self-hosted stack |
| OpenMemory | Mem0-backed MCP memory layer for coding agents | users who want coding-agent memory with auto-capture, UI/plugin flows, project scoping, and cross-client delivery | local/app/plugin experience plus Mem0 ecosystem |
| Zep / Graphiti | temporal knowledge graph and context-engineering platform | applications that need dynamic user/business context, graph search, changing facts, custom entities, and agent memory at product scale | hosted Zep plus open-source Graphiti and local Graphiti MCP options |
| MemOS | memory OS for LLM and agent systems | teams exploring broader memory management: MemCubes, textual/activation/parametric memory, memory scheduling, multi-agent sharing, cloud/self-hosted plugins | larger system/runtime architecture |

## Honest Trade-Offs

Choose Agent Memory Bridge when:

- the workflow is primarily engineering work with coding agents
- local-first, inspectable storage matters more than hosted convenience
- you want task/startup context assembly without adding more MCP tools
- you want coordination signals, but not a task queue or worker runtime
- you prefer explicit namespaces, record types, and provenance over a black-box
  memory layer

Choose Mem0 when:

- memory is part of a customer-facing or multi-user AI product
- managed hosting, SDK coverage, platform features, graph memory, rerankers, and
  framework integrations matter more than a tiny MCP contract
- the product needs broad user/agent/session memory APIs instead of a
  coding-agent-specific engineering memory loop

Choose OpenMemory when:

- the primary need is cross-client coding-agent memory with a packaged app,
  dashboard, auto-capture, and plugin/lifecycle hooks
- project-scoped memory management and automatic context delivery are more
  important than keeping the MCP surface minimal

Choose Zep / Graphiti when:

- temporal knowledge graphs are the center of the architecture
- facts change over time and invalidation/history are product requirements
- custom entity/edge types, graph search, and user/business data ingestion are
  more important than a simple local engineering-memory bridge

Choose MemOS when:

- you are researching or building a larger memory-management architecture for
  LLM systems
- memory scheduling, MemCube composition, multimodal/tool/activation/parametric
  memory, and cloud or self-hosted plugins are desirable rather than extra
  weight

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

The richer behavior sits behind that bridge:

- watcher
- checkpoint and closeout sync
- reflex promotion
- consolidation
- relation-lite metadata
- signal lifecycle with lease renewal, hard expiry, reclaim, and acknowledgement
- task-time assembly over procedures, concepts, beliefs, and linked support
- startup context assembly through ordinary recall over system/project layers

That keeps the integration contract stable while allowing the memory engine to
improve over time.

## Source Notes

- Mem0 docs describe Mem0 as a universal memory layer with platform and
  open-source paths, managed infrastructure, graph memory, rerankers, SDKs, and
  integrations: [Mem0 introduction](https://docs.mem0.ai/introduction),
  [platform overview](https://docs.mem0.ai/platform/overview), and
  [open-source overview](https://docs.mem0.ai/open-source/overview).
- Mem0 MCP is a hosted HTTP MCP server with memory tools for AI clients:
  [Mem0 MCP](https://docs.mem0.ai/platform/mem0-mcp).
- OpenMemory is Mem0's persistent MCP memory layer for coding agents, with
  auto-capture, organization, project scoping, and context delivery:
  [OpenMemory](https://mem0.ai/openmemory).
- Zep positions itself around context engineering, temporal knowledge graphs,
  agent memory, and graph/context assembly:
  [Zep key concepts](https://help.getzep.com/concepts) and
  [Graphiti overview](https://help.getzep.com/graphiti/getting-started/overview).
- Zep also offers a local Graphiti MCP server for temporal graph memory:
  [Knowledge Graph MCP Server](https://www.getzep.com/product/knowledge-graph-mcp/).
- MemOS describes a memory operating system for LLMs and AI agents with unified
  add/retrieve/manage APIs, MemCubes, multiple memory types, scheduling, and
  cloud/self-hosted paths: [MemOS GitHub](https://github.com/MemTensor/MemOS),
  [MemCube docs](https://memos-docs.openmem.net/open_source/modules/mem_cube/),
  and [MemOS paper](https://arxiv.org/abs/2507.03724).
