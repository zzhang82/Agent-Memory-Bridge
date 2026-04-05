# Promotion Rules

Last updated: 2026-04-04

## Purpose

The bridge becomes useful when it promotes memory upward instead of storing everything at the same level.

This document defines the promotion ladder:

- `summary -> learn -> gotcha -> domain-note`

## Core Rule

Do not promote based only on verbosity.

Promote based on:

- reuse value
- confidence
- recurrence
- actionability

## Stage 1: Summary

Input:
- one completed or idle Codex session
- one subagent rollout
- one manual closeout

Goal:
- preserve compact traceability

Promotion test:
- does this session contain a clear decision, constraint, failure, preference, or validated fix?

If no:
- keep as `summary` only

If yes:
- extract one or more `learn` items

## Stage 2: Learn

A `summary` should be promoted to `learn` when the content is likely useful in a later session even if the original transcript is unavailable.

Promote when the item is one of:

- a decision
- a constraint
- a user preference
- a validated implementation rule
- a failure with confirmed resolution
- a routing/orchestration lesson

Do not promote when the item is only:

- status chatter
- temporary planning noise
- unvalidated speculation
- duplicated restatement without new meaning

Recommended `learn` fields:

- one-sentence claim
- why it matters
- optional evidence
- tags for domain/topic/project

## Stage 3: Gotcha

A `learn` should be promoted to `gotcha` when it has strong future reuse as a problem-pattern.

Promote when these are true:

1. there is a concrete symptom
2. there is a concrete fix or mitigation
3. the issue is likely to recur across projects or sessions

Strong candidates:

- integration mistakes
- synchronization issues
- indexing/search pitfalls
- orchestration failures
- tool contract drift
- environment/path confusion

Each gotcha should answer:

- what broke?
- how does it show up?
- what caused it?
- what fixed it?
- what tags or links should retrieve it quickly?

## Stage 4: Domain Note

Promote to `domain-note` only after several related learns or gotchas exist.

Use a domain note when the memory is no longer about one incident.

It should summarize:

- recurring practices
- default choices
- common failure modes
- preferred model routing
- stability rules

Domain notes should be slower-moving and reviewed more carefully than summaries or learns.

## Confidence Rules

Promotion should be shaped by confidence.

Suggested labels:

- `confidence:tentative`
- `confidence:observed`
- `confidence:validated`

Rules:

- `summary` may contain mixed confidence
- `learn` should usually be at least `observed`
- `gotcha` should usually be `validated`
- `domain-note` should be built from mostly validated items

## Scope Rules

Promotion also depends on scope.

### Keep Project-Local When

- the lesson depends on one codebase
- the naming is too specific
- the fix is unlikely to generalize

### Promote To Global Operating Memory When

- the lesson affects how the active operator profile should operate anywhere
- it changes model routing, verification, or orchestration
- it applies across projects

### Promote To Domain Note When

- multiple projects point to the same stable pattern

## Reflex Rules

Reflex should not write final knowledge directly from raw chat.

Reflex should do this instead:

1. detect likely high-signal patterns from summaries
2. search for related learns and gotchas first
3. propose or write a promoted memory only when the signal is strong enough

Good reflex triggers:

- `error`
- `bug`
- `regression`
- `fix`
- `wrong db`
- `fts`
- `sqlite`
- `orchestration`
- `subagent`
- `handoff`
- `drift`
- `duplicate`

## Retrieval-First Rule

Before reflex promotes a new `gotcha`, it should check whether an equivalent gotcha already exists.

If yes:
- link to the existing one
- optionally strengthen it with new evidence

If no:
- create a new gotcha candidate

This avoids fragmented duplicates like:

- one gotcha for "wrong DB"
- another for "watcher DB mismatch"
- another for "MCP reading different database"

All three should resolve to one pattern if they are the same issue.

## Minimal Heuristics For First Implementation

The first useful implementation can stay simple.

Summary -> Learn:
- look for explicit decision/fix/lesson/mistake language

Learn -> Gotcha:
- require symptom + fix + validated outcome

Gotcha -> Domain Note:
- require multiple related gotchas or learns under the same domain

Do not require embeddings for this phase.

## Example Promotion Path

1. Session summary:
   "Watcher wrote to one DB while MCP read another."

2. Learn:
   "Automation and interactive recall must share one canonical runtime path."

3. Gotcha:
   "Wrong DB split causes healthy sync logs but missing recall."

4. Domain note:
   "In orchestration and memory systems, canonical runtime paths are mandatory for trust."

## Product Impact

These rules make the bridge useful because they turn memory into layered operational knowledge:

- traceable at the session level
- reusable at the learn level
- fast to recall at the gotcha level
- judgment-shaping at the domain level
