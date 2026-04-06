# Agent Memory Bridge

[简体中文](README.zh-CN.md)

An MCP-native memory layer for coding agents that turns coding sessions into reusable engineering memory.

Built for Codex-first workflows.

It separates two kinds of state that most memory tools collapse into one pool:

- `memory`: durable knowledge worth reusing later
- `signal`: short-lived coordination events for handoffs, polling, and workflow state

Agent Memory Bridge captures what chat history usually loses:

- durable decisions
- known fixes
- cross-session handoffs
- reusable gotchas
- compact domain knowledge

The core idea is simple: keep the memory layer small, reliable, and inspectable. Let higher-level orchestration sit on top.

The system reshapes session output into durable memory:

- sessions become reusable `learn`
- repeated failures become `gotcha`
- clusters of lessons become compact `domain-note`

## Why This Exists

Most agent memory systems drift into one of three patterns:

- memory trapped inside one app or one model
- heavier hosted infrastructure before retrieval basics are proven
- transcript dumping instead of reusable operational knowledge

Agent Memory Bridge takes a narrower path:

- MCP-native from day one
- local-first runtime
- SQLite + FTS5 instead of heavier infrastructure
- automatic promotion from session traces into reusable memory

At its core, it is a memory shaping pipeline:

`session -> summary -> learn -> gotcha -> domain-note`

## Positioning

Agent Memory Bridge is intentionally narrow.

If you want a broader memory platform with SDKs, dashboards, connectors, and multi-surface application support, projects like OpenMemory or Mem0 are closer to that shape.

This project is different on purpose:

1. It is built for coding-agent workflows, not generic note storage.
2. It keeps the MCP surface intentionally small and inspectable.
3. It promotes raw session output into compact machine-readable memory instead of treating summaries as the final artifact.
4. It is local-first and inspectable by default.

For a longer positioning note, see [docs/COMPARISON.md](docs/COMPARISON.md).

## 5-Minute Quickstart

Once the MCP server is registered in Codex, the shortest useful path is:

1. write one durable memory
2. write one coordination signal
3. inspect the namespace without opening SQLite

Example flow:

```text
store(namespace="project:demo", kind="memory", content="claim: Use WAL mode for concurrent readers.")
store(namespace="project:demo", kind="signal", content="review ready", tags=["handoff:ready"])
stats(namespace="project:demo")
browse(namespace="project:demo", limit=10)
recall(namespace="project:demo", kind="signal", since="<last_seen_id>")
```

That shows the core split:

- `memory` keeps what the agent learned
- `signal` carries what another workflow needs to know now

## How It Works

The runtime has four main pieces:

1. MCP server
   - exposes `store` and `recall`
2. watcher
   - observes Codex rollout files
   - writes `session-seen`, `checkpoint`, and `closeout`
3. reflex
   - promotes summaries into `learn`, `gotcha`, and `signal`
4. consolidation
   - synthesizes recurring `learn` and `gotcha` records into domain notes

This keeps the system understandable:

- raw sessions are not final memory
- summaries are not final memory
- durable memory is machine-first
- synthesis happens after promotion

## Quick Start

Requirements:

- Python 3.11+
- Codex with MCP enabled
- SQLite with FTS5 support

### 1. Install

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .[dev]
```

### 2. Create bridge config

Copy [config.example.toml](config.example.toml) to:

```text
$CODEX_HOME/mem-bridge/config.toml
```

Recommended setup:

- keep the live SQLite database local on each machine
- keep shared profile or source vaults on NAS or shared storage if needed
- move to a hosted backend later if you want true multi-machine live writes

### 3. Register the MCP server in Codex

Add this to `$CODEX_HOME/config.toml`:

```toml
[mcp_servers.agentMemoryBridge]
command = "D:\\path\\to\\agent-memory-bridge\\.venv\\Scripts\\python.exe"
args = ["-m", "agent_mem_bridge"]
cwd = "D:\\path\\to\\agent-memory-bridge"

