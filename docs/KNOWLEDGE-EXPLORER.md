# Knowledge Explorer — v0.31 MVP

Knowledge Explorer is a **read-only, bounded, rebuildable projection** over project knowledge that Agent Memory Bridge already owns. It makes relationships explorable without creating a graph database or a new authority.

> **Graph is projection, not authority.**

## User-visible contract

From a source checkout with an existing namespace binding, run:

```bash
agent-memory-bridge explore --namespace project:amb
agent-memory-bridge explore --namespace project:amb --format json
```

The default Markdown output shows bounded relationships, the authority of each target, and supporting source references. JSON output is deterministic and suitable for future visualization clients. The command is local and read-only; it does not add an MCP tool.

## Projection sources

| Source | Explorer role | Authority | Freshness/rebuild rule |
|---|---|---|---|
| Bound repository snapshot | Repository-derived nodes and relationships | `derived_repository` | Current clean commit only; stale, dirty, moved, unavailable, or missing snapshots contribute no repository nodes |
| Existing governed durable memory | Explicit decision/constraint nodes and existing relation metadata after existing lifecycle/eligibility checks | `governed_durable_memory` | Original memory IDs remain authoritative; Explorer never rewrites them |
| Dynamic State | Not re-modeled by this MVP | Existing Dynamic State authority | Future Explorer extensions must reference existing snapshots rather than duplicate state |
| Episodes/evidence | Not re-modeled by this MVP | Existing episode/evidence authority | Future extensions must retain the existing evidence surface |

Every node carries `authority` and `source_ref`; every edge carries `evidence`. A relationship does not strengthen the authority of its source.

## Relationship vocabulary

Repository relationships are mapped only from existing extracted fact keys: `uses`, `tests_with`, `uses_ci`, `governed_by`, `contains`, `uses_storage`, and the bounded fallback `has_fact`. Durable memory relationships are emitted only for explicit structured `record_type: decision` or `record_type: constraint` records that pass the existing applicable governance semantics: revision-predecessor suppression, shared validity status, structured-metadata validation, and degraded-lineage suppression. Explorer does not invent a second lifecycle policy. Relationships are emitted using `has_decision` or `has_constraint`, plus existing `supports`, `contradicts`, `supersedes`, and `depends_on` metadata.

A relation target is resolved by durable memory ID and must itself exist in the same namespace and pass those eligibility checks. The primary `--limit` is applied after governance suppression: Explorer scans only a bounded raw window, refills from later eligible records, and never performs an unbounded scan. Referenced targets receive a separate hard-bounded direct-by-ID lookup window, restricted to the same namespace and backed by a query-only connection, so an eligible target outside the primary scan can still be resolved without expanding the graph indefinitely. Missing targets, existing-but-ineligible targets, and targets beyond the relation-resolution budget are distinguished. None becomes an active node or active edge. The bounded `diagnostics` section may report `suppressed_memory`, `unresolved_relation`, or `relation_resolution_budget_exhausted` records, which are structurally distinct from active project knowledge.

No LLM-generated edges, filename-only architectural inference, ranking, learning, promotion, or durable writeback is performed.

## `inspect` distinction

`inspect` explains why information appeared for a question and why it was included or excluded. `explore` answers what currently eligible project knowledge exists and how eligible relationships connect it. Explorer does not replace inspect; both remain read-only and provenance-bearing.

## Deliberate non-goals

This milestone does not add Neo4j, a graph database, a vector database, embeddings-first indexing, whole-repository AST or call-graph indexing, a language-server dependency, a web dashboard, remote telemetry, a daemon, session hooks, automatic learning, automatic promotion, automatic ranking adaptation, a new durable authority, or MCP tool #18. Schema v12, 17 public MCP tools, and the existing tool-schema digest remain unchanged.

## Acceptance evidence

The focused test suite proves deterministic projection, authority separation, source and commit provenance, explicit durable decision/constraint relationships, stale fail-closed behavior, deletion and rebuild equivalence, concurrent read safety, read-only database behavior, and public MCP-surface stability. The complete existing suite and release/public-surface/onboarding/reliability gates remain required before merge.
