# Production Status

Last updated: 2026-04-07 (America/New_York)

## Current Runtime Shape

`agent-memory-bridge` now has seven cooperating runtime surfaces:

1. A stdio MCP server with `store` and `recall`
2. A shared SQLite/WAL + FTS5 bridge database under Codex home
3. A Codex session watcher that writes idle-rollout closeouts
4. An active-session checkpoint writer that stores mid-session summaries
5. A reflex pass that promotes summaries into `learn`, `gotcha`, and `domain-note`
6. An optional classifier-backed enrichment layer that can run in `shadow` or `assist` mode while rules remain the fallback
7. A recall-first helper that checks local bridge memory before external search for issue-like prompts

All of these now converge on one canonical bridge home:

- Home: `$CODEX_HOME/mem-bridge`
- DB: `$CODEX_HOME/mem-bridge/bridge.db`
- Notes: `$CODEX_HOME/mem-bridge/session-notes`
- MCP logs: `$CODEX_HOME/mem-bridge/logs`
- Watcher logs: `$CODEX_HOME/mem-bridge/watcher-logs`
- Watcher state: `$CODEX_HOME/mem-bridge/watcher-state.json`
- Reflex state: `$CODEX_HOME/mem-bridge/reflex-state.json`

Codex now autoloads this bridge through the global config at:

- `$CODEX_HOME/config.toml`

That means the bridge is no longer just a repo-local experiment. It is a shared Codex service with cross-project storage and recall.

For naming:

- `agentMemoryBridge` is the canonical Codex server name
- one legacy compatibility name remains available during transition

## Verified On 2026-04-07

- `pytest` passes: `80 passed`
- The MCP server autoloads successfully in Codex
- `recall(...)` works in-session through `agentMemoryBridge`
- The canonical benchmark fixture now includes overlap-heavy retrieval cases for:
  - review queue ownership
  - release cutover handoff
  - context-compaction checklist vs bridge-note ambiguity
- The bridge still holds `memory_expected_top1_accuracy = 1.0` on the current canonical fixture while the simple file-scan baseline sits at `0.636`
- Cross-project namespaces are active in the shared DB, including:
  - `project:mem-store`
  - `project:ç®€åŽ†`
- Unicode namespaces recall correctly through the MCP layer
- Auto-closeouts are stored with:
  - `session_id`
  - `correlation_id`
  - `thread:*`
  - `parent-thread:*`
  - `agent:*`
  - `agent-role:*`
- Mid-session checkpoints are now stored during active work when the rollout changes meaningfully
- Reflex now promotes compact machine-first records into the active global operating namespace for:
  - `kind:learn`
  - `kind:gotcha`
  - `kind:domain-note`
- Reflex can now run classifier-assisted enrichment in `shadow` or `assist` mode without removing the deterministic keyword/rule path
- Reviewed classifier calibration now compares raw classifier tags, retained classifier tags, expected tags, and keyword fallback tags before widening assist usage
- Assist-mode enrichment now honors a classifier `minimum_confidence` gate so low-confidence outputs stay visible in calibration without silently entering promoted records
- Reviewed calibration is now slice-aware, which makes it easier to see that coordination/runtime slices are stronger today than retrieval-heavy slices
- Generic signal claim selection now applies a small fairness bias inside the oldest eligible window so one polling consumer does not immediately reclaim its own stale work when other pending signals exist
- Recall-first can now surface:
  - current project memory
  - global learns
  - global gotchas
  - global domain notes

## What Is Production-Capable Today

This is production-capable today for:

- shared persistent memory storage across projects
- Codex MCP recall and store access
- post-process session closeouts
- active-session checkpoint capture
- Obsidian-style tag and wikilink extraction
- subagent lineage tracking
- compact machine-readable promotion records
- optional classifier-assisted enrichment with safe fallback
- recall-first local retrieval before external search
- one canonical bridge database for both automation and interactive use

This is enough to make the bridge genuinely useful during real Codex work instead of only as a design exercise.

## Comparison Vs Earlier Internal Build Playbook

An earlier internal build playbook set a useful validation standard for the foundation, even though it is no longer part of the public repo.

### Matches

- Step 1 transport proof: done
- Step 2 schema-first thinking: mostly done
- Step 3 MVP with `store` + `recall`: done
- SQLite/WAL + FTS5 foundation: done
- punctuation-safe recall path: done
- benchmark harness exists: done
- monitoring/state files exist for watcher and reflex: done
- real host integration with Codex session files: done

### Partial

- Step 0 metric discipline: improved, but still not the main control loop
  - We have benchmarks and tests now, but not one primary metric that governs later feature decisions.