[mcp_servers.agentMemoryBridge.env]
CODEX_HOME = "%USERPROFILE%\\.codex"
AGENT_MEMORY_BRIDGE_HOME = "%USERPROFILE%\\.codex\\mem-bridge"
AGENT_MEMORY_BRIDGE_CONFIG = "%USERPROFILE%\\.codex\\mem-bridge\\config.toml"
```

### 4. Start the service

Start the MCP server:

```powershell
.\.venv\Scripts\python.exe -m agent_mem_bridge
```

Run the background bridge service:

```powershell
.\.venv\Scripts\python.exe .\scripts\run_mem_bridge_service.py
```

Run one cycle only:

```powershell
$env:AGENT_MEMORY_BRIDGE_RUN_ONCE = "1"
.\.venv\Scripts\python.exe .\scripts\run_mem_bridge_service.py
```

Optional startup install:

```powershell
.\scripts\install_startup_watcher.ps1
```

### Optional: build a local Docker image

```powershell
docker build -t agent-memory-bridge:local .
docker --context desktop-linux run --rm -i agent-memory-bridge:local
```

The container entrypoint starts the stdio MCP server with `python -m agent_mem_bridge`.

## Core API

The MCP surface is intentionally small:

- `store`
- `recall`
- `browse`
- `stats`
- `forget`

Common `store` fields:

- `namespace`
- `content`
- `kind`
- `tags`
- `session_id`
- `actor`
- `title`
- `correlation_id`
- `source_app`

Common `recall` fields:

- `namespace`
- `query`
- `kind`
- `tags_any`
- `session_id`
- `actor`
- `correlation_id`
- `since`
- `limit`

## Typical Namespaces

- `global` for a sensible default shared bucket
- `project:<workspace>`
- `domain:<name>`
- imported profile namespaces when a team wants them

The framework is profile-agnostic. A specific operator profile can be layered on top, but the bridge itself is not tied to one persona or one protocol.

If you are starting from scratch, use `global` first and introduce `project:<workspace>` once you want project-local memory.

## Signal Handoff Example

The `signal` channel is for coordination, not durable recall.

Agent A:

```text
store(
  namespace="project:foo",
  kind="signal",
  content="frontend review ready",
  tags=["handoff:ready", "team:frontend"],
  correlation_id="ticket-142"
)
```

Agent B:

```text
recall(
  namespace="project:foo",
  kind="signal",
  since="<last_seen_id>",
  tags_any=["handoff:ready"]
)
```

That lets one workflow poll for fresh handoff events without mixing them into durable memory.

## Day-to-Day Usage

The intended layering is:

- system-level operator profile
- system-level memory substrate: `agentMemoryBridge`
- project-local overrides: [AGENTS.md](AGENTS.md)

The startup protocol is documented in [docs/STARTUP-PROTOCOL.md](docs/STARTUP-PROTOCOL.md).

In short:

1. recall global operating memory
2. recall relevant specialization memory
3. if a workspace exists, recall `project:<workspace>`
4. for issue-like work, check local memory and gotchas before external search
5. inspect live code before trusting recalled implementation details

## Useful Commands

Run tests:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Run the stdio smoke test:

```powershell
.\.venv\Scripts\python.exe .\scripts\verify_stdio.py
```

Run the benchmark:

```powershell
.\.venv\Scripts\python.exe .\scripts\run_benchmark.py
```

Run the bridge health check:

```powershell
.\.venv\Scripts\python.exe .\scripts\run_healthcheck.py --report-path .\examples\healthcheck-report.json
```

Force a checkpoint from the latest rollout:

```powershell
.\.venv\Scripts\python.exe .\scripts\sync_now.py
```

## Project Structure

The repo is intentionally small:

```text
src/agent_mem_bridge/   canonical implementation
scripts/                operational entrypoints
tests/                  verification
docs/                   design and roadmap
examples/               sanitized demo artifacts
```

The files that matter most:

- [src/agent_mem_bridge/server.py](src/agent_mem_bridge/server.py)
- [src/agent_mem_bridge/storage.py](src/agent_mem_bridge/storage.py)
- [src/agent_mem_bridge/watcher.py](src/agent_mem_bridge/watcher.py)
- [src/agent_mem_bridge/reflex.py](src/agent_mem_bridge/reflex.py)
- [src/agent_mem_bridge/consolidation.py](src/agent_mem_bridge/consolidation.py)
- [src/agent_mem_bridge/service.py](src/agent_mem_bridge/service.py)

## Design Choices

### Small MCP surface

The bridge exposes only `store` and `recall`. This keeps the contract stable and easy to integrate.

### Local-first runtime

The live DB stays local by default because SQLite on shared network storage is a reliability trap.

### Machine-first memory

Agents are the primary readers, so memory favors:

- compact fields
- stable tags
- low token cost

over polished prose.

### Layered promotion

The system tries to move upward:

- `summary`
- `learn`
- `gotcha`
- `domain-note`

instead of treating raw summaries as the final artifact.

## Status

The current foundation is working:

- MCP autoload works in Codex
- project and session sync work
- recall-first workflows work
- reflex promotion works
- first-pass domain consolidation works

Reality check and roadmap:

- [docs/PRODUCTION-STATUS.md](docs/PRODUCTION-STATUS.md)
- [docs/ROADMAP.md](docs/ROADMAP.md)

## Profile Imports

The framework can host imported operator profiles, but the framework itself stays profile-agnostic.

## Documentation

- [README.zh-CN.md](README.zh-CN.md)
- [CONTRIBUTING.md](CONTRIBUTING.md)
- [AGENTS.md](AGENTS.md)
- [docs/COMPARISON.md](docs/COMPARISON.md)
- [docs/STARTUP-PROTOCOL.md](docs/STARTUP-PROTOCOL.md)
- [docs/MEMORY-TAXONOMY.md](docs/MEMORY-TAXONOMY.md)
- [docs/PROMOTION-RULES.md](docs/PROMOTION-RULES.md)
- [docs/MODEL-ROUTING.md](docs/MODEL-ROUTING.md)
- [docs/ROADMAP.md](docs/ROADMAP.md)

## License

MIT. See [LICENSE](LICENSE).
