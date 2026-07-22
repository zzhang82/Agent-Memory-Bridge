# Client And Model Provenance

Last updated: 2026-07-22 (America/New_York)

## Why This Exists

Agent Memory Bridge has real evidence that multiple MCP clients can connect to
the same bridge and write into the same namespace.

That is good news, but it exposed a real gap:

- the bridge can prove that a record was written
- it cannot authenticate which external client, model, user, or workspace caused
  that write

AMB stores internal writer metadata such as:

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
- which external client the caller declared for the action
- which model the caller declared was active when the action happened

## Core Design Rule

Keep provenance separate from every other axis.

| Axis | Question it answers | Examples |
| --- | --- | --- |
| `namespace` | whose or where | `global`, `project:demo-app` |
| `domain` | what topic it applies to | `domain:memory-bridge` |
| `control_level` | how strongly it should steer behavior | `policy`, `belief`, `signal` |
| `record_type` | what kind of artifact it is | `learn`, `gotcha`, `persona` |
| `writer` | which bridge component wrote the row | `bridge-reflex`, `bridge-consolidation` |
| `provenance` | which external client/model the caller declared | `codex`, `antigravity`, `gpt-5.x`, `gemini-*` |

Do not overload:

- `actor` to mean client
- `source_app` to mean model
- `session_id` to mean external session identity

Those shortcuts become ambiguous as soon as multiple clients share the same
bridge.

## Trust Boundary

Provenance fields are declared metadata. They may be supplied directly by the
caller or injected by a local launcher through environment-backed defaults.

AMB stores and preserves those values for filtering, debugging, calibration, and
receipt-style local evidence. It does not authenticate them. A stored
`source_client`, `source_model`, `client_session_id`, `client_workspace`, or
`client_transport` value is not proof of origin, vendor certification, model
execution, user identity, workspace ownership, or authorization.

Caller-supplied tags have the same basic boundary: they are useful labels, not
authenticated authority. Governance-sensitive code should validate reserved
control tags and policy decisions at the bridge boundary instead of trusting a
caller-provided label by itself.

Exports preserve provenance and tags, so exported files are raw sensitive
snapshots. Sanitize them before public sharing.

For the broader security boundary, see
[TRUST-BOUNDARY.md](TRUST-BOUNDARY.md) and [SECURITY.md](../SECURITY.md).

## Captured Provenance Fields

Current first-class provenance fields:

- `source_client`
  - declared caller label examples: `codex`, `antigravity`, `claude-code`,
    `cursor`
- `source_model`
  - declared model label examples: `gpt-5.4`, `gemini-2.5-pro`
- `client_session_id`
  - declared external client session/thread identifier when available
- `client_workspace`
  - declared client-visible workspace root or project label when useful
- `client_transport`
  - `stdio`, `http`, `sse`

Optional later fields that are not part of the current public contract:

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

This describes the external caller or environment declared for the write.

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
- declared origin: `antigravity`

Without that split, multi-client evaluation becomes muddy.

## Current Shipped Shape

The shipped shape stays deliberately small. Stored rows include optional
provenance fields:

- `source_client`
- `source_model`
- `client_session_id`
- `client_workspace`
- `client_transport`

No existing client breaks if it does not supply them.

MCP store paths expose those fields where clients can provide them.

Important:

- keep them optional
- prefer caller-supplied values over defaults
- do not make legacy callers fail validation
- do not use these fields as authentication or authorization

For stdio MCP clients that cannot or do not pass provenance fields on each tool
call, the bridge also supports optional environment-backed defaults such as:

- `AGENT_MEMORY_BRIDGE_DEFAULT_SOURCE_CLIENT`
- `AGENT_MEMORY_BRIDGE_DEFAULT_SOURCE_MODEL`
- `AGENT_MEMORY_BRIDGE_DEFAULT_CLIENT_SESSION_ID`
- `AGENT_MEMORY_BRIDGE_DEFAULT_CLIENT_WORKSPACE`
- `AGENT_MEMORY_BRIDGE_DEFAULT_CLIENT_TRANSPORT`

This is a better fit for multi-client adoption than hard-coding one IDE into the
bridge config. Each client launcher can inject its own stable defaults without
changing the public MCP surface.

When reflex or consolidation writes derived records:

- keep current writer metadata
- carry forward origin metadata from the source rows when present

Recall and export preserve these fields so calibration and debugging can filter
by declared client and model. Exports also preserve raw content and metadata, so
they should be handled as sensitive snapshots.

## Why This Matters

This is not just nicer telemetry.

It supports real product needs:

- compare client behavior across the same bridge
- inspect whether a learn was declared as coming from Codex or another tool
- run cross-client dogfood with visible declared labels
- calibrate model-assisted enrichment by declared client/model slice
- understand which startup assumptions are portable and which are client-local

## Release Framing

This should be framed as:

- declared multi-client provenance
- client-aware memory hygiene
- better cross-client calibration

Not:

- analytics for analytics' sake
- a replacement for profile or control-layer architecture
- authenticated identity, model attestation, or access control

The main purpose is to make shared memory legible once more than one client is
using the same bridge.
