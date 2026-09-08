### 🏛️ Architecture Map: Agent Memory Bridge

- **Entrance (入口)**: `src/agent_mem_bridge/server.py` and `src/agent_mem_bridge/cli.py` — *Intent: public MCP tool registration and operator-facing CLI entry points.*
- **Core Domain (核心领域)**: `src/agent_mem_bridge/repository.py`, `src/agent_mem_bridge/storage.py`, `src/agent_mem_bridge/schema.py`, and `src/agent_mem_bridge/query.py` — *Identity: durable memory, Signal lifecycle, schema authority, and governed retrieval.*
- **Infrastructure (基础设施)**: `src/agent_mem_bridge/paths.py`, `src/agent_mem_bridge/database_maintenance.py`, `src/agent_mem_bridge/client_config.py`, `src/agent_mem_bridge/setup_planner.py`, and `src/agent_mem_bridge/setup_apply.py` — *Role: local paths, maintenance, client configuration rendering, and reversible setup operations.*
- **Patterns (组织模式)**: modular layered local-first service: public MCP/CLI entrances over durable storage and derived retrieval/projection/evidence layers.

### 🔍 Evidence Trail (证据链)

- **Claim**: Public interfaces are deliberately thinner than the internal evidence and projection layers.
- **Support**: `AGENTS.md` defines `server.py` as the public MCP surface and separates runtime, benchmark, and operational entry points; `src/agent_mem_bridge/public_surface.py` checks the public inventory.
- **Confidence**: High
- **Blind Spots**: This map intentionally records paths and configuration metadata, not function-level control flow or every helper module.

- **Claim**: SQLite/WAL rows are the durable authority and reports, indexes, and compiled context are derived.
- **Support**: `AGENTS.md`, `docs/ARCHITECTURE.md`, `src/agent_mem_bridge/schema.py`, and `src/agent_mem_bridge/repository.py`.
- **Confidence**: High
- **Blind Spots**: Runtime liveness and external client behavior are not inferred from this repository map.
