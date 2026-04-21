# Memory Taxonomy

Last updated: 2026-04-04

## Purpose

`agent-memory-bridge` should not become a dump of chat summaries.

Its long-term value comes from storing the right kinds of memory at the right level:

- session-level context for traceability
- project-level learning for continuity
- cross-project gotchas for reuse
- domain-level synthesis for reflex and judgment

This document defines the memory shapes the bridge should support as the product grows beyond the v1 journal foundation.

## Design Principle

Use both:

- domain-based grouping for stable long-term organization
- topic-based grouping for flexible retrieval

Obsidian-style tags and links connect the two.

That means a memory is not trapped in one folder or one tree. A single item may belong to:

- one project
- one or more domains
- one or more topics
- one or more problem/fix patterns

## Core Memory Levels

### 1. Session Summary

Purpose:
- preserve a compact trace of one session or subagent rollout

What it contains:
- what happened
- who worked on it
- major actions
- major outputs
- pending next step

Characteristics:
- lowest-value durable layer
- useful as source material
- not the final long-term knowledge object

Typical tags:
- `kind:summary`
- `project:*`
- `source:agent-client`
- `agent:*`
- `session:*`

### 2. Learn

Purpose:
- capture something we should remember next time

What it contains:
- a decision
- a constraint
- a validated finding
- a successful pattern
- a mistake and correction

Characteristics:
- durable
- specific enough to reuse
- often project-rooted first

Typical tags:
- `kind:learn`
- `domain:*`
- `topic:*`
- `project:*`
- `confidence:validated`

### 3. Gotcha

Purpose:
- capture a repeatable problem-pattern so future sessions check here before searching externally

What it contains:
- symptom
- cause
- fix
- warning signs
- links to related domains and projects

Characteristics:
- high-value cross-project memory
- should be easy to retrieve from short queries
- should become part of the reflex path

Typical tags:
- `kind:gotcha`
- `problem:*`
- `symptom:*`
- `fix:*`
- `domain:*`
- `topic:*`
- `confidence:validated`

Example:
- "watcher wrote to one DB while MCP server read another"

### 4. Domain Note

Purpose:
- synthesize multiple learns and gotchas into a stable operating note

What it contains:
- recurring patterns
- default practices
- common failure modes
- recommended model/task routing

Characteristics:
- slower-moving
- global rather than project-local
- becomes part of the active global operating memory and long-term judgment

Typical tags:
- `kind:domain-note`
- `domain:*`
- `scope:global`

Examples:
- `domain:orchestration`
- `domain:memory-store`
- `domain:sqlite`
- `domain:obsidian`

### 5. Signal

Purpose:
- lightweight coordination and polling

What it contains:
- handoff request
- review needed
- checkpoint ready
- task state transition

Characteristics:
- operational, not reflective
- append-only
- never auto-deduplicated

Typical tags:
- `kind:signal`
- `handoff`
- `review`
- `checkpoint`

## Organizational Axes

Each memory should be addressable through several axes.

### Namespace

Use namespace as the retrieval pool.

Recommended default pools:

- `project:<name>` for project-local execution memory
- `global` or a profile-specific global namespace for operating memory
- `domain:<name>` for synthesized domain notes
- `signals` for coordination-heavy consumers when needed

### Domain

Domain is the stable long-term bucket.

Examples:
- `domain:orchestration`
- `domain:memory-bridge`
- `domain:sqlite`
- `domain:retrieval`
- `domain:obsidian`

### Topic

Topic is the finer-grained subject label.

Examples:
- `topic:fts`
- `topic:session-sync`
- `topic:subagents`
- `topic:dedup`
- `topic:handoff`

### Problem / Symptom / Fix

These are crucial for gotcha retrieval.

Examples:
- `problem:duplicate-store`
- `symptom:wrong-db`
- `fix:canonical-runtime-path`

### Project

Project keeps provenance.

Examples:
- `project:agent-memory-bridge`
- `project:read-later-agent`

## Obsidian Linking Strategy

Tags should handle filtering.

Wikilinks should handle conceptual connection.

Recommended links:
- `[[Agent Client]]`
- `[[Memory Bridge]]`
- `[[SQLite]]`
- `[[FTS]]`
- `[[Gotchas]]`
- `[[Profile Core]]`

Use tags for retrieval precision and wikilinks for navigation/synthesis.

## Retrieval Order

When solving an active problem:

1. check current project namespace
2. check global operating memory
3. check `kind:gotcha`
4. check related `domain:*` notes
5. only then search online if local memory is insufficient

This keeps external search as a fallback instead of a reflex.

## Why This Model Fits The Product

This structure supports the product goals:

- long-term continuity across sessions
- cross-project reuse
- future reflex behavior
- Obsidian-friendly tagging and linking
- inspectable and auditable memory growth

It also prevents the system from collapsing into:

- raw transcript dumping
- one giant undifferentiated memory pool
- semantic search without structure

## Near-Term Implementation Implication

The next useful automation should not be "save more chat."

It should be:

1. create `summary` as source material
2. promote durable items to `learn`
3. promote repeated fixes to `gotcha`
4. synthesize recurring patterns into `domain-note`

