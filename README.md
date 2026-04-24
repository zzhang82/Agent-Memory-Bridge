# Agent Memory Bridge

[简体中文](README.zh-CN.md)

[![MCP](https://img.shields.io/badge/MCP_Server-Enabled-4A90E2?logo=protocolsdotio&logoColor=white)](https://modelcontextprotocol.io)
[![Glama](https://glama.ai/mcp/servers/zzhang82/Agent-Memory-Bridge/badges/score.svg)](https://glama.ai/mcp/servers/zzhang82/Agent-Memory-Bridge)
[![License: MIT](https://img.shields.io/badge/license-MIT-2ea44f.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB.svg)](pyproject.toml)

Your coding agent forgets what it learned between sessions.

Agent Memory Bridge is a local-first MCP memory server for coding agents. It keeps
durable engineering memory separate from short-lived coordination state, stores both
in SQLite, and works over standard MCP stdio.

- `memory` for durable knowledge worth reusing later
- `signal` for short-lived coordination such as handoffs, review requests, and workflow state

Session output can be promoted through a governed ladder:

`session -> summary -> learn / gotcha -> domain-note -> belief -> concept-note`

Codex is the reference client, not the product boundary. If a client can launch a
local stdio MCP server, it can use Agent Memory Bridge.

<p align="center">
  <img src="examples/diagrams/amb-overview.svg" alt="Agent Memory Bridge overview: clients connect to a 10-tool MCP surface, which fronts the local-first core and local proof layer." width="1000">
</p>

![Agent Memory Bridge terminal demo](examples/demo/terminal-demo.gif)

> 30-second demo: store memory + signal -> claim -> extend -> ack -> later recall
> from the same project.

`0.12.0` makes the first five minutes easier: platform-neutral setup docs,
documented client snippets, and local `doctor` / `verify` checks, while the
public MCP surface stays at the same `10` tools.

## Client Support

Status labels stay narrow on purpose:

- `verified`: actively dogfooded by this repo
- `documented`: config shape is documented here against current official docs
- `locally tested`: exercised in our own environment, but not yet a primary public test path
- `supported`: covered by the generic stdio contract rather than a client-specific workflow

| Client | Status | Notes |
|---|---|---|
| Generic stdio MCP | supported | Any client that can launch a local stdio server |
| Codex | verified | Reference workflow and deepest dogfood path today |
| Cursor | documented | JSON `mcpServers` config |
| Cline | documented | JSON `mcpServers` config |
| Claude Code | documented | CLI or project-level MCP config shape |
| Claude Desktop | documented | Local stdio server shape; extensions and remote flows are separate |
| Antigravity | locally tested | Local `mcpServers` path exercised in our own setup |

Copyable snippets live in [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md).

## Why This Exists

Coding agents usually fail in one of two ways:

- they rediscover the same fixes every session
- they keep raw transcripts and call that memory, which makes recall noisy and expensive

Agent Memory Bridge takes a narrower path:

- MCP-native from day one
- local-first runtime
- SQLite + FTS5 instead of heavier memory infrastructure
- reusable memory and coordination state on the same inspectable bridge

If you want a broader hosted platform with dashboards, connectors, or a larger
memory stack, see [docs/COMPARISON.md](docs/COMPARISON.md).

## Who Is This For?

- You use Codex, Claude, Cursor, Cline, or another MCP client and keep re-explaining the same project decisions.
- You want local, inspectable memory instead of a cloud memory platform or opaque vector stack.
- You run review, handoff, or multi-agent workflows and need lightweight coordination without building a task queue first.

## Core Capabilities

1. Small public MCP surface. The bridge still exposes `10` public MCP tools while richer behavior stays behind that surface.
2. Two-channel memory with a real signal lifecycle. Signals follow `claim -> extend -> ack / expire / reclaim`.
3. Governed structured memory. Raw session output is promoted into compact, machine-readable artifacts with relation-lite metadata.
4. Applicable task-time memory. Procedures, concept notes, beliefs, and linked support can be assembled into one issue-oriented local context.
5. Governed procedure memory. `validated` procedures are preferred, `draft` and legacy procedures stay visible with warnings, and `stale`, `replaced`, and `unsafe` procedures are suppressed from governed task packets.

## Setup

All setup snippets below use POSIX-style placeholder paths so the examples stay
neutral. Translate them to your own shell and filesystem layout as needed.

Requirements:

- Python 3.11+
- SQLite with FTS5 support
- any MCP-compatible client that can launch a local stdio server

### 1. Install

Create a virtual environment, activate it using your shell's normal venv command,
then install the package:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

If you plan to run tests or work on the repo itself, install `.[dev]` instead.

### 2. Create a bridge config

Copy [config.example.toml](config.example.toml) to a local config path you control.
A simple starting point is:

```text
~/.config/agent-memory-bridge/config.toml
```

The most important sections are:

- `[bridge]` for the local database and logs
- `[classifier]` for optional classifier-assisted enrichment
- `[telemetry]` for metadata-only spans
- `[watcher]` and `[service]` for optional background automation
- `[reflex]` for promotion scans
- `[profile]` for optional import and migration helpers

See [docs/CONFIGURATION.md](docs/CONFIGURATION.md) for the short reference.

### 3. Render a client config snippet

The CLI can generate placeholder-safe config fragments for each documented client:

```bash
agent-memory-bridge config --client generic --example
agent-memory-bridge config --client codex --example
agent-memory-bridge config --client cursor --example
```

The generic stdio shape looks like this:

```json
{
  "mcpServers": {
    "agentMemoryBridge": {
      "command": "/path/to/agent-memory-bridge/.venv/bin/python",
      "args": [
        "-m",
        "agent_mem_bridge"
      ],
      "cwd": "/path/to/agent-memory-bridge",
      "env": {
        "AGENT_MEMORY_BRIDGE_HOME": "/path/to/bridge-home",
        "AGENT_MEMORY_BRIDGE_CONFIG": "/path/to/agent-memory-bridge-config.toml",
        "AGENT_MEMORY_BRIDGE_DEFAULT_SOURCE_CLIENT": "generic",
        "AGENT_MEMORY_BRIDGE_DEFAULT_CLIENT_TRANSPORT": "stdio"
      }
    }
  }
}
```

For client-specific snippets, file hints, and current support notes, see
[docs/INTEGRATIONS.md](docs/INTEGRATIONS.md).

### 4. Verify the install

Use the onboarding checks before wiring the bridge into a real project:

```bash
agent-memory-bridge doctor
agent-memory-bridge verify
```

- `doctor` checks Python, SQLite FTS5, config parsing, and writable bridge paths.
- `verify` launches an isolated stdio server, lists the public tool surface, stores and recalls one memory, and runs `claim -> extend -> ack` on a test signal without touching your live bridge state.

## 5-Minute Quickstart

Once the MCP server is registered in your client, the shortest useful path is:

1. write one durable memory
2. write one coordination signal
3. inspect the namespace
4. claim the signal, extend if needed, then acknowledge it

```text
store(
  namespace="project:demo",
  kind="memory",
  content="claim: Use WAL mode for concurrent readers."
)

store(
  namespace="project:demo",
  kind="signal",
  content="release note review ready",
  tags=["handoff:review"],
  ttl_seconds=600
)

stats(namespace="project:demo")
browse(namespace="project:demo", limit=10)

claim_signal(
  namespace="project:demo",
  consumer="reviewer-a",
  lease_seconds=300,
  tags_any=["handoff:review"]
)

extend_signal_lease(
  id="<signal_id>",
  consumer="reviewer-a",
  lease_seconds=300
)

ack_signal(id="<signal_id>", consumer="reviewer-a")
```

Lease renewal is not reclaim. If a lease is still active, the current claimant can
extend it. If it has gone stale, another worker should reclaim it instead.
When `signal_id` is omitted, `claim_signal(...)` picks from the oldest eligible
window with a small fairness bias so one polling consumer does not keep winning by
accident.

## Task-Time Memory Micro-Example

In a later session on the same project, the bridge can assemble a compact task
packet instead of returning one flat blob:

```text
task: "prepare release cutover"

procedure_hits:
- release-cutover-checklist

concept_hits:
- reversible-change-window

belief_hits:
- prefer rollback-ready steps before irreversible ones

supporting_hits:
- latest benchmark regression check
- watcher-db-mismatch gotcha
```

That is bridge-internal task-memory behavior, not a new top-level MCP tool, but it
is the clearest example of where procedures, concepts, beliefs, and support meet.

## Procedure Governance Micro-Example

Procedure records can now carry explicit governance fields:

```text
record_type: procedure
procedure_status: validated
goal: Run release cutover with proof before tagging.
when_to_use: Before a public release.
when_not_to_use: For local-only spike branches.
prerequisites: clean working tree | current benchmark report
steps: run benchmark | run release contract | tag release
failure_mode: stale docs or benchmark numbers can mislead users
rollback_path: stop release, update docs/report, rerun checks
```

At task time, the governed packet prefers `validated` procedures, keeps `draft`
and legacy no-status procedures visible with warnings, and suppresses `stale`,
`replaced`, and `unsafe` procedures from the selected task context.

## Evidence

Runnable, not just claimed.

| Gate | Result |
|---|---|
| `pytest` | `185 passed` |
| deterministic proof | `4/4` checks |

Retrieval benchmark (`question_count = 11`):

| Metric | Score |
|---|---|
| `memory_expected_top1_accuracy = 1.0` | bridge |
| `memory_mrr = 1.0` | bridge |
| `file_scan_expected_top1_accuracy = 0.636` | file-scan baseline |
| `file_scan_mrr = 0.909` | file-scan baseline |

Optional classifier enrichment (`sample_count = 16`):

| Metric | Value |
|---|---|
| `classifier_exact_match_rate = 0.875` | classifier |
| `fallback_exact_match_rate = 0.062` | keyword fallback |
| `classifier_better_count = 13` | classifier wins |
| `fallback_better_count = 2` | fallback wins |

Procedure governance benchmark (`case_count = 7`):

| Metric | Score |
|---|---|
| `flat_case_pass_rate = 0.429` | flat packet |
| `governed_case_pass_rate = 1.0` | governed packet |
| `flat_blocked_procedure_leak_rate = 1.0` | flat packet |
| `governed_blocked_procedure_leak_rate = 0.0` | governed packet |
| `governed_governance_field_completeness = 1.0` | governed packet |

## Honest Boundaries

`0.12.0` is deliberately scoped. It is not:

- a graph database
- capable of full relation-aware traversal or ranking across the whole store
- a scheduler or agent runtime
- active worker execution on top of stored signals
- automatic procedure learning from raw transcripts
- cross-domain concept synthesis yet

## MCP Tools

The public MCP surface stays small on purpose:

- `store` and `recall`
- `browse` and `stats`
- `forget` and `promote`
- `claim_signal`, `extend_signal_lease`, and `ack_signal`
- `export`

The complexity stays behind the bridge:

- optional rollout/session watcher flows
- checkpoint and closeout sync
- reflex promotion
- consolidation
- task-time assembly

## Namespaces

Start simple:

- `global` for a default shared bucket
- `project:<workspace>` for project-local memory
- `domain:<name>` for reusable domain knowledge

The framework is profile-agnostic. A specific operator profile can sit on top, but
the bridge itself does not need to look or sound like that profile.

## Trust and Health Checks

The bridge is meant to be inspectable, not magical:

- `browse`, `stats`, `forget`, and `export` let you inspect and correct bridge state without opening SQLite
- signal status is visible and queryable through `pending`, `claimed`, `acked`, and `expired`
- watcher health checks verify that rollout files still parse into usable summaries
- metadata-only telemetry can be summarized without exposing stored memory bodies
- classifier shadow and assist behavior is covered by fixture-based regression tests
- the current test suite passes with `185 passed`

Useful commands from an active virtual environment:

```bash
python -m pytest
python ./scripts/verify_stdio.py
python ./scripts/run_deterministic_proof.py
python ./scripts/run_benchmark.py
python ./scripts/run_healthcheck.py --report-path ./.runtime/healthcheck-report.json
python ./scripts/run_watcher_healthcheck.py --report-path ./.runtime/watcher-health-report.json
```

## Proof and Benchmark

Retrieval quality is benchmarked instead of guessed.

The bridge ships with a small proof and benchmark harness:

- deterministic proof checks signal lifecycle correctness, duplicate suppression, relation metadata, and recall timing
- retrieval benchmark tracks `precision@1`, `precision@3`, `recall@1`, `recall@3`, `MRR`, and `expected_top1_accuracy`
- the retrieval report compares bridge recall against a simple file-scan baseline
- reviewed classifier calibration compares expected tags, fallback tags, raw classifier tags, retained classifier tags, and low-confidence filtering
- activation stress fixtures shake the learning ladder without touching live bridge state

On the current canonical fixture:

- `question_count = 11`
- `memory_expected_top1_accuracy = 1.0`
- `memory_mrr = 1.0`
- `file_scan_expected_top1_accuracy = 0.636`
- `file_scan_mrr = 0.909`
- `duplicate_suppression_rate = 1.0`
- `relation_metadata_passed = true`

On the current reviewed calibration set:

- `sample_count = 16`
- `classifier_exact_match_rate = 0.875`
- `fallback_exact_match_rate = 0.062`
- `classifier_better_count = 13`
- `fallback_better_count = 2`
- `classifier_filtered_low_confidence_count = 2`

For deterministic local replays of the published snapshots:

```bash
python ./scripts/run_classifier_calibration.py --fixture-gateway
python ./scripts/run_activation_stress_pack.py
```

This is not a leaderboard. It is a regression harness that keeps retrieval quality,
learning quality, and coordination semantics honest as the bridge evolves.

## Docs

Public docs:

- [CONTRIBUTING.md](CONTRIBUTING.md)
- [benchmark/README.md](benchmark/README.md)
- [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md)
- [docs/COMPARISON.md](docs/COMPARISON.md)
- [docs/CONFIGURATION.md](docs/CONFIGURATION.md)
- [docs/CLIENT-PROVENANCE.md](docs/CLIENT-PROVENANCE.md)
- [docs/MEMORY-TAXONOMY.md](docs/MEMORY-TAXONOMY.md)
- [docs/PROMOTION-RULES.md](docs/PROMOTION-RULES.md)
- [examples/README.md](examples/README.md)

Maintainer notes stay in `docs/`, but they are intentionally not part of the
public docs index.

## License

MIT. See [LICENSE](LICENSE).
