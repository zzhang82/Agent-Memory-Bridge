# Agent Memory Bridge

[English](README.md) | 简体中文

面向编码代理的 MCP-native 记忆层。它把 coding session 沉淀成可复用的工程记忆。

当前先从 Codex workflow 开始。

它把大多数 memory 工具混在一起的两类东西拆开了：

- `memory`：值得留到后面复用的持久知识
- `signal`：用于 handoff、轮询和流程协调的短期事件

Agent Memory Bridge 是一个 **MCP-native、local-first** 的 agent memory framework。它用来保存聊天上下文最容易丢掉的东西：

- 关键决策
- 已验证修复
- 跨会话 handoff
- 可复用 gotcha
- 紧凑的领域知识

核心原则很简单：**让记忆层保持小、可靠、可检查**。更高层的 orchestration 放在它上面，而不是塞进它里面。

它会自动把会话输出继续提炼成更稳定的记忆：

- session 变成可复用的 `learn`
- 重复失败变成 `gotcha`
- 一组经验再变成紧凑的 `domain-note`

## 这个项目想解决什么

很多 agent memory 系统最后会落到三种情况之一：

- 记忆被困在某一个 app 或某一个模型里
- 还没证明 retrieval 真有价值，就先上重型基础设施
- 把 transcript 当 memory，最后变成噪音仓库

这个项目走的是更克制的一条路：

- 从第一天起就是 MCP-native
- 本地优先
- 用 SQLite + FTS5，不先上重型服务
- 自动把 session 输出提升成可复用 memory

它的核心是一条 **memory shaping pipeline**：

`session -> summary -> learn -> gotcha -> domain-note`

## 项目定位

Agent Memory Bridge 是故意做窄的。

如果你要的是更大的 memory 平台，带 SDK、dashboard、connectors、多种应用接入面，那么 OpenMemory 或 Mem0 更接近那种形态。

这个项目故意不走那条路：

1. 它面向 coding-agent workflow，而不是通用笔记存储。
2. 它把 MCP surface 刻意保持得很小，而且可检查。
3. 它重点是把 session 输出提升成紧凑、机器可读的 memory，而不是把 summary 当成最终产物。
4. 它默认 local-first，而且运行形态可检查。

更完整的定位说明见 [docs/COMPARISON.md](docs/COMPARISON.md)。

## 5 分钟上手路径

在 Codex 里注册好 MCP 之后，最短的有效路径是：

1. 先写一条 durable memory
2. 再写一条 coordination signal
3. 不用碰 SQLite，直接看看 namespace 里有什么

示例流程：

```text
store(namespace="project:demo", kind="memory", content="claim: Use WAL mode for concurrent readers.")
store(namespace="project:demo", kind="signal", content="review ready", tags=["handoff:ready"])
stats(namespace="project:demo")
browse(namespace="project:demo", limit=10)
recall(namespace="project:demo", kind="signal", since="<last_seen_id>")
```

这条路径最能体现它的核心拆分：

- `memory` 保存学到的东西
- `signal` 传递另一个流程现在需要知道的事

如果你不是在一个已经注册好 MCP 的 Codex 环境里试用，而是要从零装起来，下面就是完整安装路径。

## 它怎么工作

运行时主要有四层：

1. MCP server
   - 提供存储、检查、纠错和导出相关的 MCP 工具
2. watcher
   - 观察 Codex rollout 文件
   - 写入 `session-seen`、`checkpoint`、`closeout`
3. reflex
   - 把 summary 提升成 `learn`、`gotcha`、`signal`
4. consolidation
   - 把重复出现的 `learn` 和 `gotcha` 综合成 domain note

这样做的好处是：

- 原始 session 不是最终 memory
- summary 不是最终 memory
- durable memory 默认面向 agent，而不是人类 prose
- synthesis 发生在 promotion 之后，而不是继续堆长文本

## 安装与配置

要求：

- Python 3.11+
- 启用了 MCP 的 Codex
- 带 FTS5 的 SQLite

