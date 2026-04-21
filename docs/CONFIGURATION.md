# Configuration Guide

Configuration guide for the sections most public installs will actually touch.
Copy [config.example.toml](../config.example.toml) to a local path you control,
then adjust the sections below. Advanced runtime defaults and maintainer-only
helpers still live in the sample config and codebase.

## `[bridge]`

Paths and settings for the local SQLite database.

## `[classifier]`

Optional enrichment layer that annotates stored records with inferred tags.

| Mode | Behaviour |
|---|---|
| `mode = "off"` | deterministic rule path only — no classifier involved |
| `mode = "shadow"` | classifier runs and divergence is recorded, but stored tags are unchanged |
| `mode = "assist"` | classifier tags enrich reflex output; keyword and rule logic remains the fallback |

`minimum_confidence = 0.6` prevents assist mode from merging low-confidence
classifier tags into the final record.

Shadow mode is the safe starting point. Assist mode is worth enabling once you
have reviewed the shadow divergence log and trust the classifier on your corpus.

## `[telemetry]`

Metadata-only observability spans. No raw memory content, no recall query text,
no export bodies are written.

| Mode | Behaviour |
|---|---|
| `mode = "off"` | lightweight local logs only |
| `mode = "jsonl"` | writes metadata-only spans to `$AGENT_MEMORY_BRIDGE_HOME/telemetry/spans.jsonl` |

Spans are safe to share for benchmarking purposes because they contain only
structural metadata (record kind, tag counts, timing) and not memory payloads.

## `[watcher]` and `[service]`

Optional background automation. The watcher captures Codex rollout files and
promotes session output on a configurable interval. The service wrapper keeps
that Codex-oriented watcher running as a background process.

Neither is required for basic MCP usage.

## `[reflex]`

Controls the promotion scanner that advances records through the governed ladder:

`session → summary → learn / gotcha → domain-note → belief → concept-note`

## `[profile]`

Optional profile import and migration helpers. Not required for basic bridge usage.
The example config includes `~/.codex/mem-bridge/profile-source` as a sample
optional path for import and migration helpers.

## Runtime paths

| Variable | Purpose |
|---|---|
| `AGENT_MEMORY_BRIDGE_HOME` | root directory for the bridge database and logs |
| `AGENT_MEMORY_BRIDGE_CONFIG` | path to the active `config.toml` |

### Multi-machine note

Keep the live SQLite database local on each machine. Shared SQLite is acceptable
as a transition or backup path but is not a strong multi-writer live backend.
Move to a hosted backend only if you genuinely need concurrent writes from multiple
machines.
