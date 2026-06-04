# Authority Contract

Agent Memory Bridge stores durable engineering memory, but not every artifact
around the bridge has the same authority. This contract explains what users and
contributors can safely treat as source of truth.

For cross-layer AMB/AMH/runtime-provider integrations, keep this contract as
the in-repository durable-authority source for AMB stored records and governed
mutation. Runtime or harness-layer verb ownership maps may reference this
contract, but they do not replace it.

## Authority Layers

### 1. Database records

The local SQLite database is the operational source of truth for stored memory
and signals.

- Memory records, signal records, metadata, tags, namespaces, and relation fields
  are authoritative for runtime recall.
- Deleting or mutating database records changes what future agents can recover.
- Export files, rendered views, and summaries do not override database records.

Database changes should happen through the bridge API, CLI, migrations, or
reviewed maintenance scripts. Manual database edits are possible, but should be
treated as operational maintenance and reviewed carefully.

### 2. Human-correctable records

Stored records are allowed to be corrected by humans when they are stale,
wrong, private, unsafe, or too noisy.

- `forget` may remove records that should no longer be used.
- `promote` may strengthen a useful memory into a durable record type.
- Replacement records may supersede older guidance instead of rewriting history.
- Safety, privacy, and correctness fixes should be preferred over preserving a
  flawed generated memory.

Human correction is part of the authority model. The bridge should make memory
auditable and fixable, not immutable.

### 3. Compiled views

Compiled views are generated selections or renderings of stored records.
Examples include startup context, task-time context, exports, dashboards, and
benchmark previews.

- Compiled views are not source of truth.
- They may be regenerated when ranking, filtering, rendering, or suppression
  logic changes.
- They should cite or preserve enough record identifiers to make review possible.
- A bad compiled view is fixed by improving selection policy, correcting source
  records, or both.

Compiled views are useful working material, not durable authority by themselves.

### 4. Derived indexes

Search indexes and semantic sidecars are cache layers over the database records.
They are useful for recall quality and performance, but they do not own memory.

- `memories` remains the source of truth.
- `memories_fts` is the lexical FTS5 cache.
- `memory_embeddings` is the optional local semantic sidecar cache.
- Retrieval provider settings and embedding commands choose how a sidecar is
  generated; they are not durable memory authority.
- Missing, stale, or orphan index rows should be repaired by rebuilding the
  derived index, not by editing durable records.
- Embedding vectors may be warmed lazily by semantic or hybrid recall, but their
  content is always derived from current memory rows and content hashes.
- Embedding vectors may also be warmed by the optional service scheduler. That
  scheduler is cache maintenance only; it does not approve memories, promote
  records, or change retrieval mode.

Use `agent-memory-bridge index-health` to inspect cache drift and
`agent-memory-bridge index-rebuild` to rebuild cache tables. These commands must
not change the count or content of `memories` rows.

## What Can Be Regenerated

These artifacts can be rebuilt from source records and code:

- startup-context renderings
- task-time context renderings
- exports and Markdown snapshots
- dashboards, reports, and review queues
- search indexes, semantic sidecars, and other derived caches

Regeneration should not require changing the public MCP tool surface.

## Learning Candidate Review Queue

Learning candidates are policy-gated staging records, not ordinary durable
memory. The store boundary must recompute or verify the writeback decision before
persisting a candidate; callers are not trusted to provide authoritative policy
output. Candidate records are hidden from ordinary recall, browse, export, and
stats unless explicitly requested with learning-candidate review tags such as
`kind:learning-candidate`, `kind:learning-review`, or `candidate_status:*`.

Candidate records may help reviewers decide what to promote later, but the
candidate itself is not an approved durable memory until a reviewed promotion or
replacement path creates the final record.

## What Requires Review

Review is expected before:

- changing schemas or migrations
- deleting large groups of records
- changing promotion or suppression policy
- changing generated context ranking in a way that affects startup or task-time
  behavior
- publishing generated docs, examples, or release material as public guidance

The goal is to keep the bridge inspectable while still allowing implementation
details to evolve.

## What Is Not Source Of Truth

Do not treat these as authoritative by themselves:

- a single agent response
- chat transcript history
- copied context packets
- generated summaries without record identifiers
- exported Markdown after the database has changed
- stale benchmark snapshots

These artifacts can help explain or audit behavior, but they do not replace the
stored records and reviewed project documentation.

## Startup And Task-Time Assembly

Startup and task-time assembly compile relevant records into compact context for
an agent. This is a product behavior over the existing memory and signal tools,
not a separate public MCP contract.

Agent Memory Bridge should not add `startup_packet` or `task_packet` MCP tools
just to expose this behavior. Clients can store, recall, browse, promote,
forget, export, and coordinate through the existing surface while assembly logic
improves behind it.

This keeps the public contract small:

- records remain the durable authority
- humans can correct that authority
- compiled context can be regenerated
- startup and task-time behavior can improve without forcing client migrations
