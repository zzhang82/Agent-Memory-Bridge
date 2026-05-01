# Agent Memory Bridge

[简体中文](README.zh-CN.md)

[![MCP](https://img.shields.io/badge/MCP_Server-Enabled-4A90E2?logo=protocolsdotio&logoColor=white)](https://modelcontextprotocol.io)
[![Glama](https://glama.ai/mcp/servers/zzhang82/Agent-Memory-Bridge/badges/score.svg)](https://glama.ai/mcp/servers/zzhang82/Agent-Memory-Bridge)
[![CI](https://github.com/zzhang82/Agent-Memory-Bridge/actions/workflows/ci.yml/badge.svg)](https://github.com/zzhang82/Agent-Memory-Bridge/actions/workflows/ci.yml)
[![GitHub Release](https://img.shields.io/github/v/release/zzhang82/Agent-Memory-Bridge?logo=github&color=2ea44f)](https://github.com/zzhang82/Agent-Memory-Bridge/releases)
[![License: MIT](https://img.shields.io/badge/license-MIT-2ea44f.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB.svg)](pyproject.toml)

Your coding agent should not rediscover the same project decisions every session.

Agent Memory Bridge is persistent engineering memory for coding agents: a local-first MCP memory layer and context compiler backed by SQLite + FTS5. It captures durable repo decisions, gotchas, procedures, and handoffs while keeping short-lived coordination separate.

> Codex is the reference workflow, not the product boundary. If a client can launch a local stdio MCP server, it can use Agent Memory Bridge.

<p align="center">
  <img src="examples/diagrams/amb-overview.png" alt="Agent Memory Bridge overview: clients connect to the MCP surface, which fronts the local-first memory core and proof layer." width="1000">
</p>

## Why It Exists

Most agent memory either feels too shallow or too heavy:

- summaries become stale blobs
- vector stores hide why something was recalled
- every new session re-learns the same gotchas
- handoff state turns into ad hoc notes or a queue you did not want to build

AMB takes a smaller path: local SQLite, explicit namespaces, inspectable records, benchmarked recall, and a signal lifecycle for lightweight coordination.

## What You Get

- Durable memory: decisions, gotchas, procedures, concepts, beliefs, and supporting records.
- Coordination signals: `claim -> extend -> ack / expire / reclaim` without pretending to be a scheduler.
- Governed learning: session output can move through `summary -> learn / gotcha -> domain-note -> belief -> concept-note`.
- Context assembly: startup and task-time context can be compiled from procedures, concepts, beliefs, gotchas, and linked support without adding more MCP tools.
- Proof discipline: release contract checks, public-surface checks, onboarding checks, benchmark snapshots, and `201 passed`.

## Who It Is For

- You use Codex, Claude, Cursor, Cline, Antigravity, or another MCP client and keep re-explaining the same project conventions.
- You want memory that is local and inspectable instead of a hosted platform or opaque vector stack.
- You run review, handoff, or multi-agent workflows and need coordination signals without building a full task queue.

## Install

Requirements:

- Python 3.11+
- SQLite with FTS5 support
- any MCP-compatible client that can launch a local stdio server

One-command GitHub smoke test with `uvx`:

```bash
uvx --from git+https://github.com/zzhang82/Agent-Memory-Bridge agent-memory-bridge verify
```

Local editable install:

```bash
python -m venv .venv
# Activate the virtual environment for your shell, then:
python -m pip install -e .
agent-memory-bridge doctor
agent-memory-bridge verify
```

Generate a placeholder-safe client config:

```bash
agent-memory-bridge config --client generic --example
agent-memory-bridge config --client codex --example
agent-memory-bridge config --client cursor --example
```

Dockerized stdio works too when you want an isolated runtime:

```bash
docker build -t agent-memory-bridge:local .
docker run --rm -i -e AGENT_MEMORY_BRIDGE_HOME=/data/agent-memory-bridge -v /path/to/bridge-home:/data/agent-memory-bridge agent-memory-bridge:local
```

Client-specific notes live in [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md). Runtime configuration lives in [docs/CONFIGURATION.md](docs/CONFIGURATION.md). Authority and correction rules live in [docs/AUTHORITY-CONTRACT.md](docs/AUTHORITY-CONTRACT.md). Security guidance lives in [SECURITY.md](SECURITY.md).

## The First Useful Loop

Session 1 discovers a project rule:

```text
store(
  namespace="project:demo",
  kind="memory",
  content="claim: Use WAL mode for concurrent SQLite readers."
)
```

Session 2 asks about the same project:

```text
recall(namespace="project:demo", query="SQLite concurrent readers")
```

The agent gets the rule back without the user typing it again.

For coordination, use signals:

```text
store(namespace="project:demo", kind="signal", content="release note review ready")
claim_signal(namespace="project:demo", consumer="reviewer-a", lease_seconds=300)
extend_signal_lease(id="<signal_id>", consumer="reviewer-a", lease_seconds=300)
ack_signal(id="<signal_id>")
```

The short version:

```text
WITHOUT AMB
user> We hit this last time too: run the generator after schema edits.

WITH AMB
agent> I found the previous gotcha: run the generator after schema edits.
```

The terminal demo and the before/after gotcha story are in [examples/demo](examples/demo/README.md), with the story source at [examples/demo/before-after-gotcha.cast.md](examples/demo/before-after-gotcha.cast.md).

## Client Support

Status labels are intentionally narrow.

| Client | Status | Notes |
|---|---|---|
| Generic stdio MCP | supported | Any client that can launch a local stdio server |
| Codex | verified | Reference workflow and deepest dogfood path |
| Claude Code | documented | CLI or project-level stdio MCP config |
| Claude Desktop | documented | Local stdio server config; remote/extension flows are separate |
| Cursor | documented | JSON `mcpServers` config |
| Cline | documented | JSON `mcpServers` config |
| Antigravity | locally tested | Exercised in a local setup; UI/config details can vary |

## MCP Tools

The bridge exposes `10` public MCP tools:

- `store`, `recall`, `browse`, `stats`
- `forget`, `promote`, `export`
- `claim_signal`, `extend_signal_lease`, `ack_signal`

The richer behavior stays behind that surface: reflex promotion, consolidation, startup/task-time assembly, procedure governance, telemetry summaries, and signal contention checks. There are no separate `task_packet` or `startup_packet` MCP tools.

## Proof Snapshot

`0.13.0` is focused on coordination under contention while keeping the public tool surface stable.

| Track | Current signal |
|---|---|
| Retrieval | `memory_expected_top1_accuracy = 1.0`, `file_scan_expected_top1_accuracy = 0.636` |
| Calibration | `classifier_exact_match_rate = 0.875`, `fallback_exact_match_rate = 0.062` |
| Procedure governance | `governed_case_pass_rate = 1.0`, `governed_blocked_procedure_leak_rate = 0.0` |
| Signal contention | `signal_contention_case_pass_rate = 1.0`, `duplicate_active_claim_count = 0` |
| Adversarial memory governance | `adversarial_case_count = 6`, `adversarial_task_count = 7`, `adversarial_governed_task_pass_rate = 1.0`, `adversarial_governed_blocked_record_leak_rate = 0.0` |
| Test suite | `201 passed` |

<details>
<summary>Release contract facts</summary>

Snapshot facts checked by the release contract:

```text
question_count = 11
memory_expected_top1_accuracy = 1.0
memory_mrr = 1.0
file_scan_expected_top1_accuracy = 0.636
file_scan_mrr = 0.909

sample_count = 16
classifier_exact_match_rate = 0.875
fallback_exact_match_rate = 0.062
classifier_better_count = 13
fallback_better_count = 2
classifier_filtered_low_confidence_count = 2

case_count = 7
flat_case_pass_rate = 0.429
governed_case_pass_rate = 1.0
flat_blocked_procedure_leak_rate = 1.0
governed_blocked_procedure_leak_rate = 0.0
governed_governance_field_completeness = 1.0

signal_contention_case_count = 5
signal_contention_case_pass_rate = 1.0
unique_active_claim_rate = 1.0
duplicate_active_claim_count = 0
active_reclaim_block_rate = 1.0
stale_ack_blocked_rate = 1.0
stale_reclaim_success_rate = 1.0
pending_under_pressure_claim_rate = 1.0
initial_hard_expiry_cap_rate = 1.0

adversarial_case_count = 6
adversarial_task_count = 7
adversarial_governed_task_pass_rate = 1.0
adversarial_governed_blocked_record_leak_rate = 0.0
```

</details>

Full proof details are in [benchmark/README.md](benchmark/README.md).

## Boundaries

AMB is not a graph database, hosted memory platform, scheduler, worker runtime, distributed lock, exactly-once coordination system, packet API, or automatic procedure learner. It is a small local bridge for reusable engineering memory and lightweight coordination.

For alternatives and trade-offs, see [docs/COMPARISON.md](docs/COMPARISON.md).

## Docs

- [Client integrations](docs/INTEGRATIONS.md)
- [Configuration](docs/CONFIGURATION.md)
- [Authority contract](docs/AUTHORITY-CONTRACT.md)
- [Benchmark and proof harness](benchmark/README.md)
- [Context assembly](docs/CONTEXT-ASSEMBLY.md)
- [Memory taxonomy](docs/MEMORY-TAXONOMY.md)
- [Promotion rules](docs/PROMOTION-RULES.md)
- [Client provenance](docs/CLIENT-PROVENANCE.md)
- [Examples](examples/README.md)
- [Contributing](CONTRIBUTING.md)
- [Security](SECURITY.md)

## License

MIT. See [LICENSE](LICENSE).
