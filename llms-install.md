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
GitHub. Use the available Python 3.11+ launcher: examples use `python`; on many
Linux systems use `python3`; on Windows `py -3` may be appropriate.

```bash
python -m venv .amb-venv
```

Find the environment's interpreter without assuming a Windows or POSIX layout:

```bash
python -c "import os; from pathlib import Path; print((Path('.amb-venv') / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')).absolute())"
```

Treat the printed value as local configuration data. Do not commit it to this
repository or include it in an issue report. In a POSIX shell, shell-quote the
path when needed. In Windows PowerShell, invoke it as `& "<venv-python>"`.

Install and run these commands with that interpreter in place of
`<venv-python>`:

```text
<venv-python> -m pip install "https://github.com/zzhang82/Agent-Memory-Bridge/archive/refs/tags/v0.22.2.zip"
<venv-python> -m agent_mem_bridge doctor
<venv-python> -m agent_mem_bridge verify
```

`doctor` checks local prerequisites and resolved paths. `verify` launches an
isolated AMB stdio runtime; neither command proves that an MCP client loaded
the config.

## Connect One Client

Use [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md) for the current config shape.
Set the stdio command to the derived venv interpreter and the arguments to:

```json
["-m", "agent_mem_bridge"]
```

Supported renderer names are `generic`, `codex`, `claude-desktop`,
`claude-code`, `vscode`, `cursor`, `cline`, `antigravity`, `opencode`, and
`hermes`. For the Phase 1 pilot, every client must use the same user-chosen
persistent `AGENT_MEMORY_BRIDGE_HOME`. Render one real fragment for the target
client before editing its config:

```text
<venv-python> -m agent_mem_bridge config --client <client> --python "<venv-python>" --cwd "<absolute-path-to-your-project>" --bridge-home "<absolute-path-to-one-persistent-bridge-home>"
```

The default config path in the generated fragment is optional for this baseline.
If no such `config.toml` exists, `doctor` may warn and the baseline server can
still run. Restart or reload the client, then use its own MCP status/tool view
to confirm the server connects and exposes the documented ten-tool public
surface. That client registration check is the gate that proves the config was
loaded.

## Optional `uvx` Shortcut

If `uvx` is already installed, it can run the GitHub source directly:

```bash
uvx --from git+https://github.com/zzhang82/Agent-Memory-Bridge@v0.22.2 agent-memory-bridge verify
```

Do not make this the only install instruction. `uv` is not a project baseline
requirement.

## First Useful Check

In the configured MCP client, call the `store(...)` and `recall(...)` MCP tools
to store one non-sensitive project gotcha and recall it from a later task or
session. They are not terminal subcommands. Keep the first check small and
review tool input before approval.

Agent Memory Bridge is an additional MCP memory store. Do not claim that it
replaces a client's built-in memory, instructions, rules, or project context.

## Report Install Results

Reply with pilot outcomes to
[Discussion #4](https://github.com/zzhang82/Agent-Memory-Bridge/discussions/4).
For a separate reproducible setup or documentation defect, use the
[client integration issue form](https://github.com/zzhang82/Agent-Memory-Bridge/issues/new?template=client_integration_request.yml).
Include the client and version, operating system, install source, redacted
config shape, and exact validation outcome. Remove tokens, private paths,
bridge contents, and other sensitive data first.
