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
- `forget`, `promote`, `annotate`, `revise`, `export`
- `claim_signal`, `extend_signal_lease`, `ack_signal`

Startup and task-time context assembly are derived views over those records.
There are no separate `startup_packet`, `task_packet`, or Task Brief MCP tools.

## Ask Before You Configure

Ask the human these questions before writing config:

1. Which MCP client should launch the bridge?
2. Where should the local bridge home live?
3. What source client label should be written into provenance metadata?

Unless the human asks for an alternative, use the pinned isolated Python venv
baseline below. Local editable checkout, optional `uvx`, and Docker remain
optional routes. For the Phase 1 pilot, all clients must share the same
user-chosen persistent `AGENT_MEMORY_BRIDGE_HOME`.

## Safe Install Path

1. Inspect `llms-install.md`, `docs/INTEGRATIONS.md`, and
   `docs/CONFIGURATION.md`.
2. Use the available Python 3.11+ launcher. Examples use `python`; on many
   Linux systems use `python3`; on Windows `py -3` may be appropriate. Create
   an isolated environment:

   ```bash
   python -m venv .amb-venv
   ```

3. Derive the venv interpreter as described in `llms-install.md`, then install
   with `<venv-python> -m pip install "https://github.com/zzhang82/Agent-Memory-Bridge/archive/refs/tags/v0.23.1.zip"`.
4. Choose one persistent bridge home directory owned by the human and use it in
   every pilot client config.
5. Render a real config fragment for the approved client before writing it:

   ```text
   <venv-python> -m agent_mem_bridge config --client <client> --python "<venv-python>" --cwd "<absolute-path-to-your-project>" --bridge-home "<absolute-path-to-one-persistent-bridge-home>"
   ```

6. Write the MCP client config only after confirming the target client.
7. Run local checks:

   ```text
   <venv-python> -m agent_mem_bridge doctor
   <venv-python> -m agent_mem_bridge verify
   ```

   `doctor` checks local prerequisites and paths. `verify` launches an isolated
   AMB stdio runtime. Neither proves the client loaded its config.
8. If the client already has a running MCP server process, ask the human to
   restart that client, then use its MCP status/tool view to confirm the server
   registration and tool visibility. This is the client registration gate.

The custom `config.toml` path emitted by the renderer is optional for this
baseline. If its default path has no file, `doctor` may warn and the baseline
server can still run.

`uvx` remains the fastest optional GitHub shortcut when `uv` is already
installed. It is not a prerequisite for the baseline path.

## Recommended Generic Stdio Shape

Use this shape when the client supports JSON `mcpServers` config:

```json
{
  "mcpServers": {
    "agentMemoryBridge": {
      "command": "/path/to/agent-memory-bridge/.amb-venv/bin/python",
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

Use `first-run --example` when the human wants one placeholder-safe view of the
install, client config, verification, and first Task Brief flow:

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
memory. For a runnable client registration, render the real config in step 5
without `--example` and use the approved local paths.

## First Useful Memory Loop

After the bridge is connected, prove value with a small project memory:

```text
# In the configured MCP client, call these MCP tools; they are not terminal commands.
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

Reply with pilot outcomes to
[Discussion #4](https://github.com/zzhang82/Agent-Memory-Bridge/discussions/4).
Use the [client integration issue form](https://github.com/zzhang82/Agent-Memory-Bridge/issues/new?template=client_integration_request.yml)
for a separate reproducible setup or client-doc defect. Remove secrets, private
paths, and memory contents before submitting.
