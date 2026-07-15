# Agent Memory Bridge

[简体中文](README.zh-CN.md)

[![MCP](https://img.shields.io/badge/MCP_Server-Enabled-4A90E2?logo=protocolsdotio&logoColor=white)](https://modelcontextprotocol.io)
[![Glama](https://glama.ai/mcp/servers/zzhang82/Agent-Memory-Bridge/badges/score.svg)](https://glama.ai/mcp/servers/zzhang82/Agent-Memory-Bridge)
[![CI](https://github.com/zzhang82/Agent-Memory-Bridge/actions/workflows/ci.yml/badge.svg)](https://github.com/zzhang82/Agent-Memory-Bridge/actions/workflows/ci.yml)
[![GitHub Release](https://img.shields.io/github/v/release/zzhang82/Agent-Memory-Bridge?logo=github&color=2ea44f)](https://github.com/zzhang82/Agent-Memory-Bridge/releases)
[![License: MIT](https://img.shields.io/badge/license-MIT-2ea44f.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB.svg)](pyproject.toml)

Give coding agents one shared, governed record of project decisions across tools and sessions.

Agent Memory Bridge is shared engineering memory for developers and teams that use more than one coding agent. It complements `AGENTS.md`, `CLAUDE.md`, and client-native preference memory rather than replacing them. SQLite/WAL is the durable authority, with FTS5 and optional local embeddings as derived indexes for lexical, semantic, or hybrid retrieval.

`0.21.1` keeps the **Governed Memory Under Change** contract while hardening its Windows proof path and expanding client setup assets: explicit forgetting leaves transactional, content-redacted tombstones; exact machine-owned descendants can be deleted with their source while uncertain lineage is retained as degraded audit evidence; and task context handles transitive supersession, current-premise changes, and declared procedure domains conservatively.

> Codex is the reference workflow, not the product boundary. AMB uses local stdio MCP; client integrations are documented or locally verified only where labeled below.

<p align="center">
  <img src="examples/diagrams/amb-overview.svg" alt="Agent Memory Bridge architecture: generic MCP-compatible coding agents use 10 grouped tools backed by SQLite/WAL authority, derived indexes, governed change, and no-auto-writeback context and reports; proof gates remain outside runtime." width="800">
</p>

**Runtime:** MCP-compatible coding agents -> 10 public tools -> SQLite/WAL authority -> governed context and reports with no automatic durable writeback. **Proof:** release checks and benchmarks run outside that runtime path.

## Why It Exists

Most agent memory either feels too shallow or too heavy:

- summaries become stale blobs
- vector stores hide why something was recalled
- every new session starts cold or gets a stale context dump
- handoff state turns into ad hoc notes or a queue you did not want to build

AMB takes a smaller path: local SQLite authority, explicit namespaces, inspectable records, benchmarked lexical/hybrid recall, and a signal lifecycle for lightweight coordination.

## What You Get

- Durable memory: decisions, gotchas, procedures, concepts, beliefs, and supporting records.
- Coordination signals: `claim -> extend -> ack / expire / reclaim` without pretending to be a scheduler.
- Review-first writeback: learning candidates can be staged for human review before explicit promotion into durable records.
- Context assembly: startup and task-time context can be rendered from procedures, concepts, beliefs, gotchas, and linked support without adding more MCP tools.
- Governed change: explicit deletion, supersession, changed premises, and task-domain applicability are checked before guidance becomes actionable.
- Proof discipline: release contract checks, public-surface checks, onboarding checks, benchmark snapshots, and `373 passed`.

## Who It Is For

- You use more than one coding agent and want project decisions, gotchas, and handoffs to remain shared across them.
- You already use `AGENTS.md`, `CLAUDE.md`, or native preference memory and need a governed cross-agent layer alongside it.
- You want memory that is local and inspectable instead of a hosted platform or opaque vector stack.
- You run review, handoff, or multi-agent workflows and need coordination signals without building a full task queue.

## Install

Requirements:

- Python 3.11+
- SQLite with FTS5 support; optional local embeddings are derived indexes, not durable authority
- any MCP-compatible client that can launch a local stdio server
- optional `uv` / `uvx` for the fastest one-command GitHub smoke test

Baseline editable install with Python:

```bash
python -m venv .venv
# Activate the virtual environment for your shell, then:
python -m pip install -e .
agent-memory-bridge doctor
agent-memory-bridge verify
```

Optional fastest GitHub smoke test with `uvx`:

```bash
uvx --from git+https://github.com/zzhang82/Agent-Memory-Bridge agent-memory-bridge verify
```

### Quick Start: Unified First-Run

Use `first-run` when you want a complete copy/paste setup guide for a client.
It renders install steps, a placeholder-safe config snippet, verification
commands, and a first Task Brief preview. It does not write client config files
or durable memory records.

```bash
agent-memory-bridge first-run --client generic --example
agent-memory-bridge first-run --client codex --example
agent-memory-bridge first-run --client opencode --example
agent-memory-bridge first-run --client hermes --example
```

If you only need the config snippet, use `config` directly:

```bash
agent-memory-bridge config --client generic --example
agent-memory-bridge config --client codex --example
agent-memory-bridge config --client opencode --example
agent-memory-bridge config --client hermes --example
agent-memory-bridge config --client cursor --example
```

Dockerized stdio works too when you want an isolated runtime:

```bash
docker build -t agent-memory-bridge:local .
docker run --rm -i -e AGENT_MEMORY_BRIDGE_HOME=/data/agent-memory-bridge -v /path/to/bridge-home:/data/agent-memory-bridge agent-memory-bridge:local
```

Client-specific notes live in [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md). Runtime configuration lives in [docs/CONFIGURATION.md](docs/CONFIGURATION.md). Authority and correction rules live in [docs/AUTHORITY-CONTRACT.md](docs/AUTHORITY-CONTRACT.md). Security guidance lives in [SECURITY.md](SECURITY.md). Agents that are installing the bridge should start with [INSTALL_FOR_AGENTS.md](INSTALL_FOR_AGENTS.md).

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

Task Briefs do not require Agent Memory Harness (AMH). The AMB CLI can render a
derived task context report over recalled records, including what context was
used, ignored, or marked for review. That brief is a derived view over AMB
memory; it is not a second durable store and does not add MCP tools.

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
| OpenCode | locally tested | JSON `mcp` config shape for local commands |
| Hermes | locally tested | YAML `mcp_servers` shape in local profiles; adapter workflows remain manual |

## MCP Tools

The bridge exposes `10` public MCP tools:

- `store`, `recall`, `browse`, `stats`
- `forget`, `promote`, `export`
- `claim_signal`, `extend_signal_lease`, `ack_signal`

The richer behavior stays behind that surface: reviewed promotion helpers, consolidation, startup/task-time assembly, procedure policies, telemetry summaries, signal contention checks, learning-candidate review queues, Task Brief reports, and human review workflows. There are no separate `task_packet`, `startup_packet`, `learning_candidate`, `task_brief`, `review_queue`, or `review_workflow` MCP tools.

For normal service use, log capture helpers, promotion helpers, and strong consolidation are disabled by default. That lets installs run review checks and embedding sidecar maintenance without silently promoting raw session/process chatter into durable memory.

Operator review work is available as CLI reports, not MCP tools:

```bash
agent-memory-bridge review-queue --namespace project:demo --format markdown
agent-memory-bridge review-workflow --namespace project:demo --format markdown
agent-memory-bridge task-brief --namespace project:demo --query "release handoff" --format markdown
```

`review-queue` shows staged candidates, review receipts, tombstones, stale records, and quarantined claims. `review-workflow` turns those queue items into explicit human decision prompts and manual steps. `task-brief` composes existing task-memory assembly, review queue items, and active signals into `Used`, `Ignored`, and `Needs Review` sections. All three are proposal-only/read-only reports and perform no automatic durable writeback.

### Static-schema client compatibility

Some MCP clients generate one static input schema per tool and may send signal-only fields on `kind="memory"` paths: for example `ttl_seconds` or `expires_at` on `store`, and `signal_status` on `recall`, `browse`, or `export`. AMB drops those fields at the MCP transport boundary before creating or querying memory records. The lower-level memory store contract stays strict: durable memory and coordination signals remain separate lanes, and real signal lifecycle fields still belong only to `kind="signal"` operations.

## Proof Snapshot

`0.21.1` inherits the fixed v0.21 governed-change proof for memory that is deleted, superseded, invalidated by a changed premise, or applied to a different task domain. It remains a bounded local memory system: the release does not claim general machine unlearning, graph-memory traversal, privacy compliance, vendor certification, or automatic policy enforcement. Tombstones audit deleted record IDs; they do not prevent a caller from explicitly storing the same content later under a new ID.

| Track | Current signal |
|---|---|
| Retrieval | `memory_expected_top1_accuracy = 1.0`, `file_scan_expected_top1_accuracy = 0.636` |
| Calibration | `classifier_exact_match_rate = 0.875`, `fallback_exact_match_rate = 0.062` |
| Procedure governance | `governed_case_pass_rate = 1.0`, `governed_blocked_procedure_leak_rate = 0.0` |
| Learning candidates | policy-gated staging records are suppressed from normal recall, browse, export, and stats unless explicitly queried with review tags; candidates are not durable authority until reviewed/promoted |
| Signal contention | `signal_contention_case_pass_rate = 1.0`, `duplicate_active_claim_count = 0` |
| Adversarial memory governance | `adversarial_case_count = 6`, `adversarial_task_count = 7`, `adversarial_governed_task_pass_rate = 1.0`, `adversarial_governed_blocked_record_leak_rate = 0.0` |
| Reviewed memory evolution | `memory_evolution_case_count = 6`, `memory_evolution_task_count = 7`, `memory_evolution_governed_task_pass_rate = 1.0`, `memory_evolution_governed_blocked_record_leak_rate = 0.0` |
| Reviewed memory operations | `review_queue_item_count = 6`, `review_queue_actionable_count = 6`, `review_queue_no_auto_mutation = true`, `review_queue_public_mcp_surface_change = false` |
| Human review workflow | `review_workflow_item_count = 6`, `review_workflow_manual_step_count = 27`, `review_workflow_auto_write_count = 0`, `review_workflow_public_mcp_surface_change = false` |
| Task Brief | `task_brief_used_count = 2`, `task_brief_ignored_count = 1`, `task_brief_needs_review_count = 4`, `task_brief_no_auto_writeback = true`, `task_brief_public_mcp_surface_change = false` |
| v0.19 adoption proof | synthetic fixture proof only, not clean-room external adoption: `v019_case_count = 12`, `v019_pass_rate = 1.0`, `v019_public_mcp_surface_change = false`, `v019_client_config_write_count = 0` |
| v0.20 clean-room proof | local reproducible proof only, not vendor certification: `v020_case_count = 6`, `v020_pass_rate = 1.0`, `v020_stdio_round_trip_pass = true`, `v020_client_config_write_count = 0`, `v020_external_vendor_adoption_claim = false` |
| v0.21 governed change proof | fixed local executable proof: `v021_case_count = 20`, `v021_flat_baseline_hazards = 17`, `v021_governed_failures = 0`, `v021_governed_checkpoint_passes = 40`, `v021_auto_writeback_count = 0` |
| Test suite | `373 passed` |

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

memory_evolution_case_count = 6
memory_evolution_task_count = 7
memory_evolution_governed_task_pass_rate = 1.0
memory_evolution_governed_blocked_record_leak_rate = 0.0
memory_evolution_governed_disposition_reason_hit_rate = 1.0

review_queue_item_count = 6
review_queue_actionable_count = 6
review_queue_hidden_lane_count = 2
review_queue_writeback_plan_count = 6
review_queue_no_auto_mutation = true
review_queue_public_mcp_surface_change = false
review_queue_item_type_count = 6

review_workflow_source_queue_item_count = 6
review_workflow_item_count = 6
review_workflow_manual_step_count = 27
review_workflow_requires_human_count = 6
review_workflow_auto_write_count = 0
review_workflow_no_auto_writeback = true
review_workflow_public_mcp_surface_change = false
review_workflow_item_type_count = 6

task_brief_used_count = 2
task_brief_ignored_count = 1
task_brief_needs_review_count = 4
task_brief_review_queue_item_count = 2
task_brief_active_signal_count = 1
task_brief_no_auto_writeback = true
task_brief_public_mcp_surface_change = false
task_brief_needs_review_source_type_count = 3

v019_case_count = 12
v019_pass_count = 12
v019_pass_rate = 1.0
v019_retrieval_case_count = 4
v019_retrieval_pass_rate = 1.0
v019_task_brief_case_count = 4
v019_task_brief_pass_rate = 1.0
v019_first_run_adoption_case_count = 4
v019_first_run_adoption_pass_rate = 1.0
v019_public_mcp_tool_count = 10
v019_public_mcp_surface_change = false
v019_client_config_write_count = 0
v019_durable_writeback_count = 0
v019_amh_required = false
v019_native_memory_comparison_required = true

v020_case_count = 6
v020_pass_count = 6
v020_pass_rate = 1.0
v020_import_sanity_pass = true
v020_stdio_round_trip_pass = true
v020_first_run_pass = true
v020_task_brief_pass = true
v020_public_mcp_tool_count = 10
v020_public_mcp_surface_change = false
v020_client_config_write_count = 0
v020_explicit_demo_memory_write_count = 1
v020_explicit_demo_signal_write_count = 0
v020_non_demo_durable_writeback_count = 0
v020_amh_required = false
v020_external_vendor_adoption_claim = false

v021_case_count = 20
v021_category_count = 4
v021_flat_baseline_hazards = 17
v021_flat_baseline_hazards_expected = 17/20
v021_governed_case_pass_count = 20
v021_governed_failures = 0
v021_governed_failures_target = 0/20
v021_governed_checkpoint_passes = 40
v021_governed_checkpoint_passes_target = 40/40
v021_governed_checkpoint_result_count = 40
v021_useful_current_retention_pass = true
v021_suppress_all_can_pass = false
v021_public_mcp_tool_count = 10
v021_public_mcp_surface_change = false
v021_auto_writeback_count = 0
v021_config_write_count = 0
v021_durable_live_writeback_count = 0
```

</details>

Full proof details are in [benchmark/README.md](benchmark/README.md).

## Boundaries

AMB is not a graph database, general unlearning system, hosted memory platform, scheduler, worker runtime, distributed lock, exactly-once coordination system, packet API, automatic policy engine, compliance certification, or unreviewed durable writeback path from raw transcripts. It is a small local bridge for reusable engineering memory and lightweight coordination. `forget` remains an explicit mutating operation; v0.21 makes that operation more conservative and auditable rather than automatic.

For alternatives and trade-offs, see [docs/COMPARISON.md](docs/COMPARISON.md).

## Docs

- [Client integrations](docs/INTEGRATIONS.md)
- [Configuration](docs/CONFIGURATION.md)
- [Authority contract](docs/AUTHORITY-CONTRACT.md)
- [Agent install protocol](INSTALL_FOR_AGENTS.md)
- [Benchmark and proof harness](benchmark/README.md)
- [v0.21.1 announcement](docs/v0.21.1-announcement.md)
- [Context assembly](docs/CONTEXT-ASSEMBLY.md)
- [Memory taxonomy](docs/MEMORY-TAXONOMY.md)
- [Promotion rules](docs/PROMOTION-RULES.md)
- [Client provenance](docs/CLIENT-PROVENANCE.md)
- [Examples](examples/README.md)
- [Contributing](CONTRIBUTING.md)
- [Security](SECURITY.md)

## License

MIT. See [LICENSE](LICENSE).
