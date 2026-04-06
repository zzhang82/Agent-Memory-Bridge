# Agent Memory Bridge

[简体中文](README.zh-CN.md)

Two-channel MCP memory for coding agents.

Built for Codex-first workflows.

Most memory tools put everything in one bucket. Agent Memory Bridge keeps two kinds of state separate:

- `memory`: durable knowledge worth reusing later
- `signal`: short-lived coordination events for handoffs, polling, and workflow state

So an agent can carry forward:

- durable decisions
- known fixes
- cross-session handoffs
- reusable gotchas
- compact domain knowledge

The bridge follows a small promotion ladder:

`session -> summary -> learn -> gotcha -> domain-note`

## Why This Exists

Coding agents lose too much between sessions. Memory often ends up trapped inside one client, mixed into raw transcripts, or pushed into heavier infrastructure before retrieval basics are proven.

Agent Memory Bridge takes a narrower path:

- MCP-native from day one
- local-first runtime
- SQLite + FTS5 instead of heavier infrastructure
- automatic promotion from session traces into reusable memory

## What Makes It Different

1. It is built for coding-agent workflows, not generic note storage.
2. It keeps durable knowledge and coordination signals separate.
3. It promotes raw session output into compact machine-readable memory instead of treating summaries as the final artifact.
4. It stays small and inspectable by default.

If you want a broader memory platform with SDKs, dashboards, connectors, and multi-surface application support, projects like OpenMemory or Mem0 are closer to that shape.

For a longer positioning note, see [docs/COMPARISON.md](docs/COMPARISON.md).

## 5-Minute Quickstart

Once the MCP server is registered in Codex, the shortest useful path is:

1. write one durable memory
2. write one coordination signal
3. inspect the namespace without opening SQLite

```text
store(namespace="project:demo", kind="memory", content="claim: Use WAL mode for concurrent readers.")
store(namespace="project:demo", kind="signal", content="review ready", tags=["handoff:ready"])
stats(namespace="project:demo")
browse(namespace="project:demo", limit=10)
recall(namespace="project:demo", kind="signal", since="<last_seen_id>")
```

That shows the split:

- `memory` keeps what the agent learned
- `signal` carries what another workflow needs to know now

If you are starting from scratch instead of adding the server to an existing Codex setup, the installation path is below.

## Setup

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

Optional local Docker image:

```powershell
docker build -t agent-memory-bridge:local .
docker --context desktop-linux run --rm -i agent-memory-bridge:local
```

## MCP Tools

The MCP surface is small and practical:

- `store` and `recall` for writing and retrieving bridge state
- `browse` and `stats` for inspecting what is already there
- `forget` and `promote` for correcting bad or under-classified entries
- `export` for moving memory back out as Markdown, JSON, or plain text

## Namespaces

Start simple:

- `global` for a default shared bucket
- `project:<workspace>` for project-local memory
- `domain:<name>` for reusable domain knowledge

The framework is profile-agnostic. A specific operator profile can sit on top, but the bridge itself is not tied to one persona or one protocol.

## Trust and Health Checks

The bridge is meant to be inspectable, not magical:

- `browse`, `stats`, `forget`, and `export` let you inspect and correct bridge state without opening SQLite
- watcher health checks verify that Codex rollout files still parse into usable summaries
- the current test suite passes with `53 passed`

Useful commands:

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe .\scripts\verify_stdio.py
.\.venv\Scripts\python.exe .\scripts\run_healthcheck.py --report-path .\examples\healthcheck-report.json
.\.venv\Scripts\python.exe .\scripts\run_watcher_healthcheck.py --report-path .\examples\watcher-health-report.json
```

## More Docs

- [CONTRIBUTING.md](CONTRIBUTING.md)
- [AGENTS.md](AGENTS.md)
- [docs/COMPARISON.md](docs/COMPARISON.md)
- [docs/STARTUP-PROTOCOL.md](docs/STARTUP-PROTOCOL.md)
- [docs/MEMORY-TAXONOMY.md](docs/MEMORY-TAXONOMY.md)
- [docs/PROMOTION-RULES.md](docs/PROMOTION-RULES.md)
- [docs/MODEL-ROUTING.md](docs/MODEL-ROUTING.md)
- [docs/ROADMAP.md](docs/ROADMAP.md)
- [docs/PRODUCTION-STATUS.md](docs/PRODUCTION-STATUS.md)

## License

MIT. See [LICENSE](LICENSE).
