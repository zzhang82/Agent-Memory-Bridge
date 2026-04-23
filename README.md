# Agent Memory Bridge

[简体中文](README.zh-CN.md)

[![MCP](https://img.shields.io/badge/MCP_Server-Enabled-4A90E2?logo=protocolsdotio&logoColor=white)](https://modelcontextprotocol.io)
[![Glama](https://glama.ai/mcp/servers/zzhang82/Agent-Memory-Bridge/badges/score.svg)](https://glama.ai/mcp/servers/zzhang82/Agent-Memory-Bridge)
[![License: MIT](https://img.shields.io/badge/license-MIT-2ea44f.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB.svg)](pyproject.toml)

Your coding agent keeps rediscovering the same fixes.
It mixes permanent knowledge with temporary workflow noise.
Transcripts pile up — expensive to search, too noisy to reuse.

**Agent Memory Bridge draws a clean line:**

- **Memory** — durable knowledge that persists across sessions
- **Signal** — short-lived coordination that expires when the job is done

Session output gets promoted into structured, reusable artifacts through a
governed ladder. Works with any MCP-compatible client over stdio.

![Agent Memory Bridge terminal demo](examples/demo/terminal-demo.gif)

> 30-second demo: store memory + signal → claim → extend → ack → later recall from the same project.
> Local, inspectable, and still small enough to debug.

`0.10.0` — relation-aware task memory on the same small MCP surface.

Most memory tools put everything into one bucket. Agent Memory Bridge keeps two
different kinds of state separate:

- `memory` for durable knowledge worth reusing later
- `signal` for short-lived coordination events such as handoffs, review requests, and workflow state

The bridge then promotes session output through a small governed ladder:

`session -> summary -> learn / gotcha -> domain-note -> belief -> concept-note`

Procedures sit beside that ladder as curated durable artifacts that can be
assembled at task time.

## The Problem

Coding agents lose too much between sessions. Teams either keep rediscovering the
same fixes, or they end up storing raw transcripts that are expensive to search
and noisy to reuse.

Agent Memory Bridge takes a narrower path:

- MCP-native from day one
- local-first runtime
- SQLite + FTS5 instead of heavier infrastructure
- session capture that turns real coding work into reusable memory

If you want a broader hosted platform with dashboards, connectors, or a larger
memory stack, see [docs/COMPARISON.md](docs/COMPARISON.md).

## Who Is This For?

- **Solo devs** tired of re-explaining the same architectural decisions at the start of every session.
- **Small teams** running review, handoff, or multi-agent workflows that need lightweight coordination state alongside durable memory.
- **Tool and IDE builders** who want a local MCP memory substrate they can benchmark, inspect, and build on — no cloud dependency.

## Core Capabilities

1. Small public MCP surface. The bridge still exposes `10` public MCP tools, while
   the richer behavior lives behind that surface.
2. Two-channel memory with a real signal lifecycle. Durable knowledge and
   coordination signals stay separate, and signals follow `claim -> extend -> ack / expire / reclaim`.
3. Governed structured memory. The bridge keeps promoting raw session output into
   compact machine-readable artifacts, now with relation-lite metadata and a
   conservative belief and concept-note layer.
4. Applicable task-time memory. Procedures, concept notes, beliefs, and linked
   support can now be assembled into one issue-oriented local context.

## Task-Memory Micro-Example

A session that encountered a mismatch gotcha stored it. In a later session on the
same project, the bridge surfaced that gotcha along with the relevant procedure
and belief, without being told to look for it.

Inside the bridge, issue-oriented recall now assembles a compact task packet
instead of returning one flat blob:

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

That is emerging `0.10` engine behavior, not a new top-level MCP tool, but it is
the clearest example of where procedures, concepts, beliefs, and support now meet.

## Evidence

Runnable, not just claimed.

| Gate | Result |
|---|---|
| `pytest` | `175 passed` |
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

## Honest Boundaries

`0.10.0` is deliberately scoped. It is not:

- a graph database
- capable of full relation-aware traversal or ranking across the whole store
- a scheduler or agent runtime
- active worker execution on top of stored signals
- automatic procedure learning from raw transcripts
- cross-domain concept synthesis yet

## 5-Minute Quickstart

Once the MCP server is registered in your MCP client, the shortest useful path is:

1. write one durable memory
2. write one coordination signal
3. inspect the namespace
4. claim, extend if needed, and acknowledge the signal

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

That shows the core split:

- `memory` keeps what the agent learned
- `signal` carries what another workflow needs to act on right now

Lease renewal is not reclaim. If a lease is still active, the current claimant can
extend it. If it has gone stale, another worker should reclaim it instead.
When `signal_id` is omitted, `claim_signal(...)` picks from the oldest eligible
window with a small fairness bias so one polling consumer does not keep winning by
accident.

## Demo

There is now a short terminal demo in the repo:

- GIF: [examples/demo/terminal-demo.gif](examples/demo/terminal-demo.gif)
- source: [examples/demo/README.md](examples/demo/README.md)
- more sanitized example payloads: [examples/README.md](examples/README.md)

The current public demo shows a small durable memory bundle, a compact signal
lifecycle, and a later recall moment where the bridge surfaces useful task
memory.

## Setup

Basic bridge usage does not require profile import, watcher install, telemetry, or
classifier assistance. Those are optional layers you can add later.

Requirements:

- Python 3.11+
- SQLite with FTS5 support
- any MCP-compatible client

### 1. Install

Create a virtual environment, activate it, then install the package:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
```

If you plan to run tests or work on the repo itself, install `.[dev]` instead.

### 2. Create bridge config

Copy [config.example.toml](config.example.toml) to a local config path you control.
For example:

```text
~/.config/agent-memory-bridge/config.toml
```

The important sections are `[bridge]`, `[classifier]`, `[telemetry]`, `[watcher]`,
`[reflex]`, and `[profile]`. See [docs/CONFIGURATION.md](docs/CONFIGURATION.md)
for the full reference including classifier modes, telemetry modes, and the
multi-machine SQLite note.

If you are using Codex on Windows, `%USERPROFILE%\\.codex\\mem-bridge\\config.toml`
is still a fine runtime path. It is just not required.

### 3. Register the MCP server in your MCP client

The bridge only needs a Python command, a working directory, and a few
environment variables. A platform-neutral shape looks like this:

```text
command = "/path/to/agent-memory-bridge/.venv/bin/python"
args = ["-m", "agent_mem_bridge"]
cwd = "/path/to/agent-memory-bridge"

AGENT_MEMORY_BRIDGE_HOME = "/path/to/bridge-home"
AGENT_MEMORY_BRIDGE_CONFIG = "/path/to/agent-memory-bridge-config.toml"
```

If you are using Codex on Windows, the same values often look like this in
`$CODEX_HOME/config.toml`:

```toml
[mcp_servers.agentMemoryBridge]
command = "D:\\path\\to\\agent-memory-bridge\\.venv\\Scripts\\python.exe"
args = ["-m", "agent_mem_bridge"]
cwd = "D:\\path\\to\\agent-memory-bridge"

[mcp_servers.agentMemoryBridge.env]
CODEX_HOME = "%USERPROFILE%\\.codex"
AGENT_MEMORY_BRIDGE_HOME = "%USERPROFILE%\\.codex\\mem-bridge"
AGENT_MEMORY_BRIDGE_CONFIG = "%USERPROFILE%\\.codex\\mem-bridge\\config.toml"
```

### 4. Run the bridge

Start the MCP server from the repo root:

```bash
python -m agent_mem_bridge
```

Optional Codex-oriented background automation:

```bash
python ./scripts/run_mem_bridge_service.py
```

Windows PowerShell one-cycle example:

```powershell
$env:AGENT_MEMORY_BRIDGE_RUN_ONCE = "1"
python .\scripts\run_mem_bridge_service.py
```

Optional Codex-specific integrations:

- install Codex watcher startup: `.\scripts\install_startup_watcher.ps1`
- build a local Docker image:

```powershell
docker build -t agent-memory-bridge:local .
docker --context desktop-linux run --rm -i agent-memory-bridge:local
```

## MCP Tools

The public MCP surface stays small on purpose:

- `store` and `recall`
- `browse` and `stats`
- `forget` and `promote`
- `claim_signal`, `extend_signal_lease`, and `ack_signal`
- `export`

The complexity stays behind the bridge:

- watcher capture from Codex rollout files
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
- watcher health checks verify that Codex rollout files still parse into usable summaries
- metadata-only telemetry can be summarized without exposing stored memory bodies
- classifier shadow and assist behavior is covered by fixture-based regression tests
- the current test suite passes with `175 passed`

Useful commands (run from an active virtual environment):

```bash
python -m pytest
python ./scripts/verify_stdio.py
python ./scripts/run_deterministic_proof.py
python ./scripts/run_benchmark.py
python ./scripts/run_healthcheck.py --report-path ./.runtime/healthcheck-report.json
python ./scripts/run_watcher_healthcheck.py --report-path ./.runtime/watcher-health-report.json
```

Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe .\scripts\verify_stdio.py
.\.venv\Scripts\python.exe .\scripts\run_deterministic_proof.py
.\.venv\Scripts\python.exe .\scripts\run_benchmark.py
.\.venv\Scripts\python.exe .\scripts\run_healthcheck.py --report-path .\.runtime\healthcheck-report.json
.\.venv\Scripts\python.exe .\scripts\run_watcher_healthcheck.py --report-path .\.runtime\watcher-health-report.json
```

## Proof and Benchmark

Retrieval quality is benchmarked instead of guessed.

The bridge now has a small proof and benchmark harness:

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

```powershell
.\.venv\Scripts\python.exe .\scripts\run_classifier_calibration.py --fixture-gateway
.\.venv\Scripts\python.exe .\scripts\run_activation_stress_pack.py
```

This is not a leaderboard. It is a regression harness that keeps retrieval quality,
learning quality, and coordination semantics honest as the bridge evolves.

## Docs

Public docs:

- [CONTRIBUTING.md](CONTRIBUTING.md)
- [benchmark/README.md](benchmark/README.md)
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
