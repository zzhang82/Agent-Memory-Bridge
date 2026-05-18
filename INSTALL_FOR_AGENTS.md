# Install Agent Memory Bridge For Agents

This guide is written for coding agents that are helping a human install Agent
Memory Bridge into an MCP-compatible client.

Use it as an agent-readable setup protocol. Do not treat it as a new runtime,
watcher, scheduler, or hosted service.

## What You Are Installing

Agent Memory Bridge is a local-first stdio MCP server for reusable engineering
memory and lightweight coordination.

The public MCP surface is intentionally small:

- `store`, `recall`, `browse`, `stats`
- `forget`, `promote`, `export`
- `claim_signal`, `extend_signal_lease`, `ack_signal`

Startup and task-time context assembly are compiled views over those records.
There are no separate `startup_packet` or `task_packet` MCP tools.

## Ask Before You Configure

Ask the human these questions before writing config:

1. Which MCP client should launch the bridge?
2. Where should the local bridge home live?
3. Should the install use a local editable checkout, `uvx`, or Docker?
4. Should the bridge use the default config, or a specific config file?
5. What source client label should be written into provenance metadata?

If the human is unsure, prefer the generic stdio shape and placeholder-safe
examples from `docs/INTEGRATIONS.md`.

## Safe Install Path

1. Inspect `README.md`, `docs/INTEGRATIONS.md`, and `docs/CONFIGURATION.md`.
2. Create or choose a local bridge home directory owned by the human.
3. Render a placeholder-safe config before writing a real one:

   ```bash
   agent-memory-bridge config --client generic --example
   ```

4. Write the MCP client config only after confirming the target client.
5. Run local checks:

   ```bash
   agent-memory-bridge doctor
   agent-memory-bridge verify
   ```

6. If the client already has a running MCP server process, ask the human to
   restart that client before assuming the new config is active.

## Recommended Generic Stdio Shape

Use this shape when the client supports JSON `mcpServers` config:

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

Client-specific examples live in `docs/INTEGRATIONS.md`.

## First Useful Memory Loop

After the bridge is connected, prove value with a small project memory:

```text
store(
  namespace="project:demo",
  kind="memory",
  content="claim: Run the code generator after schema edits."
)

recall(
  namespace="project:demo",
  query="schema edit generator gotcha"
)
```

The goal is not to create a large memory dump. The goal is to prove that a later
session can recover a specific engineering gotcha without the human retyping it.

## What Not To Do

Do not:

- store secrets, tokens, or private credentials
- create a watcher or scheduler inside the core bridge
- add new MCP tools just to expose startup or task packets
- write machine-specific paths into public examples
- claim a client is verified unless it has been locally tested
- replace human review with generated summaries

## Harness Boundary

A future harness or brain-kit can sit around Agent Memory Bridge. It may provide
setup wizards, watcher config, skillpacks, and evaluation replay.

That harness should depend on the bridge. It should not turn the bridge itself
into a hosted runtime, autonomous task runner, scheduler, or full brain system.

