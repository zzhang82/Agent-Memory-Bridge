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

## `[retrieval]`

Controls recall ranking mode. The default remains conservative:

```toml
[retrieval]
mode = "lexical"
semantic_scan_limit = 1000
hybrid_semantic_weight = 18.0
embedding_provider = "hash"
embedding_command = ""
embedding_model = ""
embedding_dim = 64
embedding_timeout_seconds = 10
```

| Mode | Behavior |
|---|---|
| `lexical` | stable SQLite FTS5 recall plus existing local reranking |
| `semantic` | local sidecar-vector recall over a bounded recent scan |
| `hybrid` | lexical-anchored recall that may include semantic sidecar-only additions when packet budget allows |

The semantic sidecar is a derived cache. It is not source of truth and can be
rebuilt from the `memories` table. The bundled provider is
`local-token-hash-v1`, a deterministic local token-hash vectorizer meant for
safe shadow testing and regression checks. It is not a broad hosted embedding
model and should not be used to claim general semantic retrieval quality.

Embedding providers:

| Provider | Behavior |
|---|---|
| `hash` | bundled deterministic local token-hash vectorizer; no extra dependencies |
| `command` | trusted local command that receives texts as JSON on stdin and returns vectors as JSON on stdout |

The command provider lets you connect local tools such as an Ollama wrapper,
llama.cpp wrapper, or sentence-transformers script without adding a default
runtime dependency. It is disabled unless you set both:

```toml
[retrieval]
embedding_provider = "command"
embedding_command = "python /path/to/local_embedding_gateway.py"
embedding_model = "local-model-name"
embedding_dim = 768
```

AMB sends:

```json
{"texts": ["..."], "dim": 768, "model": "local-model-name"}
```

and expects either:

```json
{"vectors": [[0.1, 0.2]], "model": "local-model-name"}
```

or, for a single text:

```json
{"vector": [0.1, 0.2], "model": "local-model-name"}
```

Command output vectors must match `embedding_dim`. AMB normalizes vectors before
storing them in the derived sidecar. If `embedding_model` is omitted, AMB stores
a command-hash model id so a changed command does not silently satisfy the old
sidecar health check. The raw command string is config only and is not stored in
the `memory_embeddings` table.

Like classifier commands, embedding commands are trusted local code. They receive
memory text. AMB does not sandbox them or restrict filesystem/network access. See
[SECURITY.md](../SECURITY.md#embedding-command-trust-boundary).

Start with `lexical`, run a hybrid shadow benchmark, then decide whether your
local corpus benefits from `hybrid`:

```bash
python ./scripts/run_benchmark.py --include-hybrid
```

Index health can be inspected without mutating durable records:

```bash
agent-memory-bridge index-health
agent-memory-bridge index-health --strict-embeddings
```

`index-health` requires the FTS5 cache to be healthy by default. Embedding
completeness is only required with `--strict-embeddings`, because embeddings are
optional until you choose semantic or hybrid retrieval.

Derived indexes can be rebuilt safely:

```bash
agent-memory-bridge index-rebuild --fts
agent-memory-bridge index-rebuild --embeddings
```

These commands rebuild cache tables only. They do not change `memories` rows.

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
| `AGENT_MEMORY_BRIDGE_RETRIEVAL_MODE` | `lexical`, `semantic`, or `hybrid` |
| `AGENT_MEMORY_BRIDGE_SEMANTIC_SCAN_LIMIT` | maximum recent rows scanned by semantic sidecar recall |
| `AGENT_MEMORY_BRIDGE_HYBRID_SEMANTIC_WEIGHT` | weight used when hybrid reranks semantic sidecar scores |
| `AGENT_MEMORY_BRIDGE_EMBEDDING_PROVIDER` | `hash` or `command` |
| `AGENT_MEMORY_BRIDGE_EMBEDDING_COMMAND` | trusted local command for semantic vectors |
| `AGENT_MEMORY_BRIDGE_EMBEDDING_MODEL` | logical model id stored with embedding sidecar rows |
| `AGENT_MEMORY_BRIDGE_EMBEDDING_DIM` | expected embedding vector dimension |
| `AGENT_MEMORY_BRIDGE_EMBEDDING_TIMEOUT_SECONDS` | timeout for each embedding command call |

## Onboarding Checks

Use the CLI when you want to validate a fresh install instead of inspecting files
by hand:

```bash
agent-memory-bridge doctor
agent-memory-bridge verify
agent-memory-bridge index-health
```

- `doctor` checks Python, SQLite FTS5, config parsing, and writable runtime paths
- `verify` runs an isolated stdio smoke test without touching your live bridge state
- `index-health` checks derived FTS and optional embedding cache health

## Multi-Machine Note

Keep the live SQLite database local on each machine. Shared SQLite can be useful as
a transition or backup path, but it is not a strong multi-writer live backend.
Move to a hosted backend only if you genuinely need concurrent writes from multiple
machines.
