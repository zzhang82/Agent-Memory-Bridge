<p align="center">
  <img src="assets/amb-hero.png" alt="Agent Memory Bridge — governed project memory across sessions and tools" width="100%" />
</p>

<h1 align="center">Agent Memory Bridge</h1>

<p align="center"><strong>Turn scattered project context into governed memory.</strong></p>

<p align="center">
  AMB helps coding agents carry forward the knowledge that matters — across sessions, tools, and time.
</p>

<p align="center"><a href="README.zh-CN.md">简体中文</a></p>

<p align="center">
  <a href="https://pypi.org/project/agent-memory-bridge/"><img src="https://img.shields.io/pypi/v/agent-memory-bridge?logo=pypi&logoColor=white" alt="PyPI" /></a>
  <a href="https://modelcontextprotocol.io"><img src="https://img.shields.io/badge/MCP_Server-Enabled-4A90E2?logo=protocolsdotio&logoColor=white" alt="MCP Server" /></a>
  <a href="https://github.com/zzhang82/Agent-Memory-Bridge/actions/workflows/ci.yml"><img src="https://github.com/zzhang82/Agent-Memory-Bridge/actions/workflows/ci.yml/badge.svg" alt="CI" /></a>
  <a href="https://github.com/zzhang82/Agent-Memory-Bridge/releases"><img src="https://img.shields.io/github/v/release/zzhang82/Agent-Memory-Bridge?logo=github&color=2ea44f" alt="GitHub Release" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-2ea44f.svg" alt="MIT License" /></a>
  <a href="pyproject.toml"><img src="https://img.shields.io/badge/python-3.11%2B-3776AB.svg" alt="Python 3.11+" /></a>
</p>

```bash
pip install agent-memory-bridge
```

**Install once; connect each coding client separately.** Installing the package does not register AMB with every coding agent. Each client you want to use must be configured to launch AMB as an MCP stdio server. Clients that should share memory need to use the same configured local AMB home.

## Your project should not start over with every session

A project is more than its current files. Over time, useful context gets scattered across repositories, chats, coding agents, reviews, fixes, and one-off decisions. A new session can see the code but still miss the reasons, constraints, corrections, and history that make the project make sense.

AMB gives that project context a durable place to accumulate without turning memory into an unreviewable transcript dump.

| Without shared project memory | With AMB |
|---|---|
| Each session reconstructs context | Useful project knowledge carries forward |
| Decisions disappear into old chats | Explicit decisions and reasons stay with the project |
| Different tools build different partial pictures | Supported MCP clients can use the same configured local AMB home |
| Memory can become stale or ambiguous | Provenance, revision, supersession, and inspection keep it governable |

AMB is local-first and inspectable. It does not silently archive every conversation or treat every remembered statement as equal authority.

## One project, memory that grows with it

AMB starts small: derive a reviewable baseline from the repository, then add the decisions, constraints, corrections, and context that are worth carrying forward.

```text
repo / project baseline
        +
explicit decisions and constraints
        +
revisions, corrections, provenance
        ↓
   governed project memory
        ↓
future sessions · coding agents · tools
```

The repository is the common starting point for software projects, but the memory model is about the **project**, not just the codebase: the durable knowledge surrounding the work can outlive any one chat, agent, or tool.

## Quick Start

AMB requires **Python 3.11+**, Git, and an MCP-compatible coding client that can launch a local stdio server.

Current source version: `0.32.1`

