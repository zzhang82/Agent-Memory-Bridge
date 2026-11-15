# Plain-Words to Code Glossary

`README.md`, `Explore`, `Inspect`, and CLI `--help` use everyday feature names so you do not need internal storage or protocol vocabulary to use Agent Memory Bridge.

Use this page when you read Python source files under `src/agent_mem_bridge/`, inspect `--json` or `--technical` CLI output, or wire raw MCP tool calls. The 17 public MCP tool names and SQLite schema `v12` stay unchanged on the wire.

## Feature-to-Code Mapping

| Plain words (README, Explore, Inspect, CLI help) | Code, JSON, and MCP wire names | When you see the code name |
|---|---|---|
| **Project memory name** | `namespace` (`project:<name>`, `domain:<name>`, `global`) | Passed to `store`, `recall`, `browse`, `stats`, `export`, `explore`, and `inspect` to scope records to one project or domain. |
| **Decisions and rules** | `kind="memory"` with `record_type: decision` or `record_type: constraint` | Stored in the `memories` and `memory_metadata` tables; shown under `CONVERSATION / WHY` in `explore`. |
| **Replacing outdated decisions** | `revise`, `relations.supersedes`, `superseded_revision`, `eligibility="historical"` | Calling `revise` creates a new record with a `supersedes` edge to the older ID so default `recall` hides the replaced version while keeping audit history. |
| **Adding notes or tags** | `annotate`, `annotations` | Enriches `title`, `tags`, or `provenance` on an existing `memory_id` without changing its `content` hash. |
| **Agent handoffs** | `kind="signal"`, `claim_signal`, `ack_signal`, `signal_status` (`pending`, `claimed`, `acked`, `expired`) | Short-lived coordination items polled via `recall(kind="signal", query="", since=...)` and cleaned up with `signal-cleanup`. |
| **Claim timeouts** | `lease`, `lease_seconds`, `extend_signal_lease`, `lease_expires_at`, `claimed_by` | Controls how long one worker holds a claimed `signal` before another worker can reclaim it. |
| **Tracked work runs** | `begin_run`, `record_run_event`, `get_run`, `complete_run`, `episode`, `run_id`, `work_item_id` | Append-only run ledger (`run_ledger.py`) recording structured steps, blockers, and outcomes for multi-step agent tasks. |
| **Proof receipts** | `recall_receipt`, `verification_receipt`, `attestation`, `mint-verification-receipt` | Signed tokens or digests proving which records surfaced during `recall` or that a `governed-v2` run passed human/deterministic verification. |
| **Code facts vs. reasons** | `derived_repository` (`WHAT`) vs. `governed_durable_memory` (`WHY`) | `bootstrap-repo` and `project init` extract rebuildable `derived_repository` facts from a clean Git commit; confirmed human reasons live in `governed_durable_memory`. |
| **Review inbox** | `needs_review`, `is_learning_candidate`, `review-queue`, `review-workflow` | Hidden staging lane for candidate lessons from `audit-memory`, `audit-logs`, `consolidate-runs`, or `lifecycle-hook` capture before human approval. |
| **Change history status** | `lineage_status` (`intact`, `degraded`), `validity_status` (`current`, `expired`, `future`, `invalid`) | Governance checks in `recall`, `explore`, and `inspect` that leave out expired or broken-history records. |

## Connectors and Triggers Vocabulary

| Plain words | CLI command or code symbol | Exact contract |
|---|---|---|
| **One-command client setup** | `agent-memory-bridge connect <client>` (`connectors.py`) | Merges the AMB MCP server entry into the native config file for `claude-code`, `cursor`, `codex`, `cline`, or `vscode` with backup and rollback. |
| **Three-tier connection check** | `connect <client> --check` -> `config_valid`, `server_spawn_verified`, `host_registration_status` | Separates config-file syntax (`config_valid`) and local MCP `tools/list` verification of `store` + `recall` (`server_spawn_verified`) from whether the host application itself reloaded (`host_cli_verified` vs. `host_reload_required`). |
| **Bridge acceptance check** | `connect --bridge-check --from-client <A> --to-client <B>` | Boots two separate MCP processes from two client configs sharing one `AGENT_MEMORY_BRIDGE_HOME`, stores a decision in Client A, and recalls it in Client B. |
| **Per-client trigger reminders** | `agent-memory-bridge triggers {install,matrix,test}` and `trigger-hook --client <client>` (`triggers.py`) | Translates host-specific hook payloads (`hook_event_name`, `tool_name`, `stop_hook_active`, `loop_count`) into host-specific reminder responses (`hookSpecificOutput`, `additional_context`, `followup_message`, `additionalContext`) outside `lifecycle-hook`. |
| **Trigger support levels** | `native_hook`, `deferred_hook`, `trust_gated_hook`, `instruction_rule` | The four honest cell types in the 6 moments × 5 clients trigger matrix. Clients without command hooks for a moment use `instruction_rule` or `deferred_hook`, never fake parity. |
| **Host-neutral lifecycle hook** | `agent-memory-bridge lifecycle-hook` (`lifecycle_activation.py`) | Low-level generic JSON stdin hook (`kind` in `session_start`, `task_prompt`, `compaction`, `ignore`, `capture`). Rejects raw vendor hook keys as `ignore`. |