- Step 5 write-side calibration: partial
  - Checkpoint and reflex promotion exist, but durable-memory selection is still heuristic rather than calibrated on reviewed samples.
- Step 8 monitoring before shipping: partial
  - We have logs and state, but not yet a strong hit-rate or promotion-quality dashboard.
- Step 9 observation window: partial
  - We have real cross-project usage, but not yet a deliberate two-session review loop that drives tuning with measured miss and noise rates.

### Still Missing Against The Playbook Standard

- a calibrated acceptance threshold for write-side promotion
- a formal promotion-quality review loop
- a tighter benchmark corpus for realistic cross-project gotcha retrieval

The good news is the foundation now deserves that calibration work. Earlier, it would have been premature.

## Comparison Vs Gemini Feedback

The feedback highlighted four value points. Here is the honest status.

### 1. Capture Before Automatic Compaction

Status: partial

What we have:

- active-session checkpoints
- manual `sync_now.py`
- automatic closeouts after idle

What we do not have:

- a true pre-compaction native Codex hook
- guaranteed capture before any model-side summarization or context loss

So we are better than end-of-session-only storage, but not yet at the original "capture before loss" ideal.

### 2. Unique IDs And Append-Style Traceability

Status: mostly matches

What we have:

- `session_id`
- `correlation_id`
- `thread:*`
- `parent-thread:*`
- append-style `signal` storage
- sortable stable memory IDs

What is still weak:

- identical non-signal memories can still deduplicate across sessions, which is good for reusable memory but not perfect for full audit trace reconstruction

So the journal is traceable enough for current use, but not yet a strict append-only audit ledger for every repeated fact.

### 3. Obsidian Tags As Semantic Routing

Status: matches the intended direction

What we have:

- `#tags` extracted into stored tags
- `[[wikilinks]]` extracted into link tags
- domain/topic/kind/problem/fix style tags
- retrieval that can use both text query and tag hints

What is still missing:

- richer domain synthesis
- automated topic clustering across many sessions
- stronger semantic bridges beyond explicit tags and FTS

This is good enough for structured routing now, but not yet an autonomous knowledge graph.

### 4. Shared Blackboard / PubSub Hub

Status: partial

What we have:

- `signal` entries
- `since` polling
- shared DB for multiple projects and sessions
- handoff-friendly lineage and tags

What we do not have:

- active subscriber wake-up
- signal consumer loop
- dispatch or orchestration on top of stored signals

So today it is a blackboard with polling, not a fully active pubsub bus.

## Comparison Vs The Later Project Direction

The later design direction added a more ambitious memory ladder:

- `summary`
- `learn`
- `gotcha`
- `domain-note`

Status:

- `summary`: working
- `learn`: working
- `gotcha`: working but still early
- `domain-note`: working but still shallow

The biggest remaining quality gap is not storage. It is promotion quality.

Right now:

- checkpoint timing is materially better
- record format is machine-first
- recall-first is useful

But:

- some checkpoint content still inherits chat noise from the source rollout
- `gotcha` coverage is still narrow
- `domain-note` synthesis is still rule-based and light

## Honest Gaps

These are the real remaining gaps, ordered by product importance.

1. Pre-compaction capture is still missing.
2. Durable-memory selection is still heuristic, not calibrated.
3. `gotcha` extraction is useful but still sparse and somewhat literal.
4. Domain synthesis is not yet doing the "auto dream" style grouping the project wants.
5. The blackboard exists, but no active consumer loop turns signals into orchestration.
6. Audit vs dedup is still a tradeoff we have not fully split into separate modes.

## Current Best Next Milestone

The next milestone should not be "more storage." It should be "calibrated learning."

That means:

1. Expand the reviewed calibration set before widening assist-mode use further, especially on retrieval-heavy and domain-synthesis cases
2. Add a stronger durable-event scorer for checkpoint and closeout lines
3. Expand `gotcha` extraction beyond a few known patterns
4. Build richer domain/topic synthesis across many sessions
5. Add retry boundaries and stronger coordination checks on top of `since`

That is the path from useful memory store to actual reflex and learning system.

## Bottom Line

Compared with the original playbook and feedback, `agent-memory-bridge` is now past the "foundation only" stage.

It already works as:

- a shared MCP memory backend
- a cross-project Codex memory bridge
- a post-process checkpoint and closeout capture system
- a first-generation reflex promoter

It does not yet fully satisfy the strongest original promise:

- capture before loss
- learn with calibrated durable selection
- synthesize domains automatically
- dispatch work through active pubsub

That is now the right frontier.

