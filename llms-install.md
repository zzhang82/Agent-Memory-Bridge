# Install Agent Memory Bridge From GitHub

Use this file when an agent is installing Agent Memory Bridge for a human from
the public GitHub repository. The project itself is installed from GitHub, not
from a claimed PyPI or client-marketplace listing.

## Requirements

- Python 3.11 or newer
- network access to GitHub and to the Python package index configured for `pip`
- an MCP client that can launch a local stdio process
- `uv` is optional; the baseline path uses Python and `pip`

## Ask Before Writing Config

Confirm the target client, the desired bridge home, and whether the config is
user-scoped or project-scoped. Do not write client config, paste secrets, or
enable automatic tool approval without the human's approval.

## Python-Only Install

Create an isolated environment and install the project source archive from
GitHub:

```bash
python -m venv .amb-venv
python -m pip --python .amb-venv install "https://github.com/zzhang82/Agent-Memory-Bridge/archive/refs/heads/main.zip"
```

Find the environment's interpreter without assuming a Windows or POSIX layout:

```bash
python -c "import os; from pathlib import Path; print((Path('.amb-venv') / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')).resolve())"
```

Treat the printed value as local configuration data. Do not commit it to this
repository or include it in an issue report.

Run these commands with that interpreter in place of `<venv-python>`:

```text
<venv-python> -m agent_mem_bridge doctor
<venv-python> -m agent_mem_bridge verify
```

`verify` uses isolated temporary bridge state. It does not write MCP client
configuration.

## Connect One Client

Use [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md) for the current config shape.
Set the stdio command to the derived venv interpreter and the arguments to:

```json
["-m", "agent_mem_bridge"]
```

Supported renderer names are `generic`, `codex`, `claude-desktop`,
`claude-code`, `vscode`, `cursor`, `cline`, `antigravity`, `opencode`, and
`hermes`. To inspect a placeholder-safe shape before editing client config:

```text
<venv-python> -m agent_mem_bridge config --client vscode --example
```

Restart or reload the client, then use its own MCP status view to confirm that
the server connects and exposes the documented ten-tool public surface.

## Optional `uvx` Shortcut

If `uvx` is already installed, it can run the GitHub source directly:

```bash
uvx --from git+https://github.com/zzhang82/Agent-Memory-Bridge agent-memory-bridge verify
```

Do not make this the only install instruction. `uv` is not a project baseline
requirement.

## First Useful Check

Store one non-sensitive project gotcha, then recall it from a later task or
session. Keep the first check small and review the tool input before approval.

Agent Memory Bridge is an additional MCP memory store. Do not claim that it
replaces a client's built-in memory, instructions, rules, or project context.

## Report Install Results

Use the [client integration issue form](https://github.com/zzhang82/Agent-Memory-Bridge/issues/new?template=client_integration_request.yml)
for a successful install, a blocker, or a docs correction. Include the client
and version, operating system, install source, redacted config shape, and exact
validation outcome. Remove tokens, private paths, bridge contents, and other
sensitive data first.
