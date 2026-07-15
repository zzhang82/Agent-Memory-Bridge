# Install Agent Memory Bridge For Agents

This guide is written for coding agents that are helping a human install Agent
Memory Bridge into an MCP-compatible client.

Use it as an agent-readable setup protocol. Do not treat it as a runtime,
watcher, scheduler, or hosted service.

For the shortest GitHub-source procedure, start with
[`llms-install.md`](llms-install.md).

## What You Are Installing

Agent Memory Bridge is a local-first stdio MCP server for reusable engineering
memory and lightweight coordination.

The public MCP surface is intentionally small:

- `store`, `recall`, `browse`, `stats`
- `forget`, `promote`, `export`
- `claim_signal`, `extend_signal_lease`, `ack_signal`

Startup and task-time context assembly are derived views over those records.
There are no separate `startup_packet`, `task_packet`, or Task Brief MCP tools.

## Ask Before You Configure

Ask the human these questions before writing config:

1. Which MCP client should launch the bridge?
2. Where should the local bridge home live?
3. Should the install use an isolated Python venv from GitHub, a local editable
   checkout, optional `uvx`, or Docker?
4. Should the bridge use the default config, or a specific config file?
5. What source client label should be written into provenance metadata?

If the human is unsure, prefer the generic stdio shape and placeholder-safe
examples from `docs/INTEGRATIONS.md`.

## Safe Install Path

1. Inspect `llms-install.md`, `docs/INTEGRATIONS.md`, and
   `docs/CONFIGURATION.md`.
2. Create an isolated environment and install the GitHub source archive:

   ```bash
   python -m venv .amb-venv
   python -m pip --python .amb-venv install "https://github.com/zzhang82/Agent-Memory-Bridge/archive/refs/heads/main.zip"
   ```

3. Derive the venv interpreter as described in `llms-install.md`, then run
   `doctor` and `verify` with that interpreter.
4. Create or choose a local bridge home directory owned by the human.
5. Render a placeholder-safe config before writing a real one:

   ```text
   <venv-python> -m agent_mem_bridge first-run --client generic --example
   <venv-python> -m agent_mem_bridge config --client generic --example
   ```

6. Write the MCP client config only after confirming the target client.
7. Run local checks:

   ```text
   <venv-python> -m agent_mem_bridge doctor
   <venv-python> -m agent_mem_bridge verify
   ```

8. If the client already has a running MCP server process, ask the human to
   restart that client before assuming the new config is active.

`uvx` remains the fastest optional GitHub shortcut when `uv` is already
installed. It is not a prerequisite for the baseline path.

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

## One-Command First Run

Use `first-run` when the human wants the shortest safe path from install to a
useful memory loop:

```text
<venv-python> -m agent_mem_bridge first-run --client vscode --namespace project:demo --query "first task" --example
<venv-python> -m agent_mem_bridge first-run --client codex --namespace project:demo --query "first task" --example
<venv-python> -m agent_mem_bridge first-run --client claude-code --namespace project:demo --query "first task" --example
<venv-python> -m agent_mem_bridge first-run --client opencode --namespace project:demo --query "first task" --example
<venv-python> -m agent_mem_bridge first-run --client hermes --namespace project:demo --query "first task" --example
```

The report includes:

- install commands
- a copy/paste client config snippet
- `doctor` / `verify` steps
- a read-only Task Brief for the first namespace/query

It does not write client config, add MCP tools, require AMH, or mutate durable
memory.

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

If a separate helper is present, keep it on this path: check AMB, connect one
MCP client, store one real gotcha, recall it, and optionally render a Task Brief
that labels used, ignored, and needs-review context. AMB remains the durable
authority for the memory.

## What Not To Do

Do not:

- store secrets, tokens, or private credentials
- create a watcher or scheduler inside the core bridge
- add new MCP tools just to expose startup packets, task packets, or Task Briefs
- write machine-specific paths into public examples
- claim a client is verified unless it has been locally tested
- claim the bridge replaces a client's built-in memory, rules, or instructions
- replace human review with generated summaries

## Helper Boundary

A helper layer can sit around Agent Memory Bridge to guide setup, run checks, or
render a Task Brief.

That helper should depend on the bridge and treat AMB as the durable source of
truth. It should not turn the bridge itself into a hosted runtime, autonomous
task runner, scheduler, watcher, or unreviewed writeback path.

## Install Feedback

Use the [client integration issue form](https://github.com/zzhang82/Agent-Memory-Bridge/issues/new?template=client_integration_request.yml)
for successful install evidence, blockers, or client-doc corrections. Remove
secrets, private paths, and memory contents before submitting.