### 1. 安装

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .[dev]
```

### 2. 创建 bridge config

把 [config.example.toml](config.example.toml) 复制到：

```text
$CODEX_HOME/mem-bridge/config.toml
```

推荐做法：

- live SQLite DB 保留在每台机器本地
- 共享 profile 或 source vault 可以放在 NAS 或共享存储
- 真正需要多机实时共享写入时，再切到 hosted backend

### 3. 在 Codex 里注册 MCP server

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

### 4. 启动服务

启动 MCP server：

```powershell
.\.venv\Scripts\python.exe -m agent_mem_bridge
```

启动后台 bridge service：

```powershell
.\.venv\Scripts\python.exe .\scripts\run_mem_bridge_service.py
```

只跑一轮：

```powershell
$env:AGENT_MEMORY_BRIDGE_RUN_ONCE = "1"
.\.venv\Scripts\python.exe .\scripts\run_mem_bridge_service.py
```

可选的开机启动安装：

```powershell
.\scripts\install_startup_watcher.ps1
```

### 可选：构建本地 Docker 镜像

```powershell
docker build -t agent-memory-bridge:local .
docker --context desktop-linux run --rm -i agent-memory-bridge:local
```

容器默认入口会直接启动 stdio MCP server，也就是 `python -m agent_mem_bridge`。

## MCP API

公开接口刻意保持很小：

- `store`
- `recall`
- `browse`
- `stats`
- `forget`
- `promote`
- `export`

常见 `store` 字段：

- `namespace`
- `content`
- `kind`
- `tags`
- `session_id`
- `actor`
- `title`
- `correlation_id`
- `source_app`

常见 `recall` 字段：

- `namespace`
- `query`
- `kind`
- `tags_any`
- `session_id`
- `actor`
- `correlation_id`
- `since`
- `limit`

## 典型 namespace

- `global`：适合作为默认共享 bucket
- `project:<workspace>`
- `domain:<name>`
- 团队自己导入的 profile namespace

这个 framework 本身是 **profile-agnostic** 的。  
可以在它上面叠加某个 operator profile，但 bridge 本身不应该绑定一个固定 persona。

如果你是第一次用，先从 `global` 开始最自然；等需要项目隔离时，再引入 `project:<workspace>`。

## Signal handoff 示例

`signal` 这一层是给协调用的，不是给长期记忆用的。

Agent A：

```text
store(
  namespace="project:foo",
  kind="signal",
  content="frontend review ready",
  tags=["handoff:ready", "team:frontend"],
  correlation_id="ticket-142"
)
```

Agent B：

```text
recall(
  namespace="project:foo",
  kind="signal",
  since="<last_seen_id>",
  tags_any=["handoff:ready"]
)
```

这样一个流程就能轮询新的 handoff 事件，而不会把这些短期事件混进 durable memory。

## 手动提升示例

如果一条记录是有价值的，但系统给它的等级还不够，你可以原地提升：

```text
promote(id="<memory_id>", to_kind="gotcha")
```

这样会保留同一个 id，但把 title、tags 和 structured content 改成更强的记录类型。

## 导出示例

如果你想把 memory 从 bridge 里导出来，而不是直接碰 SQLite：

```text
export(namespace="project:foo", format="markdown", limit=50)
export(namespace="project:foo", format="json", kind="memory")
```

`markdown` 适合人读，`json` 适合交换或再处理，`text` 适合简单终端输出。

## 日常使用方式

推荐分层：

- 系统级 operator profile
- 系统级 memory substrate：`agentMemoryBridge`
- 项目内薄覆盖：[AGENTS.md](AGENTS.md)

启动协议见 [docs/STARTUP-PROTOCOL.md](docs/STARTUP-PROTOCOL.md)。

简化版顺序是：

1. 先查全局 operating memory
2. 再查相关 specialization memory
3. 如果有 workspace，再查 `project:<workspace>`
4. 遇到 issue-like 问题先查本地 memory 和 gotcha，再考虑外部搜索
5. 设计和实现细节必须回到 live code 验证

## 常用命令

运行测试：

```powershell
.\.venv\Scripts\python.exe -m pytest
```

运行 stdio smoke test：

```powershell
.\.venv\Scripts\python.exe .\scripts\verify_stdio.py
```

运行 benchmark：

```powershell
.\.venv\Scripts\python.exe .\scripts\run_benchmark.py
```

运行 health check：

```powershell
.\.venv\Scripts\python.exe .\scripts\run_healthcheck.py --report-path .\examples\healthcheck-report.json
```

运行 watcher health check：

```powershell
.\.venv\Scripts\python.exe .\scripts\run_watcher_healthcheck.py --report-path .\examples\watcher-health-report.json
```

强制写一次 checkpoint：

```powershell
.\.venv\Scripts\python.exe .\scripts\sync_now.py
```

## 设计取向

### Small MCP surface

桥接层只暴露一小组 MCP 工具，用来存储、检查、纠错和导出状态。目标没变：让 contract 容易理解，也容易集成。

### Local-first runtime

默认 live DB 放本地，因为 SQLite 放共享网络盘做实时写入很容易踩坑。

### Machine-first memory

agent 才是主要读者，所以 memory 优先：

- 紧凑字段
- 稳定标签
- 低 token 成本

而不是打磨成漂亮 prose。

### Layered promotion

系统目标是向上提升：

- `summary`
- `learn`
- `gotcha`
- `domain-note`

而不是把 raw summary 当最终产物。

## 当前状态

当前 foundation 已经可用：

- Codex 中 MCP autoload 可用
- project 和 session sync 可用
- recall-first workflow 可用
- reflex promotion 可用
- 第一版 domain consolidation 可用

现实状态和路线图见：

- [docs/PRODUCTION-STATUS.md](docs/PRODUCTION-STATUS.md)
- [docs/ROADMAP.md](docs/ROADMAP.md)

## Profile Import

这个 framework 可以承载导入的 operator profile，但 framework 本身保持 profile-agnostic。

## 文档

- [README.md](README.md)
- [CONTRIBUTING.md](CONTRIBUTING.md)
- [AGENTS.md](AGENTS.md)
- [docs/COMPARISON.md](docs/COMPARISON.md)
- [docs/STARTUP-PROTOCOL.md](docs/STARTUP-PROTOCOL.md)
- [docs/MEMORY-TAXONOMY.md](docs/MEMORY-TAXONOMY.md)
- [docs/PROMOTION-RULES.md](docs/PROMOTION-RULES.md)
- [docs/MODEL-ROUTING.md](docs/MODEL-ROUTING.md)
- [docs/ROADMAP.md](docs/ROADMAP.md)

## License

MIT，见 [LICENSE](LICENSE)。
