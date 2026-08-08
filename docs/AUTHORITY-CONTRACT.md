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
- Run declarations, work-item declarations, structured events, outcomes,
  artifact references, and memory-attribution links are authoritative episode
  evidence.
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

### 5. Recall receipts and feedback

Recall receipts are 15-minute proof artifacts for explicit durable memory text
recall. Eligible `kind = "memory"` text recall issues a signed receipt,
including zero-result responses. A validated zero-result receipt may create only
a metadata-only `memory_recalled` event with zero `run_memory_links`; it cannot
create applied or rejected links or utility credit. Receipts bind the bridge
instance id, namespace, query hash, retrieval mode, database epoch, result
memory ids and ranks, issue time, and expiry.

The receipt token is HMAC-signed, so it is tamper-evident. It is not encrypted
and should be treated as sensitive metadata. A receipt is not durable authority,
does not authenticate caller provenance, and does not prove external adoption or
vendor identity.

The database epoch in a receipt is a restore-instance guard. It helps reject
receipts across restore or epoch rotation boundaries, but it is not a per-write
freshness guarantee.

Retrieval feedback is append-only shadow evidence over one receipt-bound result.
It can help humans and future tooling review retrieval quality, but it does not
mutate memory rows, indexes, belief records, recall output, ranking behavior, or
promotion policy.

### 6. Run and episode ledger

The run ledger records what a task declared, what happened, what evidence was
produced, how the run ended, and which exact memory versions were recalled or
used.

- `agent_runs` and `run_work_items` hold the declared run and task tree.
- `run_events`, `run_outcomes`, `run_artifacts`, and `run_memory_links` hold
  structured evidence. Events and outcomes are append-only; outcomes use an
  append-only supersession chain for corrections.
- Receipt tokens are not stored. Attribution keeps the verified receipt hash,
  exact memory content version, exposure rank, and optional feedback link.
- A source-linked applied or rejected record must select a prior same-run recalled
  exposure. A manual exact-version reference is retained only as review-required
  evidence and receives no shadow utility credit.
- Shadow utility counts require the linked feedback to remain its current
  effective head and the run to have a current outcome head. They remain derived
  review evidence with a fixed score of zero.
- `artifact_created` stores one server-minted version-1 artifact reference from
  a strict event payload: digest, MIME type, URI, and bounded metadata only.
  Large files, binaries, and caller-managed artifact identity or linkage stay
  outside SQLite. `get_run` returns artifact references only for its returned
  event page.
- Raw chain-of-thought, transcript, analysis, and other hidden-reasoning fields
  are outside the durable contract, including normalized case/separator variants.
- The first outcome is allowed only after every declared work item has an
  explicit terminal event (`completed`, `failed`, or `abandoned`). Corrections
  supersede that valid outcome head without inventing work-item events.

`run_state_projection`, `run_work_item_state_projection`, and
`memory_utility_shadow` are derived views. They can be checked and rebuilt from
authority rows. They do not change memory ranking, promotion, policy, prompts,
or training data by themselves.

## What Can Be Regenerated

These artifacts can be rebuilt from source records and code:

- startup-context renderings
- task-time context renderings
- exports and Markdown snapshots
- dashboards, reports, and review queues
- search indexes, semantic sidecars, and other derived caches
- current run/work-item state and shadow memory-utility projections

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
just to expose this behavior. Clients can use `store`, `recall`, `feedback`,
`browse`, `stats`, `forget`, `promote`, `annotate`, `revise`, `export`,
`begin_run`, `record_run_event`, `get_run`, `complete_run`, `claim_signal`,
`extend_signal_lease`, and `ack_signal` while assembly logic improves behind
that surface.

This keeps the public contract small:

- records remain the durable authority
- humans can correct that authority
- compiled context can be regenerated
- startup and task-time behavior can improve without forcing client migrations
