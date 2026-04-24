# Client Integrations

Agent Memory Bridge is a local-first stdio MCP server. The stable contract is:

- the client launches a local subprocess
- the bridge reads JSON-RPC on `stdin`
- the bridge writes JSON-RPC on `stdout`
- optional environment variables set bridge home, config path, and provenance defaults

That means the generic stdio shape matters more than any one IDE's UI.

## Status Labels

- `Verified`: we have a real local proof path for this client or config surface
- `Documented`: official docs support the shape and we provide a copyable example
- `Locally tested`: we dogfooded the client path locally, but the config UX is still app-specific
- `Supported`: the MCP spec path is generic stdio, not a client-specific promise

## Support Matrix

| Client | Status | Notes |
|---|---|---|
| Codex | Verified reference client | Strongest dogfood path today |
| Generic stdio MCP | Supported | Works anywhere the client can launch a local stdio server |
| Claude Code | Documented | Official `claude mcp add --transport stdio` flow exists |
| Claude Desktop | Documented | Local `mcpServers` JSON is documented; desktop extensions are separate |
| Cursor | Documented | Current docs describe MCP JSON config with stdio entries |
| Cline | Documented | Uses `mcpServers` JSON config |
| Antigravity | Locally tested | Shared-MCP writes were observed locally; exact config file path can vary |

## Generic Stdio First

If your client can launch a local subprocess and speak stdio MCP, start here:

```json
{
  "mcpServers": {
    "agentMemoryBridge": {
      "command": "/path/to/agent-memory-bridge/.venv/bin/python",
      "args": ["-m", "agent_mem_bridge"],
      "cwd": "/path/to/agent-memory-bridge",
      "env": {
        "AGENT_MEMORY_BRIDGE_HOME": "/path/to/bridge-home",
        "AGENT_MEMORY_BRIDGE_CONFIG": "/path/to/agent-memory-bridge-config.toml",
        "AGENT_MEMORY_BRIDGE_DEFAULT_SOURCE_CLIENT": "generic",
        "AGENT_MEMORY_BRIDGE_DEFAULT_CLIENT_TRANSPORT": "stdio"
      }
    }
  }
}
```

Use the built-in renderer if you want client-specific output:

```bash
agent-memory-bridge config --client generic --example
agent-memory-bridge config --client codex --example
agent-memory-bridge config --client cursor --example
```

`--example` keeps the output placeholder-safe. Without it, the renderer uses
your current Python path together with the resolved bridge home and config path.

## Codex

Status: `Verified reference client`

Codex uses `config.toml` with `[mcp_servers.<name>]` entries for stdio servers.

```toml
[mcp_servers.agentMemoryBridge]
command = "/path/to/agent-memory-bridge/.venv/bin/python"
args = ["-m", "agent_mem_bridge"]
cwd = "/path/to/agent-memory-bridge"

[mcp_servers.agentMemoryBridge.env]
AGENT_MEMORY_BRIDGE_HOME = "/path/to/bridge-home"
AGENT_MEMORY_BRIDGE_CONFIG = "/path/to/agent-memory-bridge-config.toml"
AGENT_MEMORY_BRIDGE_DEFAULT_SOURCE_CLIENT = "codex"
AGENT_MEMORY_BRIDGE_DEFAULT_CLIENT_TRANSPORT = "stdio"
```

You can also manage Codex MCP servers from the `codex mcp` CLI, but the static
TOML example above is the most direct bridge-side shape.

## Claude Code

Status: `Documented`

Claude Code has a local stdio add flow:

```bash
claude mcp add --transport stdio \
  --env AGENT_MEMORY_BRIDGE_HOME=/path/to/bridge-home \
  --env AGENT_MEMORY_BRIDGE_CONFIG=/path/to/agent-memory-bridge-config.toml \
  --env AGENT_MEMORY_BRIDGE_DEFAULT_SOURCE_CLIENT=claude-code \
  --env AGENT_MEMORY_BRIDGE_DEFAULT_CLIENT_TRANSPORT=stdio \
  agentMemoryBridge -- /path/to/agent-memory-bridge/.venv/bin/python -m agent_mem_bridge
```

If you prefer file-based configuration, use the same generic `mcpServers` JSON
shape shown above.

## Claude Desktop

Status: `Documented`

Claude Desktop local MCP configuration uses an `mcpServers` JSON object for
local stdio servers. Desktop extensions and MCPB packaging are a separate
distribution path and are intentionally out of scope for this release.

