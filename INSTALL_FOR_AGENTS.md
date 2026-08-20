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

The pinned `v0.27.0` and local `0.27.4` source surfaces both expose 17 tools:

- `store`, `recall`, `browse`, `stats`, `export`
- `forget`, `feedback`, `promote`, `annotate`, `revise`
- `begin_run`, `record_run_event`, `get_run`, `complete_run`
- `claim_signal`, `extend_signal_lease`, `ack_signal`

The pinned `v0.27.0` release-install route exposes `17` public MCP tools at client registration.

Source `0.27.4` differs from pinned release-install `0.27.0`; use a source checkout until its exact-commit CI gate passes and its tag is created.

The pinned GitHub tag route is the published `v0.27.0` baseline. The local source candidate has no release tag; use a source checkout with `<venv-python> -m pip install -e .` to evaluate it.

Startup and task-time context assembly are derived views over those records.
There are no separate `startup_packet`, `task_packet`, or Task Brief MCP tools.
Receipt-bearing recall binds the complete returned exposure set and exact
content versions from one SQLite snapshot. Retrieval feedback supports
append-only votes, corrections, and retractions while exposing at most one
current effective vote per receipt-bound subject. It remains shadow-only and
does not change memory records, recall results, or ranking behavior.

Run tools create explicit server-minted handles and append bounded episode
evidence. The local 0.27.4 source uses schema v12 with existing governed-v2
episode authority plus an internal exact-key Dynamic State lane. Dynamic State
uses typed status/owner/restore commands, version/database-epoch guards,
lifecycle idempotency, immutable mutation/request-outcome history, and a
rebuildable state head; it adds no MCP tools and does not alter memory recall,
ranking, policy, prompts, or ordinary memory. Run rows remain durable authority;
downstream utility and consolidation remain shadow-only.

## Ask Before You Configure

Ask the human these questions before writing config:

1. Which MCP client should launch the bridge?
2. Where should the local bridge home live?
3. What source client label should be written into provenance metadata?

Unless the human asks for an alternative, use the pinned isolated Python venv
baseline below. Local editable checkout, optional `uvx`, and Docker remain
optional routes. For the Phase 1 pilot, all clients must share the same
user-chosen persistent `AGENT_MEMORY_BRIDGE_HOME`.

## Safe Setup Preview and Apply

Start with the bounded P2A preview. It remains read-only and always reports
`Changes written: 0`:

```text
agent-memory-bridge setup --client <client>
agent-memory-bridge setup --client <client> --json
```

P2B can apply only a fresh P2A plan whose target, format, ownership state, and
bytes remain eligible at the time of the write. For a human-reviewed change,
run:

```text
agent-memory-bridge setup --apply --client <client>
```

The command displays the client, target, classified existing state, planned
action, and backup behavior, then requires `Apply these changes? [y/N]`.
Declining or EOF writes nothing. Machine-readable automation is deliberately
narrow: use `agent-memory-bridge setup --apply --yes --json`; `--yes` requires
`--apply` and never bypasses a safety check. There is no `--force` switch.

Automatic mutation is limited to structurally parsed JSON plans for Claude
Code, VS Code, and the normal supported OpenCode JSON path when P2A has no
alternate/project/custom/JSONC marker. Conflict, unreadable,
inspection-unavailable, path-unknown, unsupported, and manual-review plans are
never overwritten. Codex TOML remains preview-only in P2B because this source
has a TOML parser but no safe TOML writer; P2B does not use string replacement
or an ad-hoc TOML serializer.

Before replacing an existing config, P2B re-plans, checks that target metadata
is unchanged, writes a unique adjacent byte-for-byte backup, applies an
atomic same-directory replacement, and re-reads the JSON to verify the AMB
entry and preserved structure. It creates only the exact missing target parent
for an eligible new config; it does not create bridge paths, a database, logs,
or package-manager state. A metadata-only adjacent receipt supports one latest
safe rollback per target:

