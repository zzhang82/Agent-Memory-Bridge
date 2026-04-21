# Client And Model Provenance

Last updated: 2026-04-09 (America/New_York)

## Why This Exists

Agent Memory Bridge now has real evidence that multiple MCP clients can connect
to the same bridge and write into the same namespace.

That is good news, but it exposed a real gap:

- the bridge can prove that a record was written
- it cannot always prove which external client or model caused that write

Today, AMB already stores internal writer metadata such as:

- `actor`
- `source_app`
- `session_id`
- `correlation_id`

Those fields are useful, but they mostly describe the bridge-side writer path.

Examples:

- `bridge-reflex`
- `agent-memory-bridge-consolidation`

That is not the same as client provenance.

For multi-client operation, the bridge needs to distinguish:

- who wrote the record inside the bridge
- which external client initiated the action
- which model was active when the action happened

## Core Design Rule

Keep provenance separate from every other axis.

| Axis | Question it answers | Examples |
| --- | --- | --- |
| `namespace` | whose or where | `global`, `project:demo-app` |
| `domain` | what topic it applies to | `domain:memory-bridge` |
| `control_level` | how strongly it should steer behavior | `policy`, `belief`, `signal` |
| `record_type` | what kind of artifact it is | `learn`, `gotcha`, `persona` |
| `writer` | which bridge component wrote the row | `bridge-reflex`, `bridge-consolidation` |
| `provenance` | which external client/model initiated the work | `codex`, `antigravity`, `gpt-5.x`, `gemini-*` |

Do not overload:

- `actor` to mean client
- `source_app` to mean model
- `session_id` to mean external session identity

Those shortcuts become ambiguous as soon as multiple clients share the same
bridge.

## What Needs To Be Captured

Recommended first-class provenance fields:

- `source_client`
  - examples: `codex`, `antigravity`, `claude-code`, `cursor`
- `source_model`
  - examples: `gpt-5.4`, `gemini-2.5-pro`
- `client_session_id`
  - external client session/thread identifier when available
- `client_workspace`
  - the client-visible workspace root or project label when useful
- `client_transport`
  - `stdio`, `http`, `sse`

Optional later fields:

- `client_instance`
  - useful when one machine has multiple live windows of the same client
- `source_prompt_kind`
  - `chat`, `tool-call`, `automation`, `review`

## Writer Metadata vs Origin Metadata

AMB should preserve both.

### Writer metadata

This is what the bridge or its internal helpers actually used to write the row.

Examples:

- `actor = bridge-reflex`
- `source_app = agent-memory-bridge-reflex`

### Origin metadata

This describes the external caller or environment that caused the write.

Examples:

- `source_client = antigravity`
- `source_model = gemini-2.5-pro`

This distinction matters most for derived records.

For example:

1. Antigravity stores a raw note
2. reflex later promotes it into a learn
3. consolidation later rolls it into a domain note

The final domain note should still preserve the origin chain:

- writer: `bridge-consolidation`
- origin: `antigravity`

Without that split, multi-client evaluation becomes muddy.

## Minimal Rollout

The first rollout should stay small.

### Phase 1

Add optional fields to stored rows:

- `source_client`
- `source_model`
- `client_session_id`
- `client_workspace`
- `client_transport`

No existing client should break if it does not supply them.

### Phase 2

Expose those fields through MCP store paths where clients can provide them.

Important:

- keep them optional
- prefer caller-supplied values over defaults
- do not make legacy callers fail validation

For stdio MCP clients that cannot or do not pass provenance fields on each tool
call, the bridge should also support optional environment-backed defaults such as:

- `AGENT_MEMORY_BRIDGE_DEFAULT_SOURCE_CLIENT`
- `AGENT_MEMORY_BRIDGE_DEFAULT_SOURCE_MODEL`
- `AGENT_MEMORY_BRIDGE_DEFAULT_CLIENT_SESSION_ID`
- `AGENT_MEMORY_BRIDGE_DEFAULT_CLIENT_WORKSPACE`
- `AGENT_MEMORY_BRIDGE_DEFAULT_CLIENT_TRANSPORT`

This is a better fit for multi-client adoption than hard-coding one IDE into the
bridge config. Each client launcher can inject its own stable defaults without
changing the public MCP surface.

### Phase 3

When reflex or consolidation writes derived records:

- keep current writer metadata
- carry forward origin metadata from the source rows when present

### Phase 4

Make recall and export preserve the new fields so calibration and debugging can
filter by client and model.

## Why This Matters

This is not just nicer telemetry.

It supports real product needs:

- compare client behavior across the same bridge
- verify whether a learn came from Codex or another tool
- run cross-client dogfood without guesswork
- calibrate model-assisted enrichment by client/model slice
- understand which startup assumptions are portable and which are client-local

## Release Framing

This should be framed as:

- multi-client provenance
- client-aware memory hygiene
- better cross-client calibration

Not:

- analytics for analytics' sake
- a replacement for profile or control-layer architecture

The main purpose is to make shared memory legible once more than one client is
using the same bridge.
