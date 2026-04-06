# Agent Memory Bridge

[English](README.md) | 简体中文

[![Glama](https://glama.ai/mcp/servers/zzhang82/Agent-Memory-Bridge/badges/score.svg)](https://glama.ai/mcp/servers/zzhang82/Agent-Memory-Bridge)
[![License: MIT](https://img.shields.io/badge/license-MIT-2ea44f.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB.svg)](pyproject.toml)

面向编码代理的双通道 MCP 记忆层。

当前先从 Codex 工作流开始。

大多数记忆工具会把所有状态混在一个桶里。Agent Memory Bridge 把两类状态分开：

- `memory`：值得留到后面复用的持久知识
- `signal`：用于 handoff、轮询和流程协调的短期事件

这样，代理就有地方保留下列信息：

- 关键决策
- 已验证修复
- 跨会话 handoff
- 可复用 gotcha
- 紧凑的领域知识

它沿着一条小而清楚的提升路径工作：

`session -> summary -> learn -> gotcha -> domain-note`

## 这个项目想解决什么

编码代理在跨会话时会丢掉太多状态。很多记忆系统最后会落到三种情况之一：

- 记忆被困在某一个应用或某一个模型里
- 还没证明检索真有价值，就先上重型基础设施
- 把对话原文当记忆，最后变成噪音仓库

这个项目走的是更克制的一条路：

- 从第一天起就是 MCP-native
- 本地优先
- 用 SQLite + FTS5，不先上重型服务
- 自动把 session 输出提升成可复用记忆

## 它有什么不同

1. 它面向编码代理工作流，而不是通用笔记存储。
2. 它把持久知识和协调信号拆开处理。
3. 它重点是把 session 输出提升成紧凑、机器可读的记忆，而不是把摘要当成最终产物。
4. 它默认 local-first，而且运行形态可检查。

如果你要的是更大的记忆平台，带 SDK、dashboard、connectors、多种应用接入面，那么 OpenMemory 或 Mem0 更接近那种形态。

更完整的定位说明见 [docs/COMPARISON.md](docs/COMPARISON.md)。

## 5 分钟上手路径

在 Codex 里注册好 MCP 之后，最短的有效路径是：

1. 先写一条持久记忆
2. 再写一条协调信号
3. 不用碰 SQLite，直接看看命名空间里有什么

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

- live SQLite 数据库保留在每台机器本地
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

可选：构建本地 Docker 镜像

```powershell
docker build -t agent-memory-bridge:local .
docker --context desktop-linux run --rm -i agent-memory-bridge:local
```

## MCP 工具

MCP 接口保持得很小，也很实用：

- `store` 和 `recall`：写入和读取桥里的状态
- `browse` 和 `stats`：先看里面有什么
- `forget` 和 `promote`：修正错误条目或提升条目等级
- `export`：把记忆导出成 Markdown、JSON 或纯文本

## 命名空间

最自然的起步方式是：

- `global`：默认共享 bucket
- `project:<workspace>`：项目级记忆
- `domain:<name>`：可复用的领域知识

这个 framework 本身是 profile-agnostic 的。你可以在上面叠加某个 operator profile，但桥本身不绑定某一个 persona 或某一种协议。

## 可检查性与健康检查

这个桥的目标是可检查，不是黑盒：

- `browse`、`stats`、`forget`、`export` 让你不用打开 SQLite 也能检查和修正状态
- watcher health check 会验证 Codex rollout 文件是否还能解析成可用 summary
- 当前测试套件结果是 `53 passed`

常用命令：

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe .\scripts\verify_stdio.py
.\.venv\Scripts\python.exe .\scripts\run_healthcheck.py --report-path .\examples\healthcheck-report.json
.\.venv\Scripts\python.exe .\scripts\run_watcher_healthcheck.py --report-path .\examples\watcher-health-report.json
```

## 更多文档

- [CONTRIBUTING.md](CONTRIBUTING.md)
- [AGENTS.md](AGENTS.md)
- [docs/COMPARISON.md](docs/COMPARISON.md)
- [docs/STARTUP-PROTOCOL.md](docs/STARTUP-PROTOCOL.md)
- [docs/MEMORY-TAXONOMY.md](docs/MEMORY-TAXONOMY.md)
- [docs/PROMOTION-RULES.md](docs/PROMOTION-RULES.md)
- [docs/MODEL-ROUTING.md](docs/MODEL-ROUTING.md)
- [docs/ROADMAP.md](docs/ROADMAP.md)
- [docs/PRODUCTION-STATUS.md](docs/PRODUCTION-STATUS.md)

## License

MIT，见 [LICENSE](LICENSE)。
