# Generic Installer and Server Contract

Agent Memory Bridge is a local-first stdio MCP server. The generic installer and server contract is:

- the host or client launches `<venv-python> -m agent_mem_bridge` as a local subprocess
- the bridge reads JSON-RPC on `stdin`
- the bridge writes JSON-RPC on `stdout`
- every client or process sharing project memory points to one shared `AGENT_MEMORY_BRIDGE_HOME`

Connecting a specific coding client can be done either with AMB's `connect` CLI command (for Claude Code, Cursor, Codex, Cline, and VS Code) or by writing the client's native MCP stdio configuration manually. The historical `v0.35.0` tag still contains the older v0.35 notes.

The current source/package line is `0.39.0` with exactly 17 public MCP tools. The normal install route is `pip install agent-memory-bridge`; GitHub Releases remains the publication authority for source tags and release notes. Published release availability is listed in GitHub Releases; the published v0.30.0 source archive is `https://github.com/zzhang82/Agent-Memory-Bridge/archive/refs/tags/v0.30.0.zip`. The pinned `v0.27.0` route is a historical published baseline.

## One-Command Client Connectors and Three-Tier Check

Use `connect` to merge the `agent-memory-bridge` entry into a supported client's configuration file (preserving existing sibling servers) and run the automated three-tier connection check:

```bash
<venv-python> -m agent_mem_bridge connect claude-code --yes
<venv-python> -m agent_mem_bridge connect cursor --yes
<venv-python> -m agent_mem_bridge connect codex --yes
<venv-python> -m agent_mem_bridge connect cline --config-path ~/.cline/data/settings/cline_mcp_settings.json --yes
<venv-python> -m agent_mem_bridge connect vscode --yes
<venv-python> -m agent_mem_bridge connect cursor --check
<venv-python> -m agent_mem_bridge connect --bridge-check --from-client claude-code --to-client codex
```

`connect <client> --check` reports three separate tiers:
1. `tier1_config_valid`: Target JSON/TOML parses cleanly, uses the client's native key (`mcpServers`, `servers`, or `[mcp_servers]`), and uses an absolute executable path.
2. `tier2_server_spawn_verified`: Spawns the exact configured command over MCP stdio and confirms `store` and `recall` are listed among the 17 tools.
3. `tier3_host_registration_status`: Reports `host_cli_verified` when a non-interactive host CLI confirms registration, or `host_reload_required` when the client application must be reloaded to pick up the file.

## Per-Client Trigger Reminders (6×5 Capability Matrix)

Use `triggers` to inspect the 6 moments × 5 clients support matrix (`native_hook`, `deferred_hook`, `trust_gated_hook`, `instruction_rule`), install per-client reminders, or run the synthetic multi-turn self-test:

```bash
<venv-python> -m agent_mem_bridge triggers matrix
<venv-python> -m agent_mem_bridge triggers install claude-code --yes
<venv-python> -m agent_mem_bridge triggers install cursor --yes
<venv-python> -m agent_mem_bridge triggers test claude-code
```

Host hook entries invoke `<venv-python> -m agent_mem_bridge trigger-hook --client <client>`, which translates vendor hook payloads outside `lifecycle-hook` and never auto-writes to SQLite. See the [Plain-Words to Code Glossary](GLOSSARY.md) for the exact vocabulary mapping.

## Generic Stdio Command Shape

Use the virtualenv Python as the command and `-m agent_mem_bridge` as arguments:

```json
{
  "mcpServers": {
    "agentMemoryBridge": {
      "command": "<venv-python>",
      "args": ["-m", "agent_mem_bridge"],
      "env": {
        "AGENT_MEMORY_BRIDGE_HOME": "/path/to/shared-bridge-home",
        "AGENT_MEMORY_BRIDGE_DEFAULT_SOURCE_CLIENT": "generic",
        "AGENT_MEMORY_BRIDGE_DEFAULT_CLIENT_TRANSPORT": "stdio"
      }
    }
  }
}
```

Point every client or process that should share this project memory at the same `AGENT_MEMORY_BRIDGE_HOME`.

## Standardized Host Recipes

Clients launch the local stdio subprocess independently using their native configuration format.

### Cursor (`.cursor/mcp.json`)

```json
{
  "mcpServers": {
    "agent-memory-bridge": {
      "command": "<venv-python>",
      "args": ["-m", "agent_mem_bridge"],
      "env": {
        "AGENT_MEMORY_BRIDGE_HOME": "/path/to/shared-bridge-home",
        "AGENT_MEMORY_BRIDGE_DEFAULT_SOURCE_CLIENT": "cursor"
      }
    }
  }
}
```

