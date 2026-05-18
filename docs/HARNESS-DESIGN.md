# Harness Design

Agent Memory Bridge can support a GBrain-like agent experience, but the bridge
should not become a full brain runtime. This document defines the boundary.

## Thesis

AMB Core should stay small. A future harness can make it feel brain-capable.

```text
agent-memory-harness
  install protocol
  setup wizard
  watcher config
  skillpacks
  startup/task preview runners
  eval replay

        depends on
            |
            v

agent-memory-bridge
  10 MCP tools
  SQLite + FTS5
  durable memory
  signal lifecycle
  governed learning
  context assembly internals
```

The harness is a companion layer, not a fork. It should depend on AMB instead of
duplicating or widening AMB Core.

## What Belongs In AMB Core

AMB Core owns durable, inspectable memory behavior:

- local SQLite + FTS5 storage
- `store`, `recall`, `browse`, `stats`
- `forget`, `promote`, `export`
- `claim_signal`, `extend_signal_lease`, `ack_signal`
- namespace, provenance, and relation metadata
- governed learning records such as gotchas, domain notes, beliefs, concepts,
  procedures, and supporting records
- internal startup and task-time context assembly
- benchmark, proof, public-surface, and onboarding checks

Core should remain usable by any stdio MCP client without requiring the harness.

## What Belongs In A Harness

A harness can make AMB easier to use without changing the core contract:

- agent-readable install flow
- first-run setup questions
- client config generation and validation
- watcher configuration
- closeout or checkpoint capture wiring
- startup and task packet previews
- skillpack templates
- real-query replay and review reports
- local dashboards over existing bridge records

The harness may automate operator workflow. It should not own durable memory
authority.

## What Should Stay Out Of Both For Now

Avoid expanding into:

- hosted multi-user backend
- autonomous task runner
- scheduler as product center
- distributed queue or lock service
- full graph database
- remote OAuth integration platform
- unreviewed automatic procedure learning from raw transcript text

Those may become separate integrations later, but they should not drive the next
core milestone.

## Packet Compiler Direction

The next useful core-adjacent design is packet compilation.

`startup_packet` is a compiled startup view:

- core policy or operating rules
- persona/profile notes when present
- project defaults
- current gotchas
- active signals
- stale warnings
- first recommended actions
- why each item was included

`task_packet` is a compiled task-time view:

- relevant procedures
- gotchas
- concepts
- beliefs
- supporting records
- suppressed stale or contradicted records
- why each item was included

These packet names describe internal compiler outputs and preview artifacts.
They are not public MCP tools yet.

## Compiled Truth And Evidence Timeline

The harness should render two layers:

```text
Current compiled truth
  concise answer to what the agent should know now
  ranked and token-budgeted
  includes record ids or stable references

Evidence timeline
  source records that support the compiled truth
  old gotchas and related decisions
  suppressed stale or contradicted records
  review notes for human correction
```

This keeps packets useful without making them source of truth. The database
records remain authoritative.

## Repository Boundary

Start in this repository with:

- `INSTALL_FOR_AGENTS.md`
- `llms.txt`
- this design document
- sanitized preview examples

Move into a companion repository when the work becomes executable tooling:

- watcher runtime
- setup wizard
- skillpack runner
- recurring maintenance command
- dashboards

The companion repository should be named and documented as a harness, not as a
fork of AMB Core.