Published releases: see [GitHub Releases](https://github.com/zzhang82/Agent-Memory-Bridge/releases)

For the `0.32.1` release line, the normal install route is PyPI. GitHub Releases remains the publication authority for source tags and release notes; an exact source checkout can still be installed with `pip install -e .` for development or audit work.

### 1. Install AMB

For a normal install:

```bash
pip install agent-memory-bridge
```

For a pinned, reproducible `0.32.1` environment, replace `<venv-python>` with the Python executable inside `.amb-venv` for your operating system:

```bash
python -m venv .amb-venv
<venv-python> -m pip install agent-memory-bridge==0.32.1
```

### 2. Connect the coding client(s) you actually use

Installation and client registration are separate. Preview the setup for one client first:

```bash
<venv-python> -m agent_mem_bridge setup --client <client>
```

`setup` is read-only by default: it detects or inspects only bounded client configuration locations and shows the exact AMB fragment or action it recommends. Use a supported client name such as `codex`, `claude-code`, `vscode`, `cursor`, `cline`, `opencode`, or another client listed in [Integrations](docs/INTEGRATIONS.md).

If the preview marks that client as eligible for safe automatic configuration, you can explicitly apply it after review:

```bash
<venv-python> -m agent_mem_bridge setup --client <client> --apply
```

Some clients remain preview/manual because AMB will not guess or rewrite an unsafe configuration format or path. In that case, copy the rendered fragment or follow the client-specific [Integration guide](docs/INTEGRATIONS.md). Repeat this step for every coding client you want to connect. To share the same project memory across clients, keep them pointed at the same configured `AGENT_MEMORY_BRIDGE_HOME`, then reload each client after registration.

### 3. Initialize the project

```bash
<venv-python> -m agent_mem_bridge project init .
```

Project Init detects the local Git repository, proposes a namespace such as `project:my-app`, and waits for confirmation. It then derives a current repository baseline and opens the Human-first Explore view. It does not automatically learn decisions.

### 4. Teach the project one decision that matters

For example, tell the connected coding agent:

> Remember that we decided not to add Redis because this project is intentionally local-first and single-node.

The connected agent uses AMB's existing public memory tools to store the explicit decision and reason. AMB does not infer a durable decision from the code or archive the whole conversation.

### 5. Open a fresh session and reuse the memory

```bash
<venv-python> -m agent_mem_bridge explore \
  --namespace project:my-app

<venv-python> -m agent_mem_bridge inspect \
  --namespace project:my-app \
  --query "Should we add Redis?"
```

Explore answers “What does AMB currently know about this project?” Inspect answers “Why did this information surface for this question?” Both are local and read-only.

This is a conceptual view, not verbatim CLI output:

```text
CODE / WHAT                     CONVERSATION / WHY
────────────────────            ──────────────────────────
Runtime: Python >=3.11          Decision: Do not add Redis
Package: my-app                 Reason: local-first,
Tests: pytest                   single-node project
```

Under the hood, AMB keeps repository-derived facts separate from explicitly taught project knowledge:

**Code tells AMB WHAT the project is.**

**Conversations teach AMB WHY it is that way.**

That distinction is a trust boundary, not the whole product story: derived facts can be rebuilt from current code, while durable project knowledge remains explicit, reviewable, and governed.

<details>
<summary>Refresh and troubleshooting boundaries</summary>

Repository WHAT comes from a clean Git commit. If HEAD changes or the worktree is dirty, AMB will not present an old snapshot as current truth. Refresh is not automatic. Rerun the explicit primitive:

```bash
<venv-python> -m agent_mem_bridge bootstrap-repo . \
  --namespace project:<name>
```

Refreshing repository WHAT leaves durable project WHY unchanged. Explore is CLI-only, not MCP tool #18, and it does not rank context for the model.

`first-run` remains optional guided help; it is not the modern Project Learning entrypoint:

```bash
<venv-python> -m agent_mem_bridge first-run --namespace project:my-app --query "What should I remember?"
```

Use health checks only when setup is uncertain:

```bash
<venv-python> -m agent_mem_bridge doctor
<venv-python> -m agent_mem_bridge verify
```

</details>

## Integrations

AMB works through local stdio MCP. Generic MCP clients are supported; Codex is the reference workflow; Claude Code, Claude Desktop, Cursor, and Cline are documented; and Antigravity, OpenCode, and Hermes have locally tested configuration paths.

Integration labels are deliberately narrow and do not imply client certification. See [Integrations](docs/INTEGRATIONS.md) for current setup instructions and boundaries.

## Why the memory stays trustworthy

The useful part of long-lived project memory is not simply remembering more. It is being able to tell where knowledge came from, whether it is still current, and how it changed.

AMB therefore keeps several boundaries explicit:

| Memory concern | AMB approach |
|---|---|
| Current repository truth | Derived from a clean repository state and refreshed explicitly |
| Human decisions and constraints | Stored explicitly as governed durable memory |
| Changed knowledge | Revised or superseded instead of silently overwritten |
| Why context surfaced | Inspectable through local derived views and evidence paths |
| Cross-session reuse | Shared through the same configured local AMB home |
| Privacy | Local-first; no hosted memory service is required |

This is where the earlier **WHAT / WHY** model belongs: it explains one of the mechanisms that keeps memory trustworthy, rather than defining the entire product.

## What AMB is — and is not

AMB is a governed local project-memory layer for coding agents. It is designed to preserve useful context across sessions and tools while keeping durable knowledge, derived repository facts, provenance, and corrections distinguishable.

It is not a transcript archive, a promise that an agent will remember everything, or a system that silently converts every conversation into durable truth.

## Want the details?

| Read | For |
|---|---|
| [Architecture](docs/ARCHITECTURE.md) | System shape and data flow |
| [Authority model](docs/AUTHORITY-CONTRACT.md) | Durable authority, derived views, correction, and audit rules |
| [Knowledge Explorer](docs/KNOWLEDGE-EXPLORER.md) | Human-first read-only project view |
| [Production Status](docs/PRODUCTION-STATUS.md) | Current implementation facts, evidence, and known limits |
| [Integrations](docs/INTEGRATIONS.md) | Client-specific local MCP setup |
| [Install for Agents](INSTALL_FOR_AGENTS.md) | Full install-to-first-success workflow |
| [Configuration](docs/CONFIGURATION.md) | Complete configuration reference |
| [Examples](examples/README.md) | Sanitized demos and artifacts |

## Technical model

The product story above intentionally postpones implementation vocabulary. Internally, AMB keeps `derived_repository` data separate from governed durable memory so one cannot silently become the other. For maintainers and reviewers, the current authority flow is:

```mermaid
flowchart LR
    A[Durable Memory / WHY] --> C[Lifecycle-aware Recall]
    B[Repository Knowledge / WHAT] --> D[Context Compiler]
    S[Dynamic State Authority] --> D
    C --> E[Governed Task Memory]
    E --> D
    D --> F[Transient Bounded Context]
    F --> G[Metadata-only Context Attestation]
    G --> H[Episode and Run Authority]
    H --> I[Verification Receipt]
    I --> J[Current Verified Outcome]
```

SQLite/WAL rows are durable authority. Repository snapshots, FTS rows, embedding sidecars, compiled context, reports, and Explorer views are derived. Context bodies are rendered in process and are not durably persisted by the compiler.

## Trust and privacy

AMB is local-first. It does not require a hosted memory service. It separates durable memory from coordination Signals and mutable Dynamic State, keeps provenance visible, and rejects raw transcripts, hidden reasoning, and inline artifact bodies from the durable episode path.

Read the [Trust Boundary](docs/TRUST-BOUNDARY.md), [Authority Contract](docs/AUTHORITY-CONTRACT.md), and [Closed-Loop Episode Authority](docs/CLOSED-LOOP-EPISODE.md) for the exact boundaries.

## MCP Tools

AMB exposes **17 public MCP tools**:

- `store`, `recall`, `browse`, and `stats`
- `forget`, `feedback`, `promote`, `annotate`, `revise`, and `export`
- `begin_run`, `record_run_event`, `get_run`, and `complete_run`
- `claim_signal`, `extend_signal_lease`, and `ack_signal`

The public tool surface stays small. Setup, Project Init, Explore, Inspect, context assembly, and review reports remain CLI or internal derived workflows rather than becoming more MCP tools.

The local protocol cache contract is `300000/public` for discovery and `0/private` for the tool list; see [MCP Compatibility](docs/MCP-2026-COMPATIBILITY.md) for details.

## Current maturity

The current source is `0.32.1`, uses schema v12, and retains the frozen 17-tool MCP surface. `project init` is the preferred first-project path. Default Explore is a Human-first view over existing repository-derived context and governed project knowledge. Current evidence and non-claims live in [Production Status](docs/PRODUCTION-STATUS.md); published artifacts live in [GitHub Releases](https://github.com/zzhang82/Agent-Memory-Bridge/releases).

## Contributing

Read [CONTRIBUTING.md](CONTRIBUTING.md) for development and public-surface expectations, and [SECURITY.md](SECURITY.md) for vulnerability reporting.

Licensed under [MIT](LICENSE).
