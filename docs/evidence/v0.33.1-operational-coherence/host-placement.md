# Host Placement Evidence

This is sanitized development evidence for the v0.33.1 host adapters. It
records instruction/configuration placement facts separately from semantic
memory-invocation proof.

## Observed host facts

| Host | Runtime evidence | Instruction surface | MCP registration | Semantic invocation trial |
|---|---|---|---|---|
| Codex | `codex-cli 0.147.0`; `codex debug prompt-input` exposed global and target project instructions | Project-root `AGENTS.md`; a deliberate `AGENTS.override.md` can take precedence | Native `config.toml` `[mcp_servers.<name>]` entry | NOT RUN / UNOBSERVED |
| OpenCode | `opencode 1.18.29`; source/documentation inspection of project instruction loading | Project-root `AGENTS.md` (with explicitly configured instruction files also possible) | Native `opencode.json`/`opencode.jsonc` MCP configuration | NOT RUN / UNOBSERVED |

The shared placement recommendation is a reviewed Markdown block in the
project-root `AGENTS.md`. The policy renderer does not mutate that file and
does not assume that a host loaded it. An `AGENTS.override.md`, custom
instruction setting, or other host-specific surface must be checked by the
operator before placement.

## Sources and limits

The reconnaissance used current local CLI behavior and host source/docs
references for the pinned runtimes. It verified instruction assembly/config
placement, not whether an agent chose to call `recall`. A tool being available,
`doctor`/`verify` passing, or a natural-language memory claim is insufficient
for semantic invocation evidence. No Codex or OpenCode model trial was run in
this evidence set, so no cross-host PASS is claimed.
