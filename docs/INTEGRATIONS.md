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
| VS Code / Copilot | Documented | Uses VS Code's `servers` object in `mcp.json` |
| Claude Code | Documented | Official `claude mcp add --transport stdio` flow exists |
| Claude Desktop | Documented | Local `mcpServers` JSON is documented; desktop extensions are separate |
| Cursor | Documented | Current docs describe MCP JSON config with stdio entries |
| Cline | Documented | Uses `mcpServers` JSON config |
| Antigravity | Locally tested | Shared-MCP writes were observed locally; exact config file path can vary |
| OpenCode | Locally tested | Local JSON `mcp` command shape was dogfooded locally |
| Hermes | Locally tested | Local YAML `mcp_servers` shape was dogfooded locally; adapter workflows remain manual |

## Evidence And Contributions

`Documented` means the example matches the linked first-party client docs. It
does not mean the client vendor certified Agent Memory Bridge or that a
marketplace listing exists.

To report a successful install, blocker, or stale client shape, use the
[client integration issue form](https://github.com/zzhang82/Agent-Memory-Bridge/issues/new?template=client_integration_request.yml).
Include the client and version, operating system, GitHub revision or install
source, redacted config shape, and exact validation result. A status should move
to `Locally tested` or `Verified` only with reproducible evidence.

Contributions should stay client-specific: cite the official client docs,
update the renderer test when generated output changes, and avoid secrets,
machine paths, marketplace claims, or claims that AMB replaces client-native
memory.

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
agent-memory-bridge first-run --client generic --example
agent-memory-bridge config --client generic --example
agent-memory-bridge config --client codex --example
agent-memory-bridge config --client vscode --example
agent-memory-bridge config --client opencode --example
agent-memory-bridge config --client hermes --example
agent-memory-bridge config --client cursor --example
```

`--example` keeps the output placeholder-safe. Without it, the renderer uses
your current Python path together with the resolved bridge home and config path.

For a GitHub-source install that does not assume `uv`, follow
[`llms-install.md`](../llms-install.md), derive the isolated venv interpreter,
and use that value as the stdio command. `uvx` is an optional shortcut only.

### Static-schema placeholders

Some MCP clients keep static tool schemas and may include signal-only fields on
`kind="memory"` paths: for example `ttl_seconds` or `expires_at` on `store`, and
`signal_status` on `recall`, `browse`, or `export`. AMB normalizes those fields at
the stdio MCP boundary when `kind="memory"`, so static-schema clients do not have
to strip them before calling the tools.

That compatibility does not merge the memory and signal lanes. Non-empty signal
lifecycle values are not applied to `kind="memory"`; they remain valid only on
`kind="signal"` paths, and lower-level store/repository behavior stays strict.

### Dockerized Stdio

If your client can launch Docker as the subprocess, keep stdin open and mount a
host-owned bridge home into the image:

```bash
docker build -t agent-memory-bridge:local .
docker run --rm -i \
  -e AGENT_MEMORY_BRIDGE_HOME=/data/agent-memory-bridge \
  -v /path/to/bridge-home:/data/agent-memory-bridge \
  agent-memory-bridge:local
```

The equivalent `mcpServers` shape is:

```json
{
  "mcpServers": {
    "agentMemoryBridge": {
      "command": "docker",
      "args": [
        "run",
        "--rm",
        "-i",
        "-e",
        "AGENT_MEMORY_BRIDGE_HOME=/data/agent-memory-bridge",
        "-e",
        "AGENT_MEMORY_BRIDGE_DEFAULT_SOURCE_CLIENT=generic",
        "-e",
        "AGENT_MEMORY_BRIDGE_DEFAULT_CLIENT_TRANSPORT=stdio",
        "-v",
        "/path/to/bridge-home:/data/agent-memory-bridge",
        "agent-memory-bridge:local"
      ]
    }
  }
}
```

If you mount a config file, pass it explicitly:

```bash
docker run --rm -i \
  -e AGENT_MEMORY_BRIDGE_HOME=/data/agent-memory-bridge \
  -e AGENT_MEMORY_BRIDGE_CONFIG=/config/config.toml \
  -v /path/to/bridge-home:/data/agent-memory-bridge \
  -v /path/to/agent-memory-bridge-config.toml:/config/config.toml:ro \
  agent-memory-bridge:local
```

## VS Code / Copilot

Status: `Documented`

[VS Code's current MCP configuration reference](https://code.visualstudio.com/docs/agents/reference/mcp-configuration)
uses a top-level `servers` object in workspace or user-profile `mcp.json`. The
workspace file is `.vscode/mcp.json`; use **MCP: Open User Configuration** for a
user-scoped install.

```json
{
  "servers": {
    "agentMemoryBridge": {
      "type": "stdio",
      "command": "<venv-python>",
      "args": ["-m", "agent_mem_bridge"],
      "env": {
        "AGENT_MEMORY_BRIDGE_DEFAULT_SOURCE_CLIENT": "vscode",
        "AGENT_MEMORY_BRIDGE_DEFAULT_CLIENT_TRANSPORT": "stdio"
      }
    }
  }
}
```

Review the command and trust prompt before starting the server. This is a VS
Code MCP configuration for use by agent chat, not a Visual Studio Marketplace
extension or an MCP gallery claim.

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

[Claude Code documents](https://code.claude.com/docs/en/mcp) a local stdio add
flow:

```bash
claude mcp add --transport stdio \
  --env AGENT_MEMORY_BRIDGE_HOME=/path/to/bridge-home \
  --env AGENT_MEMORY_BRIDGE_CONFIG=/path/to/agent-memory-bridge-config.toml \
  --env AGENT_MEMORY_BRIDGE_DEFAULT_SOURCE_CLIENT=claude-code \
  --env AGENT_MEMORY_BRIDGE_DEFAULT_CLIENT_TRANSPORT=stdio \
  agentMemoryBridge -- /path/to/agent-memory-bridge/.venv/bin/python -m agent_mem_bridge
```

For a checked-in project configuration, use the generic `mcpServers` shape in
`.mcp.json` and review Claude Code's project trust prompt. For user scope, prefer
`claude mcp add --scope user ...` rather than editing internal config by hand.

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

[Cursor's MCP docs](https://docs.cursor.com/en/tools/mcp)
describe project or user JSON config with `mcpServers` entries. This repository
does not claim an **Add to Cursor** listing; use the JSON path below.

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

[Cline's MCP docs](https://docs.cline.bot/mcp/mcp-overview) use JSON
`mcpServers` entries for local stdio servers and expose an MCP configuration UI
and `cline mcp` wizard. An agent-led GitHub install should follow
[`llms-install.md`](../llms-install.md), then add the derived interpreter and
arguments below through Cline's approved config flow.

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

## OpenCode

Status: `Locally tested`

[OpenCode's current MCP docs](https://opencode.ai/docs/mcp-servers/) define
local servers under `mcp`, with a command array and an `environment` object.
Use `opencode mcp add` for the guided flow or merge the generated shape into the
intended user or project config:

```bash
agent-memory-bridge first-run --client opencode --example
agent-memory-bridge config --client opencode --example
```

```json
{
  "mcp": {
    "agentMemoryBridge": {
      "type": "local",
      "command": [
        "/path/to/agent-memory-bridge/.venv/bin/python",
        "-m",
        "agent_mem_bridge"
      ],
      "enabled": true,
      "environment": {
        "AGENT_MEMORY_BRIDGE_HOME": "/path/to/bridge-home",
        "AGENT_MEMORY_BRIDGE_CONFIG": "/path/to/agent-memory-bridge-config.toml",
        "AGENT_MEMORY_BRIDGE_DEFAULT_SOURCE_CLIENT": "opencode",
        "AGENT_MEMORY_BRIDGE_DEFAULT_CLIENT_TRANSPORT": "stdio"
      }
    }
  }
}
```

## Hermes

Status: `Locally tested`

[Hermes's current MCP docs](https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp)
use `mcp_servers` in `~/.hermes/config.yaml` for local stdio servers. AMH/Hermes
adapter commands remain a manual helper workflow; AMB itself remains a separate
MCP store.

```bash
agent-memory-bridge first-run --client hermes --example
agent-memory-bridge config --client hermes --example
```

```yaml
mcp_servers:
  agentMemoryBridge:
    command: '/path/to/agent-memory-bridge/.venv/bin/python'
    args:
      - '-m'
      - 'agent_mem_bridge'
    env:
      AGENT_MEMORY_BRIDGE_HOME: '/path/to/bridge-home'
      AGENT_MEMORY_BRIDGE_CONFIG: '/path/to/agent-memory-bridge-config.toml'
      AGENT_MEMORY_BRIDGE_DEFAULT_SOURCE_CLIENT: 'hermes'
      AGENT_MEMORY_BRIDGE_DEFAULT_CLIENT_TRANSPORT: 'stdio'
```

After editing the config, run `hermes mcp test agentMemoryBridge` and
`hermes mcp list`, or reload MCP servers from Hermes and inspect the connection
status.

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
