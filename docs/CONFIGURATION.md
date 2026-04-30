# Configuration Guide

Copy [config.example.toml](../config.example.toml) to a local path you control,
then edit only the sections you actually need. Basic bridge usage does not require
watchers, profile imports, telemetry, or classifier assistance.

A clean starting point is:

```text
~/.config/agent-memory-bridge/config.toml
```

For client registration examples, see [INTEGRATIONS.md](INTEGRATIONS.md).
Security model and vulnerability reporting guidance live in
[SECURITY.md](../SECURITY.md).

## Docker Runtime Defaults

The Docker image sets the bridge home to a neutral container path:

```text
AGENT_MEMORY_BRIDGE_HOME=/data/agent-memory-bridge
```

Mount a host directory at that path to keep the SQLite database and logs across
container restarts. The image does not set a client-specific home by default.

If you use a config file with Docker, mount it read-only and point the bridge at
the container path:

```bash
docker run --rm -i \
  -e AGENT_MEMORY_BRIDGE_HOME=/data/agent-memory-bridge \
  -e AGENT_MEMORY_BRIDGE_CONFIG=/config/config.toml \
  -v /path/to/bridge-home:/data/agent-memory-bridge \
  -v /path/to/agent-memory-bridge-config.toml:/config/config.toml:ro \
  agent-memory-bridge:local
```

## `[bridge]`

Core local runtime settings.

- `home`: root directory for the bridge database and logs
- `db_path`: SQLite database file name or path relative to `home`
- `log_dir`: log directory relative to `home`

The sample config uses neutral defaults under `~/.local/share/agent-memory-bridge`.

## `[classifier]`

Optional enrichment layer that adds inferred tags to stored records.

| Mode | Behavior |
|---|---|
| `mode = "off"` | deterministic rule path only |
| `mode = "shadow"` | classifier runs and divergence is recorded, but stored tags are unchanged |
| `mode = "assist"` | classifier tags enrich reflex output while keyword and rule logic remain the fallback |

`minimum_confidence = 0.6` prevents assist mode from merging low-confidence tags
into the final record.

Shadow mode is the safe starting point. Assist mode makes sense after you trust
the classifier on your own corpus.

When `provider = "command"` is configured, the classifier is a trusted local
command. AMB executes the configured command through the local shell, sends
classification candidates as JSON on stdin, and expects JSON on stdout. Candidate
payloads can include memory text, titles, source ids, and fallback tags.

AMB does not sandbox the classifier command, restrict its filesystem or network
access, or hide the bridge process environment from it. Only configure commands
you control and are willing to run with the same local privileges as AMB. Keep
classifier mode `off` unless you need it, prefer `shadow` before `assist`, and do
not point the command at unreviewed scripts or hosted services that should not
receive memory content. See [SECURITY.md](../SECURITY.md#classifier-command-trust-boundary).

## `[telemetry]`

Metadata-only observability spans.

| Mode | Behavior |
|---|---|
| `mode = "off"` | lightweight local logs only |
| `mode = "jsonl"` | writes metadata-only spans to `$AGENT_MEMORY_BRIDGE_HOME/telemetry/spans.jsonl` |

Telemetry intentionally excludes raw memory content, recall query text, and export
bodies. It is meant for local proof, benchmark summaries, and runtime diagnostics,
not transcript capture.

## `[watcher]` and `[service]`

Optional background automation.

- `[watcher]` controls local rollout and session-file capture helpers
- `[service]` controls the lightweight polling wrapper around that watcher

These helpers are not required for basic MCP usage. The current watcher workflow is
best developed around Codex-style rollout files, but the bridge itself does not
depend on Codex.

## `[reflex]`

Controls the promotion scanner that advances records through the governed ladder:

`session -> summary -> learn / gotcha -> domain-note -> belief -> concept-note`

## `[profile]`

Optional profile import and migration helpers.

The sample config points `source_root` at a neutral local path so imports and
migration helpers do not assume a Codex-specific home layout. You can leave this
section alone if you only want the basic bridge runtime.

## Runtime Environment Variables

| Variable | Purpose |
|---|---|
| `AGENT_MEMORY_BRIDGE_HOME` | root directory for the bridge database and logs |
| `AGENT_MEMORY_BRIDGE_CONFIG` | path to the active `config.toml` |
| `AGENT_MEMORY_BRIDGE_DEFAULT_SOURCE_CLIENT` | optional provenance default for the launching client |
| `AGENT_MEMORY_BRIDGE_DEFAULT_CLIENT_TRANSPORT` | optional transport default, usually `stdio` |

## Onboarding Checks

Use the CLI when you want to validate a fresh install instead of inspecting files
by hand:

```bash
agent-memory-bridge doctor
agent-memory-bridge verify
```

- `doctor` checks Python, SQLite FTS5, config parsing, and writable runtime paths
- `verify` runs an isolated stdio smoke test without touching your live bridge state

## Multi-Machine Note

Keep the live SQLite database local on each machine. Shared SQLite can be useful as
a transition or backup path, but it is not a strong multi-writer live backend.
Move to a hosted backend only if you genuinely need concurrent writes from multiple
machines.
