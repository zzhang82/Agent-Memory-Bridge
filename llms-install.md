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

Current package/source version is `0.29.0`. Use a source checkout with `<venv-python> -m pip install -e .` to evaluate an exact checkout. For live publication availability, consult GitHub Releases. When using the `v0.29.0` tagged release, use the pinned archive route documented in the release notes. The historical `v0.27.0` release-install archive was `https://github.com/zzhang82/Agent-Memory-Bridge/archive/refs/tags/v0.27.0.zip`.

```text
<venv-python> -m pip install "https://github.com/zzhang82/Agent-Memory-Bridge/archive/refs/tags/v0.29.0.zip"
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
to confirm the server connects and exposes the documented 17-tool public
surface. That client registration check is the gate that proves the config was
loaded.

The historical `v0.27.0` release-install route exposed `17` public MCP tools at client registration. The current source/package line is `0.29.0`; for live release availability, consult GitHub Releases.

## Optional `uvx` Shortcut

If `uvx` is already installed, it can run the GitHub source directly:

```bash
uvx --from git+https://github.com/zzhang82/Agent-Memory-Bridge@v0.29.0 agent-memory-bridge verify
```

Do not make this the only install instruction. `uv` is not a project baseline
requirement.

## First Useful Check

In the configured MCP client, call the `store(...)` and `recall(...)` MCP tools
to store one non-sensitive project gotcha and recall it from a later task or
session. They are not terminal subcommands. Keep the first check small and
review tool input before approval.

For non-empty `kind="memory"` text recall, AMB can return a 15-minute
`recall_receipt`. Treat it as sensitive: the token is HMAC-signed and
tamper-evident, not encrypted, and it does not authenticate caller provenance.
The returned rows, database epoch, complete exposure set, exact content
versions, and signature are assembled from the same SQLite read snapshot.
`recall(...)` can optionally sign bounded SHA-256 digests for caller-declared
`model`, `harness`, and `chat_template` labels; raw labels are not included.

The `feedback(...)` tool can append a vote, correction, or retraction for a
receipt-bound result. One current effective vote is exposed per signed
retrieval subject, and caller-declared client or session labels cannot create
additional votes. Feedback remains shadow-only: it does not mutate memories,
indexes, recall results, or ranking behavior.

For explicit episode evidence in the local `0.27.4` source, use
`begin_run(...)` to obtain server-minted run/work-item handles, then use
`record_run_event(...)`, `get_run(...)`, and `complete_run(...)`. These calls
create durable run, event, and outcome authority. Schema v12 retains
governed-v2 events/CAS, operator verification receipts, and watcher continuity,
and adds an internal exact-key Dynamic State release lane with typed commands,
epoch/version guards, lifecycle idempotency, immutable history, and rebuildable
heads. It adds no MCP tool and does not alter recall, ranking, policy, prompts,
or ordinary memory. Credit and consolidation remain shadow-only.

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
