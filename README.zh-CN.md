# Agent Memory Bridge

[English](README.md)

[![MCP](https://img.shields.io/badge/MCP_Server-Enabled-4A90E2?logo=protocolsdotio&logoColor=white)](https://modelcontextprotocol.io)
[![Glama](https://glama.ai/mcp/servers/zzhang82/Agent-Memory-Bridge/badges/score.svg)](https://glama.ai/mcp/servers/zzhang82/Agent-Memory-Bridge)
[![CI](https://github.com/zzhang82/Agent-Memory-Bridge/actions/workflows/ci.yml/badge.svg)](https://github.com/zzhang82/Agent-Memory-Bridge/actions/workflows/ci.yml)
[![GitHub Release](https://img.shields.io/github/v/release/zzhang82/Agent-Memory-Bridge?logo=github&color=2ea44f)](https://github.com/zzhang82/Agent-Memory-Bridge/releases)
[![License: MIT](https://img.shields.io/badge/license-MIT-2ea44f.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB.svg)](pyproject.toml)

让不同 coding agents 在不同工具和 session 之间共享一份受治理的项目决策记录。

Agent Memory Bridge 为使用多个 coding agents 的开发者和团队提供 shared engineering memory。它补充 `AGENTS.md`、`CLAUDE.md` 和客户端原生 preference memory，而不是取代它们。SQLite/WAL 是 durable authority，FTS5 和可选本地 embeddings 则是用于 lexical、semantic 或 hybrid retrieval 的 derived indexes。

`0.21.2` 是基于同一套 **Governed Memory Under Change** contract 的小型 MIT attribution patch。许可证现在通过公开 GitHub handle `zzhang82` 标识维护者，同时保持现有 runtime、proof 和 10-tool MCP surface 不变。

> Codex 是参考工作流，不是产品边界。AMB 使用本地 stdio MCP；客户端集成只按下方标注声明为 documented 或 locally verified。

<p align="center">
  <img src="examples/diagrams/amb-overview.svg" alt="Agent Memory Bridge 架构：通用 MCP-compatible coding agents 通过分组的 10 个工具访问 SQLite/WAL authority、derived indexes、governed change，以及不会自动 durable writeback 的 context 和 reports；proof gates 独立于 runtime。" width="800">
</p>

**Runtime：**MCP-compatible coding agents -> 10 个 public tools -> SQLite/WAL authority -> 不会自动 durable writeback 的 governed context 和 reports。**Proof：**release checks 和 benchmarks 位于 runtime path 之外。

## 为什么存在

很多 agent memory 会落入两个极端：

- summary 变成过时的大块文本
- vector store 能召回，但很难解释为什么召回
- 每个新 session 都要重新学习同一个 gotcha
- handoff 状态变成临时笔记，或者被迫搭一个并不想要的队列

AMB 选择更小的路径：本地 SQLite authority、显式 namespace、可检查记录、benchmark 过的 lexical / hybrid recall，以及轻量 signal lifecycle。

## 能带来什么

- Durable memory：决策、gotcha、procedure、concept、belief 和 supporting records。
- Coordination signals：`claim -> extend -> ack / expire / reclaim`，但不假装自己是 scheduler。
- Governed learning：runtime learning 可以先进入 policy-gated learning candidate staging，再经过 review/promotion 变成 durable records。
- Context assembly：startup 和 task-time context 可以从 procedure、concept、belief、gotcha 和 linked support 编译出来，不需要增加更多 MCP tools。
- Governed change：在 guidance 变为 actionable 之前检查显式 deletion、supersession、changed premise 和 task-domain applicability。
- Proof discipline：release contract、public-surface check、onboarding check、benchmark snapshot，以及 `373 passed`。

## 适合谁

- 你在使用多个 coding agents，希望项目决策、gotcha 和 handoff 能在它们之间共享。
- 你已经在使用 `AGENTS.md`、`CLAUDE.md` 或原生 preference memory，还需要一个并行的 governed cross-agent layer。
- 你想要本地、可检查的 memory，而不是云平台或不透明的 vector stack。
- 你在跑 review、handoff 或 multi-agent workflow，需要轻量 coordination signal，但还不想搭完整 task queue。

## 安装

要求：

- Python 3.11+
- 带 FTS5 的 SQLite；可选本地 embeddings 是 derived index，不是 durable authority
- 任意能启动本地 stdio server 的 MCP-compatible client
- 可选的 `uv` / `uvx`，用于最快的一条命令 GitHub smoke test

基线 editable install 只需要 Python：

```bash
python -m venv .venv
# 先按你的 shell 激活 virtual environment，然后：
python -m pip install -e .
agent-memory-bridge doctor
agent-memory-bridge verify
```

可选的最快 GitHub smoke test 使用 `uvx`：

```bash
uvx --from git+https://github.com/zzhang82/Agent-Memory-Bridge agent-memory-bridge verify
```

### 快速开始：Unified First-Run

如果你想直接得到某个客户端的完整 copy/paste setup guide，先用
`first-run`。它会渲染安装步骤、placeholder-safe config snippet、验证命令，
以及第一份 Task Brief preview。它不会写 client config 文件，也不会写 durable
memory records。

```bash
agent-memory-bridge first-run --client generic --example
agent-memory-bridge first-run --client codex --example
agent-memory-bridge first-run --client opencode --example
agent-memory-bridge first-run --client hermes --example
```

如果你只需要 config snippet，再直接用 `config`：

```bash
agent-memory-bridge config --client generic --example
agent-memory-bridge config --client codex --example
agent-memory-bridge config --client opencode --example
agent-memory-bridge config --client hermes --example
agent-memory-bridge config --client cursor --example
```

如果你想要隔离运行时，也可以用 Dockerized stdio：

```bash
docker build -t agent-memory-bridge:local .
docker run --rm -i -e AGENT_MEMORY_BRIDGE_HOME=/data/agent-memory-bridge -v /path/to/bridge-home:/data/agent-memory-bridge agent-memory-bridge:local
```

客户端配置见 [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md)。运行时配置见 [docs/CONFIGURATION.md](docs/CONFIGURATION.md)。authority / correction 边界见 [docs/AUTHORITY-CONTRACT.md](docs/AUTHORITY-CONTRACT.md)。安全说明见 [SECURITY.md](SECURITY.md)。安装 bridge 的 agent 应该从 [INSTALL_FOR_AGENTS.md](INSTALL_FOR_AGENTS.md) 开始。

## 第一个有用闭环

Session 1 发现一条项目规则：

```text
store(
  namespace="project:demo",
  kind="memory",
  content="claim: Use WAL mode for concurrent SQLite readers."
)
```

Session 2 回到同一个项目：

```text
recall(namespace="project:demo", query="SQLite concurrent readers")
```

agent 可以自己拿回那条规则，不需要用户再讲一遍。

协作状态用 signal：

```text
store(namespace="project:demo", kind="signal", content="release note review ready")
claim_signal(namespace="project:demo", consumer="reviewer-a", lease_seconds=300)
extend_signal_lease(id="<signal_id>", consumer="reviewer-a", lease_seconds=300)
ack_signal(id="<signal_id>")
```

短版是：

```text
WITHOUT AMB
user> We hit this last time too: run the generator after schema edits.

WITH AMB
agent> I found the previous gotcha: run the generator after schema edits.
```

终端 demo 和 before/after gotcha story 都在 [examples/demo](examples/demo/README.md)，故事源文件在 [examples/demo/before-after-gotcha.cast.md](examples/demo/before-after-gotcha.cast.md)。

Task Brief 不需要 Agent Memory Harness (AMH)。AMB CLI 可以把召回的 AMB
records 渲染成针对具体任务的 derived context report，标出哪些 context 被
used、ignored 或 needs review。这个 brief 是 AMB memory 之上的 derived
view；它不是第二个 durable store，也不会增加 MCP tools。

## 客户端支持

状态标签刻意保持保守。

| Client | Status | Notes |
|---|---|---|
| Generic stdio MCP | supported | 任意能启动本地 stdio server 的客户端 |
| Codex | verified | 参考工作流，也是最深的 dogfood 路径 |
| Claude Code | documented | CLI 或 project-level stdio MCP config |
| Claude Desktop | documented | 本地 stdio server config；remote/extension flow 是另一层 |
| Cursor | documented | JSON `mcpServers` config |
| Cline | documented | JSON `mcpServers` config |
| Antigravity | locally tested | 在本地 setup 里验证过；UI/config 细节可能变化 |
| OpenCode | locally tested | JSON `mcp` local command config |
| Hermes | locally tested | 本地 profile 使用 YAML `mcp_servers`；adapter workflow 仍保持手工边界 |

## MCP Tools

bridge 暴露 `10` public MCP tools：

- `store`, `recall`, `browse`, `stats`
- `forget`, `promote`, `export`
- `claim_signal`, `extend_signal_lease`, `ack_signal`

更复杂的能力留在 surface 后面：reflex promotion、consolidation、startup/task-time assembly、procedure governance、telemetry summaries、signal contention checks、learning-candidate review queues、Task Brief reports，以及 human review workflows。当前没有单独的 `task_packet`、`startup_packet`、`learning_candidate`、`task_brief`、`review_queue` 或 `review_workflow` MCP tools。

正常 always-on service 使用时，Codex-log watcher capture、reflex promotion 和 strong consolidation 默认关闭。这样多 runtime 安装仍然可以运行 governance checks 和 embedding sidecar maintenance，但不会静默地把原始 session / process chatter 提升成 durable memory。

operator review work 是 CLI report，不是 MCP tool：

```bash
agent-memory-bridge review-queue --namespace project:demo --format markdown
agent-memory-bridge review-workflow --namespace project:demo --format markdown
agent-memory-bridge task-brief --namespace project:demo --query "release handoff" --format markdown
```

`review-queue` 会显示 staged candidates、review receipts、tombstones、stale records 和 quarantined claims。`review-workflow` 会把这些 queue items 转成明确的 human decision prompts 和 manual steps。`task-brief` 会把已有 task-memory assembly、review queue items 和 active signals 编译成 `Used`、`Ignored` 和 `Needs Review` 区块。三者都是 proposal-only/read-only CLI reports，不会自动 durable writeback。

### 静态 schema 客户端兼容性

有些 MCP client 会为 tool 生成静态 input schema，因此在 `kind="memory"` 路径上也可能带上 signal-only fields：例如 `store` 里的 `ttl_seconds` 或 `expires_at`，以及 `recall`、`browse` 或 `export` 里的 `signal_status`。AMB 只会在 MCP transport 边界丢弃这些字段，不会把它们写入或用于查询 memory records；底层 memory store contract 仍然保持严格，durable memory 和 coordination signal 仍是两条独立 lane，真实的 signal lifecycle fields 仍只属于 `kind="signal"`。

## Proof Snapshot

`0.21.2` 继承固定的 v0.21 governed-change proof，覆盖被删除、被 supersede、因 premise 变化而失效，或被用于不同 task domain 的 memory。它仍然是边界明确的本地 memory system；本版本不声称通用 machine unlearning、graph-memory traversal、隐私合规、vendor certification 或 automatic policy enforcement。Tombstone 只审计被删除的 record ID；它不会阻止调用方以后把相同内容显式写成一个新的 ID。

| Track | Current signal |
|---|---|
| Retrieval | `memory_expected_top1_accuracy = 1.0`, `file_scan_expected_top1_accuracy = 0.636` |
| Calibration | `classifier_exact_match_rate = 0.875`, `fallback_exact_match_rate = 0.062` |
| Procedure governance | `governed_case_pass_rate = 1.0`, `governed_blocked_procedure_leak_rate = 0.0` |
| Learning candidates | policy-gated staging records 默认不进入 normal recall、browse、export 和 stats；只有显式 review tag 才会出现，且在 review/promotion 之前不属于 durable authority |
| Signal contention | `signal_contention_case_pass_rate = 1.0`, `duplicate_active_claim_count = 0` |
| Adversarial memory governance | `adversarial_case_count = 6`, `adversarial_task_count = 7`, `adversarial_governed_task_pass_rate = 1.0`, `adversarial_governed_blocked_record_leak_rate = 0.0` |
| Reviewed memory evolution | `memory_evolution_case_count = 6`, `memory_evolution_task_count = 7`, `memory_evolution_governed_task_pass_rate = 1.0`, `memory_evolution_governed_blocked_record_leak_rate = 0.0` |
| Reviewed memory operations | `review_queue_item_count = 6`, `review_queue_actionable_count = 6`, `review_queue_no_auto_mutation = true`, `review_queue_public_mcp_surface_change = false` |
| Human review workflow | `review_workflow_item_count = 6`, `review_workflow_manual_step_count = 27`, `review_workflow_auto_write_count = 0`, `review_workflow_public_mcp_surface_change = false` |
| Task Brief | `task_brief_used_count = 2`, `task_brief_ignored_count = 1`, `task_brief_needs_review_count = 4`, `task_brief_no_auto_writeback = true`, `task_brief_public_mcp_surface_change = false` |
| v0.19 adoption proof | 仅为 synthetic fixture proof，不代表 clean-room external adoption：`v019_case_count = 12`, `v019_pass_rate = 1.0`, `v019_public_mcp_surface_change = false`, `v019_client_config_write_count = 0` |
| v0.20 clean-room proof | 仅为 local reproducible proof，不代表 vendor certification：`v020_case_count = 6`, `v020_pass_rate = 1.0`, `v020_stdio_round_trip_pass = true`, `v020_client_config_write_count = 0`, `v020_external_vendor_adoption_claim = false` |
| v0.21 governed change proof | 固定的本地 executable proof：`v021_case_count = 20`, `v021_flat_baseline_hazards = 17`, `v021_governed_failures = 0`, `v021_governed_checkpoint_passes = 40`, `v021_auto_writeback_count = 0` |
| Test suite | `373 passed` |

<details>
<summary>Release contract facts</summary>

这些 snapshot facts 会由 release contract 检查：

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

完整 proof 见 [benchmark/README.md](benchmark/README.md)。

## 边界

AMB 不是 graph database、通用 unlearning system、hosted memory platform、scheduler、worker runtime、distributed lock、exactly-once coordination system、packet API、automatic policy engine、compliance certification，也不是从原始 transcript 自动 durable writeback 的通道。它是一个小而可检查的本地 bridge，用来保存可复用工程记忆和轻量协作状态。`forget` 仍然是显式 mutating operation；v0.21 让它更保守、可审计，而不是自动执行。

替代方案和取舍见 [docs/COMPARISON.md](docs/COMPARISON.md)。

## 文档

- [Client integrations](docs/INTEGRATIONS.md)
- [Configuration](docs/CONFIGURATION.md)
- [Authority contract](docs/AUTHORITY-CONTRACT.md)
- [Agent install protocol](INSTALL_FOR_AGENTS.md)
- [Benchmark and proof harness](benchmark/README.md)
- [v0.21.2 announcement](docs/v0.21.2-announcement.md)
- [Context assembly](docs/CONTEXT-ASSEMBLY.md)
- [Memory taxonomy](docs/MEMORY-TAXONOMY.md)
- [Promotion rules](docs/PROMOTION-RULES.md)
- [Client provenance](docs/CLIENT-PROVENANCE.md)
- [Examples](examples/README.md)
- [Contributing](CONTRIBUTING.md)
- [Security](SECURITY.md)

## License

MIT. See [LICENSE](LICENSE).
