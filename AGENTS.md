# Agent Memory Bridge Repo Instructions

This repository assumes a system-level operator profile already exists.

Use the installed global operator profile together with `agentMemoryBridge` as the primary memory surface.

## Session Start

1. Follow the startup order in [docs/STARTUP-PROTOCOL.md](docs/STARTUP-PROTOCOL.md)
2. Recall `project:mem-store` for repository-specific context
3. Check the live codebase before trusting any recalled implementation detail

## Repo-Specific Guidance

- Keep the bridge core small and explicit
- Preserve the `store` / `recall` contract unless there is a deliberate migration plan
- Keep machine-readable memory formats compact
- Treat worker execution as a separate layer above the bridge core

## Writeback

- Cross-project operating lessons belong in the active global operating namespace
- Repository-specific lessons belong in `project:mem-store`
- Do not dump long prose summaries when a compact structured record is enough
