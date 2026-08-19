# Choosing a Memory Architecture for Coding Agents

This document compares **decision categories**, not benchmark scores or vendor rankings. Agent Memory Bridge (AMB), broad memory layers, packaged coding-agent memory, managed Context Graph platforms, temporal graph frameworks, and memory operating systems can all be described as “agent memory,” but they make materially different choices about authority, deployment, context construction, automation, and review.

**Externally reviewed:** 2026-08-19. External product facts below are limited to the primary sources listed in [Sources reviewed](#sources-reviewed). Product capabilities and release information can change after that review date.

> **Comparison discipline:** a documented feature is not proof of feature parity, production fitness, adoption, security certification, or superior outcomes. Architectural fit is an interpretation stated as such; it is not a vendor ranking.

## Start With the Operating Model

AMB is a **local-first, governed engineering-memory bridge for coding agents**. Its public boundary is a small local stdio MCP surface. SQLite/WAL holds durable memory, exact-key Dynamic State, and episode authority; lifecycle-aware recall and governed task-memory assembly are rebuildable derived views. The Context Compiler turns governed task memory, exact state snapshots, and explicit session-local material into transient bounded context. Context attestation stores metadata and digests only, while evaluation linkage is read-only evidence rather than a causal or learning claim. See the [Architecture](ARCHITECTURE.md), [Production Status](PRODUCTION-STATUS.md), [Authority Contract](AUTHORITY-CONTRACT.md), [Trust Boundary](TRUST-BOUNDARY.md), and [Closed-Loop Episode Authority](CLOSED-LOOP-EPISODE.md) for the current implementation and limits.

This means AMB is not a hosted multi-tenant platform, graph database, universal cognitive-memory layer, agent runtime, task queue, automatic-learning system, or authentication/ACL service. It keeps a deliberately narrow distinction between durable authority, derived selection, transient prompt context, historical evidence, and human-reviewed improvement.

## Comparison at a Glance

The entries below are intentionally adjacent but **not treated as interchangeable categories**. The table records documented facts; the final column gives a stated architectural-fit interpretation.

| Target | Documented primary shape | Documented operating model | Decision-oriented interpretation |
|---|---|---|---|
| **AMB** | Local-first governed engineering memory exposed through a 17-tool local stdio MCP surface. | SQLite/WAL durable authority; lifecycle-aware governed selection; transient context; metadata-only attestation; read-only evaluation linkage. | Choose when local engineering memory, explicit authority boundaries, and reviewable evidence matter more than platform breadth. |
| **Mem0** | A long-term memory layer for AI agents, with a managed platform, open-source/self-hosted option, API, framework integrations, and coding-agent plugins. [1] | Managed API-key path or self-hosted library/Docker path. [1] | Consider when an application or agent needs a broader memory layer and deployment/integration choice beyond a narrow local bridge. |
| **OpenMemory** | A persistent MCP memory layer for coding agents that documents automatic capture, project/repository-scoped memory retrieval, automatic context delivery, and memory management controls. [2] | Packaged coding-agent workflow in the Mem0 ecosystem. [2] | Consider when automatic coding-preference capture and delivery are the desired control boundary. |
| **Zep** | A managed enterprise agent-memory platform built on Context Graphs, describing context assembly, access control, retention, provenance, and audit. [3] | Managed graph-oriented platform with enterprise deployment options. [3] | Consider when a managed, enterprise-scale Context Graph service is the primary architectural need. |
| **Graphiti** | Zep’s open-source temporal Context Graph framework, with real-time incremental updates, episodes, hybrid semantic/keyword/graph search, and a documented MCP server. [4] | Framework and graph-backed integration model, distinct from Zep’s managed platform. [4] | Consider when temporal graph modeling and graph-native retrieval are foundational requirements. |
| **MemOS** | A memory operating-system project with unified memory APIs, MemCubes, multiple memory representations, scheduling, cloud/self-hosted paths, and agent plugins. [5] [6] | Broader memory-system architecture with cloud, self-hosted, and local-plugin routes. [5] [6] | Consider when a team wants a wider memory operating system, including memory modules and plugin ecosystem, rather than a narrow engineering-memory bridge. |

## The Decision Dimensions That Matter

| Dimension | AMB | Broad memory layers and packaged coding memory | Context Graph platforms and temporal graph frameworks | Memory operating systems |
|---|---|---|---|---|
| **Primary job** | Govern reusable engineering knowledge and task-time context for coding agents. | Persist and deliver agent/application or coding-workflow memory. [1] [2] | Model and retrieve evolving relationships and facts as graph context. [3] [4] | Organize and operate multiple kinds of memory across an agent system. [5] [6] |
| **Authority model** | Durable memory, state, and episode records are distinct from derived views and transient context. | Product-specific memory representations and controls; do not assume AMB-style authority semantics. | Graph facts, provenance, and retrieval are central concepts; do not assume AMB-style episode authority. [3] [4] | MemCube and memory-module abstractions are central; do not assume AMB-style governance or review semantics. [5] [6] |
| **Context construction** | Governed task-memory assembly feeds a transient bounded Context Compiler; the compiler does not create a second retrieval path. | OpenMemory documents automatic capture and context delivery; Mem0 documents a general memory layer. [1] [2] | Zep documents automated context assembly; Graphiti documents graph/semantic/keyword hybrid search. [3] [4] | MemOS documents store/retrieve/manage APIs and memory scheduling. [5] [6] |
| **Automation boundary** | No automatic durable writeback, ranking/prompt mutation, or autonomous skill acquisition; downstream consolidation is shadow-only and reviewable. | OpenMemory documents automatic capture and delivery. [2] | Graph updates and context assembly are product/framework concerns documented by Zep and Graphiti. [3] [4] | MemOS documents plugin routes with automatic recall and retention, and a tiered skill-evolution model. [5] |
| **Deployment and integration** | Local stdio MCP; intentionally not hosted HTTP or multi-tenant identity/ACL infrastructure. | Mem0 documents managed and self-hosted paths; OpenMemory targets MCP-compatible coding agents. [1] [2] | Zep is managed; Graphiti is open source and documents an MCP server. [3] [4] | MemOS documents cloud, self-hosted, and local agent-plugin paths. [5] [6] |

The table does not mark a single approach as “more capable.” It identifies which concern each architecture makes central. For example, a team may rationally choose a Context Graph because temporal entity relationships are its core product data model, or choose a packaged coding-agent memory layer because automatic capture is intentional. Neither choice makes AMB’s local authority and review boundary obsolete; it means the team has a different operating requirement.

## What the Sources Support About Each Alternative

### Mem0: broad memory layer for applications and agents

Mem0’s official documentation describes long-term memory that persists across sessions, tools, and runs. It presents a managed platform, an open-source option that can run as a library or Docker stack, coding-agent plugins, and framework integrations. [1] The source supports describing Mem0 as a broader memory layer with managed and self-hosted routes. It does **not** support treating it as a drop-in implementation of AMB’s lifecycle-governed task-memory assembly, Dynamic State authority, or episode/verification contracts.

**Architectural-fit interpretation.** Mem0 is a reasonable starting point when a product needs broad memory infrastructure and deployment choice for application or agent integration. AMB is more focused when the problem is local, inspectable engineering memory for coding-agent work and the team wants explicit separation between durable records, derived context, and reviewable evidence.

### OpenMemory: packaged coding-agent memory with automatic delivery

OpenMemory describes itself as a persistent MCP memory layer for coding agents. Its official page documents automatic capture of coding preferences, patterns, and setup; project/repository-matching retrieval; automatic injection of relevant memory into an agent; and controls to browse, tag, manage, version, and set visibility for memories, with access logs. [2]

**Architectural-fit interpretation.** OpenMemory is relevant when automatic capture and automatic context delivery are deliberate product choices for a coding-agent workflow. AMB instead makes lifecycle-governed selection explicit, renders compiled context transiently, and does not automatically write lessons back into durable memory. This is a difference in automation and authority boundaries, not a claim that one design is universally superior.

### Zep: managed Context Graph memory for enterprise-scale operation

Zep’s official product page positions its service as managed agent memory built on Context Graphs. It documents graph construction from chat history, business data, and user interactions, along with automated context assembly and substrate-level access control, retention, provenance, and audit. [3] Those are managed-platform facts, not evidence that Zep’s model is equivalent to a local MCP bridge.

**Architectural-fit interpretation.** Zep is a relevant alternative when a managed enterprise Context Graph service, graph-scale operation, and centrally governed access/retention/audit are primary requirements. AMB is the narrower choice for trusted local operators who want a local engineering-memory authority and who do not need a hosted multi-tenant product surface.

### Graphiti: open-source temporal Context Graph framework

Graphiti’s official documentation calls it an open-source framework for temporal knowledge graphs, also called Context Graphs. The docs describe real-time incremental graph updates without batch recomputation, adding text or JSON episodes, hybrid semantic/keyword/graph search, custom entities, and a Graphiti MCP server for assistants such as Claude Desktop or Cursor. [4]

**Architectural-fit interpretation.** Graphiti is relevant when temporal graph structure, custom entities/edges, and graph-native retrieval are core design requirements. AMB’s lifecycle state and revision history govern engineering-memory eligibility, but AMB does not claim to be a temporal knowledge-graph framework or graph database.

### MemOS: a broader memory operating-system architecture

The MemOS repository and documentation describe a Memory Operating System for LLMs and agents. Its documented surface includes a unified API to add, retrieve, edit, and delete memory; multimodal and tool memory; composable MemCubes; asynchronous ingestion; feedback/correction; cloud API, self-hosted service, and local/cloud plugin routes. Its documentation also presents textual, activation/KV-cache, and parametric memory modules. [5] [6]

The repository additionally documents some local and cloud plugin behaviors that automatically recall context before a task and retain experience after a successful turn, plus a tiered skill-evolution model. [5] This is evidence of MemOS’s stated operating model; it is not a claim about its outcome quality or a cross-project benchmark conclusion.

**Architectural-fit interpretation.** MemOS is relevant when a team wants a broad memory-system architecture with multiple memory representations, plugins, and optional automated retention or skill-evolution behavior. AMB explicitly chooses a smaller local engineering-memory scope, keeps learning/consolidation shadow-only, and requires review rather than automatic durable change.

## Choosing a Direction

Choose **AMB** when the principal need is a shared and inspectable engineering record for coding agents, with a small local MCP boundary, lifecycle-governed context selection, separate mutable state authority, and evidence that can inform human review without becoming automatic learning. It is especially appropriate when a team wants to preserve the distinction between “selected context,” “evidence linked to an outcome,” and “a reviewed change to durable knowledge.”

Choose **Mem0** when the principal need is a general-purpose memory layer for an application or agent system, particularly when managed API access, framework integrations, or a self-hosted memory stack are stronger requirements than AMB’s narrow local engineering-memory contract. [1]

Choose **OpenMemory** when the principal need is coding-agent memory with automatic capture, project-scoped retrieval, automatic context delivery, and the product controls documented by its official page. [2]

Choose **Zep** when the principal need is a managed enterprise Context Graph platform with graph-centric context assembly and centrally operated governance features. Choose **Graphiti** when the requirement is instead an open-source temporal graph framework with graph-native retrieval and an MCP integration path. [3] [4]

Choose **MemOS** when the principal need is a broader memory operating system with memory modules, MemCube composition, scheduling, and plugin/deployment options that are intentionally beyond AMB’s scope. [5] [6]

## AMB’s Deliberate Boundaries

The narrowness of AMB is intentional. The current implementation does not claim hosted execution, an HTTP product surface, a scheduler or queue, OAuth/ACLs, authenticated actor identity, automatic reranking, online training, automatic durable writeback, automatic prompt/policy mutation, or autonomous skill acquisition. The Context Compiler is internal and transient; it does not add a public tool, persist prompt-facing context bodies, or bypass governed recall. A metadata-only context attestation and a read-only evaluation linkage do not prove that a memory was applied or caused a verified result. See [Production Status](PRODUCTION-STATUS.md), [Architecture](ARCHITECTURE.md), [Trust Boundary](TRUST-BOUNDARY.md), and [Closed-Loop Episode Authority](CLOSED-LOOP-EPISODE.md).

These boundaries should remain visible when comparing AMB with products that intentionally automate capture, delivery, retention, graph updates, or skill evolution. The correct question is not which label—“memory,” “graph,” or “OS”—sounds broader. It is which authority, deployment, automation, and review model fits the work.

## Sources Reviewed

| Source | Product / scope | Reviewed |
|---|---|---|
| [Mem0 documentation][1] | Managed platform, integrations, and open-source/self-hosted overview. | 2026-08-19 |
| [OpenMemory official product page][2] | Coding-agent MCP positioning, automatic capture/delivery, project scope, and controls. | 2026-08-19 |
| [Zep official product page][3] | Managed Context Graph platform, context assembly, governance, and provenance/audit positioning. | 2026-08-19 |
| [Graphiti official documentation][4] | Open-source temporal Context Graph framework, graph updates, search, and MCP server. | 2026-08-19 |
| [MemOS official repository][5] | Memory Operating System, APIs, plugins, deployment routes, and documented automation behavior. | 2026-08-19 |
| [MemOS official documentation][6] | Modular memory architecture, MemCube, memory types, and open-source concepts. | 2026-08-19 |

[1]: https://docs.mem0.ai/introduction "Mem0 Documentation — Build with Mem0"
[2]: https://mem0.ai/openmemory "OpenMemory — Mem0"
[3]: https://www.getzep.com/ "Zep — Agent memory at enterprise scale"
[4]: https://help.getzep.com/graphiti/getting-started/welcome "Graphiti Documentation — Welcome"
[5]: https://github.com/MemTensor/MemOS "MemTensor/MemOS — GitHub"
[6]: https://memos-docs.openmem.net/open_source/home/overview/ "MemOS Documentation — Overview"