```json
{
  "mcpServers": {
    "agentMemoryBridge": {
      "type": "stdio",
      "command": "/path/to/agent-memory-bridge/.venv/bin/python",
      "args": ["-m", "agent_mem_bridge"],
      "cwd": "/path/to/agent-memory-bridge",
      "env": {
        "AGENT_MEMORY_BRIDGE_HOME": "/path/to/bridge-home",
        "AGENT_MEMORY_BRIDGE_CONFIG": "/path/to/agent-memory-bridge-config.toml",
        "AGENT_MEMORY_BRIDGE_DEFAULT_SOURCE_CLIENT": "claude-desktop",
        "AGENT_MEMORY_BRIDGE_DEFAULT_CLIENT_TRANSPORT": "stdio"
      }
    }
  }
}
```

## Cursor

Status: `Documented`

Cursor's current MCP docs describe JSON config with `mcpServers` entries, and
the field tables currently call out `type = "stdio"` for stdio servers.

```json
{
  "mcpServers": {
    "agentMemoryBridge": {
      "type": "stdio",
      "command": "/path/to/agent-memory-bridge/.venv/bin/python",
      "args": ["-m", "agent_mem_bridge"],
      "cwd": "/path/to/agent-memory-bridge",
      "env": {
        "AGENT_MEMORY_BRIDGE_HOME": "/path/to/bridge-home",
        "AGENT_MEMORY_BRIDGE_CONFIG": "/path/to/agent-memory-bridge-config.toml",
        "AGENT_MEMORY_BRIDGE_DEFAULT_SOURCE_CLIENT": "cursor",
        "AGENT_MEMORY_BRIDGE_DEFAULT_CLIENT_TRANSPORT": "stdio"
      }
    }
  }
}
```

## Cline

Status: `Documented`

Cline uses JSON `mcpServers` entries. Its stdio examples do not require a
separate `type` field.

```json
{
  "mcpServers": {
    "agentMemoryBridge": {
      "command": "/path/to/agent-memory-bridge/.venv/bin/python",
      "args": ["-m", "agent_mem_bridge"],
      "cwd": "/path/to/agent-memory-bridge",
      "env": {
        "AGENT_MEMORY_BRIDGE_HOME": "/path/to/bridge-home",
        "AGENT_MEMORY_BRIDGE_CONFIG": "/path/to/agent-memory-bridge-config.toml",
        "AGENT_MEMORY_BRIDGE_DEFAULT_SOURCE_CLIENT": "cline",
        "AGENT_MEMORY_BRIDGE_DEFAULT_CLIENT_TRANSPORT": "stdio"
      }
    }
  }
}
```

## Antigravity

Status: `Locally tested`

Antigravity has a raw MCP config view with `mcpServers`, and AMB shared-memory
writes were dogfooded locally through that path. The exact file path can vary by
app install, so document the JSON shape rather than promising one hard-coded
location.

```json
{
  "mcpServers": {
    "agentMemoryBridge": {
      "command": "/path/to/agent-memory-bridge/.venv/bin/python",
      "args": ["-m", "agent_mem_bridge"],
      "cwd": "/path/to/agent-memory-bridge",
      "env": {
        "AGENT_MEMORY_BRIDGE_HOME": "/path/to/bridge-home",
        "AGENT_MEMORY_BRIDGE_CONFIG": "/path/to/agent-memory-bridge-config.toml",
        "AGENT_MEMORY_BRIDGE_DEFAULT_SOURCE_CLIENT": "antigravity",
        "AGENT_MEMORY_BRIDGE_DEFAULT_CLIENT_TRANSPORT": "stdio"
      }
    }
  }
}
```

## Verify Before You Trust It

After adding the config, run:

```bash
agent-memory-bridge doctor
agent-memory-bridge verify
```

`doctor` explains install problems without touching your live bridge state.
`verify` launches an isolated temp runtime and proves that the local stdio path
actually works.

## What This Guide Does Not Claim

This page is intentionally narrower than a broad MCP ecosystem survey.

It does not claim:

- packaged desktop extension distribution
- OAuth support across every client
- remote HTTP deployment parity
- that every stdio-capable client has been locally verified

If your client can launch a local stdio server but is not listed above, start
from the generic shape first and treat it as `Supported`, not automatically
`Verified`.
