# Agent Memory Bridge

[English](README.md) | 简体中文

[![MCP](https://img.shields.io/badge/MCP_Server-Enabled-4A90E2?logo=protocolsdotio&logoColor=white)](https://modelcontextprotocol.io)
[![Glama](https://glama.ai/mcp/servers/zzhang82/Agent-Memory-Bridge/badges/score.svg)](https://glama.ai/mcp/servers/zzhang82/Agent-Memory-Bridge)
[![License: MIT](https://img.shields.io/badge/license-MIT-2ea44f.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB.svg)](pyproject.toml)

给编码代理用的小而可检视的 MCP 记忆层：
让真正会复用的工程知识留下来，而不是每次都从旧聊天里重新翻找。

代理很容易忘掉修复、保留太多 transcript、又把持久知识和短期工作流状态混在一起。Agent Memory Bridge 把这些通道拆开，让它们在一个可验证、可解释、可回放的本地系统里持续累积。

桥本身不绑定 Codex。任何能通过 stdio 启动 MCP server 的客户端都可以接入；Codex 只是这个仓库当前最完整的集成路径。

`0.11.0` 的重点是：在不扩大公开 MCP surface 的前提下，让 procedure memory 更受治理：validated procedure 会被优先使用，draft / legacy procedure 仍可见但带 warning，stale / replaced / unsafe procedure 会从 governed task packet 里被压住。

- 公开 MCP 工具仍然只有 `10` 个，复杂度主要留在桥内部
- relation-lite 结构化记忆已经出现在 recall、export、stats、proof 和 health tooling 里
- retrieval 现在有 `precision@k`、`recall@k`、`MRR`、`expected_top1_accuracy` 这些可重复运行的指标
- 已经有第一版 task-time memory assembly，可以把 procedure、concept note、belief 和 supporting records 组合起来
- procedure governance 已经能区分 validated / draft / stale / replaced / unsafe，并在任务时组装中影响选择和 suppression

![Agent Memory Bridge terminal demo](examples/demo/terminal-demo.gif)

很多记忆工具会把所有状态都塞进一个桶里。Agent Memory Bridge 把两类状态拆开：

- `memory`：值得长期复用的持久知识
- `signal`：handoff、review、轮询和流程协调这类短期事件

桥内部沿着一条很小但受治理的梯子工作：

`session -> summary -> learn / gotcha -> domain-note -> belief -> concept-note`

`procedure` 不直接放进这条提升梯子里，而是作为可策划、可复用的 durable artifact，在任务时被组装进来。

## 适合谁

如果你要的是下面这种形状，AMB 会比较合适：

- 你在做多会话编码代理，需要把真正会复用的知识留下来，而不是保留整段 transcript
- 你想要一个小而可检视的 MCP 记忆层，而不是一个更大的 hosted 平台
- 你在意 coordination state，希望 `signal` 有清楚的 claim / extend / ack 生命周期
- 你想要可重复运行的 proof、benchmark 和 health check，而不是只看“感觉更聪明了”

如果你要的是 dashboard、connectors、hosted-first deployment 或更重的平台能力，OpenMemory 或 Mem0 会更接近那条路；AMB 的优势在于更小、更本地、更容易验证。

## 它解决什么问题

编码代理跨会话时会丢掉太多状态。团队最后往往会落到两条低效路径之一：

- 一直重复发现同样的问题和修复
- 把原始对话直接当记忆保存，最后变成难检索、难复用的噪音仓库

Agent Memory Bridge 走的是更克制的一条路：

- 从第一天起就是 MCP-native
- 本地优先
- 用 SQLite + FTS5，而不是先上重基础设施
- 从真实编码会话里提炼可复用记忆

## 四个核心能力

1. 小而稳定的公开 MCP surface。桥仍然只暴露 `10` 个顶层工具，更多能力留在桥内部演进。
2. 双通道记忆和完整 signal 生命周期。持久知识和协调信号分开，signal 遵循 `claim -> extend -> ack / expire / reclaim`。
3. 受治理的结构化记忆。session 输出会继续被提升成紧凑、机器可读的 artifact，现在还带有 relation-lite metadata 以及保守的 belief / concept-note 层。
4. 可直接应用的任务时记忆。procedure、concept note、belief 和 supporting records 可以被组装成一次 issue-oriented 的本地上下文。

## 一个 task-memory 小例子

假设你现在要处理一个 “release cutover” 问题，桥内部不会只吐回一堆命中的段落，而是尝试把几层东西拼成更有用的上下文：

- `procedure`：release-cutover checklist
- `concept-note`：为什么先 freeze 再迁移，能减少隐性漂移
- `belief`：这类切换更依赖显式边界和可回滚步骤
- `supporting record`：最近一次相关 gotcha 或 superseded note

这还不是一个新的公开 MCP tool，也不是完整 agent runtime。
它更像是桥内部已经开始具备的“任务时组装能力”。

## 证据

这个仓库现在有一套真实的验证面，而不是只有功能列表：

- `pytest` 当前结果是 `175 passed`
- deterministic proof 当前结果是 `4/4` checks passed
- canonical retrieval benchmark 当前结果是：
  - `question_count = 11`
  - `memory_expected_top1_accuracy = 1.0`
  - `memory_mrr = 1.0`
  - `file_scan_expected_top1_accuracy = 0.636`
  - `file_scan_mrr = 0.909`

可选 enrichment 也在测量：

- reviewed classifier calibration 当前结果是：
  - `sample_count = 16`
  - `classifier_exact_match_rate = 0.875`
  - `fallback_exact_match_rate = 0.062`
  - `classifier_better_count = 13`
  - `fallback_better_count = 2`

## 诚实边界

Agent Memory Bridge `0.11.0` 仍然不是：

- 图数据库
- 面向全库的 relation-aware traversal 或 ranking
- scheduler 或 agent runtime
- 构建在 signal 之上的 active worker execution
- 从原始 transcript 自动学出 procedure
- 跨 domain 的 concept synthesis

## 5 分钟上手

在你的 MCP 客户端里注册好服务器之后，最短的有效路径是：

1. 写一条 durable memory
2. 写一条 coordination signal
3. 看看命名空间里现在有什么
4. claim 那条信号，必要时 extend，然后 ack

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

这条路径最能体现它的核心拆分：

- `memory` 保存已经学到的东西
- `signal` 传递另一个流程现在需要处理的事

这里要特别说明一点：续租不等于重新认领。lease 还活着时，由当前 claimant 续租；lease 过期后，应该由新的 worker 重新 claim。
当 `signal_id` 留空时，`claim_signal(...)` 会在最老的 eligible 窗口里做一个很小的 fairness 偏置，减少“谁先轮询谁总赢”的偶然优势。

## Demo

仓库里已经有一个很短的终端演示：

- GIF: [examples/demo/terminal-demo.gif](examples/demo/terminal-demo.gif)
- source: [examples/demo/README.md](examples/demo/README.md)
- 更多净化过的示例载荷: [examples/README.md](examples/README.md)

这个公开 demo 会依次展示一个小型 durable memory bundle、一段精简的 signal lifecycle，以及一次“后面真的想起了有用东西”的 recall 时刻。

## 安装

基础 bridge 使用并不要求你先启用 profile import、watcher、telemetry 或 classifier。这些都是后续可选层。

要求：

- Python 3.11+
- 任意能启动 MCP / stdio server 的客户端或 IDE，例如 Codex
- 带 FTS5 的 SQLite

### 1. 安装依赖

PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .[dev]
```

macOS / Linux：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
```

如果你打算跑测试或直接参与这个 repo 的开发，再改用 `pip install -e .[dev]`。

### 2. 创建 bridge config

把 [config.example.toml](config.example.toml) 复制到你自己控制的本地配置路径，例如：

```text
~/.config/agent-memory-bridge/config.toml
```

几个关键 section：

- `[bridge]`：本地 bridge 数据库
- `[telemetry]`：可选的 metadata-only spans
- `[watcher]` 和 `[service]`：可选的后台自动化
- `[reflex]`：promotion 扫描
- `[classifier]`：可选的 enrichment
- `[profile]`：可选的 profile import / startup helpers

示例配置里保留了 `~/.codex/mem-bridge/profile-source` 这条中性 sample path，供可选导入和迁移 helper 使用。基础 bridge 使用并不要求你启用它。

如果你在 Windows 上配合 Codex 使用，`%USERPROFILE%\.codex\mem-bridge\config.toml` 当然也完全可以；只是它不是唯一要求的路径。

classifier 模式：

- `mode = "off"`：只走 deterministic rule path
- `mode = "shadow"`：记录 classifier 分歧，但不改存储标签
- `mode = "assist"`：让 classifier 标签参与 enrich，同时保留 keyword / rule fallback
- `minimum_confidence = 0.6`：避免 assist mode 合并低置信度标签

telemetry 模式：

- `mode = "off"`：只保留轻量本地日志
- `mode = "jsonl"`：把 metadata-only spans 写到 `$CODEX_HOME/mem-bridge/telemetry/spans.jsonl`

这些 spans 刻意不保存原始 memory 内容、recall query 文本和 export 正文，这样可以做 dogfood 和 benchmark，又不会把 observability 变成 transcript exhaust。

推荐做法：

- live SQLite 数据库保留在每台机器本地
- shared source root 只当可选 helper 输入，不当成基础要求
- 只有在你真的需要多机实时写入时，再考虑 hosted backend

注意：shared SQLite 适合过渡或备份，不适合作为强一致、多写入者的最终 live backend。

### 3. 在你的 MCP 客户端里注册 MCP server

桥真正需要的只有 Python 命令、工作目录和几项环境变量。一个平台中性的形状大概是：

```text
command = "/path/to/agent-memory-bridge/.venv/bin/python"
args = ["-m", "agent_mem_bridge"]
cwd = "/path/to/agent-memory-bridge"

AGENT_MEMORY_BRIDGE_HOME = "/path/to/bridge-home"
AGENT_MEMORY_BRIDGE_CONFIG = "/path/to/agent-memory-bridge-config.toml"
```

下面再用 Codex 举例，因为仓库当前主要用它做演示和验证；如果你用的是别的兼容客户端，只需要把同样的命令、参数和环境变量填到对应的 MCP 配置里即可。

把下面内容加到 `$CODEX_HOME/config.toml`：

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

### 4. 运行 bridge

启动 MCP server：

```powershell
.\.venv\Scripts\python.exe -m agent_mem_bridge
```

可选的 Codex 风格后台自动化：

```powershell
.\.venv\Scripts\python.exe .\scripts\run_mem_bridge_service.py
```

只跑一轮：

```powershell
$env:AGENT_MEMORY_BRIDGE_RUN_ONCE = "1"
.\.venv\Scripts\python.exe .\scripts\run_mem_bridge_service.py
```

可选集成：

- 安装 Codex watcher 开机启动：`.\scripts\install_startup_watcher.ps1`
- 构建本地 Docker 镜像：

```powershell
docker build -t agent-memory-bridge:local .
docker --context desktop-linux run --rm -i agent-memory-bridge:local
```

## MCP 工具

公开 MCP surface 刻意保持很小：

- `store` 和 `recall`
- `browse` 和 `stats`
- `forget` 和 `promote`
- `claim_signal`、`extend_signal_lease` 和 `ack_signal`
- `export`

真正的复杂度放在桥背后：

- watcher 从 Codex rollout 文件抓取状态
- checkpoint / closeout 同步
- reflex promotion
- consolidation
- task-time assembly

## 命名空间

最自然的起步方式：

- `global`：默认共享 bucket
- `project:<workspace>`：项目级记忆
- `domain:<name>`：可复用的领域知识

这个 framework 本身是 profile-agnostic 的。你可以在上面叠某个 operator profile，但桥本身不需要长成那个 profile 的样子。

## 可检视性与健康检查

这座桥的目标是可检视，不是黑盒：

- `browse`、`stats`、`forget`、`export` 让你不打开 SQLite 也能看清状态
- `signal` 状态可以直接查：`pending`、`claimed`、`acked`、`expired`
- watcher health check 会验证 Codex rollout 文件是否还能被解析成可用 summary
- metadata-only telemetry 可以做摘要，不暴露存储的 memory 正文
- classifier 的 shadow / assist 行为有基于 fixture 的回归测试覆盖
- 当前测试套件结果是 `175 passed`

常用命令：

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe .\scripts\verify_stdio.py
.\.venv\Scripts\python.exe .\scripts\run_deterministic_proof.py
.\.venv\Scripts\python.exe .\scripts\run_benchmark.py
.\.venv\Scripts\python.exe .\scripts\run_healthcheck.py --report-path .\.runtime\healthcheck-report.json
.\.venv\Scripts\python.exe .\scripts\run_watcher_healthcheck.py --report-path .\.runtime\watcher-health-report.json
```

## Proof 与 Benchmark

retrieval 质量现在已经是“可 benchmark”，不是“凭感觉猜”。

这座桥现在已经有一套小而可重复运行的 proof / benchmark harness：

- deterministic proof 会检查 signal lifecycle correctness、duplicate suppression、relation metadata 和 recall timing
- retrieval benchmark 会跟踪 `precision@1`、`precision@3`、`recall@1`、`recall@3`、`MRR`、`expected_top1_accuracy`
- retrieval report 会把 bridge recall 和简单 file-scan baseline 放在一起比较
- reviewed classifier calibration 会对比 expected tags、fallback tags、raw classifier tags、retained classifier tags 和 low-confidence filtering
- activation stress fixtures 会在不碰 live bridge state 的前提下摇一摇 learning ladder

在当前 canonical fixture 上：

- `question_count = 11`
- `memory_expected_top1_accuracy = 1.0`
- `memory_mrr = 1.0`
- `file_scan_expected_top1_accuracy = 0.636`
- `file_scan_mrr = 0.909`
- `duplicate_suppression_rate = 1.0`
- `relation_metadata_passed = true`

在当前 reviewed calibration set 上：

- `sample_count = 16`
- `classifier_exact_match_rate = 0.875`
- `fallback_exact_match_rate = 0.062`
- `classifier_better_count = 13`
- `fallback_better_count = 2`
- `classifier_filtered_low_confidence_count = 2`

如果你想在本地做确定性的 snapshot replay：

```powershell
.\.venv\Scripts\python.exe .\scripts\run_classifier_calibration.py --fixture-gateway
.\.venv\Scripts\python.exe .\scripts\run_activation_stress_pack.py
```

这不是排行榜，而是一套回归护栏，用来在 bridge 继续演化时持续盯住 retrieval 质量、learning 质量和 coordination 语义。

## 更多文档

公开产品文档：

- [CONTRIBUTING.md](CONTRIBUTING.md)
- [benchmark/README.md](benchmark/README.md)
- [docs/COMPARISON.md](docs/COMPARISON.md)
- [docs/CLIENT-PROVENANCE.md](docs/CLIENT-PROVENANCE.md)
- [docs/MEMORY-TAXONOMY.md](docs/MEMORY-TAXONOMY.md)
- [docs/PROMOTION-RULES.md](docs/PROMOTION-RULES.md)
- [examples/README.md](examples/README.md)

维护者说明仍然保留在 `docs/` 里，但不放进公开文档索引。

## License

MIT，见 [LICENSE](LICENSE)。