```text
agent-memory-bridge setup --rollback --client <client>
```

Rollback is interactive and restores an exact backup for modified files, or
removes a P2B-created file only if its digest still matches the applied result.
It stops for post-apply user edits and never recursively removes user
directories. Apply configures a client file only; it does not prove that a
client loaded AMB or that AMB connected successfully. Follow with
`agent-memory-bridge doctor --include-stdio` and `agent-memory-bridge verify`
when their runtime prerequisites are available.

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
   the published pinned `v0.27.0` archive with `<venv-python> -m pip install
   "https://github.com/zzhang82/Agent-Memory-Bridge/archive/refs/tags/v0.27.0.zip"`.
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
   registration and its 17-tool public surface. This is the client registration
   gate for the published `v0.27.0` route.

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

## First Run After Setup

`setup` owns safe client connection. After the client is connected, use
`first-run` as a read-only product guide for one useful durable-memory loop:

```text
<venv-python> -m agent_mem_bridge first-run --namespace project:demo --query "What should I check before submitting changes?"
<venv-python> -m agent_mem_bridge first-run --namespace project:demo --query "What should I check before submitting changes?" --format json
```

The report guides a human and connected coding agent through:

- remembering one or two concise project facts with the existing `store` tool
- asking a realistic task question
- seeing what AMB remembered and why it appeared
- recording bounded feedback with the existing `feedback` tool
- reopening the agent against the same durable database and asking again

It is state-aware: if suitable memory already surfaces, it shows it; otherwise
it asks the user to remember one useful fact first. `first-run` never seeds
memory, writes client config, adds MCP tools, or proves client connection.
Run `doctor` or `verify` when connection health is unresolved.

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
  kind="memory",
  query="schema edit generator gotcha",
  evidence_context={
    "model": "<caller-declared model label>",
    "harness": "<caller-declared harness label>",
    "chat_template": "<caller-declared chat-template label>"
  }
)
```

The goal is not to create a large memory dump. The goal is to prove that a later
session can recover a specific engineering gotcha without the human retyping it.

For non-empty `kind="memory"` text recall, the response can include a 15-minute
`recall_receipt`. The returned rows, database epoch, complete exposure set,
exact content versions, and receipt signature come from the same SQLite read
snapshot. Treat the token as sensitive local metadata. It is HMAC-signed and
tamper-evident, not encrypted, and it does not authenticate the caller, client,
model, workspace, or user.

`evidence_context` is optional and accepts only `model`, `harness`, and
`chat_template`. The signed receipt contains bounded SHA-256 digests, never the
raw labels. These caller-declared values do not affect retrieval order or
feedback identity.

If the human wants retrieval feedback, bind it to that receipt:

```text
feedback(
  namespace="project:demo",
  recall_receipt="<token from recall>",
  memory_id="<recalled memory id>",
  result_rank=1,
  outcome="helpful"
)
```

Feedback is append-only shadow evidence. It does not promote records, rewrite
memories, update indexes, or change future ranking. A recorded feedback receipt
proves that the feedback event was stored for review/evaluation; it does not
prove that feedback caused a later recall or that AMB learned automatically.

The default `feedback_type` is `vote`. To change or withdraw the current vote,
append a `correction` or `retraction` with the current
`supersedes_feedback_id`; existing rows are never rewritten. Caller-declared
client or session labels cannot create additional votes for the same signed
retrieval subject.

If a separate helper is present, keep it on this path: check AMB, connect one
MCP client, store one real gotcha, recall it, and optionally render a Task Brief
that labels used, ignored, and needs-review context. AMB remains the durable
authority for the memory.

## What Not To Do

Do not:

- store secrets, tokens, or private credentials
- create a watcher or scheduler inside the core bridge
- add new MCP tools just to expose startup packets, task packets, or Task Briefs
- claim feedback actively changes ranking, memory, or policy
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
