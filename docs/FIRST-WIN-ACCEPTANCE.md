# First-Win Acceptance Packet

This packet is the human/client observation procedure for AMB's first win:
teach one explicit project decision in a coding agent, then recall that
decision from a genuinely fresh agent session.

It is not a CLI Explorer/Inspect walkthrough. It is not a claim that
`doctor` or `verify` loaded MCP config into an external client.

## Status

| Lane | Result |
|---|---|
| Automated stdio first-win (`tests/test_first_win_cross_session.py`) | PASS when that test is green |
| External client first-win (Codex) | **NOT RUN** |

This worktree did not launch or control a fresh real Codex, Cursor, or
Claude instance. Do not round **NOT RUN** up to PASS.

Reference client: **Codex**. Codex remains the verified reference workflow
in [Integrations](INTEGRATIONS.md). Cursor is documented, not the first-win
canonical client.

## Non-claims

- Passing `doctor` or `verify` does not prove that Codex, Cursor, Claude, or
  any other external client loaded AMB MCP config or can see AMB tools.
- CLI `explore` and `inspect` can help a human review stored knowledge. They
  are not the first-win success check.
- AMB does not automatically learn decisions from chat or repository code.
- This packet does not certify a client vendor.

## Preconditions

- Python 3.11+
- Git
- Codex, or another MCP-compatible coding client that can launch a local
  stdio server
- One persistent `AGENT_MEMORY_BRIDGE_HOME` that every client sharing this
  memory will use
- A real project decision you already believe, not an AMB architecture slogan

## Exact Codex procedure

Replace `<venv-python>` with the Python executable inside `.amb-venv`.

### 1. Clean environment

Use a fresh Codex thread later. For install, start from a directory you
control and a dedicated virtual environment:

```bash
python -m venv .amb-venv
```

Do not reuse an unrelated operating-system venv.

### 2. Install AMB

Installing the package does not register AMB with Codex.

```bash
<venv-python> -m pip install agent-memory-bridge
```

Pinned release line:

```bash
<venv-python> -m pip install agent-memory-bridge==0.32.2
```

### 3. Render / preview client config

```bash
<venv-python> -m agent_mem_bridge setup --client codex
```

`setup` is read-only by default. Confirm the rendered fragment points at
`<venv-python> -m agent_mem_bridge` and the same persistent
`AGENT_MEMORY_BRIDGE_HOME`.

If the preview marks Codex eligible for safe automatic configuration:

```bash
<venv-python> -m agent_mem_bridge setup --client codex --apply
```

Otherwise copy the fragment into Codex `config.toml` using the shape in
[Integrations](INTEGRATIONS.md). Reload Codex after registration.

### 4. Connect

Start Codex against the project. Connection is proven only by the client
itself, not by AMB CLI health checks.

### 5. Verify Codex itself sees AMB tools

Inside Codex, confirm the connected MCP server exposes AMB's public tools,
including `store` and `recall`. If Codex cannot list those tools, stop. Do
not continue to Project Init as a substitute for client connection.

Optional later troubleshooting, not a connection proof:

```bash
<venv-python> -m agent_mem_bridge doctor
<venv-python> -m agent_mem_bridge verify
```

### 6. Initialize the project

```bash
<venv-python> -m agent_mem_bridge project init .
```

Confirm the proposed namespace, such as `project:my-app`. Project Init
derives a repository baseline. It does not automatically learn decisions.

### 7. Teach one real decision

In the connected Codex session, teach a decision you actually made. Example:

> Remember that we merge pull requests only after CI is green on the target
> branch, because broken main blocked two releases this month.

The agent should persist that through the public `store` tool as an explicit
decision with a reason, conceptually:

```text
record_type: decision
claim: Merge pull requests only after CI is green on the target branch.
reason: Broken main blocked two releases this month.
scope: project:<name>
confidence: observed
```

AMB must not infer the decision from the repository or archive the whole
chat.

### 8. Confirm store

Ask Codex to confirm the store result: a stored memory id, the project
namespace, and the decision plus reason. Keep that confirmation in this
session only as a write receipt, not as the first win.

### 9. Close session A

End the Codex thread completely. Do not reuse the same chat transcript as
the recall source.

### 10. Open a fresh session B

Start a new Codex session against the same project, same client
registration, and same `AGENT_MEMORY_BRIDGE_HOME`.

### 11. Ask about the decision

Use a generic project question, not an AMB implementation prompt. Example:

> What is required before we merge a pull request?

### 12. Confirm an AMB-backed answer

PASS only if session B answers with the stored decision and reason, and the
agent can show that `recall` returned that memory from AMB.

FAIL if session B only restates session A's chat, only shows CLI
Explore/Inspect output, or cannot attribute the answer to AMB recall.

If this procedure was not executed against a live Codex instance, the
external-client result remains **NOT RUN**.

## Observation log

Fill this in only from a live client run. Leave it blank when the client
lane is NOT RUN.

```text
Date:
Operator:
Client / version:
OS:
AMB version / git revision:
AGENT_MEMORY_BRIDGE_HOME:
Project namespace:
Decision taught:
Store confirmation (memory id):
Fresh session question:
Recall evidence:
Result: PASS | FAIL | NOT RUN
Notes:
```

Current observation:

```text
Date: unrun in this worktree
Client / version: Codex (reference, not launched here)
Result: NOT RUN
Notes: Automated stdio proof exists; no live Codex/Cursor/Claude instance was controlled.
```