### Claude Code (`.mcp.json` at project root, or `~/.claude.json`)

```json
{
  "mcpServers": {
    "agent-memory-bridge": {
      "command": "<venv-python>",
      "args": ["-m", "agent_mem_bridge"],
      "env": {
        "AGENT_MEMORY_BRIDGE_HOME": "/path/to/shared-bridge-home",
        "AGENT_MEMORY_BRIDGE_DEFAULT_SOURCE_CLIENT": "claude-code"
      }
    }
  }
}
```

### OpenCode (`~/.config/opencode/opencode.json`)

```json
{
  "mcp": {
    "agent-memory-bridge": {
      "type": "local",
      "command": ["<venv-python>", "-m", "agent_mem_bridge"],
      "environment": {
        "AGENT_MEMORY_BRIDGE_HOME": "/path/to/shared-bridge-home",
        "AGENT_MEMORY_BRIDGE_DEFAULT_SOURCE_CLIENT": "opencode"
      }
    }
  }
}
```

### Zero-Install Runner (`uvx`)

```json
{
  "mcpServers": {
    "agent-memory-bridge": {
      "command": "uvx",
      "args": ["agent-memory-bridge", "serve"],
      "env": {
        "AGENT_MEMORY_BRIDGE_HOME": "/path/to/shared-bridge-home"
      }
    }
  }
}
```

### Dockerized Stdio

If running via Docker, keep stdin open and mount a host-owned bridge home into the container:

```bash
docker run --rm -i \
  -e AGENT_MEMORY_BRIDGE_HOME=/data/agent-memory-bridge \
  -e AGENT_MEMORY_BRIDGE_DEFAULT_SOURCE_CLIENT=generic \
  -e AGENT_MEMORY_BRIDGE_DEFAULT_CLIENT_TRANSPORT=stdio \
  -v /path/to/bridge-home:/data/agent-memory-bridge \
  agent-memory-bridge:local
```

### Static-Schema Compatibility

Some MCP clients keep static tool schemas and may include signal-only fields on `kind="memory"` paths (for example `ttl_seconds` or `expires_at` on `store`, and `signal_status` on `recall`, `browse`, or `export`). AMB normalizes those fields at the stdio MCP boundary when `kind="memory"`, without merging the memory and signal lanes.

## Project Initialization

Initialize or resolve the project checkout using AMB's project primitives:

```bash
<venv-python> -m agent_mem_bridge project init .
```

`project init` detects the local Git repository, proposes a namespace such as `project:my-app`, waits for confirmation, and derives a repository baseline.

To inspect an existing binding without writing or rebinding:

```bash
<venv-python> -m agent_mem_bridge project resolve .
```

## Generic Lifecycle-Hook Input

AMB provides an optional lifecycle hook for hosts that wish to invoke memory recall or review candidate capture directly. It does not ship client-specific plugins or hook manifests. The host invokes the generic hook command:

```bash
<venv-python> -m agent_mem_bridge lifecycle-hook
```

The hook reads one generic JSON event on standard input:

```json
{
  "kind": "task_prompt",
  "cwd": "/path/to/project",
  "session_id": "session-123",
  "prompt": "How do we run tests in this repo?"
}
```

Accepted `kind` values are `session_start`, `task_prompt`, `compaction`, `ignore`, and `capture`. A normal session start resolves the bound project without dumping memory; `session_start` with `source="compact"`, and `compaction`, maintain continuity without recall. A material task prompt may recall once; an exact repeated prompt is suppressed.

`capture` may store one hidden `needs_review` candidate when `visible_artifact` is already a valid `memory.visible_artifact.v1` object and both `session_id` and `turn_id` are provided. It does not read transcripts or automatically promote candidates.

Historical write-side capture checks from the v0.35 line are documented in historical reports; they do not establish external client acceptance of the current generic hook input.

## Server Verification

Verify server installation and stdio execution with:

```bash
<venv-python> -m agent_mem_bridge doctor
<venv-python> -m agent_mem_bridge verify
```

`doctor` checks local prerequisites, dependencies, and configured paths. `verify` launches an isolated temporary runtime to confirm that the local stdio MCP server functions correctly. These commands verify the server itself; they do not prove that an external client loaded configuration or registered the server. Client registration is proven only when the coding client itself connects and exposes AMB's 17 public MCP tools.
